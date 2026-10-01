"""Dataset actions, as Temporal activities.

One activity per action in the de-identification pipeline. Each declares a source and a
target contract, validates its output against the target before the version is
sealed, and returns only identifiers rather than data. Returning data would put
transcripts into Temporal's workflow history, which is durable, replicated and
not classified as RAW, and that is a data leak with a very long half-life.

Models load and unload around each activity because 8 GB of VRAM will not hold
Whisper and the detection ensemble at the same time. That is a real constraint
of this machine, not a design position.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from . import config, contracts, platform_client as cp, transcribe_backend


def _key(prefix: str, name: str) -> str:
    return f"{prefix}/{name}"


def _tenant(params: dict) -> str:
    """The tenant this run belongs to, taken from the run and never guessed.

    This used to be `config.TENANT`, the worker's own environment, which made a
    multi-tenant platform's pipeline a single-tenant process: correct only
    while the tenant the worker was configured for happened to be the tenant
    whose data it was processing, with nothing enforcing that. Refused rather
    than defaulted, because a default here is exactly the failure it caused.
    """
    tenant = params.get("tenant")
    if not tenant:
        raise ValueError(
            "this run does not say which tenant it belongs to, so it will not "
            "seal anything. The workflow sets 'tenant' from the version being "
            "processed, or from the command line's own configured tenant"
        )
    return tenant


def _pipeline_principal(tenant: str) -> str:
    """The registered `pipeline_action` workload this tenant's runs act as.

    Derived from `<tenant>-pipeline` (item 63's naming convention) rather
    than read from a single worker-wide config value, for exactly the
    reason `_tenant()` above stopped reading `config.TENANT`: one worker
    process serves every tenant's runs (`config.TASK_QUEUE` is one shared
    queue, not one per tenant), so a static `config.PIPELINE_PRINCIPAL`
    would claim whichever tenant that value happened to name while actually
    processing a different one's data.
    """
    return f"{tenant}-pipeline"


def _location(params: dict, dataset_id: str) -> dict:
    """Where this step's objects go, with the bucket checked rather than assumed.

    The control plane is asked for both the prefix and the bucket. When the run
    already carries a bucket, from the version it began at, the two are compared:
    they are the same tenant's bucket resolved twice, so a disagreement means
    something moved underneath the run and sealing into either would be a guess.
    """
    where = cp.next_location(dataset_id, _tenant(params))

    if where["backend"] not in cp.SERVED_BACKENDS:
        raise ValueError(
            f"this dataset's objects belong on {where['backend']!r}, and this "
            f"worker can only write to {', '.join(cp.SERVED_BACKENDS)}. "
            f"Refusing rather than writing them to the storage it does have "
            f"under the other one's bucket name, which would seal a version "
            f"nothing can read and report no error"
        )
    if not where["bucket"]:
        raise ValueError(
            f"the control plane could not say which bucket this dataset's "
            f"objects belong in: {where.get('bucket_error') or 'no reason given'}"
        )

    carried = params.get("bucket")
    if carried and where["bucket"] != carried:
        raise ValueError(
            f"this run has been writing to {carried!r} but the control plane "
            f"now says this dataset's objects belong in {where['bucket']!r}. "
            f"Refusing to seal a version whose objects are split across two "
            f"buckets"
        )
    return where


class _Checkpoint:
    """Per-record progress for an activity, so a retry resumes.

    Written as JSON lines and appended after each record. Append-only matters:
    rewriting a whole file after every record means a crash during the write
    can corrupt everything done so far, whereas a torn append costs the last
    line and the loader skips it.

    Local to the worker rather than in object storage. That is a deliberate
    limit and it is worth naming: this survives a process crash and a retry on
    the same machine, and does not survive the machine going away. Making it
    survive that means putting partial results in object storage, which puts
    unsealed intermediate data outside the version model, and that is a design
    decision rather than a detail.
    """

    def __init__(self, name: str, idempotency_key: str) -> None:
        self.path = config.WORK / "checkpoints" / f"{name}-{idempotency_key}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        done: dict[str, dict] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # A torn final line from a crash mid-append. Skipped rather
                # than fatal, because the record it describes is simply redone.
                continue
            done[record["record_id"]] = record
        return done

    def append(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()

    def clear(self) -> None:
        """Remove the checkpoint once the output version is sealed.

        Only after sealing. Clearing earlier would discard the work in the
        window where the activity has finished but its result is not yet
        durable anywhere.
        """
        self.path.unlink(missing_ok=True)


@activity.defn
def ingest(params: dict) -> dict:
    """Read the synthetic corpus and produce a RAW dataset version.

    Ingest declares no source contract, because its input is outside the
    platform. That is the one place the chain of custody legitimately starts,
    and it is worth naming rather than glossing.
    """
    corpus = Path(params["corpus"])
    limit = params.get("limit", 0)
    records = sorted(corpus.glob("synth-*.json"))
    if limit:
        records = records[:limit]

    schema_id = cp.register_contract(contracts.RAW_AUDIO, _tenant(params))
    dataset_id = cp.ensure_dataset(params["dataset"], _tenant(params))
    run = cp.start_run(
        params["action_id"], params["idempotency_key"], [], params,
        operator=_pipeline_principal(_tenant(params)),
        trigger_kind=params.get("trigger_kind", "manual"),
        triggered_by=params.get("triggered_by"),
        schedule_id=params.get("schedule_id"),
        pipeline_run_id=params.get("pipeline_run_id"),
        tenant_id=_tenant(params),
    )
    run_id = run["id"]

    # Objects go under the prefix the version will actually carry, in the
    # bucket the control plane names for this tenant, so a prefix-scoped
    # credential issued after sealing reaches them.
    where = _location(params, dataset_id)
    params = {**params, "prefix": where["prefix"], "bucket": where["bucket"],
              "backend": where["backend"]}
    write_client = cp.s3_scoped_write(
        run["task_credential"], dataset_id, _tenant(params),
        _pipeline_principal(_tenant(params)),
        "ingest: write this run's own sealed output",
    )

    rows, manifest = [], []
    for path in records:
        record = json.loads(path.read_text(encoding="utf-8"))
        record_id = record["record_id"]
        timing_path = corpus / "audio" / f"{record_id}.timing.json"
        audio_path = corpus / "audio" / f"{record_id}.wav"
        if not audio_path.exists():
            continue
        timing = json.loads(timing_path.read_text(encoding="utf-8"))

        audio_key = _key(params["prefix"], f"{record_id}.wav")
        manifest.append(cp.put_file(write_client, audio_key, audio_path, params["bucket"]))
        manifest.append(cp.put_json(
            write_client,
            _key(params["prefix"], f"{record_id}.truth.json"),
            # The reference transcript travels with the answer key. Without it
            # scoring depends on a directory outside the platform, and a
            # dataset version that cannot be scored from its own contents is
            # not reproducible.
            {"spans": record["spans"], "hazard": record["asr_hazard"],
             "hazards": record["hazards"], "template": record["template"],
             "reference_transcript": record["transcript"]},
            params["bucket"],
        ))

        rows.append({
            "record_id": record_id,
            "audio_key": audio_key,
            "duration_seconds": float(timing["duration_seconds"]),
            "sample_rate": int(timing["sample_rate"]),
        })

    contracts.RAW_AUDIO.validate(rows)
    manifest.append(cp.put_json(write_client, _key(params["prefix"], "records.json"), rows,
                                params["bucket"]))

    version = cp.seal_version(
        dataset_id, schema_id, "RAW", manifest, len(rows), run_id,
        _tenant(params), params["backend"],
        records_key=_key(params["prefix"], "records.json"),
    )
    activity.logger.info("ingested %d records into %s", len(rows), version["id"])
    return {"version_id": version["id"], "prefix": version["storage_prefix"],
            "records_key": _key(params["prefix"], "records.json"),
            "record_count": len(rows), "run_id": run_id,
            # Handed on rather than re-derived. Every later step writes into
            # this same tenant's bucket, and one answer carried forward cannot
            # disagree with itself the way two lookups can.
            "bucket": params["bucket"]}


@activity.defn
def adopt_version(params: dict) -> dict:
    """Begin from a version somebody already sealed, rather than from a disk.

    `ingest` above is the right first step for the synthetic corpus and the
    wrong one for data a person uploaded through the console: that data is
    already inside the platform, already sealed, and already carries a content
    hash. Copying it into a second prefix would produce a duplicate whose only
    distinction is that the pipeline made it.

    No `action_run` is opened here, deliberately. An action run in this
    platform is a transformation: it names an action, takes input versions, and
    is closed by `versions.seal` setting its `output_version`. This transforms
    nothing and seals nothing, so a run opened here would have no way to close
    and would sit at `running` for as long as the database exists. The lineage
    chain keeps its first link either way, because the version is that link and
    it already records who sealed it.

    The prefix arrives in the params rather than being looked up again here.
    The endpoint that started this run already read it in order to validate the
    request, and two reads of the same fact are two chances for them to
    disagree.

    Reads through a task_credential.py-scoped client, not the pipeline's
    static, all-purpose key: this is the one real read of a dataset version
    somebody else registered and sealed, everything downstream only reads the
    pipeline's own intermediate output. `params["task_credential"]` is minted
    for the `pipeline_run` this step belongs to (see open_pipeline_run's own
    comment for why a `pipeline_run` token rather than an `action_run` one:
    this step seals nothing, so it never opens an `action_run` to bind one
    to).
    """
    prefix = params["source_prefix"]
    records_key = _key(prefix, "records.json")

    task_cred = params.get("task_credential")
    if not task_cred:
        raise ValueError(
            "adopt_version was not handed a task credential for "
            f"{params['source_version_id']!r}. open_pipeline_run mints one "
            "whenever source_version_id is set; this run's workflow input "
            "or dag_workflow.py's step wiring is missing it, not something "
            "to fall back from silently"
        )
    client = cp.s3_scoped(
        task_cred, params["source_version_id"], _tenant(params),
        _pipeline_principal(_tenant(params)),
        "adopt_version: read the sealed version this pipeline starts from",
    )
    rows = json.loads(client.get_object(Bucket=params["bucket"], Key=records_key)["Body"].read())
    if not isinstance(rows, list) or not rows:
        raise ValueError(
            f"{records_key} is empty or is not a list of records, so this "
            f"version has nothing for the pipeline to de-identify"
        )
    contracts.RAW_AUDIO.validate(rows)

    activity.logger.info("adopted %d records from version %s",
                         len(rows), params["source_version_id"])
    return {"version_id": params["source_version_id"], "prefix": prefix,
            "records_key": records_key, "record_count": len(rows),
            "run_id": None, "bucket": params["bucket"]}


@activity.defn
def resolve_actions(params: dict) -> dict:
    """Find the dataset actions this run needs, by name.

    `run_pipeline.py` registers these before starting a workflow and passes
    their ids in. A console-started run has no such step, and the console has
    no business knowing action ids, so they are resolved here instead.

    Read straight from PostgreSQL through the same narrow exception
    `run_pipeline.py`'s own `ensure_actions()` already uses, because the
    control plane still has no action registration endpoint. That gap is
    recorded rather than papered over: it means an action's contract pair is
    not validated at registration time the way a dataset version's is.

    Missing actions are refused rather than created. Creating one as a side
    effect of needing it would make the registry a log of what happened rather
    than a statement of what is allowed to happen, which is the distinction
    run_pipeline.py's own docstring opens with.
    """
    from .db import _db

    tenant = _tenant(params)
    found: dict[str, str] = {}
    with _db() as conn:
        for name in params["names"]:
            row = conn.execute(
                "select id from dataset_action where tenant_id = %s and name = %s",
                (tenant, name),
            ).fetchone()
            if not row:
                raise ValueError(
                    f"the dataset action {name!r} is not registered for tenant "
                    f"{tenant!r}. Run `python -m worker.run_pipeline` once "
                    f"against that tenant, which registers them"
                )
            found[name] = str(row[0])
    return found


@activity.defn
def transcribe(params: dict) -> dict:
    """faster-whisper with word-level timings.

    The timings are load bearing rather than incidental. They are what lets an
    identifier found in the transcript be masked in the waveform without a fuzzy
    match back to audio, and what lets an annotator's correction be realigned.
    Masking audio depends on them entirely.

    Known names are passed as an initial prompt. This biases recognition towards
    getting identifiers right, which sounds backwards for a de-identification
    pipeline and is not: an identifier transcribed correctly can be detected and
    removed, while one mangled into nonsense is invisible to the detector and
    survives into the output.

    Results are checkpointed per record. Temporal restarts an activity from its
    first line on retry, so without this a failure at record 55 of 60 redoes all
    60. That is repetition rather than resumption, and it was measured: a run
    that should have transcribed 60 records transcribed 112 before anyone
    noticed, because nothing in the output distinguishes slow from repeating.

    The actual transcription runs through transcribe_backend.load_backend()
    rather than importing faster-whisper directly, so a second engine (a
    Metal-accelerated one for Apple Silicon, say) can be added there without
    touching this function -- see that module's own docstring.
    """
    rows = cp.get_json(params["records_key"], params["bucket"])

    schema_id = cp.register_contract(contracts.TRANSCRIBED, _tenant(params))
    dataset_id = cp.ensure_dataset(params["dataset"], _tenant(params))
    run = cp.start_run(
        params["action_id"], params["idempotency_key"], [params["input_version"]], params,
        operator=_pipeline_principal(_tenant(params)),
        trigger_kind=params.get("trigger_kind", "manual"),
        triggered_by=params.get("triggered_by"),
        schedule_id=params.get("schedule_id"),
        pipeline_run_id=params.get("pipeline_run_id"),
        tenant_id=_tenant(params),
    )
    run_id = run["id"]
    where = _location(params, dataset_id)
    params = {**params, "prefix": where["prefix"], "bucket": where["bucket"],
              "backend": where["backend"]}
    write_client = cp.s3_scoped_write(
        run["task_credential"], dataset_id, _tenant(params),
        _pipeline_principal(_tenant(params)),
        "transcribe: write this run's own sealed output",
    )

    # Keyed by the idempotency key, so a retry of this activity finds its own
    # earlier work and a different run does not.
    checkpoint = _Checkpoint("transcribe", params["idempotency_key"])
    done = checkpoint.load()
    if done:
        activity.logger.info("resuming with %d records already transcribed", len(done))

    out_rows, manifest = [], []
    started = time.time()
    backend = None
    for row in rows:
        if row["record_id"] in done:
            out_rows.append(done[row["record_id"]])
            activity.heartbeat(row["record_id"])
            continue

        # Loaded lazily, so a fully resumed activity never pays for the model
        # at all. On a cold run this costs one extra branch.
        if backend is None:
            backend = transcribe_backend.load_backend(
                config.WHISPER_MODEL,
                config.WHISPER_DEVICE,
                config.WHISPER_COMPUTE,
            )

        local = config.WORK / row["audio_key"].replace("/", "_")
        local.parent.mkdir(parents=True, exist_ok=True)
        cp.s3().download_file(params["bucket"], row["audio_key"], str(local))

        segments = backend.transcribe(local)
        words, pieces = [], []
        for segment in segments:
            pieces.append(segment["text"])
            words.extend(segment["words"])

        transcript = "".join(pieces).strip()
        result = {
            "record_id": row["record_id"],
            "audio_key": row["audio_key"],
            "transcript": transcript,
            "words": words,
            "duration_seconds": row["duration_seconds"],
        }
        out_rows.append(result)
        checkpoint.append(result)
        activity.heartbeat(row["record_id"])

    del backend
    _free_vram()

    contracts.TRANSCRIBED.validate(out_rows)
    manifest.append(cp.put_json(write_client, _key(params["prefix"], "transcribed.json"),
                                out_rows, params["bucket"]))

    version = cp.seal_version(dataset_id, schema_id, "RAW", manifest, len(out_rows), run_id,
                              _tenant(params), params["backend"],
                              records_key=_key(params["prefix"], "transcribed.json"))
    checkpoint.clear()
    activity.logger.info(
        "transcribed %d records in %.1fs (%d resumed from checkpoint)",
        len(out_rows), time.time() - started, len(done),
    )
    return {"version_id": version["id"],
            "records_key": _key(params["prefix"], "transcribed.json"),
            "record_count": len(out_rows), "run_id": run_id}


@activity.defn
def detect(params: dict) -> dict:
    """Run the three-detector ensemble. Output stays RAW.

    Nothing has been removed yet, so the class does not move. A common mistake
    is to treat "we have found the identifiers" as progress towards a lower
    sensitivity; it is not, because the data still contains all of them plus a
    map of where they are, which is strictly worse than the input.
    """
    from .detect import Ensemble

    rows = cp.get_json(params["records_key"], params["bucket"])
    ensemble = Ensemble()
    ensemble.load()

    schema_id = cp.register_contract(contracts.DETECTED, _tenant(params))
    dataset_id = cp.ensure_dataset(params["dataset"], _tenant(params))
    run = cp.start_run(
        params["action_id"], params["idempotency_key"], [params["input_version"]], params,
        operator=_pipeline_principal(_tenant(params)),
        trigger_kind=params.get("trigger_kind", "manual"),
        triggered_by=params.get("triggered_by"),
        schedule_id=params.get("schedule_id"),
        pipeline_run_id=params.get("pipeline_run_id"),
        tenant_id=_tenant(params),
    )
    run_id = run["id"]
    where = _location(params, dataset_id)
    params = {**params, "prefix": where["prefix"], "bucket": where["bucket"],
              "backend": where["backend"]}
    write_client = cp.s3_scoped_write(
        run["task_credential"], dataset_id, _tenant(params),
        _pipeline_principal(_tenant(params)),
        "detect: write this run's own sealed output",
    )

    out_rows = []
    tally = {"presidio": 0, "spacy": 0, "gliner": 0, "unanimous": 0, "single": 0}
    for row in rows:
        candidates = ensemble.detect(row["transcript"])
        for candidate in candidates:
            for detector in candidate.detectors:
                tally[detector] = tally.get(detector, 0) + 1
            if candidate.votes == 3:
                tally["unanimous"] += 1
            elif candidate.votes == 1:
                tally["single"] += 1

        out_rows.append({
            "record_id": row["record_id"],
            "transcript": row["transcript"],
            "words": row["words"],
            "candidates": [c.as_dict() for c in candidates],
            "detector_votes": tally.copy(),
        })
        activity.heartbeat(row["record_id"])

    ensemble.release()
    _free_vram()

    contracts.DETECTED.validate(out_rows)
    manifest = [cp.put_json(write_client, _key(params["prefix"], "detected.json"), out_rows,
                     params["bucket"])]
    version = cp.seal_version(dataset_id, schema_id, "RAW", manifest, len(out_rows), run_id,
                              _tenant(params), params["backend"],
                              records_key=_key(params["prefix"], "detected.json"))

    return {"version_id": version["id"],
            "records_key": _key(params["prefix"], "detected.json"),
            "record_count": len(out_rows), "detector_tally": tally, "run_id": run_id}


@activity.defn
def handoff(params: dict) -> dict:
    """Build Label Studio tasks with predictions pre-populated.

    Pre-annotation is not a convenience. The gap between what the model predicted
    and what the reviewer accepted is a recall measurement that costs nothing
    extra, and it is the only measurement in the pipeline taken against human
    judgement rather than against synthetic ground truth. V12 depends on it.

    Word timings ride along as metadata so a corrected span can be mapped back
    to audio without re-running alignment.

    The tasks contain unredacted transcripts, so they are RAW and get their own
    sealed version rather than being written to a loose bucket path. Anything
    holding PHI has to sit inside the class system, or the class system is
    describing only the parts of the pipeline that were convenient.
    """
    rows = cp.get_json(params["records_key"], params["bucket"])
    schema_id = cp.register_contract(contracts.DETECTED, _tenant(params))
    dataset_id = cp.ensure_dataset(params["dataset"], _tenant(params))
    run = cp.start_run(
        params["action_id"], params["idempotency_key"], [params["input_version"]], params,
        operator=_pipeline_principal(_tenant(params)),
        trigger_kind=params.get("trigger_kind", "manual"),
        triggered_by=params.get("triggered_by"),
        schedule_id=params.get("schedule_id"),
        pipeline_run_id=params.get("pipeline_run_id"),
        tenant_id=_tenant(params),
    )
    run_id = run["id"]
    where = _location(params, dataset_id)
    params = {**params, "prefix": where["prefix"], "bucket": where["bucket"],
              "backend": where["backend"]}
    write_client = cp.s3_scoped_write(
        run["task_credential"], dataset_id, _tenant(params),
        _pipeline_principal(_tenant(params)),
        "handoff: write this run's own sealed output",
    )

    tasks = []
    for row in rows:
        tasks.append({
            "data": {
                "text": row["transcript"],
                "record_id": row["record_id"],
                "words": row["words"],
            },
            "predictions": [{
                "model_version": cp.code_hash(),
                "result": [
                    {
                        "from_name": "label",
                        "to_name": "text",
                        "type": "labels",
                        "value": {
                            "start": c["start"],
                            "end": c["end"],
                            "text": c["text"],
                            "labels": [c["entity"]],
                        },
                        "score": c["confidence"],
                    }
                    for c in row["candidates"]
                ],
            }],
        })

    key = _key(params["prefix"], "label-studio-tasks.json")
    manifest = [cp.put_json(write_client, key, tasks, params["bucket"])]
    version = cp.seal_version(dataset_id, schema_id, "RAW", manifest, len(tasks), run_id,
                              _tenant(params), params["backend"])

    return {"version_id": version["id"], "tasks_key": key, "task_count": len(tasks),
            "predicted_spans": sum(len(r["candidates"]) for r in rows),
            "run_id": run_id}


@activity.defn
def redact(params: dict) -> dict:
    """Substitute surrogates in text and overwrite the audio spans.

    This is the first action whose output is less sensitive than its input, so
    it is the first that produces UNDER_REVIEW rather than RAW.
    """
    import numpy as np
    import soundfile as sf

    from .redact import redact_audio, redact_text

    rows = cp.get_json(params["records_key"], params["bucket"])
    schema_id = cp.register_contract(contracts.REDACTED, _tenant(params))
    dataset_id = cp.ensure_dataset(params["dataset"], _tenant(params))
    run = cp.start_run(
        params["action_id"], params["idempotency_key"], [params["input_version"]], params,
        operator=_pipeline_principal(_tenant(params)),
        trigger_kind=params.get("trigger_kind", "manual"),
        triggered_by=params.get("triggered_by"),
        schedule_id=params.get("schedule_id"),
        pipeline_run_id=params.get("pipeline_run_id"),
        tenant_id=_tenant(params),
    )
    run_id = run["id"]
    where = _location(params, dataset_id)
    params = {**params, "prefix": where["prefix"], "bucket": where["bucket"],
              "backend": where["backend"]}
    write_client = cp.s3_scoped_write(
        run["task_credential"], dataset_id, _tenant(params),
        _pipeline_principal(_tenant(params)),
        "redact: write this run's own sealed output",
    )

    threshold = params.get("confidence_threshold", 0.4)
    run_salt = params["idempotency_key"]

    out_rows, manifest = [], []
    for row in rows:
        accepted = [c for c in row["candidates"] if c["confidence"] >= threshold]
        redacted, substitutions = redact_text(
            row["transcript"], accepted, row["record_id"], run_salt
        )

        time_spans = _spans_to_time(accepted, row["words"], row["transcript"])
        source_key = next(
            (r["audio_key"] for r in cp.get_json(params["audio_index_key"], params["bucket"])
             if r["record_id"] == row["record_id"]), None
        )

        redacted_key = None
        if source_key:
            local = config.WORK / source_key.replace("/", "_")
            local.parent.mkdir(parents=True, exist_ok=True)
            cp.s3().download_file(params["bucket"], source_key, str(local))
            audio, rate = sf.read(str(local), dtype="int16")
            masked = redact_audio(np.asarray(audio), rate, time_spans)
            out_path = config.WORK / f"redacted_{row['record_id']}.wav"
            sf.write(str(out_path), masked, rate)
            redacted_key = _key(params["prefix"], f"{row['record_id']}.redacted.wav")
            manifest.append(cp.put_file(write_client, redacted_key, out_path, params["bucket"]))

        out_rows.append({
            "record_id": row["record_id"],
            "redacted_transcript": redacted,
            "redacted_audio_key": redacted_key or "",
            "substitutions": len(substitutions),
            # The mapping is never written anywhere. This flag exists so the
            # claim is checkable rather than merely asserted in a document.
            "surrogate_map_stored": False,
        })
        activity.heartbeat(row["record_id"])

    contracts.REDACTED.validate(out_rows)
    manifest.append(cp.put_json(write_client, _key(params["prefix"], "redacted.json"),
                                out_rows, params["bucket"]))

    version = cp.seal_version(
        dataset_id, schema_id, "UNDER_REVIEW", manifest, len(out_rows), run_id,
        _tenant(params), params["backend"],
        records_key=_key(params["prefix"], "redacted.json"),
    )
    return {"version_id": version["id"],
            "records_key": _key(params["prefix"], "redacted.json"),
            "record_count": len(out_rows),
            "substitutions": sum(r["substitutions"] for r in out_rows),
            "run_id": run_id}


def score_store_problem() -> str | None:
    """Why the score store cannot take a score card, or None if it can.

    verify writes every run's score card to MLflow, and the gate decision
    cites that card as its evidence, so a run that cannot reach it cannot
    finish honestly. Asked before any GPU work and again by verify, so a
    missing MLflow costs one clear refusal instead of minutes of
    transcription followed by five identical retries.
    """
    import httpx

    try:
        r = httpx.get(f"{config.MLFLOW}/health", timeout=5.0)
    except httpx.HTTPError as exc:
        return (
            f"the score store (MLflow) is not reachable at {config.MLFLOW} "
            f"({exc.__class__.__name__}), so this run could not record its score. "
            "Start it with `docker compose --profile full up -d mlflow`, then start the run again"
        )
    if r.status_code != 200:
        return (
            f"the score store (MLflow) at {config.MLFLOW} answered HTTP "
            f"{r.status_code} to its health check, so this run could not record "
            "its score"
        )
    return None


@activity.defn
def check_score_store() -> None:
    """Refuse, once and without retrying, when the score store is down."""
    problem = score_store_problem()
    if problem:
        raise ApplicationError(problem, non_retryable=True)


@activity.defn
def verify(params: dict) -> dict:
    """Score the redaction against ground truth and write a score card.

    Two independent measurements that ought to agree: what the ensemble found,
    and what the corpus knows is there. The promotion gate depends on
    this rather than on a reviewer's judgement, because a statistical component
    degrades rather than breaks and nothing errors when it does.

    A score store that refuses the connection will refuse the next attempt
    too, so that is not retried: see score_store_problem.
    """
    problem = score_store_problem()
    if problem:
        raise ApplicationError(problem, non_retryable=True)
    import mlflow

    from .align import Alignment
    from .scoring import ScoreCard, pseudonymised, score_record

    detected = cp.get_json(params["detected_key"], params["bucket"])
    truth_prefix = params["truth_prefix"]

    card = ScoreCard()
    similarities: list[float] = []

    for row in detected:
        truth = cp.get_json(
            f"{truth_prefix}/{row['record_id']}.truth.json", params["bucket"])
        reference = truth["reference_transcript"]
        similarities.append(Alignment(reference, row["transcript"]).similarity())
        score_record(card, reference, row["transcript"], truth["spans"],
                     row["candidates"], record_id=row["record_id"])
        activity.heartbeat(row["record_id"])

    card.similarity = sum(similarities) / len(similarities) if similarities else 0.0

    mlflow.set_tracking_uri(config.MLFLOW)
    mlflow.set_experiment("munitas-deid")
    with mlflow.start_run(run_name=f"verify-{params['idempotency_key'][:12]}") as run:
        mlflow.log_params({
            "whisper_model": config.WHISPER_MODEL,
            "gliner_model": config.GLINER_MODEL,
            "spacy_model": config.SPACY_MODEL,
            "code_hash": cp.code_hash(),
            "record_count": len(detected),
            "scoring": "alignment and interval overlap",
        })
        mlflow.log_metrics(card.as_metrics())
        # The leaks go in the score card, not just the count. A gate decision
        # that points at a number nobody can inspect is not evidence. The
        # identifiers stay out of MLflow and go to Postgres under the owning
        # tenant instead: see scoring.pseudonymised for why.
        mlflow.log_dict({"leaks": pseudonymised(card.leak_detail)}, "leaks.json")
        mlflow.log_dict({"per_entity": card.per_entity}, "per_entity.json")
        score_card_id = run.info.run_id

    return {
        "score_card_id": score_card_id,
        "recall": card.recall_effective,
        "recall_spoken": card.recall_spoken,
        "destruction_rate": card.destruction_rate,
        "ground_truth_spans": card.total,
        "survived_asr": card.survived,
        "leaks_total": card.leaked,
        "leaks_direct": card.direct_leaks,
        "leaks_quasi": card.quasi_leaks,
        "metrics": card.as_metrics(),
        # Carried out of here so the gate decision can store it under the
        # owning tenant. It holds the identifiers, so it goes to Postgres and
        # not to MLflow: see scoring.pseudonymised.
        "leak_detail": card.leak_detail,
    }


@activity.defn
def open_pipeline_run(params: dict) -> dict:
    """Open the pipeline run row, returning its id for every step to carry.

    One id for the whole run, stamped on every action_run below it and handed
    to anything the promotion later starts. Without it the only thread between
    a run's steps is the lineage chain, because the idempotency key hashes the
    workflow id beyond recovery.

    Also returns a task_credential.py token scoped to this run, when
    `source_version_id` names a real input: `adopt_version` reads that
    version but opens no `action_run` of its own (it seals nothing, so
    nothing would ever close one), and this run's own id is what it proves
    identity against instead. See task_credential.py's own docstring.
    """
    source_version_id = params.get("source_version_id")
    result = cp.start_pipeline_run(
        _tenant(params), params["dataset"], params["workflow_id"],
        trigger_kind=params.get("trigger_kind", "manual"),
        triggered_by=params.get("triggered_by"),
        schedule_id=params.get("schedule_id"),
        input_versions=[source_version_id] if source_version_id else [],
        principal=_pipeline_principal(_tenant(params)) if source_version_id else None,
    )
    return {"pipeline_run_id": result["pipeline_run_id"],
            "task_credential": result.get("task_credential")}


def describe_failure(exc: BaseException) -> str:
    """A run's failure in words, for the record: every message down the chain
    of causes, since the job runner wraps an activity's own error in its own.
    Pure, so a workflow may call it."""
    parts, seen = [], exc
    for _ in range(6):
        if seen is None:
            break
        message = str(getattr(seen, "message", "") or seen).strip()
        if message and message not in parts:
            parts.append(message)
        seen = seen.__cause__ or seen.__context__
    return ": ".join(parts)[:2000] or type(exc).__name__


@activity.defn
def close_pipeline_run(params: dict | str) -> None:
    """End the run open_pipeline_run opened, whatever happened in between,
    recording how it ended.

    Run from each pipeline workflow's `finally`, so a failed run ends as
    surely as a successful one. A run killed from outside (terminated, or a
    worker that died) never reaches its `finally`; the API records those from
    the job runner's answer, in platform/api/app/pipeline.py.

    A bare run id is what a workflow started before outcomes were recorded
    sends. It still ends the run, as 'unknown', rather than failing.
    """
    if isinstance(params, str):
        params = {"pipeline_run_id": params, "status": "unknown", "error": None}
    cp.end_pipeline_run(params["pipeline_run_id"], params["status"], params.get("error"))


@activity.defn
def record_gate_decision(params: dict) -> dict:
    """Apply the promotion gate, and record it for a person to decide.

    This used to promote. It no longer does, and nothing in the pipeline does:
    promotion is the one act that widens who can see clinical data, it cannot
    be undone, and it now needs a qualified person who has seen the evidence.

    The gate still refuses rather than warns. What changed is that its refusal,
    and its pass, are both a recommendation written down rather than an act.
    """
    import uuid

    from .db import _db
    from .scoring import GATE_DIRECT_LEAKS, GATE_RECALL, ScoreCard, evaluate_gate

    score = params["score_card"]
    threshold = params.get("recall_threshold", GATE_RECALL)
    max_direct = params.get("max_direct_leaks", GATE_DIRECT_LEAKS)

    # Rebuild a card from the metrics the verify activity returned, because
    # activity results cross a process boundary and cannot carry objects.
    card = ScoreCard()
    metrics = score["metrics"]
    card.total = metrics["ground_truth_spans"]
    card.destroyed = metrics["destroyed_by_asr"]
    card.effective = round(metrics["recall_effective"] * (card.total - card.destroyed))
    card.leaked = metrics["leaks_total"]
    card.direct_leaks = metrics["leaks_direct"]
    card.quasi_leaks = metrics["leaks_quasi"]

    passed, reason = evaluate_gate(card, threshold, max_direct)

    decision_id = str(uuid.uuid4())
    with _db() as conn:
        owner = conn.execute(
            "select tenant_id from dataset_version where id = %s",
            (params["version_id"],),
        ).fetchone()
        if not owner:
            raise RuntimeError(f"no such dataset version: {params['version_id']}")

        conn.execute(
            """insert into gate_decision
                 (id, tenant_id, pipeline_run_id, dataset_version_id, to_class,
                  score_card_id, metrics, recommendation, recommendation_reason,
                  state, triggered_by, handoff)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)""",
            (decision_id, owner[0], params.get("pipeline_run_id"),
             params["version_id"], params["to_class"], score["score_card_id"],
             json.dumps({
                 **metrics,
                 "recall_threshold": threshold,
                 "max_direct_leaks": max_direct,
                 "scoring_method": "alignment and interval overlap",
             }),
             "pass" if passed else "fail", reason,
             params.get("triggered_by"),
             json.dumps(params.get("handoff", {}))),
        )

        # The identifiers, under the tenant that owns them. The reviewer needs
        # these to judge severity: one leak can be a partial postcode or a
        # patient's full name, and the count cannot tell them apart.
        for leak in params.get("leak_detail", []):
            conn.execute(
                """insert into gate_leak
                     (id, gate_decision_id, record_id, span_start, span_end,
                      entity, identifier, left_in_the_clear, coverage, direct)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (str(uuid.uuid4()), decision_id, leak["record_id"],
                 leak["span_start"], leak["span_end"], leak["entity"],
                 leak["identifier"], leak["left_in_the_clear"],
                 leak["coverage"], leak["direct"]),
            )

    return {
        "gate_decision_id": decision_id,
        "recommendation": "pass" if passed else "fail",
        "reason": reason,
        "promoted": False,
    }


