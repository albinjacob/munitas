"""Table jobs: a large table is written by a worker, and the seal is finished when the worker reports.

WHY

A table of many gigabytes cannot be written while a request waits. It takes minutes, it would hold a thread of the control
plane and its memory and bandwidth for all that time, and the caller's own timeout would end it first. So a seal that names a
records file too large for that (or whose caller asks for it) becomes a job. The request is answered at once, 202, with the
job, the version number and the folder the version will have. A table worker, a separate process that holds no database and
no organisation's standing key, reads the records and writes the table. The version is sealed when the worker reports.

WHAT KEEPS IT SAFE

* The version does not exist until the job has finished, so a version is sealed with its table inside it or not at all, and
  nothing is written under a sealed folder afterwards. The version number and folder are reserved by the job meanwhile
  (versions.next_version counts a waiting or running job), so a second seal of the dataset takes the next number.
* The worker is given one storage key, made for the job: read and write on the one folder the job reserved, in the one
  bucket of its organisation, for as long as the job lasts, and the right to list the names of objects in that bucket (storage
  authorises a listing on a bucket and on nothing narrower, and the table library lists before it creates a file). It is
  compiled into the storage permissions document while the job is active and left out of the next print once it ends. It is
  not the pipeline role's standing key, which can read every organisation's bucket.
* The worker proves it is the worker for this job with a signed credential that only the job's workflow carries, so a worker
  of one organisation, or one that only ever served another queue, holds nothing that names a job it was not given. The
  credential is accepted on this job's endpoints and nowhere else (auth.organisation_scope refuses it).
* The platform finishes the seal itself. It lists the table's folder and hashes every file with its own client, and opens the
  table to check the snapshot and the number of rows, so what is sealed is what is in storage and not what the worker said.
* A job that nobody finishes in time expires, and its number and folder are free again.

THE ORGANISATION'S OWN WORKER

An organisation can be given a worker of its own. Its jobs then go to a line of work only that worker takes, and they wait
for it: a job is put on its line when it is made and does not move to the shared pool if that worker is slow or not running,
because that would end the isolation the organisation was given. The housekeeping screen reports a job that has waited too
long.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import tablewriter

from . import auth, config, db, grants, iceberg, logs, models, opa, storage, task_credential, temporal_client, versions

log = logs.get_logger("table_jobs")
router = APIRouter()

KIND = "table_write_job"
ACTIVE = ("pending", "running")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def queue_for(tenant_id: str) -> str:
    row = db.one("select table_queue from tenant where id = %s", (tenant_id,))
    return (row or {}).get("table_queue") or config.TABLE_SHARED_QUEUE


def dedicated_queue_name(tenant_id: str) -> str:
    return f"{config.TABLE_SHARED_QUEUE}-{tenant_id}"


# ----------------------------------------------------------------- starting one


def maybe_start(body: models.DatasetVersionIn):
    """A 202 response if this seal becomes a job, or None if the table is written while the request waits."""
    if not body.records_key or body.table_mode == "inline":
        return None
    explicit = body.table_mode == "background"
    if not config.TABLE_JOBS:
        if explicit:
            raise HTTPException(422, {"reasons": ["table jobs are switched off on this platform, so a table cannot be written in the background"]})
        return None
    if body.storage_backend != "seaweedfs":
        if explicit:
            raise HTTPException(422, {"reasons": [f"tables are written in the background only for SeaweedFS-backed versions, not {body.storage_backend!r}"]})
        return None
    if not config.ICEBERG_PROJECTION:
        return None

    if not explicit:
        size = _records_size(body)
        if size is None or size <= config.TABLE_JOB_INLINE_BYTES:
            return None

    reserved = versions.next_version(body.tenant_id, body.dataset_id)
    prefix = reserved["storage_prefix"]
    if not body.records_key.startswith(prefix + "/"):
        raise HTTPException(422, {"reasons": [
            f"a table written in the background must be read from the version's own folder, {prefix}/, and the records file is "
            f"not in it. Write it there, where the platform said the next version goes"]})
    dataset = db.one("select name from dataset where id = %s and tenant_id = %s", (body.dataset_id, body.tenant_id))
    if not dataset:
        raise HTTPException(404, {"reasons": ["no such dataset in this organisation"]})
    try:
        iceberg.contract_of(body.schema_id)
    except iceberg.Skipped as exc:
        raise HTTPException(422, {"reasons": [str(exc)]}) from exc

    job_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
    queue = queue_for(body.tenant_id)
    expires = _now() + timedelta(seconds=config.TABLE_JOB_TTL_SECONDS)
    try:
        db.execute(
            """insert into table_job (id, tenant_id, dataset_id, version, storage_prefix, storage_backend, request, queue,
                                      status, expires_at, version_id)
               values (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)""",
            (job_id, body.tenant_id, body.dataset_id, reserved["version"], prefix, body.storage_backend,
             json.dumps(body.model_dump(mode="json")), queue, expires, version_id))
    except Exception as exc:  # noqa: BLE001 - the one that matters is the reserved number being taken
        if "table_job_reserved" in str(exc):
            raise HTTPException(409, {"reasons": ["another version of this dataset is being written at the same moment. "
                                                  "Ask for the next version number again"]}) from exc
        raise
    log.info("table job made", extra={"tenant_id": body.tenant_id, "job_id": job_id, "queue": queue, "version": reserved["version"]})
    return JSONResponse(status_code=202, headers={"Location": f"/table-jobs/{job_id}"}, content=_shown(_job_row(job_id)))


def _records_size(body: models.DatasetVersionIn) -> int | None:
    try:
        client = storage.admin_client_for(body.storage_backend, body.tenant_id)
        return int(client.head_object(Bucket=storage.bucket_for(body.storage_backend, body.tenant_id),
                                      Key=body.records_key)["ContentLength"])
    except Exception:  # noqa: BLE001 - the inline path says what is wrong with the file, in its usual words
        return None


# ----------------------------------------------------------------- what a person sees


def _job_row(job_id: str) -> dict | None:
    return db.one("select * from table_job where id = %s", (job_id,))


def _shown(job: dict) -> dict:
    """A job as its owner sees it. Never the request, which holds the whole seal, and never a credential."""
    created = job["created_at"]
    waiting = None
    if job["status"] == "pending":
        waiting = int((_now() - created).total_seconds())
    return json.loads(json.dumps({
        "job_id": str(job["id"]), "status": job["status"], "version": job["version"], "storage_prefix": job["storage_prefix"],
        "dedicated_worker": job["queue"] != config.TABLE_SHARED_QUEUE,
        "created_at": created, "started_at": job["started_at"], "finished_at": job["finished_at"],
        "waiting_seconds": waiting,
        "stalled": waiting is not None and waiting > config.TABLE_JOB_STALL_SECONDS,
        # The version exists only once the job is sealed. Until then it is the id the version will have.
        "version_id": str(job["version_id"]) if job["status"] == "sealed" and job["version_id"] else None,
        "outcome": job["outcome"], "reason": job["reason"],
        "status_url": f"/table-jobs/{job['id']}",
    }, default=str))


@router.get("/table-jobs/{job_id}")
def get_job(job_id: str, tenant_id: str | None = Depends(auth.organisation_scope)) -> dict:
    """Where one job stands. A job of another organisation is not found, not forbidden, as a version is."""
    job = _job_row(job_id)
    if not job or (tenant_id is not None and str(job["tenant_id"]) != tenant_id):
        raise HTTPException(404, {"reasons": ["no such table job"]})
    return _shown(job)


@router.get("/table-jobs")
def list_jobs(status: str | None = None, limit: int = 50, tenant_id: str | None = Depends(auth.organisation_scope)) -> dict:
    where, args = ["true"], []
    if tenant_id is not None:
        where.append("tenant_id = %s")
        args.append(tenant_id)
    if status:
        where.append("status = %s")
        args.append(status)
    rows = db.all_rows(f"select * from table_job where {' and '.join(where)} order by created_at desc limit %s", (*args, min(limit, 200)))
    return {"jobs": [_shown(r) for r in rows]}


# ----------------------------------------------------------------- the worker's side


def _claim(x_task_credential: str | None = Header(default=None)) -> task_credential.TaskClaim:
    if not x_task_credential:
        raise HTTPException(403, {"reasons": ["a table job's credential is required"]})
    try:
        claim = task_credential.verify(x_task_credential)
    except task_credential.InvalidTaskCredential as exc:
        raise HTTPException(403, {"reasons": [f"table job credential rejected: {exc}"]}) from exc
    if claim.task_kind != KIND:
        raise HTTPException(403, {"reasons": ["this credential is not for a table job"]})
    return claim


def _owned(job_id: str, claim: task_credential.TaskClaim) -> dict:
    """The job, only if the credential is for exactly this job, of the organisation the job belongs to."""
    job = _job_row(job_id)
    if claim.task_id != job_id or not job or str(job["tenant_id"]) != claim.tenant_id:
        raise HTTPException(403, {"reasons": ["this credential is not for this job"]})
    return job


def _active_or_409(job: dict) -> None:
    if job["status"] not in ACTIVE:
        raise HTTPException(409, {"reasons": [f"this job has already ended ({job['status']})"], "status": job["status"]})
    if job["expires_at"] < _now():
        raise HTTPException(409, {"reasons": ["this job has run out of time"], "status": "expired"})


@router.get("/table-jobs/{job_id}/work")
def work(job_id: str, claim: task_credential.TaskClaim = Depends(_claim)) -> dict:
    """What a worker needs to write one job's table. Asking for it marks the job running."""
    job = _owned(job_id, claim)
    _active_or_409(job)
    if job["attempts"] >= 1:
        # An earlier attempt did not finish. The worker cannot remove what it wrote (its key cannot list the folder), so the
        # folder is cleared here, with a key that can, before the work is handed out again.
        _clear_folder(job)
    db.execute("update table_job set status = 'running', started_at = coalesce(started_at, now()), attempts = attempts + 1 "
               "where id = %s and status in ('pending', 'running')", (job_id,))
    request = job["request"] if isinstance(job["request"], dict) else json.loads(job["request"])
    tenant_id, backend = str(job["tenant_id"]), job["storage_backend"]
    dataset = db.one("select name from dataset where id = %s", (job["dataset_id"],))
    contract = iceberg.contract_of(request["schema_id"])
    cfg = dataclasses.replace(iceberg.settings(), max_stream_bytes=config.TABLE_JOB_MAX_BYTES)
    return {
        "job_id": job_id, "tenant_id": tenant_id, "dataset_id": str(job["dataset_id"]), "dataset_name": dataset["name"],
        "version": job["version"], "storage_prefix": job["storage_prefix"], "bucket": storage.bucket_for(backend, tenant_id),
        "location": iceberg.table_location(backend, tenant_id, job["storage_prefix"]),
        "table_name": iceberg.table_name_for(job["version"]), "records_key": request["records_key"],
        "contract": contract,
        "summary_base": iceberg.summary_base(tenant_id=tenant_id, dataset_id=str(job["dataset_id"]), version_id=str(job["version_id"]),
                                             version=job["version"], produced_by_run=request.get("produced_by_run")),
        "settings": dataclasses.asdict(cfg), "endpoint": config.S3_ENDPOINT, "expires_at": job["expires_at"].isoformat(),
    }


