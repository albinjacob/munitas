"""Bringing data in.

Separate from everything else for one reason worth seeing in the file list:
**this is the only module that accepts dataset bytes.** Every other endpoint
moves identifiers and decisions. If a reviewer wants to know where dataset
data can enter the platform, the answer should be one file rather than a
search. Agent *code* is a different kind of bytes with its own file,
`agent_upload.py`, for the same reason kept separate rather than merged in
here: two different ingress stories are easier to review as two files than
one file with two purposes.

Ingress only. Nothing here returns file contents. The single way data leaves is
`/datasets/{id}/export`, which asks the policy engine first and records the
decision either way. The design's rule was that no endpoint returns records,
because an endpoint that returns records is an endpoint to abuse. Export is a
deliberate and narrow exception for data that was already public before it
arrived.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import wave
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from . import (auth, config, db, models, opa, storage, task_credential,
              temporal_client, versions)

router = APIRouter(tags=["ingest"])

# Large enough for a consultation recording, small enough that one mistake does
# not fill the volume. A limit somebody hits and complains about beats no limit
# and a full disk at three in the morning.
MAX_UPLOAD_BYTES = 512 * 1024 * 1024

PROVENANCE = "^(external_public|external_licensed|internal_regulated)$"
CLASSES = "^(RAW|UNDER_REVIEW|OPEN_FOR_ANNOTATION|OPEN_FOR_TRAINING|PUBLISHED)$"

# The most restrictive class. Declaring this is not a claim: it asks for less
# than the platform would give you anyway, and being wrong costs an
# inconvenience rather than an exposure.
MOST_RESTRICTIVE = "RAW"


class RegisterDataset(BaseModel):
    tenant_id: str
    name: str
    # Required, unlike everywhere else. A dataset arriving with no owner is one
    # nobody can approve access to, and there are already thirty-nine of those.
    department_id: str
    registered_by: str
    provenance: str = Field(pattern=PROVENANCE)
    declared_class: str = Field(default=MOST_RESTRICTIVE, pattern=CLASSES)
    source_kind: str = Field(default="upload", pattern="^(upload|huggingface)$")
    locator: str = ""
    # What kind of thing this holds. Declared rather than inferred from file
    # extensions, because a guess that looks like a fact is worse than a blank.
    modality: list[str] = Field(default_factory=list)
    # Which backend this dataset's bytes should land on. Null means "use
    # this tenant's own default_storage_backend," resolved once, here, at
    # registration time, not re-resolved later if the tenant's default
    # changes afterward.
    storage_backend: str | None = Field(default=None, pattern="^(seaweedfs|r2)$")


class ConfirmClassification(BaseModel):
    confirmed_by: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dataset(dataset_id: str) -> dict:
    row = db.one(
        """select d.*, dept.custodian, dept.name as department_name
           from dataset d
           left join department dept on dept.id = d.department_id
           where d.id = %s""",
        (dataset_id,),
    )
    if not row:
        raise HTTPException(404, "no such dataset")
    return row


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str, tenant_id: str | None = None) -> dict:
    """One dataset's own record.

    What the console reads to resume bringing data into a dataset that was
    registered and then left, rather than sealed on the spot: registering
    and uploading were always two separate steps, but until now nothing let
    a person come back to the second one. Narrowed to one organisation when
    asked, and answering "no such dataset" rather than "not yours" for a
    dataset in another tenant, the same non-disclosure posture the other
    detail endpoints already use.
    """
    dataset = _dataset(dataset_id)
    if tenant_id and dataset["tenant_id"] != tenant_id:
        raise HTTPException(404, "no such dataset")
    return dataset


@router.post("/datasets/register", status_code=201)
def register(body: RegisterDataset) -> dict:
    """Register a dataset and say where it came from.

    Registering exposes nothing, so it needs nobody's approval. It does need an
    owning department, because that is who becomes accountable, and it does need
    a provenance, because that is what decides whether the data may ever leave.

    The sensitivity claim is recorded here rather than assumed. Asking for the
    most restrictive class is not a claim and carries no basis. Asking for
    anything less is one, and every claim starts out `asserted`: a person said
    so, at registration, before any bytes have arrived.

    `verified_source` is not granted here, even when `source_kind` names
    HuggingFace. That would trust a label the caller supplied instead of
    checking anything, the same shape of bug this project has already fixed
    three times over: identity or provenance taken from what was claimed
    rather than from what was done. The basis is upgraded later, by
    `fetch_huggingface`, and only once the platform has made the request
    itself and can show the origin.

    The `provenance` chosen here is provisional in exactly the same way when
    `source_kind` is `huggingface`: `fetch_huggingface` looks up the repo's
    actual licence and overwrites it once the fetch runs, the same as
    `declared_class` is provisional until a custodian confirms it.
    """
    department = db.one(
        "select id, custodian from department where id = %s and tenant_id = %s",
        (body.department_id, body.tenant_id),
    )
    if not department:
        raise HTTPException(
            400,
            {
                "reasons": [
                    "that department does not exist, and a dataset without an "
                    "owning department is one nobody can approve access to"
                ]
            },
        )

    if not db.one("select id from directory where id = %s", (body.registered_by,)):
        raise HTTPException(
            403, {"reasons": [f"{body.registered_by!r} is not in the directory"]}
        )

    claiming = body.declared_class != MOST_RESTRICTIVE
    basis = "asserted" if claiming else None

    resolved_backend = body.storage_backend
    if resolved_backend is None:
        tenant_row = db.one(
            "select default_storage_backend from tenant where id = %s",
            (body.tenant_id,),
        )
        resolved_backend = (tenant_row or {}).get("default_storage_backend") or "seaweedfs"

    dataset_id = str(uuid.uuid4())
    try:
        db.execute(
            """insert into dataset
                 (id, tenant_id, name, department_id, provenance,
                  registered_by, registered_at, declared_class, declared_by,
                  declared_at, declaration_basis, modality, storage_backend)
               values (%s, %s, %s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s)""",
            (dataset_id, body.tenant_id, body.name, body.department_id,
             body.provenance, body.registered_by, body.declared_class,
             body.registered_by if claiming else None,
             _now() if claiming else None, basis, body.modality or None,
             resolved_backend),
        )
    except Exception as exc:  # unique (tenant_id, name)
        raise HTTPException(409, {"reasons": [f"could not register: {exc}"]}) from exc

    db.execute(
        """insert into dataset_source (id, dataset_id, kind, locator, fetched_by)
           values (%s, %s, %s, %s, %s)""",
        (str(uuid.uuid4()), dataset_id, body.source_kind,
         body.locator or "uploaded by hand", body.registered_by),
    )

    return {
        "id": dataset_id,
        "declared_class": body.declared_class,
        "declaration_basis": basis,
        "needs_confirmation": basis == "asserted",
        "custodian": department["custodian"],
        "note": (
            "Nothing is readable yet. It becomes readable when the data is sealed "
            "and, if you declared it less sensitive than the default, when the "
            "custodian agrees with you."
        ),
    }


AUDIO_SUFFIX = ".wav"
ANSWER_KEY_SUFFIX = ".truth.json"


def _audio_facts(filename: str, payload: bytes) -> dict:
    """What the encounter_raw contract needs, taken from the bytes themselves.

    Derived here because this is the one moment the platform holds the data and
    has an unambiguous right to look at it. A later job would have to ask for a
    credential, and POST /credentials is scoped to a sealed dataset version,
    which does not exist while somebody is still deciding whether to seal.

    The whole file is parsed rather than a ranged header read. The bytes are
    already in memory, so there is nothing to save, and a file carrying extra
    chunks before its data would yield a wrong duration rather than an error.
    """
    try:
        with wave.open(io.BytesIO(payload), "rb") as handle:
            frames, rate = handle.getnframes(), handle.getframerate()
    except Exception as exc:  # noqa: BLE001 - any parse failure is the same answer
        raise HTTPException(400, {"reasons": [
            f"{filename!r} is named as audio but could not be read as a wav "
            f"file: {exc}"
        ]}) from exc
    if not rate:
        raise HTTPException(400, {"reasons": [
            f"{filename!r} reports a sample rate of zero, so no duration can "
            f"be derived from it"
        ]})
    return {"audio_duration_seconds": frames / rate, "audio_sample_rate": rate}


def _answer_key_facts(filename: str, payload: bytes) -> dict:
    """Check an answer key carries what scoring actually consumes.

    `worker/activities.py`'s verify reads only `reference_transcript` and
    `spans`, so those two are required. `hazard` is recorded and never
    required: the pipeline never reads it, but verify/v14_aligned.py subscripts
    it directly, so its absence is worth reporting and is not worth refusing an
    upload over.
    """
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - any parse failure is the same answer
        raise HTTPException(400, {"reasons": [
            f"{filename!r} is named as an answer key but is not valid JSON: {exc}"
        ]}) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(400, {"reasons": [
            f"{filename!r} is named as an answer key but holds a "
            f"{type(parsed).__name__}, not an object"
        ]})
    missing = [k for k in ("spans", "reference_transcript") if k not in parsed]
    if missing:
        raise HTTPException(400, {"reasons": [
            f"{filename!r} is missing {' and '.join(missing)}, which scoring reads"
        ]})
    return {"truth_has_hazard": "hazard" in parsed}


@router.post("/datasets/{dataset_id}/files", status_code=201)
async def upload(dataset_id: str, file: UploadFile = File(...)) -> dict:
    """Accept a file.

    The only endpoint in the platform that takes bytes from a person. It writes
    them where the dataset's first version will point, records the size and a
    checksum, and returns nothing that could be used to read them back.
    """
    dataset = _dataset(dataset_id)
    if dataset["department_id"] is None:
        raise HTTPException(400, {"reasons": ["this dataset has no owning department"]})

    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            {"reasons": [
                f"{file.filename!r} is {len(payload)} bytes, over the "
                f"{MAX_UPLOAD_BYTES} byte limit"
            ]},
        )
    if not payload:
        raise HTTPException(400, {"reasons": ["that file is empty"]})

    # Refused before the object is written, so a file the platform cannot read
    # leaves nothing behind to clean up or to confuse a later count.
    derived: dict = {}
    if file.filename.endswith(AUDIO_SUFFIX):
        derived = _audio_facts(file.filename, payload)
    elif file.filename.endswith(ANSWER_KEY_SUFFIX):
        derived = _answer_key_facts(file.filename, payload)

    reserved = versions.next_version(dataset["tenant_id"], dataset_id)
    key = f"{reserved['storage_prefix']}/{file.filename}"
    checksum = hashlib.sha256(payload).hexdigest()

    client = storage.admin_client_for(dataset["storage_backend"], dataset["tenant_id"])
    client.put_object(
        Bucket=storage.bucket_for(dataset["storage_backend"], dataset["tenant_id"]),
        Key=key, Body=payload,
    )

    # The ledger row's id is returned so the caller can later withdraw this
    # exact upload. It is not a handle on the bytes: nothing here returns file
    # contents, and the withdraw endpoint it feeds only marks a row.
    source_id = str(uuid.uuid4())
    db.execute(
        """insert into dataset_source
             (id, dataset_id, kind, locator, checksum, bytes, fetched_by,
              audio_duration_seconds, audio_sample_rate, truth_has_hazard)
           values (%s, %s, 'upload', %s, %s, %s, %s, %s, %s, %s)""",
        (source_id, dataset_id, file.filename, checksum, len(payload),
         dataset["registered_by"], derived.get("audio_duration_seconds"),
         derived.get("audio_sample_rate"), derived.get("truth_has_hazard")),
    )

    return {"source_id": source_id, "filename": file.filename,
            "bytes": len(payload), "checksum": checksum, **derived}


# Must stay identical to worker/contracts.py's RAW_AUDIO. Restated rather than
# imported because the worker is a separate process with its own dependencies
# and the API cannot import from it. U60g checks the two agree, which is the
# only thing that makes restating them safe.
#
# Built through models.Field_ rather than as plain dicts, and that is not
# decoration. The contract's digest is taken over exactly what
# POST /schema-contracts stores, which is `model_dump()` of this model, and
# that carries every optional key the model declares. Hand-written dicts missing
# `format` hashed differently, so the worker registering the same contract made
# a second row under the same name. Going through the model means a key added to
# it later cannot reintroduce that.
ENCOUNTER_RAW_FIELDS = [
    models.Field_(name="record_id", type="string",
                  added_by="munitas-worker").model_dump(),
    models.Field_(name="audio_key", type="string",
                  added_by="munitas-worker").model_dump(),
    models.Field_(name="duration_seconds", type="float",
                  added_by="munitas-worker").model_dump(),
    models.Field_(name="sample_rate", type="int",
                  added_by="munitas-worker").model_dump(),
]


def _pending_uploads(dataset_id: str) -> list[dict]:
    """The uploads that count, newest first per filename.

    Re-uploading a file to replace a bad one inserts a second row rather than
    updating the first, because there is no unique constraint on
    (dataset_id, locator). Taking every row would list that file twice and
    count one too many. The generic seal path has the same flaw and is left
    alone deliberately: it is pre-existing and gets its own fix rather than a
    quiet change as a side effect of this work.
    """
    return db.all_rows(
        """select distinct on (locator)
                 locator, checksum, bytes, audio_duration_seconds,
                 audio_sample_rate, truth_has_hazard
           from dataset_source
           where dataset_id = %s and kind = 'upload'
             and checksum is not null and withdrawn_at is null
           order by locator, fetched_at desc""",
        (dataset_id,),
    )


@router.post("/datasets/{dataset_id}/seal-audio", status_code=201)
def seal_audio(dataset_id: str,
               identity: dict = Depends(auth.current_session)) -> dict:
    """Seal the uploads as a version the de-identification pipeline can read.

    There is no separate check step, deliberately. Sealing is irreversible, so
    a mistake cannot be undone, but a refusal seals nothing, which makes
    pressing this the check. A second Check button would ask the same question
    and say the same thing twice.

    Every problem is reported together. Reporting only the first would turn
    twelve problems into twelve attempts.

    The ordinary seal uses a two-field contract because the platform does not
    know what an uploaded file is. This path does know, because the upload
    endpoint read the audio, so it can claim encounter_raw honestly rather than
    asserting a shape nothing checked.
    """
    dataset = _dataset(dataset_id)

    running = db.one(
        "select id from huggingface_fetch_job where dataset_id = %s and status = 'running'",
        (dataset_id,),
    )
    if running:
        raise HTTPException(409, {"reasons": [
            "a HuggingFace fetch is still running for this dataset; sealing "
            "now could produce a version missing files still on the way"
        ]})

    uploads = _pending_uploads(dataset_id)
    audio = {f["locator"][:-len(AUDIO_SUFFIX)]: f
             for f in uploads if f["locator"].endswith(AUDIO_SUFFIX)}
    keys = {f["locator"][:-len(ANSWER_KEY_SUFFIX)]: f
            for f in uploads if f["locator"].endswith(ANSWER_KEY_SUFFIX)}

    reasons: list[str] = []
    if not uploads:
        reasons.append("nothing has been uploaded to this dataset yet")
    elif not audio:
        reasons.append(
            "none of the uploaded files is audio, so there is nothing to "
            "de-identify. Audio files are named <record>.wav"
        )
    for stem in sorted(set(keys) - set(audio)):
        reasons.append(
            f"{stem}{ANSWER_KEY_SUFFIX} is an answer key for a recording that "
            f"was not uploaded. Upload {stem}{AUDIO_SUFFIX} or withdraw the key"
        )
    for other in sorted(f["locator"] for f in uploads
                        if not f["locator"].endswith(AUDIO_SUFFIX)
                        and not f["locator"].endswith(ANSWER_KEY_SUFFIX)):
        reasons.append(
            f"{other} is neither a recording nor an answer key. Withdraw it, "
            f"or seal this dataset with the ordinary Seal instead"
        )
    if reasons:
        raise HTTPException(400, {"reasons": reasons})

    reserved = versions.next_version(dataset["tenant_id"], dataset_id)
    prefix = reserved["storage_prefix"]

    records = [
        {
            "record_id": stem,
            "audio_key": f"{prefix}/{stem}{AUDIO_SUFFIX}",
            "duration_seconds": float(row["audio_duration_seconds"]),
            "sample_rate": int(row["audio_sample_rate"]),
        }
        for stem, row in sorted(audio.items())
    ]
    payload = json.dumps(records).encode("utf-8")

    client = storage.admin_client_for(dataset["storage_backend"], dataset["tenant_id"])
    client.put_object(
        Bucket=storage.bucket_for(dataset["storage_backend"], dataset["tenant_id"]),
        Key=f"{prefix}/records.json", Body=payload,
    )

    contract = versions.register_contract(
        dataset["tenant_id"], "encounter_raw", ENCOUNTER_RAW_FIELDS, ["record_id"]
    )

    # Full keys, matching what the pipeline's own ingest writes, because this
    # version is read by the pipeline rather than by the uploads screen.
    manifest = [{"key": f"{prefix}/{f['locator']}", "bytes": f["bytes"],
                 "sha256": f["checksum"]} for f in uploads]
    manifest.append({"key": f"{prefix}/records.json", "bytes": len(payload),
                     "sha256": hashlib.sha256(payload).hexdigest()})

    version = versions.seal(
        tenant_id=dataset["tenant_id"],
        dataset_id=dataset_id,
        schema_id=contract["id"],
        visibility_class="RAW",
        storage_backend=dataset["storage_backend"] or "seaweedfs",
        object_manifest=manifest,
        # The number of recordings, not the number of objects. An answer key is
        # evidence about a record, not a record of its own, and the pipeline
        # sizes its own timeouts from this.
        record_count=len(records),
    )
    return {**version, "records": len(records), "sealed_by": identity["id"]}


@router.post("/datasets/{dataset_id}/uploads/{source_id}/withdraw")
def withdraw_upload(dataset_id: str, source_id: str,
                    identity: dict = Depends(auth.current_session)) -> dict:
    """Take one upload out of the pending set, without erasing that it arrived.

    The row stays, so the ledger continues to show the file was uploaded and by
    whom it was withdrawn. Deleting it would leave no answer to "what happened
    to that recording", which is the question somebody asks precisely when a
    file has gone missing.

    Withdrawing twice is not an error. The second call finds the row already
    withdrawn and reports the same result, because the caller's intent is
    already satisfied.
    """
    dataset = _dataset(dataset_id)
    row = db.one(
        """select id, locator, withdrawn_at from dataset_source
           where id = %s and dataset_id = %s""",
        (source_id, dataset_id),
    )
    if not row:
        raise HTTPException(404, {"reasons": ["no such upload on this dataset"]})

    if row["withdrawn_at"] is None:
        db.execute(
            """update dataset_source
                 set withdrawn_at = now(), withdrawn_by = %s
               where id = %s""",
            (identity["id"], source_id),
        )
    return {"withdrawn": True, "locator": row["locator"],
            "tenant_id": dataset["tenant_id"]}


class FetchHuggingFace(BaseModel):
    fetched_by: str
    repo_id: str = Field(min_length=1)
    revision: str = "main"
    # A folder within the repo, e.g. "data". Empty means the repo root.
    path: str = ""


@router.post("/datasets/{dataset_id}/fetch-huggingface", status_code=202)
async def fetch_huggingface(dataset_id: str, body: FetchHuggingFace) -> dict:
    """Start fetching files from a public HuggingFace dataset repo, in the
    background.

    This used to do the whole fetch inline, one HTTP request holding the
    connection until every file arrived. A real repo's single largest file
    turned out to be several hundred megabytes, downloaded whole into memory
    before this container's own 512MB limit, which got the process
    OOM-killed mid-request; the browser had nothing to show but a dropped
    connection. Buffering was fixed independently of this, but the deeper
    problem was the shape: a slow or large fetch has no business holding a
    browser tab, or living or dying with the same process that serves every
    other request.

    What happens here is only what can be checked before anything talks to
    HuggingFace: the department exists, `fetched_by` is registered. Every
    HuggingFace-specific fact (does the repo exist, is it gated, what licence
    governs it, how many files it holds) is a network call, and none of that
    belongs in something a browser is waiting on. It happens in
    `worker/hf_ingest_workflow.py`, a Temporal workflow running in a separate
    process, so a crash there is not a crash here. The job row this creates
    is what the console polls instead of waiting on this request; see
    `GET /datasets/{id}/huggingface-fetch-jobs`.
    """
    dataset = _dataset(dataset_id)
    if dataset["department_id"] is None:
        raise HTTPException(400, {"reasons": ["this dataset has no owning department"]})

    if not db.one("select id from directory where id = %s", (body.fetched_by,)):
        raise HTTPException(
            403, {"reasons": [f"{body.fetched_by!r} is not in the directory"]}
        )

    job_id = str(uuid.uuid4())
    workflow_id = f"hf-fetch-{job_id[:10]}"

    try:
        client = temporal_client.get()
    except temporal_client.TemporalUnavailable as exc:
        raise HTTPException(
            503, {"reasons": [f"the background job runner is unavailable: {exc}"]}
        ) from exc

    db.execute(
        """insert into huggingface_fetch_job
             (id, dataset_id, repo_id, revision, path, fetched_by, status, workflow_id)
           values (%s, %s, %s, %s, %s, %s, 'running', %s)""",
        (job_id, dataset_id, body.repo_id, body.revision, body.path,
         body.fetched_by, workflow_id),
    )

    try:
        await client.start_workflow(
            "HuggingFaceFetchWorkflow",
            {
                "job_id": job_id,
                "dataset_id": dataset_id,
                "tenant_id": dataset["tenant_id"],
                "repo_id": body.repo_id,
                "revision": body.revision,
                "path": body.path,
                "fetched_by": body.fetched_by,
                # Proof that fetch_one_file's later write really is this job,
                # for POST /write-credentials -- the same task_credential.py
                # shape action_run/pipeline_run already use, minted here
                # rather than left to the old static pipeline_action key,
                # which no longer holds standing Write at all (see
                # docs/internal/design/write-credential-rationale.md). Minted once, at
                # the one moment this job genuinely begins, never by the
                # workflow or activity that only ever receives it.
                "task_credential": task_credential.mint(
                    principal=f"{dataset['tenant_id']}-pipeline",
                    task_kind="huggingface_fetch_job",
                    task_id=job_id, tenant_id=dataset["tenant_id"],
                ),
            },
            id=workflow_id,
            task_queue=config.HF_INGEST_TASK_QUEUE,
        )
    except Exception as exc:
        db.execute(
            """update huggingface_fetch_job
                 set status = 'failed', error = %s, ended_at = now()
               where id = %s""",
            (f"could not start the background job: {exc}", job_id),
        )
        raise HTTPException(
            502, {"reasons": [f"could not start the background job: {exc}"]}
        ) from exc

    return {"job_id": job_id, "status": "running"}


@router.post("/datasets/{dataset_id}/huggingface-fetch-jobs/{job_id}/cancel")
async def cancel_huggingface_fetch(dataset_id: str, job_id: str) -> dict:
    """Stop a running HuggingFace fetch.

    Asks Temporal to cancel the workflow; the workflow itself
    (`worker/hf_ingest_workflow.py`) is what marks the job row 'cancelled'
    once the cancellation actually lands there, not this endpoint, so this
    can return before that has happened. Files already recorded before the
    cancellation reaches the workflow are not undone: a later fetch of the
    same repo picks up from there.
    """
    job = db.one(
        "select id, status, workflow_id from huggingface_fetch_job where id = %s and dataset_id = %s",
        (job_id, dataset_id),
    )
    if not job:
        raise HTTPException(404, {"reasons": ["no such fetch job on this dataset"]})
    if job["status"] != "running":
        raise HTTPException(409, {"reasons": [f"this job already ended ({job['status']}); nothing to cancel"]})

    try:
        client = temporal_client.get()
    except temporal_client.TemporalUnavailable as exc:
        raise HTTPException(
            503, {"reasons": [f"the background job runner is unavailable: {exc}"]}
        ) from exc

    await client.get_workflow_handle(job["workflow_id"]).cancel()
    return {"job_id": job_id, "status": "cancelling"}


@router.post("/datasets/{dataset_id}/seal", status_code=201)
def seal(dataset_id: str) -> dict:
    """Close the upload and create the first sealed version.

    Goes through the same sealing path the pipeline uses, so nothing arriving
    this way sidesteps immutability or the prefix rules.
    """
    dataset = _dataset(dataset_id)

    running = db.one(
        "select id from huggingface_fetch_job where dataset_id = %s and status = 'running'",
        (dataset_id,),
    )
    if running:
        raise HTTPException(
            409,
            {"reasons": [
                "a HuggingFace fetch is still running for this dataset; sealing "
                "now could produce a version missing files still on the way"
            ]},
        )

    files = db.all_rows(
        """select locator, checksum, bytes from dataset_source
           where dataset_id = %s and kind in ('upload', 'huggingface')
             and checksum is not null and withdrawn_at is null""",
        (dataset_id,),
    )
    if not files:
        raise HTTPException(
            400, {"reasons": ["nothing has been uploaded or fetched yet"]}
        )

    schema = db.one(
        "select id from schema_contract where tenant_id = %s and name = %s",
        (dataset["tenant_id"], "uploaded_files"),
    )
    if not schema:
        # One contract for uploaded files, because the platform genuinely does
        # not know their shape. Claiming a richer contract than it can check
        # would be the kind of unverified label this project keeps removing.
        schema_id = str(uuid.uuid4())
        fields = [
            {"name": "filename", "type": "string", "sensitivity": "none",
             "added_by": "ingest"},
            {"name": "bytes", "type": "int", "sensitivity": "none",
             "added_by": "ingest"},
        ]
        db.execute(
            """insert into schema_contract
                 (id, tenant_id, name, fields, primary_key, content_hash)
               values (%s, %s, 'uploaded_files', %s, %s, %s)""",
            # The tenant is part of the hash because the contract row is scoped
            # to a tenant and `content_hash` is unique across the whole table.
            # Without it the first tenant to upload took the hash and every
            # other tenant's first upload failed on the unique constraint.
            (schema_id, dataset["tenant_id"], json.dumps(fields), ["filename"],
             versions.content_hash(
                 {"n": "uploaded_files", "t": dataset["tenant_id"]})),
        )
    else:
        schema_id = str(schema["id"])

    manifest = [
        {"key": f["locator"], "bytes": f["bytes"], "sha256": f["checksum"]}
        for f in files
    ]

    version = versions.seal(
        tenant_id=dataset["tenant_id"],
        dataset_id=dataset_id,
        schema_id=schema_id,
        visibility_class=dataset["declared_class"] or MOST_RESTRICTIVE,
        storage_backend=dataset["storage_backend"] or "seaweedfs",
        object_manifest=manifest,
        record_count=len(manifest),
    )
    return {**version, "files": len(manifest)}


@router.post("/datasets/{dataset_id}/confirm-classification")
def confirm(dataset_id: str, body: ConfirmClassification) -> dict:
    """The custodian agreeing with somebody's sensitivity claim.

    Refused for anybody who is not the custodian of the owning department, and
    refused for the person who made the claim, by a check constraint as well as
    by the branch below. Until this happens the data cannot be released above
    the class that was claimed for it.
    """
    dataset = _dataset(dataset_id)

    if dataset["declaration_basis"] != "asserted":
        raise HTTPException(
            409,
            {"reasons": [
                "there is no claim to confirm: this dataset's sensitivity was "
                "either the safe default or established from a verified source"
            ]},
        )

    if not dataset["custodian"]:
        raise HTTPException(
            409, {"reasons": ["this dataset has no owning department, so nobody can confirm"]}
        )

    if body.confirmed_by != dataset["custodian"]:
        raise HTTPException(
            403,
            {"reasons": [
                f"this data is owned by {dataset['department_name']}, "
                f"whose custodian is {dataset['custodian']}"
            ]},
        )

    try:
        db.execute(
            """update dataset
                 set classification_confirmed_by = %s,
                     classification_confirmed_at = now()
               where id = %s""",
            (body.confirmed_by, dataset_id),
        )
    except Exception as exc:
        if "dataset_confirmation_not_self" in str(exc):
            raise HTTPException(
                403,
                {"reasons": ["you cannot confirm a claim you made yourself"]},
            ) from exc
        raise

    return {"dataset_id": dataset_id, "confirmed_by": body.confirmed_by}


@router.get("/datasets/{dataset_id}/export")
def export(
    dataset_id: str,
    purpose: str = "",
    identity: dict = Depends(auth.current_session),
) -> dict:
    """May this data leave, and where from.

    Answered by provenance rather than by class. Two datasets at the same class
    can differ here: a public corpus was already published, and de-identified
    clinical data was not, because de-identification reduces re-identification
    risk without removing it and the original consent did not cover
    republishing.

    Returns object keys rather than bytes even when allowed. Streaming the data
    through the control plane would make this endpoint the data plane, which is
    the thing the design spent its effort avoiding.

    Whether a licensed dataset's export is allowed can depend on whether this
    platform has modified it: a no-derivatives licence permits the data
    exactly as fetched but not after a pipeline action has touched it. That
    fact is already on record (`dataset_version.produced_by_run`, set only
    when a version is a pipeline's output rather than an original ingest),
    so it needs no new tracking, only reading.

    `principal` used to be a caller-supplied query parameter: anyone could
    ask "may this leave" as anyone else, and if OPA said yes, the real
    `access_decision` row it wrote down attributed the export to whichever
    name the caller typed. Taken from the verified session instead, the
    same fix `read_models.py`'s own endpoints already apply everywhere else.
    """
    principal = identity["id"]
    dataset = _dataset(dataset_id)
    if dataset["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such dataset")

    version = db.one(
        """select vc.dataset_version_id, vc.current_class, vc.storage_prefix,
                  dv.produced_by_run
           from version_class vc
           join dataset_version dv on dv.id = vc.dataset_version_id
           where vc.dataset_id = %s
           order by vc.version desc limit 1""",
        (dataset_id,),
    )
    if not version:
        raise HTTPException(409, {"reasons": ["this dataset has no sealed version"]})

    modified = version["produced_by_run"] is not None

    allowed, reasons = opa.may_export({
        "dataset": {
            "provenance": dataset["provenance"],
            "visibility_class": version["current_class"],
            "license_tag": dataset["license_tag"],
            "license_export_unmodified": dataset["license_export_unmodified"],
            "license_export_modified": dataset["license_export_modified"],
        },
        "export": {"modified": modified},
    })

    db.execute(
        """insert into access_decision
             (principal, principal_kind, principal_roles, tenant_id,
              dataset_version_id, requested_class, purpose, allowed, reasons,
              phase)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'grant')""",
        (principal, identity["kind"], identity["roles"], dataset["tenant_id"],
         version["dataset_version_id"], version["current_class"],
         purpose or "export", allowed, reasons),
    )

    if not allowed:
        raise HTTPException(403, {"allowed": False, "reasons": reasons})

    return {
        "allowed": True,
        "reasons": reasons,
        "prefix": version["storage_prefix"],
        "license_tag": dataset["license_tag"],
        "modified": modified,
        "note": "Object keys, not contents. Fetch them from object storage.",
    }