# --- CountRecordsPipeline: the barebones starter kind ------------------
#
# The two activities below back the smallest complete pipeline kind, meant
# to be downloaded from the console and run unmodified before it is edited.
# See web/public/starter-pipeline/starter_pipeline.py for the copy a person
# actually reads; this is the registered version that file mirrors.


@activity.defn
def count_records(params: dict) -> dict:
    """The entire "pipeline logic" of the starter example: count records.

    A real step reads the version's own manifest from storage (transcribe
    and detect both do, above). This one reads the count the API already
    read off dataset_version.record_count and forwarded as params['limit'],
    because the point of this activity is showing where a step's real work
    would go, not adding a storage round trip a teaching example gains
    nothing from.
    """
    return {"version_id": params["source_version_id"],
            "record_count": params.get("limit") or 0}


@activity.defn
def record_simple_gate_decision(params: dict) -> dict:
    """Write a gate_decision row directly, for a kind with no leak scoring.

    record_gate_decision, above, rebuilds a ScoreCard from verify's leak
    metrics and derives pass/fail from a recall threshold. A kind that never
    ran detection has nothing to rebuild, and inventing leak metrics to reuse
    that activity would claim a measurement that never happened. This writes
    the same row with a recommendation the caller already decided directly,
    so review and promotion afterward work exactly the same either way.
    """
    import uuid

    from .db import _db

    decision_id = str(uuid.uuid4())
    with _db() as conn:
        owner = conn.execute(
            "select tenant_id from dataset_version where id = %s",
            (params["version_id"],),
        ).fetchone()
        if not owner:
            raise RuntimeError(f"no such dataset version: {params['version_id']}")

        conn.execute(
            """insert into gate_decision
                 (id, tenant_id, pipeline_run_id, dataset_version_id, to_class,
                  score_card_id, metrics, recommendation, recommendation_reason,
                  state, triggered_by, handoff)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)""",
            (decision_id, owner[0], params.get("pipeline_run_id"),
             params["version_id"], params["to_class"],
             params.get("score_card_id", "no-scoring"),
             json.dumps(params.get("metrics", {})),
             params["recommendation"], params["recommendation_reason"],
             params.get("triggered_by"), json.dumps(params.get("handoff", {}))),
        )

        # Optional: a reviewer sees the same per-identifier detail on a DAG's
        # gate decision that they already see on the built-in pipeline's,
        # regardless of which step actually found the leaks. Mirrors
        # record_gate_decision's own insert loop above, exactly.
        for leak in params.get("leak_detail") or []:
            conn.execute(
                """insert into gate_leak
                     (id, gate_decision_id, record_id, span_start, span_end,
                      entity, identifier, left_in_the_clear, coverage, direct)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (str(uuid.uuid4()), decision_id, leak["record_id"],
                 leak["span_start"], leak["span_end"], leak["entity"],
                 leak["identifier"], leak["left_in_the_clear"],
                 leak["coverage"], leak["direct"]),
            )

    return {
        "gate_decision_id": decision_id,
        "recommendation": params["recommendation"],
        "reason": params["recommendation_reason"],
        "promoted": False,
    }


def _spans_to_time(
    spans: list[dict], words: list[dict], transcript: str
) -> list[tuple[float, float]]:
    """Map character spans to audio time using word timings.

    Walks the word list accumulating character positions. Whisper's words carry
    their leading space, so the cursor arithmetic has to match how the transcript
    was assembled or every span lands slightly early.
    """
    positions: list[tuple[int, int, float, float]] = []
    cursor = 0
    for word in words:
        text = word["word"]
        index = transcript.find(text.strip(), cursor)
        if index < 0:
            continue
        positions.append((index, index + len(text.strip()), word["start"], word["end"]))
        cursor = index + len(text.strip())

    out: list[tuple[float, float]] = []
    for span in spans:
        overlapping = [
            (s, e) for start, end, s, e in positions
            if start < span["end"] and span["start"] < end
        ]
        if overlapping:
            out.append((min(s for s, _ in overlapping), max(e for _, e in overlapping)))
    return out


def _free_vram() -> None:
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass
