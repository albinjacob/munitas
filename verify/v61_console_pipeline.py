"""U61: a person starts the de-identification pipeline, and is refused when they should be.

Until now the pipeline could only be started by typing a command on the machine
with the GPU. That is not a missing button, it is a missing accountable act: a
run started at a terminal has no session behind it, so nothing the platform
records can say who is responsible for it, and the rule that a run's own starter
may not clear its gate has nothing to bind to.

So the claim being verified is not "an endpoint exists". It is that the endpoint
refuses every request that should not produce a run, refuses it before a
workflow exists rather than minutes into one, and attributes the runs it does
start to a real session.

Per assertion, never in aggregate.

    docker compose exec -T munitas-api python /verify/v61_console_pipeline.py
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import uuid

from common import (CANARY, ENGINEER, REVIEWER, api, bearer_for, check, db, skip,
                    heading, require_api, summary, tiny_wav)

TENANT = CANARY
NIL = "00000000-0000-0000-0000-000000000000"


def department(name: str) -> str:
    with db() as conn:
        row = conn.execute(
            "select id from department where tenant_id = %s and name = %s",
            (TENANT, name),
        ).fetchone()
    if not row:
        raise RuntimeError(
            f"department {name!r} is missing from tenant {TENANT!r}. Apply "
            "infra/postgres/seed-canary.sql."
        )
    return str(row["id"])


def register(department_id: str) -> str:
    return api("POST", "/datasets/register", json={
        "tenant_id": TENANT,
        "name": f"deid-{uuid.uuid4().hex[:8]}",
        "department_id": department_id,
        "registered_by": ENGINEER,
        "provenance": "internal_regulated",
        "declared_class": "RAW",
        "source_kind": "upload",
    }).json()["id"]


def put(dataset_id: str, name: str, payload: bytes):
    return api("POST", f"/datasets/{dataset_id}/files",
               files={"file": (name, io.BytesIO(payload),
                               "application/octet-stream")})


def answer_key() -> bytes:
    return json.dumps({
        "spans": [{"entity": "PERSON", "start": 0, "end": 5, "text": "Aoife"}],
        "reference_transcript": "Aoife attended on Tuesday",
        "hazard": False,
    }).encode("utf-8")


def reasons_of(response) -> list[str]:
    """The refusal's own reasons, or nothing.

    Looked up here rather than searched for in the raw body, because a version
    id appears in a success response too. A check that greps the whole body for
    an id passes whether or not the request was refused, which is a check that
    cannot fail.
    """
    if response.status_code < 400:
        return []
    try:
        detail = response.json().get("detail")
    except Exception:  # noqa: BLE001 - a non-JSON error body carries no reasons
        return []
    if isinstance(detail, dict):
        return [str(r) for r in detail.get("reasons", [])]
    return [str(detail)] if detail else []


def score_store_up() -> bool:
    """Whether MLflow answers, asked from where the API asks it."""
    import os

    import httpx

    url = os.environ.get("MUNITAS_MLFLOW_URL", "http://mlflow:5000")
    try:
        return httpx.get(f"{url}/health", timeout=5.0).status_code == 200
    except httpx.HTTPError:
        return False


def start(dataset_id: str, version_id: str, headers: dict | None = None):
    return api("POST", f"/datasets/{dataset_id}/versions/{version_id}/deidentify",
               headers=headers or {})


async def terminate(workflow_id: str) -> bool:
    """Stop a workflow this script started, so the suite leaves no GPU job.

    Uses the API's own Temporal client module, connected here because the
    lifespan that normally opens it does not run in this process.
    """
    sys.path.insert(0, "/app")
    from app import temporal_client

    await temporal_client.connect()
    handle = temporal_client.get().get_workflow_handle(workflow_id)
    await handle.terminate("stopped by verify/v61_console_pipeline.py")
    return True


def versions_of(dataset_id: str) -> int:
    with db() as conn:
        return conn.execute(
            "select count(*) as n from dataset_version where dataset_id = %s",
            (dataset_id,),
        ).fetchone()["n"]


def main() -> int:
    require_api()
    owning = department("Verification")
    engineer = bearer_for(ENGINEER)
    reviewer = bearer_for(REVIEWER)

    # One prepared dataset, reused by the checks that need a startable version.
    # Built through spec A's own path rather than by writing rows, so what is
    # being started is the thing a person would actually produce.
    ready = register(owning)
    put(ready, "synth-9000.wav", tiny_wav(0.5, 16000))
    put(ready, "synth-9000.truth.json", answer_key())
    sealed = api("POST", f"/datasets/{ready}/seal-audio", headers=engineer)
    if sealed.status_code != 201:
        raise RuntimeError(
            f"could not prepare a version to start: HTTP {sealed.status_code} "
            f"{sealed.text[:200]}"
        )
    ready_version = sealed.json()["id"]

    heading("U61a: starting a run requires a session")

    anonymous = start(ready, ready_version)
    check("no session is refused", anonymous.status_code == 401,
          f"HTTP {anonymous.status_code}")

    heading("U61b: and the role whose job it is")

    wrong_role = start(ready, ready_version, reviewer)
    check("a reviewer may not start a run", wrong_role.status_code == 403,
          f"HTTP {wrong_role.status_code}")
    said = reasons_of(wrong_role)
    check("and the refusal carries the policy's own reason",
          any("no role that may start a pipeline run" in r for r in said),
          f"reasons {said}")

    heading("U61c: the version has to exist, sealed, on this dataset")

    unknown = start(ready, NIL, engineer)
    check("an unknown version is refused", unknown.status_code == 404,
          f"HTTP {unknown.status_code}")
    said = reasons_of(unknown)
    check("the refusal says what was looked for",
          any("sealed version" in r for r in said), f"reasons {said}")

    # A real, sealed version, but belonging to a different dataset. This is the
    # check the one above cannot make: a 404 for a made-up id proves only that
    # the id was not found, not that the dataset is part of the lookup.
    other = register(owning)
    put(other, "synth-9100.wav", tiny_wav(0.5, 16000))
    put(other, "synth-9100.truth.json", answer_key())
    other_version = api("POST", f"/datasets/{other}/seal-audio",
                        headers=engineer).json()["id"]
    crossed = start(ready, other_version, engineer)
    check("a version belonging to another dataset is refused",
          crossed.status_code == 404, f"HTTP {crossed.status_code}")

    heading("U61d: a version sealed the ordinary way is refused, and told why")

    plain = register(owning)
    put(plain, "notes.txt", b"a file the platform has no opinion about")
    plain_version = api("POST", f"/datasets/{plain}/seal").json()["id"]

    refused = start(plain, plain_version, engineer)
    check("the ordinary seal's version cannot be de-identified",
          refused.status_code == 400, f"HTTP {refused.status_code}")
    said = reasons_of(refused)
    check("the refusal names the contract it found",
          any("uploaded_files" in r for r in said), f"reasons {said}")
    check("and says what to do instead",
          any("Seal as recordings" in r for r in said), f"reasons {said}")

    heading("U61e: a recording with no answer key is refused, by name")

    keyless = register(owning)
    put(keyless, "synth-9200.wav", tiny_wav(0.5, 16000))
    put(keyless, "synth-9201.wav", tiny_wav(0.5, 16000))
    put(keyless, "synth-9200.truth.json", answer_key())
    keyless_version = api("POST", f"/datasets/{keyless}/seal-audio",
                          headers=engineer).json()["id"]

    no_key = start(keyless, keyless_version, engineer)
    check("a version missing an answer key is refused",
          no_key.status_code == 400, f"HTTP {no_key.status_code}")
    said = reasons_of(no_key)
    check("the refusal names the record that is missing one",
          any("synth-9201" in r for r in said), f"reasons {said}")
    check("and does not accuse the record that has one",
          not any("synth-9200" in r for r in said), f"reasons {said}")
    check("it says scoring without an answer key is not built, not that it broke",
          any("not built yet" in r for r in said), f"reasons {said}")

    heading("U61f: a refusal starts nothing")

    with db() as conn:
        started = conn.execute(
            """select count(*) as n from pipeline_run
               where source_version_id in (%s, %s, %s)""",
            (plain_version, keyless_version, other_version),
        ).fetchone()["n"]
    check("no run row exists for any refused version", started == 0,
          f"{started} row(s)")

    heading("U61g0: a run that scores its output needs the score store")

    # The console refuses a de-identification run while MLflow is down,
    # because verify writes its score card there and the run could not
    # finish. So the checks that need a real start can only run while it is
    # up; while it is down they are reported as skipped, never as passes.
    store_up = score_store_up()
    if not store_up:
        refused_store = start(ready, ready_version, engineer)
        reasons = (refused_store.json().get("detail") or {}).get("reasons", [])
        check("with the score store down, a start is refused at once",
              refused_store.status_code == 503, f"HTTP {refused_store.status_code}")
        check("and the refusal names the score store and how to start it",
              any("score store" in r and "mlflow" in r.lower() for r in reasons),
              str(reasons)[:200])
        with db() as conn:
            none_started = conn.execute(
                "select count(*) as n from pipeline_run where source_version_id = %s",
                (ready_version,),
            ).fetchone()["n"]
        check("and nothing was started", none_started == 0, f"{none_started} row(s)")
        for label in ['U61g: the run row exists before any worker sees the workflow', 'U61h: a second start against the same version is refused', 'U61j: the started run is stopped again', 'U61j2: a run that has stopped no longer blocks its version']:
            skip(label, "the score store (MLflow) is not running, so no run can be "
                 "started to test this. Start it with `docker compose --profile "
                 "full up -d mlflow` to run these")

    if store_up:
        heading("U61g: the run row exists before any worker sees the workflow")

        accepted = start(ready, ready_version, engineer)
        check("a prepared version starts", accepted.status_code == 202,
              f"HTTP {accepted.status_code} {accepted.text[:200]}")
        if accepted.status_code != 202:
            return summary("U61")

        body = accepted.json()
        run_id = body["pipeline_run_id"]
        check("the response names the run and its workflow",
              bool(run_id) and body.get("workflow_id", "").startswith("deid-"),
              f"run {run_id} workflow {body.get('workflow_id')}")
        check("and says how many recordings it covers", body.get("records") == 1,
              f"records {body.get('records')}")

        # Read immediately, with no wait. This is the check that proves writing the
        # row before starting the workflow was worth doing: if the row were opened
        # by the workflow's first activity, this read would race a worker and would
        # sometimes 404.
        seen = api("GET", f"/pipeline-runs/{run_id}", headers=bearer_for(ENGINEER))
        check("the run reads back at once", seen.status_code == 200,
              f"HTTP {seen.status_code}")
        run = seen.json() if seen.status_code == 200 else {}
        check("it records that it was started from the console",
              run.get("started_from") == "console",
              f"started_from {run.get('started_from')}")
        check("it points at the version it began from",
              run.get("source_version_id") == ready_version,
              f"source_version_id {run.get('source_version_id')}")
        check("it is attributed to the session that started it, not to a claim",
              run.get("triggered_by") == ENGINEER,
              f"triggered_by {run.get('triggered_by')}")

        heading("U61h: a second start against the same version is refused")

        again = start(ready, ready_version, engineer)
        check("the second start is refused", again.status_code == 409,
              f"HTTP {again.status_code}")
        check("and hands back the run that is already going",
              (again.json().get("detail") or {}).get("pipeline_run_id") == run_id,
              f"offered {(again.json().get('detail') or {}).get('pipeline_run_id')}")

        with db() as conn:
            rows = conn.execute(
                "select count(*) as n from pipeline_run where source_version_id = %s",
                (ready_version,),
            ).fetchone()["n"]
        check("so one version has one run, not two", rows == 1, f"{rows} row(s)")

    heading("U61i: a version on storage the pipeline cannot read is refused")

    # Sealed straight through POST /dataset-versions, because that endpoint
    # records the backend without needing it to be configured. R2 is unset on
    # most installs, which is exactly why this path is worth a check: nothing
    # else here would ever exercise it, and the failure it prevents is silent.
    elsewhere = register(owning)
    put(elsewhere, "synth-9300.wav", tiny_wav(0.5, 16000))
    put(elsewhere, "synth-9300.truth.json", answer_key())
    r2_version = api("POST", "/dataset-versions", json={
        "tenant_id": TENANT,
        "dataset_id": elsewhere,
        "schema_id": api("POST", "/schema-contracts", json={
            "tenant_id": TENANT, "name": "encounter_raw",
            "fields": [
                {"name": "record_id", "type": "string", "sensitivity": "none",
                 "added_by": "munitas-worker"},
                {"name": "audio_key", "type": "string", "sensitivity": "none",
                 "added_by": "munitas-worker"},
                {"name": "duration_seconds", "type": "float",
                 "sensitivity": "none", "added_by": "munitas-worker"},
                {"name": "sample_rate", "type": "int", "sensitivity": "none",
                 "added_by": "munitas-worker"},
            ],
            "primary_key": ["record_id"],
        }).json()["id"],
        "visibility_class": "RAW",
        "object_manifest": [],
        "record_count": 1,
        "storage_backend": "r2",
    })
    check("a version can be sealed against another backend",
          r2_version.status_code == 201, f"HTTP {r2_version.status_code}")

    if r2_version.status_code == 201:
        refused_backend = start(elsewhere, r2_version.json()["id"], engineer)
        check("a run against it is refused", refused_backend.status_code == 400,
              f"HTTP {refused_backend.status_code}")
        said = reasons_of(refused_backend)
        check("the refusal names the storage it found",
              any("'r2'" in r for r in said), f"reasons {said}")
        check("and says what would have happened",
              any("nothing can read" in r for r in said), f"reasons {said}")

    if store_up:
        heading("U61j: the started run is stopped again, so the suite leaves no GPU job")

        # U61g had to start a real run, because the fact it proves is that the row
        # exists before a worker sees the workflow, and only a real start can show
        # that. Left alone, every run of the suite would launch several minutes of
        # transcription on the GPU. What this slice needs to prove about a
        # completed run is proved once, in U61j below, against a run made on
        # purpose rather than as a side effect of a refusal test.
        stopped = asyncio.run(terminate(body["workflow_id"]))
        check("the run started by this script is stopped", stopped,
              f"terminated {body['workflow_id']}")
        after = api("GET", f"/pipeline-runs/{run_id}", headers=bearer_for(ENGINEER)).json()
        check("and the platform reports it as stopped from outside",
              after.get("status") == "terminated",
              f"status {after.get('status')}")
        with db() as conn:
            recorded = conn.execute(
                "select status, error, ended_at, ended_source from pipeline_run where id = %s",
                (run_id,)).fetchone()
        check("which is now recorded in the database, with why and when",
              recorded["status"] == "terminated" and recorded["error"]
              and recorded["ended_at"] is not None, f"{dict(recorded)}")
        check("and says it came from the job runner, because the workflow never reported it",
              recorded["ended_source"] == "job_runner", f"{recorded['ended_source']}")
        check("the run page says the same",
              after.get("ended_source") == "job_runner", f"{after.get('ended_source')}")

        heading("U61j2: a run that has stopped no longer blocks its version")

        # Terminated from outside, this run never reached the `finally` that ends
        # its record, so the record is still open. Until the console asked
        # Temporal before refusing, every version that had been run once could
        # never be run again, because no run record was ever ended.
        rerun = start(ready, ready_version, engineer)
        check("the same version starts again once its run has stopped",
              rerun.status_code == 202, f"HTTP {rerun.status_code} {rerun.text[:200]}")
        with db() as conn:
            first_ended = conn.execute(
                "select ended_at from pipeline_run where id = %s", (run_id,)
            ).fetchone()["ended_at"]
        check("and the stopped run's record is now ended", first_ended is not None,
              f"ended_at {first_ended}")
        if rerun.status_code == 202:
            rerun_workflow = rerun.json()["workflow_id"]
            check("the rerun is stopped too, so the suite leaves no GPU job",
                  asyncio.run(terminate(rerun_workflow)), f"terminated {rerun_workflow}")

    heading("U61k: the worker check refuses a queue nothing polls")

    # The endpoint's own queue has a worker on it, so the refusal cannot be
    # provoked through the endpoint without stopping that worker and waiting
    # out Temporal's poller record. The helper is exercised directly against a
    # queue name nothing has ever polled, which is the same code path the
    # endpoint takes, and the sample below proves the helper can also return a
    # non-zero answer rather than always saying zero.
    # The API's own package, which lives at /app in this image while these
    # scripts run from /verify. Added explicitly rather than relying on the
    # working directory, which is not on sys.path when a script is run by path.
    sys.path.insert(0, "/app")
    from app import config, pipeline, temporal_client

    async def pollers() -> int:
        # The module-level client is opened by the API's lifespan, which does
        # not run in this process. Connected here rather than reached for,
        # because a TemporalUnavailable raised by the setup would look exactly
        # like the answer the check is asking for.
        await temporal_client.connect()
        return await pipeline._pipeline_worker_pollers()

    real = asyncio.run(pollers())
    check("the real pipeline queue has a worker polling it", real > 0,
          f"{real} poller(s) on {config.PIPELINE_TASK_QUEUE!r}; "
          "the end to end run needs one")

    made_up = f"munitas-nobody-{uuid.uuid4().hex[:8]}"
    original = config.PIPELINE_TASK_QUEUE
    try:
        config.PIPELINE_TASK_QUEUE = made_up
        empty = asyncio.run(pollers())
    finally:
        config.PIPELINE_TASK_QUEUE = original
    check("a queue nothing polls reports no workers", empty == 0,
          f"{empty} poller(s) on {made_up!r}")
    check("and the queue name was put back",
          config.PIPELINE_TASK_QUEUE == original,
          f"now {config.PIPELINE_TASK_QUEUE!r}")

    heading("U61l: a run's outcome is recorded, and the first ending stands")
    workflow_id = f"u61l-{uuid.uuid4().hex[:8]}"
    made = api("POST", "/pipeline-runs", json={
        "tenant_id": CANARY, "dataset": "u61l", "workflow_id": workflow_id,
        "triggered_by": ENGINEER}).json()["pipeline_run_id"]
    try:
        # Read from the database, not the run page: this record has no real
        # job behind it, and the page would ask the job runner, find none,
        # and rightly record the run as ended 'unknown' before this test
        # could end it itself.
        with db() as conn:
            fresh = conn.execute("select status, ended_at from pipeline_run where id = %s",
                                 (made,)).fetchone()
        check("a run just started is recorded as running, with no end time",
              fresh["status"] == "running" and fresh["ended_at"] is None, f"{dict(fresh)}")
        ended = api("POST", f"/pipeline-runs/{made}/end",
                    json={"status": "failed", "error": "u61l: the redact step ran out of disk"})
        check("ending it as failed is accepted", ended.status_code == 200,
              f"HTTP {ended.status_code} {ended.text[:120]}")
        again = api("POST", f"/pipeline-runs/{made}/end", json={"status": "succeeded"})
        with db() as conn:
            row = conn.execute("select status, error, ended_at, ended_source from pipeline_run where id = %s",
                               (made,)).fetchone()
        check("the workflow's own ending is recorded as coming from the workflow",
              row["ended_source"] == "workflow", f"{row['ended_source']}")
        check("the outcome and the reason are recorded",
              row["status"] == "failed" and row["error"] == "u61l: the redact step ran out of disk",
              f"{dict(row)}")
        check("a second ending does not overwrite the first (negative)",
              again.status_code == 200 and row["status"] == "failed", f"{dict(row)}")
        shown = api("GET", f"/pipeline-runs/{made}", headers=bearer_for(ENGINEER)).json()
        check("and the run page reads them from the record",
              shown.get("status") == "failed" and "ran out of disk" in (shown.get("error") or ""),
              f"status {shown.get('status')}, error {shown.get('error')}")
        refused = api("POST", f"/pipeline-runs/{made}/end", json={"status": "finished-ish"})
        check("an outcome outside the list is refused (negative)", refused.status_code == 422,
              f"HTTP {refused.status_code}")
        with db() as conn:
            try:
                conn.execute("update pipeline_run set status = 'running' where id = %s", (made,))
                blocked = None
            except Exception as exc:  # noqa: BLE001 - the refusal is the point
                blocked = str(exc)
        check("the database refuses an ended run marked running (negative)",
              blocked is not None and "pipeline_run_status_matches_end" in blocked,
              (blocked or "the update was accepted")[:120])
    finally:
        with db() as conn:
            conn.execute("delete from pipeline_run where id = %s", (made,))

    # A run record the job runner has never heard of is not running. Opening
    # it closes it as 'unknown', with that reason, rather than leaving it
    # 'running' for ever.
    orphan = api("POST", "/pipeline-runs", json={
        "tenant_id": CANARY, "dataset": "u61l", "workflow_id": f"u61l-{uuid.uuid4().hex[:8]}",
        "triggered_by": ENGINEER}).json()["pipeline_run_id"]
    try:
        shown = api("GET", f"/pipeline-runs/{orphan}", headers=bearer_for(ENGINEER)).json()
        with db() as conn:
            row = conn.execute("select status, error, ended_at, ended_source from pipeline_run where id = %s",
                               (orphan,)).fetchone()
        check("a run the job runner never heard of is closed as unknown when opened",
              shown.get("status") == "unknown" and row["status"] == "unknown"
              and row["ended_at"] is not None and "no record" in (row["error"] or ""),
              f"page {shown.get('status')}, record {dict(row)}")
        check("and says the job runner had no record, not that it reported a failure",
              row["ended_source"] == "job_runner_no_record" and shown.get("ended_source") == "job_runner_no_record",
              f"record {row['ended_source']}, page {shown.get('ended_source')}")
    finally:
        with db() as conn:
            conn.execute("delete from pipeline_run where id = %s", (orphan,))

    return summary("U61")


if __name__ == "__main__":
    sys.exit(main())