@router.post("/table-jobs/{job_id}/credentials")
def credentials(job_id: str, claim: task_credential.TaskClaim = Depends(_claim)):
    """The job's own storage key. 202 while the key is not yet in the live permissions document."""
    job = _owned(job_id, claim)
    _active_or_409(job)
    if not grants.is_active(job["created_at"]):
        try:
            grants.reconcile(trigger="request")
        except Exception:  # noqa: BLE001 - the same answer the write credential gives
            status = grants.activation_status()
            due = status["retry_due_at"]
            wait = max(1, int((due - _now()).total_seconds())) if due else grants.MAX_RETRY_SECONDS
            return JSONResponse(status_code=202, headers={"Retry-After": str(wait)}, content={
                "active": False, "retry_after_seconds": wait,
                "reasons": ["the key is made and takes effect once storage permissions are updated, which the platform is retrying"]})
    return {"active": True, "access_key": grants.table_job_access_key(job_id), "secret_key": grants.table_job_secret(job_id),
            "endpoint": config.S3_ENDPOINT, "bucket": storage.bucket_for(job["storage_backend"], str(job["tenant_id"])),
            "prefix": job["storage_prefix"]}


class FinishIn(BaseModel):
    outcome: Literal["written", "skipped", "failed"]
    reason: str | None = None
    blocking: bool = True
    metadata_location: str | None = None
    snapshot_id: int | None = None
    record_count: int | None = None
    records_sha256: str | None = None


@router.post("/table-jobs/{job_id}/finish")
def finish(job_id: str, body: FinishIn, claim: task_credential.TaskClaim = Depends(_claim)) -> dict:
    """The worker reports. The platform checks what it says, and seals the version or refuses it."""
    job = _owned(job_id, claim)
    if job["status"] not in ACTIVE:
        return _shown(job)  # a report that was repeated after the first was handled
    result = _finish(job, body)
    _drop_key()
    return result


def _finish(job: dict, body: FinishIn) -> dict:
    job_id, tenant_id, prefix, backend = str(job["id"]), str(job["tenant_id"]), job["storage_prefix"], job["storage_backend"]
    request = job["request"] if isinstance(job["request"], dict) else json.loads(job["request"])

    # A report repeated after the version was written but before the job was marked: finish marking.
    if db.one("select 1 as x from dataset_version where id = %s", (job["version_id"],)):
        _end(job_id, "sealed", "written", "the version was already sealed")
        return _shown(_job_row(job_id))

    projection, why = None, None
    if body.outcome == "written":
        projection, why = _verified(job, body)
    else:
        message = (body.reason or "the table could not be written")[:400]
        why = (body.outcome, message, body.blocking)

    try:
        iceberg.enforce(why, request.get("table_required"))
    except iceberg.TableRequired as exc:
        _clear_folder(job)
        _end(job_id, "refused", exc.outcome, exc.reason)
        log.info("table job refused", extra={"tenant_id": tenant_id, "job_id": job_id, "outcome": exc.outcome})
        return _shown(_job_row(job_id))

    if projection is None:
        _clear_folder(job)  # a version sealed without a table has nothing of the table under its folder
    manifest = list(request.get("object_manifest") or []) + (projection.objects if projection else [])
    sealed = versions.insert_sealed(
        version_id=str(job["version_id"]), tenant_id=tenant_id, dataset_id=str(job["dataset_id"]), version=job["version"], prefix=prefix,
        storage_backend=backend, visibility_class=request["visibility_class"], manifest=manifest, schema_id=request["schema_id"],
        produced_by_run=request.get("produced_by_run"), record_count=request.get("record_count") or 0, projection=projection, why=why)
    _end(job_id, "sealed", "written" if projection else why[0], None if projection else why[1])
    log.info("table job sealed", extra={"tenant_id": tenant_id, "job_id": job_id, "dataset_version_id": sealed["id"],
                                                    "outcome": "with a table" if projection else "without a table"})
    return _shown(_job_row(job_id))


def _verified(job: dict, body: FinishIn):
    """The projection the platform itself can stand behind, or the reason it cannot. Lists and hashes the table's folder
    with its own client and opens the table, so what is sealed is what is in storage and not what a worker said."""
    from pyiceberg.table import StaticTable

    tenant_id, prefix, backend = str(job["tenant_id"]), job["storage_prefix"], job["storage_backend"]
    bucket = storage.bucket_for(backend, tenant_id)
    location = iceberg.table_location(backend, tenant_id, prefix)
    failed = lambda why: (None, ("failed", f"The table the worker reported did not check out: {why}.", True))  # noqa: E731
    if not (body.metadata_location and body.metadata_location.startswith(location + "/") and body.snapshot_id
            and body.record_count and body.records_sha256):
        return failed("its location, snapshot or row count was missing or outside the version's folder")
    try:
        objects = tablewriter.writer._manifest_entries(storage.admin_client_for(backend, tenant_id), bucket, f"{prefix}/{tablewriter.TABLE_DIR}/")
        table = StaticTable.from_metadata(body.metadata_location, properties=iceberg._file_io(tenant_id, backend))
        snapshot = table.current_snapshot()
        rows = int(snapshot.summary["total-records"]) if snapshot else -1
    except Exception as exc:  # noqa: BLE001 - reported by kind only
        return failed(f"it could not be opened ({type(exc).__name__})")
    if not objects or snapshot is None or snapshot.snapshot_id != body.snapshot_id or rows != body.record_count:
        return failed("its snapshot or its number of rows is not what was reported")
    # Every data file in the folder must be one the table uses. A file the table does not use is the trace of another attempt:
    # a worker that was thought dead and wrote after the next one had started. Sealing it would put a file in the version's
    # manifest that nothing reads, and a row count that does not describe what is stored.
    try:
        used = {task.file.file_path for task in table.scan().plan_files()}
    except Exception as exc:  # noqa: BLE001
        return failed(f"its files could not be listed ({type(exc).__name__})")
    stored = {f"s3://{bucket}/{o['key']}" for o in objects if "/data/" in o["key"][len(prefix):] and o["key"].endswith(".parquet")}
    if stored != used:
        return failed(f"the folder holds {len(stored - used)} data files the table does not use and lacks {len(used - stored)} it does")
    # The producer declared a hash for the records file when it sealed. The worker read that file, so the two must agree.
    request = job["request"] if isinstance(job["request"], dict) else json.loads(job["request"])
    declared = next((o.get("sha256") for o in request.get("object_manifest") or [] if o.get("key") == request.get("records_key")), None)
    if declared and declared != body.records_sha256:
        return failed("the records file is not the one the producer declared, because its hash differs from the manifest")
    dataset = db.one("select name from dataset where id = %s", (job["dataset_id"],))
    return tablewriter.Projection(
        namespace=dataset["name"], table_name=iceberg.table_name_for(job["version"]), location=location,
        metadata_location=body.metadata_location, snapshot_id=body.snapshot_id, record_count=body.record_count,
        records_sha256=body.records_sha256, objects=objects), None


def _clear_folder(job: dict) -> None:
    """Remove whatever is under the job's table folder. The worker removes what it wrote when it fails; this is for the
    case where it could not, or where the platform has decided against what it wrote."""
    try:
        backend, tenant_id = job["storage_backend"], str(job["tenant_id"])
        tablewriter.writer._remove_written(storage.admin_client_for(backend, tenant_id), storage.bucket_for(backend, tenant_id),
                                           f"{job['storage_prefix']}/{tablewriter.TABLE_DIR}/")
    except Exception as exc:  # noqa: BLE001 - a clean-up that failed is logged, and does not undo a decision
        log.error("a table job's folder could not be cleared", extra={"job_id": str(job["id"]), "error_type": type(exc).__name__})


def _end(job_id: str, status: str, outcome: str | None, reason: str | None) -> None:
    db.execute("update table_job set status = %s, outcome = %s, reason = %s, finished_at = now() "
               "where id = %s and status in ('pending', 'running', 'sealed')", (status, outcome, reason, job_id))


def _drop_key() -> None:
    """Print the permissions again so the job's key, which is compiled only while the job is active, stops working."""
    try:
        grants.reconcile(trigger="table-job")
    except Exception as exc:  # noqa: BLE001 - the key is also limited by the job's own expiry, and the next print removes it
        log.error("storage permissions could not be printed after a table job ended", extra={"error_type": type(exc).__name__})


# ----------------------------------------------------------------- handing jobs to workers


async def dispatch_once() -> int:
    """Start the workflow of every job that has none yet, and expire the ones nobody finished in time."""
    started = 0
    for job in db.all_rows("select id, tenant_id, queue, expires_at from table_job where status = 'pending' and dispatched_at is null "
                           "order by created_at limit 20"):
        job_id, tenant_id = str(job["id"]), str(job["tenant_id"])
        left = max(60, int((job["expires_at"] - _now()).total_seconds()))
        credential = task_credential.mint(principal=f"{tenant_id}-table-writer", task_kind=KIND, task_id=job_id,
                                          tenant_id=tenant_id, ttl_seconds=left)
        try:
            client = temporal_client.get()
            await client.start_workflow("TableWriteWorkflow", {"job_id": job_id, "tenant_id": tenant_id, "credential": credential},
                                        id=f"table-job-{job_id}", task_queue=job["queue"],
                                        # A job nobody picks up ends with its time, so it does not wait for ever.
                                        execution_timeout=timedelta(seconds=left + 600))
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ != "WorkflowAlreadyStartedError":
                log.error("a table job could not be handed to its workers yet", extra={"job_id": job_id, "error_type": type(exc).__name__})
                continue
        db.execute("update table_job set dispatched_at = now() where id = %s", (job_id,))
        started += 1
    if await asyncio.to_thread(expire_due):
        await asyncio.to_thread(_drop_key)
    return started


def expire_due() -> int:
    """A job that nobody finished in time has no version, and its number and folder are free again."""
    rows = db.all_rows(
        """update table_job set status = 'expired', outcome = 'expired', finished_at = now(),
                  reason = 'Nobody finished writing the table in time, so no version was made.'
            where status in ('pending', 'running') and expires_at < now() returning *""")
    for row in rows:
        _clear_folder(row)
        log.error("a table job ran out of time", extra={"tenant_id": str(row["tenant_id"]), "job_id": str(row["id"])})
    return len(rows)


async def run_forever() -> None:
    while True:
        try:
            await dispatch_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the loop must outlive a bad tick
            log.error("handing table jobs to workers failed", extra={"error_type": type(exc).__name__})
        await asyncio.sleep(config.TABLE_JOB_DISPATCH_SECONDS)


# ----------------------------------------------------------------- an organisation's own worker


class WorkerIn(BaseModel):
    dedicated: bool


@router.put("/tenants/{organisation_id}/table-worker")
def set_worker(organisation_id: str, body: WorkerIn, session: dict = Depends(auth.current_session)) -> dict:
    """Give an organisation a worker of its own, or take it back to the shared pool. Platform administrators only.

    Jobs already made stay on the line they were put on. Only the jobs made afterwards use the new setting."""
    allowed, reasons = opa.may_set_table_worker({"viewer": {"id": session["id"], "roles": session["roles"], "tenant_id": session["tenant_id"]},
                                                 "tenant_id": organisation_id})
    if not allowed:
        raise HTTPException(403, {"allowed": False, "reasons": reasons})
    if not db.one("select 1 as x from tenant where id = %s", (organisation_id,)):
        raise HTTPException(404, {"reasons": ["no such organisation"]})
    queue = dedicated_queue_name(organisation_id) if body.dedicated else None
    db.execute("update tenant set table_queue = %s where id = %s", (queue, organisation_id))
    log.info("an organisation's table worker setting changed", extra={"tenant_id": organisation_id, "status": "dedicated" if body.dedicated else "shared"})
    return {"tenant_id": organisation_id, "dedicated": body.dedicated, "queue": queue or config.TABLE_SHARED_QUEUE}


@router.get("/tenants/{organisation_id}/table-worker")
def get_worker(organisation_id: str, scope: str | None = Depends(auth.organisation_scope)) -> dict:
    if scope is not None and scope != organisation_id:
        raise HTTPException(404, {"reasons": ["no such organisation"]})
    queue = queue_for(organisation_id)
    return {"tenant_id": organisation_id, "dedicated": queue != config.TABLE_SHARED_QUEUE, "queue": queue}
