"""Munitas control plane.

Five things happen here and nothing else. Contracts and datasets get registered,
versions get sealed, credentials get minted through a policy decision, leases get
requested and approved by two different people, and classes get promoted.

The rule that shapes the whole file: no request for data access returns without
an `access_decision` row having been written first. Denials are logged as
carefully as grants, because a permission that is missing and a person probing
for data they should not have look identical until you can count.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg import errors as pg_errors

from crypto import DestroyedKeyError, EnvelopeCrypto

from . import (access_preview, activation, agent_upload, agents, auth, config,
              dag_pipelines, db, derivations, external_accounts, grants, housekeeping, iceberg,
              iceberg_catalog, ingest, logs, models, opa, people, pipeline, r2,
              read_models, seaweed, storage, task_credential, temporal_client,
              versions)

log = logs.get_logger("main")

crypto = EnvelopeCrypto()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logs.configure()
    db.pool.open()
    log.info("control plane starting")
    try:
        # A print at start-up. On a fresh install this is what creates the
        # document, compiled the same way as on every later print; on a
        # running one it activates whatever was allowed while the API was
        # down. Armed: a boot that would remove most of the live access is
        # refused rather than performed. A failure is recorded in
        # storage_permission_print like any other print, and the activator
        # below takes it from there, so it does not stop the control plane
        # doing everything else it does.
        grants.reconcile(trigger="start-up")
    except Exception as exc:
        log.error("storage permissions could not be printed at start-up; "
                  "the activator will keep trying",
                  extra={"reason": str(exc)})
    await temporal_client.connect()
    activator = asyncio.create_task(activation.run_forever())
    yield
    activator.cancel()
    try:
        await activator
    except asyncio.CancelledError:
        pass
    db.pool.close()


app = FastAPI(title="Munitas control plane", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def tag_request(request, call_next):
    """One id per request, on every line that request produces.

    Taken from X-Request-Id when the caller sent one, so a console action
    and the control plane's own lines can be lined up, and returned on the
    response so whoever saw the failure can quote the id rather than the
    time they think it happened at.
    """
    rid = request.headers.get("X-Request-Id") or logs.new_request_id()
    token = logs.request_id.set(rid)
    try:
        response = await call_next(request)
    finally:
        logs.request_id.reset(token)
    response.headers["X-Request-Id"] = rid
    return response

# The console is a separate origin, so it needs CORS. The allowed origins are
# listed rather than wildcarded, because `allow_origins=["*"]` on an API with no
# authentication in front of it means any page the operator visits can drive it.
#
# allow_credentials is now true so a Kratos session cookie (auth.py) reaches
# this API from the console's origin. Safe alongside the explicit origin
# list above: the two together are the only combination CORS itself permits
# for credentialed requests, since a wildcard origin is rejected outright
# once credentials are allowed. Every endpoint that existed before auth.py
# still answers exactly as it did with no cookie sent at all.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CONSOLE_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(read_models.router)
app.include_router(housekeeping.router)
app.include_router(people.router)
app.include_router(ingest.router)
app.include_router(external_accounts.router)
app.include_router(agents.router)
app.include_router(agent_upload.router)
app.include_router(dag_pipelines.router)
app.include_router(auth.router)
app.include_router(pipeline.router)
app.include_router(access_preview.router)
app.include_router(iceberg_catalog.router)
app.include_router(derivations.router)
# The catalog answers in the shape Iceberg clients read, not FastAPI's default.
app.add_exception_handler(iceberg_catalog.CatalogError, iceberg_catalog.handle_error)


@app.exception_handler(pg_errors.ReadOnlySqlTransaction)
def closed_tenant(request, exc: pg_errors.ReadOnlySqlTransaction):
    """A write to a retired tenant, refused by the database.

    The refusal is a trigger rather than a check in each endpoint, because there
    is no single place the control plane resolves `tenant_id`. Without this
    handler the caller would see a 500, which reads as a broken platform rather
    than as a closed tenant, and the distinction is the whole point of retiring
    one instead of deleting it.
    """
    return JSONResponse(
        status_code=409,
        content={"detail": str(exc).strip().splitlines()[0]},
    )


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@app.get("/health")
def health() -> dict:
    row = db.one("select 1 as ok")
    return {
        "database": bool(row),
        # Whether allowed storage access is taking effect: when permissions
        # last printed, whether printing is failing and since when, and what
        # is waiting on it.
        "storage_permissions": json.loads(json.dumps(grants.activation_status(), default=str)),
        "temporal_error": temporal_client.error(),
    }


# ------------------------------------------------------------- contracts --


@app.post("/schema-contracts", status_code=201)
def register_contract(body: models.SchemaContractIn) -> dict:
    """Register a schema contract.

    Contracts are content addressed and never edited. Registering the same
    fields twice returns the existing row rather than creating a second one, so
    a pipeline that re-registers on every run stays idempotent.
    """
    fields = [f.model_dump() for f in body.fields]
    # The work itself lives in versions.py, because the audio seal path in
    # ingest.py registers the same encounter_raw contract directly and two
    # implementations of the digest would put one contract in the table twice
    # under one name, with the pipeline and the console each holding a
    # different id for it. The tenant is part of that digest, and why is
    # explained there.
    return versions.register_contract(
        body.tenant_id, body.name, fields, body.primary_key
    )


# -------------------------------------------------------------- datasets --


@app.post("/datasets", status_code=201)
def create_dataset(body: models.DatasetIn) -> dict:
    existing = db.one(
        "select id from dataset where tenant_id = %s and name = %s",
        (body.tenant_id, body.name),
    )
    if existing:
        return {"id": str(existing["id"]), "created": False}
    row = db.execute(
        "insert into dataset (id, tenant_id, name) values (%s, %s, %s) returning id",
        (_uuid(), body.tenant_id, body.name),
    )
    return {"id": str(row["id"]), "created": True}


@app.post("/action-runs", status_code=201)
def start_run(body: models.ActionRunIn) -> dict:
    """Start an action run, keyed by an idempotency key.

    Re-submitting the same key returns the original run instead of starting a
    second one. This is what makes V6 checkable: kill the worker mid-action, let
    Temporal retry, and exactly one output version should exist afterwards.

    A manual run must name who triggered it, and a scheduled one must name its
    schedule instead; each is refused before the workflow even reaches this
    endpoint's insert, the same "an unattributable run should not start" logic
    `run_pipeline.py`'s CLI applies before starting the Temporal workflow at all.
    Retrying an existing idempotency key returns the original row unchanged
    regardless of what trigger fields this call carries: the row already exists,
    so nothing here is re-validated or re-attributed.
    """
    existing = db.one(
        "select id, status, output_version from action_run where idempotency_key = %s",
        (body.idempotency_key,),
    )
    if existing:
        return {
            "id": str(existing["id"]),
            "status": existing["status"],
            "output_version": str(existing["output_version"]) if existing["output_version"] else None,
            "created": False,
            # Minted fresh on every call, including a retry that finds this
            # run already exists: a task credential is not stored anywhere,
            # so there is nothing to hand back except a new one, and a
            # Temporal replay after a crash is exactly the case where the
            # process that needs it has lost whatever it held before.
            "task_credential": task_credential.mint(
                principal=body.operator, task_kind="action_run",
                task_id=str(existing["id"]), tenant_id=body.tenant_id,
            ),
        }

    if body.trigger_kind == "manual":
        if not body.triggered_by:
            raise HTTPException(400, "a manual run must name who triggered it")
        if body.schedule_id:
            raise HTTPException(400, "a manual run has no schedule_id")
        person = db.one(
            "select kind from directory where id = %s", (body.triggered_by,)
        )
        if not person or person["kind"] != "human":
            raise HTTPException(
                403, {"reasons": [f"{body.triggered_by!r} is not a registered human"]}
            )
    else:  # scheduled
        if body.triggered_by:
            raise HTTPException(400, "a scheduled run is not triggered by a person")
        if not body.schedule_id:
            raise HTTPException(400, "a scheduled run must name its schedule")

    row = db.execute(
        """insert into action_run
             (id, tenant_id, action_id, code_hash, image_digest, params,
              input_versions, status, operator, idempotency_key,
              trigger_kind, triggered_by, schedule_id, pipeline_run_id)
           values (%s, %s, %s, %s, %s, %s, %s, 'running', %s, %s, %s, %s, %s, %s)
           returning id""",
        (_uuid(), body.tenant_id, body.action_id, body.code_hash,
         body.image_digest, json.dumps(body.params), body.input_versions,
         body.operator, body.idempotency_key, body.trigger_kind,
         body.triggered_by, body.schedule_id, body.pipeline_run_id),
    )
    return {
        "id": str(row["id"]), "status": "running", "created": True,
        "task_credential": task_credential.mint(
            principal=body.operator, task_kind="action_run",
            task_id=str(row["id"]), tenant_id=body.tenant_id,
        ),
    }


@app.post("/pipeline-runs", status_code=201)
def start_pipeline_run(body: models.PipelineRunIn) -> dict:
    """Open one pipeline run, so its steps can be found together later.

    Every action_run the pipeline produces carries this id, and so does any
    workflow the promotion later starts. Without it the only thread between a
    run's steps is the lineage chain, because the idempotency key hashes the
    workflow id beyond recovery.

    Idempotent on workflow_id, for the same reason `/action-runs` is idempotent
    on its own key: Temporal replays a workflow after a crash, and a replay is
    the same run rather than a second one.
    """
    existing = db.one(
        "select id from pipeline_run where workflow_id = %s", (body.workflow_id,)
    )
    if existing:
        result = {"pipeline_run_id": str(existing["id"]), "created": False}
        if body.principal:
            # Minted fresh, the same reason /action-runs does on a retry:
            # nothing is stored server-side to hand back a second time.
            result["task_credential"] = task_credential.mint(
                principal=body.principal, task_kind="pipeline_run",
                task_id=str(existing["id"]), tenant_id=body.tenant_id,
            )
        return result

    run_id = _uuid()
    db.execute(
        """insert into pipeline_run
             (id, tenant_id, dataset, workflow_id, trigger_kind, triggered_by,
              schedule_id, input_versions)
           values (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (run_id, body.tenant_id, body.dataset, body.workflow_id,
         body.trigger_kind, body.triggered_by, body.schedule_id,
         body.input_versions),
    )
    result = {"pipeline_run_id": run_id, "created": True}
    if body.principal:
        result["task_credential"] = task_credential.mint(
            principal=body.principal, task_kind="pipeline_run",
            task_id=run_id, tenant_id=body.tenant_id,
        )
    return result


@app.post("/pipeline-runs/{run_id}/end")
def end_pipeline_run(run_id: str, body: models.EndPipelineRun) -> dict:
    """Mark a pipeline run finished, and record how it ended.

    Called by the workflow itself as it finishes. The outcome is kept here,
    in the database, rather than left for the job runner to remember: it
    keeps a finished workflow's history only for a limited time.

    Idempotent, keeping the first ending: Temporal can retry the activity
    that calls this, and a retry is the same ending rather than a later one.
    Every SET reads the row as it was, so a run already ended keeps its
    original time, outcome and error.
    """
    row = db.execute(
        """update pipeline_run
              set status = case when ended_at is null then %s else status end,
                  error = case when ended_at is null then %s else error end,
                  ended_at = coalesce(ended_at, now())
            where id = %s
        returning ended_at, status""",
        (body.status, body.error, run_id),
    )
    if not row:
        raise HTTPException(404, f"no pipeline run {run_id}")
    return {"pipeline_run_id": run_id, "ended_at": row["ended_at"],
            "status": row["status"]}


@app.get("/datasets/{dataset_id}/next-version")
def next_version(dataset_id: str, tenant_id: str) -> dict:
    """Tell a producer where the next version's objects must be written.

    A pipeline has to write its objects before it can seal a version, but the
    storage prefix belongs to the version, so it does not exist yet. Without
    this the producer invents its own prefix, the sealed version points
    somewhere else, and a prefix-scoped credential grants access to nothing.
    That failure is silent: the version looks fine and the grant looks fine.

    This is a reservation in name only. It computes the same prefix
    `create_version` will compute, and two producers racing on one dataset would
    both be told the same answer and one would lose. Single producer per dataset
    is the assumption, and a real deployment needs an allocation that takes a
    lock instead.

    The bucket is answered here too, and for the same reason the prefix is. A
    producer that picks its own bucket from a constant writes into the shared
    one while the sealed version's credential points at the tenant's own, and
    that failure is silent in exactly the way this endpoint exists to prevent:
    the objects are somewhere real, the version looks fine, the grant looks
    fine, and there is nothing to read. Every tenant grandfathered onto the
    shared bucket hides it, which is why it went unnoticed until a tenant with
    its own provisioned bucket was asked the question.
    """
    row = db.one(
        "select coalesce(max(version), 0) as v from dataset_version where dataset_id = %s",
        (dataset_id,),
    )
    version = row["v"] + 1

    dataset = db.one(
        "select storage_backend from dataset where id = %s", (dataset_id,)
    )
    backend = (dataset or {}).get("storage_backend")
    if not backend:
        tenant = db.one(
            "select default_storage_backend from tenant where id = %s", (tenant_id,)
        )
        backend = (tenant or {}).get("default_storage_backend") or "seaweedfs"

    # A bucket that cannot be resolved is reported, not raised. This endpoint's
    # first job is the prefix, and it answered that long before it answered the
    # bucket; an install with no R2 configured would otherwise get a 500 here
    # for an R2-backed dataset where it used to get a usable prefix. The
    # producer refuses on the null bucket, which is the honest outcome, and it
    # refuses knowing why.
    bucket, bucket_error = None, None
    try:
        bucket = storage.bucket_for(backend, tenant_id)
    except Exception as exc:  # noqa: BLE001 - reported, not raised, on purpose
        bucket_error = str(exc)

    return {
        "version": version,
        "storage_prefix": f"{tenant_id}/{dataset_id}/v{version}",
        "storage_backend": backend,
        "bucket": bucket,
        "bucket_error": bucket_error,
        "reserved": False,
    }


@app.post("/dataset-versions", status_code=201)
def create_version(body: models.DatasetVersionIn) -> dict:
    """Seal a new dataset version.

    Sealed on creation, which is why there is no update endpoint anywhere in
    this file. The storage prefix encodes tenant, dataset and version and never
    the class, so a later promotion changes a grant rather than moving bytes.
    """
    latest = db.one(
        "select coalesce(max(version), 0) as v from dataset_version where dataset_id = %s",
        (body.dataset_id,),
    )
    version = latest["v"] + 1
    prefix = f"{body.tenant_id}/{body.dataset_id}/v{version}"

    # A tabular version is also written as an Iceberg table, under its own
    # prefix, BEFORE the row exists: nothing may be written under a sealed
    # prefix afterwards, and the table's files belong in the manifest the
    # content hash is taken over. A version that cannot be written as a table
    # is sealed all the same (iceberg.try_project says why in the log).
    version_id = _uuid()
    manifest = list(body.object_manifest)
    projection = None
    if body.records_key:
        dataset = db.one("select name from dataset where id = %s", (body.dataset_id,))
        if dataset:
            projection = iceberg.try_project(
                tenant_id=body.tenant_id, backend=body.storage_backend,
                dataset_id=body.dataset_id, dataset_name=dataset["name"],
                version_id=version_id, version=version, prefix=prefix,
                schema_id=body.schema_id, records_key=body.records_key,
                produced_by_run=body.produced_by_run)
            if projection:
                manifest += projection.objects
    content_hash = _hash(
        {"manifest": manifest, "count": body.record_count, "prefix": prefix}
    )

    row = db.execute(
        """insert into dataset_version
             (id, tenant_id, dataset_id, version, visibility_class,
              storage_prefix, storage_backend, object_manifest, schema_id,
              produced_by_run, record_count, content_hash, iceberg_snapshot_id, sealed)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
           returning id""",
        (version_id, body.tenant_id, body.dataset_id, version,
         body.visibility_class, prefix, body.storage_backend,
         json.dumps(manifest), body.schema_id, body.produced_by_run,
         body.record_count, content_hash,
         projection.snapshot_id if projection else None),
    )
    version_id = str(row["id"])
    if projection:
        iceberg.record(version_id, body.tenant_id, body.dataset_id, projection)

    if body.produced_by_run:
        db.execute(
            "update action_run set output_version = %s, status = 'succeeded', ended_at = now() where id = %s",
            (version_id, body.produced_by_run),
        )

    return {
        "id": version_id,
        "version": version,
        "storage_prefix": prefix,
        "content_hash": content_hash,
        "sealed": True,
    }


@app.get("/dataset-versions/{version_id}")
def get_version(version_id: str, tenant_id: str | None = Depends(auth.organisation_scope)) -> dict:
    """One version, with the fields a detail screen needs.

    `version_class` deliberately carries only the class question. The name,
    content hash and record count are joined in here rather than added to the
    view, because widening the view would make every caller pay for columns most
    of them ignore, and the view's single purpose is what makes it trustworthy.

    The organisation comes from the caller's session (`auth.organisation_scope`), never from
    the URL, and a version belonging to another is **not found** rather than forbidden. That
    distinction is the point: 403 confirms the version exists, which is half of what somebody
    probing ids was trying to learn. The platform's own workers send the worker token instead
    and name the organisation they act for. This endpoint also returns `object_manifest`, the
    storage keys of the version's files, so it must never answer without one or the other.
    """
    row = db.one(
        """select vc.*, d.name as dataset_name,
                  dv.content_hash, dv.record_count, dv.sealed, dv.created_at,
                  dv.object_manifest
           from version_class vc
           join dataset d on d.id = vc.dataset_id
           join dataset_version dv on dv.id = vc.dataset_version_id
           where vc.dataset_version_id = %s
             and (%s::text is null or vc.tenant_id = %s)""",
        (version_id, tenant_id, tenant_id),
    )
    if not row:
        raise HTTPException(404, "no such dataset version")
    return json.loads(json.dumps(row, default=str))


@app.get("/lineage/{version_id}")
def get_lineage(version_id: str, tenant_id: str | None = Depends(auth.organisation_scope)) -> dict:
    row = db.one(
        """select l.* from lineage l
           join dataset_version dv on dv.id = l.dataset_version_id
           where l.dataset_version_id = %s
             and (%s::text is null or dv.tenant_id = %s)""",
        (version_id, tenant_id, tenant_id),
    )
    if not row:
        raise HTTPException(404, "no such dataset version")
    return json.loads(json.dumps(row, default=str))


# ---------------------------------------------------------------- leases --


@app.post("/leases/requests", status_code=201)
def request_lease(body: models.LeaseRequestIn,
                  identity: dict = Depends(auth.current_session)) -> dict:
    """Ask for access below your role's floor. This grants nothing.

    The organisation is read from the directory rather than taken from the
    request, for the same reason `/credentials` reads it: the tenant on this row
    decides whose queue the request appears in, so a caller who could set it
    could put work in front of a custodian in another organisation, naming a
    version that custodian cannot see.

    Asking for a version outside your own organisation is refused here rather
    than at approval time. The policy would refuse it in the end, but a request
    that can never be granted should not sit in somebody's queue until they read
    far enough to work that out.

    `principal` is who will read. A human may only name themselves (checked
    against the session); a human may name a workload, in which case
    `requested_by` is always the session's own id, never taken from the
    request body: a workload can never be made to read as somebody the
    caller merely claimed asked.
    """
    version = db.one(
        "select tenant_id from dataset_version where id = %s",
        (body.dataset_version_id,),
    )
    if not version:
        raise HTTPException(404, "no such dataset version")

    principal_row = db.one(
        "select tenant_id, kind from directory where id = %s", (body.principal,)
    )
    acting_tenant = principal_row["tenant_id"] if principal_row else identity["tenant_id"]

    if version["tenant_id"] != acting_tenant:
        raise HTTPException(404, "no such dataset version")

    if body.standing and (not principal_row or principal_row["kind"] != "workload"):
        raise HTTPException(
            400,
            "a standing lease may only be requested for a workload principal",
        )

    requested_by: str | None = None
    if principal_row and principal_row["kind"] == "workload":
        # On behalf of. The session is who asked; it is never read from the
        # body, so a caller cannot name somebody else as having asked.
        requested_by = identity["id"]
    elif body.principal != identity["id"]:
        # A human naming another human, or an unregistered id, as the reader.
        # A human may only request for themselves; asking on another human's
        # behalf is not a shape this platform supports, and refusing it here
        # is the same non-disclosure posture as the cross-tenant check above.
        raise HTTPException(404, "no such dataset version")

    row = db.execute(
        """insert into lease_request
             (id, tenant_id, principal, requested_by, dataset_version_id,
              purpose, justification, standing, requested_ttl_hours)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           returning id""",
        (_uuid(), acting_tenant, body.principal, requested_by,
         body.dataset_version_id, body.purpose, body.justification,
         body.standing, body.ttl_hours),
    )
    return {"id": str(row["id"]), "state": "pending"}


@app.post("/leases/requests/{request_id}/approve", status_code=201)
async def approve_lease(
    request_id: str,
    body: models.LeaseApprovalIn = models.LeaseApprovalIn(),
    identity: dict = Depends(auth.current_session),
) -> dict:
    """Approve a request, creating the lease.

    Self-approval is rejected by a check constraint on `access_lease`, not by
    the branch below. The branch produces a readable error; the constraint is
    what makes the guarantee hold even if this code is wrong.

    `body.pattern` is the custodian's own call, made here rather than at
    request time: the requester says what they want and why, but whether this
    particular principal has earned a looser leash on this data is the
    custodian's judgment to make, not the requester's to ask for.
    """
    req = db.one("select * from lease_request where id = %s", (request_id,))
    if not req:
        raise HTTPException(404, "no such lease request")
    if req["state"] != "pending":
        raise HTTPException(409, f"request already {req['state']}")

    # Who is entitled to approve this, according to the asset's owner.
    custodian = db.one(
        "select custodian, department_name, dataset_name from version_custodian "
        "where dataset_version_id = %s",
        (req["dataset_version_id"],),
    ) or {}

    # The same floor the database trigger enforces, checked here first so the
    # custodian gets a readable refusal instead of a raw constraint error.
    if body.pattern == "simple":
        version = db.one(
            "select current_class from version_class where dataset_version_id = %s",
            (req["dataset_version_id"],),
        )
        if version and version["current_class"] == "RAW":
            raise HTTPException(
                400,
                "a simple (any-purpose) lease may not be granted against a "
                "RAW dataset version; approve this as strict instead",
            )

    permitted, reasons = opa.may_approve({
        "approver": {"id": identity["id"], "roles": identity["roles"]},
        "request": {
            "principal": req["principal"],
            "requested_by": req["requested_by"],
            "dataset_version": str(req["dataset_version_id"]),
        },
        "asset": {
            "dataset_version": str(req["dataset_version_id"]),
            "custodian": custodian.get("custodian"),
        },
    })
    if not permitted:
        # Refused before the write is attempted, so the caller gets an
        # explanation rather than a constraint violation. The database checks
        # remain the guarantee; this is the part that can say why.
        raise HTTPException(403, {"approved": False, "reasons": reasons})

    expires = None if req["standing"] else _now() + timedelta(hours=req["requested_ttl_hours"])
    lease_id = _uuid()
    try:
        db.execute(
            """insert into access_lease
                 (id, tenant_id, principal, requested_by, dataset_version_id,
                  purpose, approved_by, expires_at, pattern)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               returning id""",
            (lease_id, req["tenant_id"], req["principal"], req["requested_by"],
             req["dataset_version_id"], req["purpose"], identity["id"], expires,
             body.pattern),
        )
    except pg_errors.CheckViolation as exc:
        if "lease_no_self_approval" in str(exc):
            # Matches both `lease_no_self_approval` and
            # `lease_no_self_approval_requester`: the same failure, one
            # identity removed.
            raise HTTPException(
                403,
                "a lease cannot be approved by the principal or the requester "
                "who asked for it",
            ) from exc
        if "access_lease_no_simple_for_raw" in str(exc):
            # The Python check above already covers this; this branch is the
            # backstop for anything that reaches the database without going
            # through it (a race, or code added later that forgets to check).
            raise HTTPException(
                400,
                "a simple (any-purpose) lease may not be granted against a "
                "RAW dataset version; approve this as strict instead",
            ) from exc
        raise

    db.execute(
        """update lease_request
             set state = 'approved', decided_by = %s, decided_at = now(), lease_id = %s
           where id = %s""",
        (identity["id"], lease_id, request_id),
    )

    # An agent run may have been waiting on exactly this decision. Granting the
    # access is what lets it begin, so it begins here rather than making the
    # person who started it come back and press start a second time for a
    # question that has now been answered.
    started = await agents.runs_waiting_on_lease(request_id, granted=True)

    return {
        "lease_id": lease_id,
        "expires_at": expires.isoformat() if expires else None,
        "standing": expires is None,
        "agent_runs_started": started,
    }


@app.post("/leases/requests/{request_id}/reject")
async def reject_lease(request_id: str, body: models.LeaseRejection,
                       identity: dict = Depends(auth.current_session)) -> dict:
    """Refuse a request, with a reason.

    Exists because a queue offering only Approve is a queue that gets approved.
    Refusing has to be as available as agreeing, and as recorded: the reason is
    required, because "no" without a reason tells the person who asked nothing
    about whether to ask again or ask differently.

    The same authority check as approval. Somebody who cannot grant access
    should not be able to close the request either, or they can quietly bury
    something the real custodian would have granted.
    """
    req = db.one("select * from lease_request where id = %s", (request_id,))
    if not req:
        raise HTTPException(404, "no such lease request")
    if req["state"] != "pending":
        raise HTTPException(409, f"request already {req['state']}")

    custodian = db.one(
        "select custodian, department_name from version_custodian "
        "where dataset_version_id = %s",
        (req["dataset_version_id"],),
    ) or {}

    permitted, reasons = opa.may_approve({
        "approver": {"id": identity["id"], "roles": identity["roles"]},
        "request": {
            "principal": req["principal"],
            "requested_by": req["requested_by"],
            "dataset_version": str(req["dataset_version_id"]),
        },
        "asset": {
            "dataset_version": str(req["dataset_version_id"]),
            "custodian": custodian.get("custodian"),
        },
    })
    if not permitted:
        raise HTTPException(403, {"decided": False, "reasons": reasons})

    db.execute(
        """update lease_request
             set state = 'rejected', decided_by = %s, decided_at = now()
           where id = %s""",
        (identity["id"], request_id),
    )

    # A run parked on this request is now never going to get its data. Closing
    # it here, with the refusal as its reason, keeps it from sitting in the list
    # looking pending forever while the thing it waits for has already been
    # decided.
    closed = await agents.runs_waiting_on_lease(
        request_id, granted=False,
        reason=f"access was refused by {identity['id']}: {body.reason}",
    )

    return {"request_id": request_id, "state": "rejected", "reason": body.reason,
            "agent_runs_closed": closed}


@app.post("/leases/{lease_id}/revoke")
def revoke_lease(lease_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """Withdraw a lease before it would otherwise expire.

    Same authority as approving one: the asset's custodian, not the principal
    or requester it was granted to, and not anybody outside that relationship.
    Unauthenticated and unauthorized revocation was a real, open gap before
    this: nothing checked who was asking, or whether they had any standing
    over the asset at all.
    """
    lease = db.one("select * from access_lease where id = %s", (lease_id,))
    if not lease:
        raise HTTPException(404, "no such lease")

    custodian = db.one(
        "select custodian from version_custodian where dataset_version_id = %s",
        (lease["dataset_version_id"],),
    ) or {}

    permitted, reasons = opa.may_approve({
        "approver": {"id": identity["id"], "roles": identity["roles"]},
        "request": {
            "principal": lease["principal"],
            "requested_by": lease["requested_by"],
            "dataset_version": str(lease["dataset_version_id"]),
        },
        "asset": {
            "dataset_version": str(lease["dataset_version_id"]),
            "custodian": custodian.get("custodian"),
        },
    })
    if not permitted:
        raise HTTPException(403, {"revoked": False, "reasons": reasons})

    row = db.execute(
        "update access_lease set revoked = true, revoked_by = %s, revoked_at = now() "
        "where id = %s returning id",
        (identity["id"], lease_id),
    )
    if not row:
        raise HTTPException(404, "no such lease")
    # What the person made from this version while the lease lasted was made
    # on its authority, and the lease they were given to read it goes with it.
    db.execute(
        """update access_lease set revoked = true, revoked_by = %s, revoked_at = now()
            where revoked = false and derivation_id in
                  (select id from derivation where submitted_by = %s and inputs @> %s::jsonb)""",
        (identity["id"], lease["principal"],
         json.dumps([{"version_id": str(lease["dataset_version_id"])}])))
    # Print now, as an approval does, so the lease's key stops working now and not
    # at whatever print comes next. If the print fails the revocation still stands
    # (it is in the register and the policy refuses the lease at once); the
    # activator counts the ended lease and retries until the key is gone.
    try:
        grants.reconcile(trigger="revocation")
    except Exception as exc:
        log.error("storage permissions could not be printed after a revocation; "
                  "the activator will keep trying",
                  extra={"lease_id": lease_id, "error_type": type(exc).__name__})
    return {"id": lease_id, "revoked": True}


def _active_leases(principal: str, version_id: str) -> list[dict]:
    """Leases as OPA expects them. See `db.active_leases` for why it lives there."""
    return db.active_leases(principal, version_id)


# ----------------------------------------------------------- credentials --


def _record_decision(
    body: models.CredentialRequest,
    version: dict,
    phase: str,
    allowed: bool,
    reasons: list[str],
) -> None:
    """Append one row to the audit log.

    Called twice per credential request: once for what policy decided, once for
    what the grant actually did. The two can disagree, and before they were
    recorded separately the log said "allowed" for requests where nothing was
    granted at all.
    """
    db.execute(
        """insert into access_decision
             (principal, principal_kind, principal_roles, tenant_id,
              dataset_version_id, requested_class, purpose, allowed, reasons,
              phase, agent_run_id)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (body.principal, body.principal_kind, body.roles, body.tenant_id,
         body.dataset_version_id, version["current_class"], body.purpose,
         allowed, reasons, phase, body.agent_run_id),
    )


def _granting_lease(principal: str, version_id: str, purpose: str) -> str | None:
    """The active lease that justifies this read, if any.

    A grant that rests on the role's own floor has no lease and keeps the role's
    shared key. A grant that rests on a lease gets that lease's own key, so
    revoking the lease can cut this holder without cutting the role. Matched on
    principal, version and purpose, the same three the credential request itself
    is decided on.
    """
    row = db.one(
        """select id from access_lease
             where principal = %s and dataset_version_id = %s and purpose = %s
               and revoked = false and (expires_at is null or expires_at > now())
             order by created_at desc limit 1""",
        (principal, version_id, purpose),
    )
    return str(row["id"]) if row else None


def _resolve_pipeline_task(
    task_credential_token: str | None, principal: str, acting_tenant: str
) -> tuple["task_credential.TaskClaim", dict, set[str]]:
    """Verify a pipeline_action workload's task_credential.py token and
    resolve the real action_run, pipeline_run, or huggingface_fetch_job row
    it names.

    Shared by /credentials' pipeline_action block and /write-credentials,
    rather than duplicating this ~30-line security-critical check across two
    places where a future fix to one copy and not the other is exactly the
    kind of drift this codebase's own docstrings warn about elsewhere.

    Returns (claim, run, input_versions): the verified claim, the resolved
    row, and the set of dataset versions that row's task was actually
    launched against. `input_versions` is what /credentials' own read-scope
    check needs; /write-credentials has no equivalent check and simply
    ignores it.

    Raises HTTPException(403, {"allowed": False, "reasons": [...]}) on any
    failure: missing or invalid token, a token naming the wrong principal or
    tenant, or a task row that does not exist or belongs to someone else.
    Both callers are responsible for their own audit recording around this,
    since /credentials records to access_decision (keyed to a dataset
    version) and /write-credentials has no such row to key one to.
    """
    claim = None
    if task_credential_token:
        try:
            claim = task_credential.verify(task_credential_token)
        except task_credential.InvalidTaskCredential as exc:
            raise HTTPException(
                403, {"allowed": False, "reasons": [f"task credential rejected: {exc}"]}
            )

    if (not claim
            or claim.task_kind not in ("action_run", "pipeline_run", "huggingface_fetch_job")
            or claim.principal != principal or claim.tenant_id != acting_tenant):
        raise HTTPException(403, {"allowed": False, "reasons": [
            "a registered pipeline action must present the task "
            "credential minted for the real task it is acting as"
        ]})

    if claim.task_kind == "action_run":
        run = db.one(
            "select tenant_id, input_versions from action_run where id = %s",
            (claim.task_id,),
        )
        input_versions = {str(v) for v in (run["input_versions"] or [])} if run else set()
    elif claim.task_kind == "pipeline_run":
        # pipeline_run has no input_versions array of its own for the
        # console-facing case: a pipeline run adopts exactly one starting
        # version, recorded in source_version_id by pipeline.py's
        # start_deidentification before the workflow even starts.
        # input_versions is also read and unioned in for the generic DAG
        # engine's own runs, which can declare more than one.
        run = db.one(
            "select tenant_id, source_version_id, input_versions "
            "from pipeline_run where id = %s",
            (claim.task_id,),
        )
        input_versions = set()
        if run:
            if run["source_version_id"]:
                input_versions.add(str(run["source_version_id"]))
            input_versions |= {str(v) for v in (run["input_versions"] or [])}
    else:
        # huggingface_fetch_job carries no dataset version at all -- it
        # writes new files in, never reads one -- so input_versions stays
        # empty. Its own tenant is not a column on the table itself (only
        # dataset_id is), so it is resolved through the dataset it names,
        # the same authority-from-what-the-platform-can-verify rule applied
        # everywhere else in this function.
        run = db.one(
            "select d.tenant_id from huggingface_fetch_job j "
            "join dataset d on d.id = j.dataset_id where j.id = %s",
            (claim.task_id,),
        )
        input_versions = set()

    if not run or run["tenant_id"] != acting_tenant:
        raise HTTPException(403, {"allowed": False, "reasons": [
            "a registered pipeline action must name the real task "
            "it is acting as to request a credential"
        ]})

    return claim, run, input_versions


@app.post("/credentials")
def request_credential(body: models.CredentialRequest):
    """Mint a prefix-scoped credential, or refuse and say why.

    The order of operations matters and is not negotiable: decide, record the
    decision, then act on it. Recording after acting would lose the audit trail
    for exactly the requests that fail partway.

    Three outcomes: 200 with the key when access is allowed and in effect,
    403 when it is refused, and 202 when it is allowed but the storage
    permissions could not be updated yet. A 202 is never a refusal: the
    activator finishes the job and resumes a run that parked on it.
    """
    version = db.one(
        "select * from version_class where dataset_version_id = %s",
        (body.dataset_version_id,),
    )
    if not version:
        raise HTTPException(404, "no such dataset version")

    # Which organisation the requester belongs to is read from the directory,
    # not taken from the request.
    #
    # The policy refuses a principal whose tenant differs from the dataset's,
    # and it was comparing the tenant the caller supplied, so anybody could
    # reach another organisation's data by naming that organisation. The
    # registered answer is the authoritative one, exactly as an approver's
    # authority comes from the directory rather than from the name in the body.
    #
    # An unregistered *human* principal has no registered tenant, so the
    # claim stands and the request is decided on what it says. That half of
    # the gap closes when authentication replaces the fake identity, not
    # before; every such request is still recorded with the name it was
    # made under.
    #
    # A workload gets no such benefit of the doubt: every real workload
    # principal is provisioned in the directory before it ever runs
    # (registering an agent creates its runtime identity at the same time;
    # every other workload principal is seeded or registered ahead of any
    # task it performs), so a workload name with no directory row at all has
    # nothing genuine behind it and is refused outright, before any of the
    # role or run checks below even ask what it claims to be.
    registered = db.one(
        "select tenant_id, roles from directory where id = %s", (body.principal,)
    )
    if body.principal_kind == "workload" and not registered:
        reasons = ["no such workload is registered"]
        _record_decision(body, version, "policy", False, reasons)
        raise HTTPException(403, {"allowed": False, "reasons": reasons})

    acting_tenant = registered["tenant_id"] if registered else body.tenant_id

    # An agent's own run-scope is enforced here, not left to whatever the
    # calling code claims about itself. Keyed on the *registry's* roles,
    # not body.roles (which stays caller-supplied for everything else this
    # check does not touch), so a workload cannot dodge this by simply
    # omitting "agent_runtime" from the roles it claims while still being
    # registered as one.
    #
    # A real dataset-version boundary needed a fact the server owns and
    # pins once, at the moment a run starts (agent_run.dataset_version_id),
    # checked against a real, resolved run row, never against a
    # dataset_version_id or agent_run_id the request merely asserts. This
    # is what actually stops a registered agent's own code from reading a
    # dataset it was never launched against, which a client-side check
    # inside agent/tools.py cannot: nothing requires a third party's agent
    # code to call that module at all.
    #
    # Every other workload role (`pipeline_action`, `training_job`,
    # `model_eval`, `annotation_tool`) has no equivalent check: nothing pins
    # a per-task identity for them the way `agent_run` does for an agent, so
    # a *registered* one of these is still exactly as easy to impersonate as
    # before this change. That is real, and separate, larger work -- the
    # same shape of fix as this one, generalized to a kind of task this
    # platform does not yet spawn with anything to check a claim against.
    if registered and "agent_runtime" in (registered["roles"] or []):
        # Naming a real run is not proof of being its code: anything that
        # could read or guess a run id could otherwise ask for that run's
        # credentials. `run_secret` is now a signed task_credential.py token,
        # minted once at run creation and handed only to that run's own
        # process, verified here by signature and expiry alone -- never
        # compared against a value stored in the database, the way the
        # earlier plain shared secret was. See task_credential.py for why:
        # this is the same "identity proof through a channel only the real
        # thing could receive" principle OCI resource principals use.
        claim = None
        if body.run_secret:
            try:
                claim = task_credential.verify(body.run_secret)
            except task_credential.InvalidTaskCredential as exc:
                reasons = [f"task credential rejected: {exc}"]
                _record_decision(body, version, "policy", False, reasons)
                raise HTTPException(403, {"allowed": False, "reasons": reasons})

        if (not claim or claim.task_kind != "agent_run"
                or claim.principal != body.principal
                or claim.tenant_id != acting_tenant):
            reasons = [
                "a registered agent must present the task credential minted "
                "for the real run it is acting as"
            ]
            _record_decision(body, version, "policy", False, reasons)
            raise HTTPException(403, {"allowed": False, "reasons": reasons})

        run = db.one(
            """select ar.dataset_version_id, ar.tenant_id, a.principal_id
                 from agent_run ar
                 join agent a on a.id = ar.agent_id
                where ar.id = %s""",
            (claim.task_id,),
        )

        if not run or run["principal_id"] != body.principal or run["tenant_id"] != acting_tenant:
            reasons = [
                "a registered agent must name the real run it is acting as "
                "to request a credential"
            ]
            _record_decision(body, version, "policy", False, reasons)
            raise HTTPException(403, {"allowed": False, "reasons": reasons})

        if run["dataset_version_id"] and str(run["dataset_version_id"]) != body.dataset_version_id:
            reasons = ["dataset version is outside the scope this run was launched for"]
            _record_decision(body, version, "policy", False, reasons)
            raise HTTPException(403, {"allowed": False, "reasons": reasons})

    # `pipeline_action`'s equivalent of the block above: a registered
    # workload naming this role must present the task_credential.py token
    # minted for a real task at its creation, not merely be registered under
    # that name. Two task kinds are accepted, not one, because not every
    # pipeline step has a natural close event to bind an `action_run` to:
    # `action_run` closes when a step seals a new dataset version, but a step
    # like `adopt_version` (worker/activities.py) only reads an existing one
    # and produces no sealed output, so it never opens an `action_run` at
    # all. `pipeline_run` already opens before that step runs and closes
    # once, at the end of the whole pipeline, so its token covers exactly
    # that gap -- the same reason a GitHub Actions OIDC token is scoped to
    # the whole workflow run rather than each job step, or a Kubernetes
    # ServiceAccount token to the whole pod rather than each container in
    # it. Scope comes from the named row's own `input_versions` array either
    # way -- a pipeline step, or a whole pipeline run, can have several
    # inputs, so this is membership, not the single exact match agent_run
    # uses.
    if registered and "pipeline_action" in (registered["roles"] or []):
        try:
            _claim, _run, input_versions = _resolve_pipeline_task(
                body.task_credential, body.principal, acting_tenant
            )
        except HTTPException as exc:
            _record_decision(body, version, "policy", False, exc.detail["reasons"])
            raise

        if body.dataset_version_id not in input_versions:
            reasons = ["dataset version is outside the inputs this task was launched for"]
            _record_decision(body, version, "policy", False, reasons)
            raise HTTPException(403, {"allowed": False, "reasons": reasons})

    # Which roles this principal actually holds, the same authority-comes-
    # from-the-directory rule `acting_tenant` above already applies to tenant.
    #
    # Before this, `body.roles` -- entirely caller-supplied -- was what
    # policy evaluated `role_reaches` against, for every principal, human or
    # workload. A registered principal's real roles are read here and used
    # instead; an unregistered principal has no registered roles to fall
    # back on, so the claim still stands for that one case; nothing yet
    # verifies that the caller *is* the principal it names, at all. Human
    # traffic reaches this endpoint through the console with the console's
    # own claim already checked against nothing further here (an unfinished
    # workload authentication, tracked separately). This closes the sharper
    # half of the gap: a workload that reads this code and learns that
    # `pipeline_action` reads any class could otherwise claim that role for
    # any principal name, registered or not, and be believed regardless of
    # what that principal is actually registered to do.
    effective_roles = (
        registered["roles"] if registered and registered["roles"] else body.roles
    )

    payload = {
        "principal": {
            "id": body.principal,
            "tenant": acting_tenant,
            "roles": effective_roles,
            "leases": _active_leases(body.principal, body.dataset_version_id),
        },
        "dataset": {
            "tenant": version["tenant_id"],
            "version_id": str(version["dataset_version_id"]),
            "visibility_class": version["current_class"],
        },
        "purpose": body.purpose,
    }

    allowed, reasons = opa.evaluate(payload)

    _record_decision(body, version, "policy", allowed, reasons)

    if not allowed:
        raise HTTPException(
            403, {"allowed": False, "reasons": reasons, "class": version["current_class"]}
        )

    if body.decide_only:
        if body.principal_kind != "human":
            raise HTTPException(422, {"allowed": False, "reasons": [
                "only a person can ask for a decision without a key; a workload needs the key"]})
        # No key is minted here. The caller is a workspace that issues its own,
        # and it writes the grant row when it has.
        return {"allowed": True, "decide_only": True, "class": version["current_class"]}

    # Policy said yes. Whether the grant can actually be applied is a separate
    # question with a separate answer, and it gets its own audit row either way.
    #
    # `effective_roles`, not `body.roles`: this selects which storage
    # identity's key actually gets minted, so it is the second half of the
    # same fix `role_reaches` above needed -- deciding "yes" on the real role
    # and then minting a key for the claimed one would still hand out
    # whatever storage access the caller asked for by name.
    role = effective_roles[0]
    try:
        creds, action = storage.mint_read_credential(
            version["storage_backend"], role, version["storage_prefix"], body.purpose,
            version["tenant_id"],
        )
    except (seaweed.StorageUnavailable, r2.StorageUnavailable, r2.UsageCeilingExceeded) as exc:
        _record_decision(
            body, version, "grant", False,
            [f"policy allowed but no access was granted: {exc}"],
        )
        raise HTTPException(
            503,
            {
                "allowed": True,
                "granted": False,
                "reasons": [str(exc)],
                "note": (
                    "Policy permitted this read, but no credential exists for the "
                    "role, so nothing was granted. Both facts are in the audit log."
                ),
            },
        ) from exc

    _record_decision(body, version, "grant", True, [f"granted {action}"])

    # If a lease is what justified this read, hand back that lease's own key
    # rather than the role's shared one, so the lease's end can cut this holder
    # without cutting the role. Created before the reconcile below so the print
    # emits its identity in the same pass. SeaweedFS only: R2 already mints a
    # scoped credential that expires by itself, so it needs none of this.
    lease_id = None
    if version["storage_backend"] == "seaweedfs":
        lease_id = _granting_lease(body.principal, body.dataset_version_id, body.purpose)
        if lease_id:
            leased = grants.identity_for_lease(lease_id, acting_tenant)
            creds = {**creds, "access_key": leased["access_key"],
                     "secret_key": leased["secret_key"]}
    creds["identity"] = "lease" if lease_id else "role"

    # The grant exists only once the document is compiled with this decision
    # in the register: nothing else writes it. So the key is handed back only
    # after that succeeds. A key returned before would look like access and be
    # refused by storage, which is the platform claiming something it had not
    # done. When the print fails the decision stands, the failure is recorded
    # in storage_permission_print, and the activator retries until it takes
    # effect.
    if version["storage_backend"] == "seaweedfs":
        try:
            grants.reconcile(trigger="request")
        except Exception:
            status = grants.activation_status()
            due = status["retry_due_at"]
            wait = (max(1, int((due - _now()).total_seconds())) if due
                    else grants.MAX_RETRY_SECONDS)
            return JSONResponse(
                status_code=202,
                headers={"Retry-After": str(wait)},
                content={
                    "allowed": True,
                    "active": False,
                    "retry_after_seconds": wait,
                    "reasons": [
                        "access is approved and recorded, and takes effect once "
                        "storage permissions are updated, which the platform "
                        "is retrying"
                    ],
                },
            )

    return {
        "allowed": True,
        "reasons": reasons,
        "granted_action": action,
        "prefix": version["storage_prefix"],
        "expires_at": (_now() + timedelta(minutes=config.CREDENTIAL_TTL_MINUTES)).isoformat(),
        **creds,
    }


@app.post("/write-credentials")
def request_write_credential(body: models.WriteCredentialRequest):
    """Mint a prefix-scoped write credential for a real pipeline task, or
    refuse and say why.

    The write-side counterpart to /credentials, and narrower: only a
    *registered* pipeline_action workload may even ask (no unregistered-
    caller benefit of the doubt the way an unregistered human keeps at
    /credentials, since nothing legitimate is ever an unregistered
    workload), and what it proves is not "may read this", it is "is a real
    task, of this tenant, writing into the next version-location for a
    dataset it names" -- computed here, from versions.next_version(), never
    from anything the caller supplies. Same three-outcome shape as
    /credentials: 200 with the key when granted and in effect, 403 when
    refused, 202 when granted but storage permissions have not caught up yet.
    """
    registered = db.one(
        "select tenant_id, roles from directory where id = %s", (body.principal,)
    )
    if not registered or "pipeline_action" not in (registered["roles"] or []):
        raise HTTPException(403, {
            "allowed": False,
            "reasons": ["only a registered pipeline_action workload may request write access"],
        })
    acting_tenant = registered["tenant_id"]

    claim, _run, _input_versions = _resolve_pipeline_task(
        body.task_credential, body.principal, acting_tenant
    )

    dataset = db.one(
        "select storage_backend from dataset where id = %s", (body.dataset_id,)
    )
    if not dataset:
        raise HTTPException(404, "no such dataset")
    backend = dataset["storage_backend"]
    if not backend:
        tenant = db.one(
            "select default_storage_backend from tenant where id = %s", (acting_tenant,)
        )
        backend = (tenant or {}).get("default_storage_backend") or "seaweedfs"

    reserved = versions.next_version(acting_tenant, body.dataset_id)
    role = "pipeline_action"

    try:
        bucket = storage.bucket_for(backend, acting_tenant)
    except (seaweed.StorageUnavailable, r2.StorageUnavailable) as exc:
        raise HTTPException(503, {
            "allowed": True, "granted": False, "reasons": [str(exc)],
        }) from exc

    # The grant is recorded before the credential is minted, the same order
    # /credentials keeps for reads: a key handed back before the register
    # justifies it would be the platform claiming something it had not done.
    # ON CONFLICT DO NOTHING: a retried request for the same task and prefix
    # (Temporal replay, or a second call within the same run) is the same
    # grant, not a second one.
    db.execute(
        """insert into write_grant
             (id, tenant_id, role, bucket, storage_prefix, task_kind, task_id, principal)
           values (%s, %s, %s, %s, %s, %s, %s, %s)
           on conflict (tenant_id, task_kind, task_id, storage_prefix) do nothing""",
        (_uuid(), acting_tenant, role, bucket, reserved["storage_prefix"],
         claim.task_kind, claim.task_id, body.principal),
    )

    try:
        creds, action = storage.mint_write_credential(
            backend, role, reserved["storage_prefix"], body.purpose, acting_tenant
        )
    except (seaweed.StorageUnavailable, r2.StorageUnavailable) as exc:
        raise HTTPException(503, {
            "allowed": True, "granted": False, "reasons": [str(exc)],
            "note": (
                "The write is recorded, but no credential exists for the "
                "role, so nothing was granted. Both facts are in the audit log."
            ),
        }) from exc

    if backend == "seaweedfs":
        try:
            grants.reconcile(trigger="request")
        except Exception:
            status = grants.activation_status()
            due = status["retry_due_at"]
            wait = (max(1, int((due - _now()).total_seconds())) if due
                    else grants.MAX_RETRY_SECONDS)
            return JSONResponse(
                status_code=202,
                headers={"Retry-After": str(wait)},
                content={
                    "allowed": True,
                    "active": False,
                    "retry_after_seconds": wait,
                    "reasons": [
                        "the write is approved and recorded, and takes effect "
                        "once storage permissions are updated, which the "
                        "platform is retrying"
                    ],
                },
            )

    return {
        "allowed": True,
        "granted_action": action,
        "prefix": reserved["storage_prefix"],
        "version": reserved["version"],
        "bucket": bucket,
        **creds,
    }


@app.get("/access-decisions")
def list_decisions(
    principal: str | None = None,
    allowed: bool | None = None,
    resource: str | None = None,
    limit: int = 50,
    offset: int = 0,
    identity: dict = Depends(auth.current_session),
) -> dict:
    """Read the audit log. Denials included, which is the point of having it.

    `tenant_id` used to be a caller-supplied query parameter, and this
    endpoint took no session at all: anyone who could reach the API could
    read any tenant's full audit log, unauthenticated, just by naming it.
    Scoped to the caller's own tenant now, the same fix every read endpoint
    in `read_models.py` already has; this one was missed because it is a
    read stranded in `main.py`, which by that file's own docstring is
    supposed to hold only endpoints with a consequence.

    Now the same `{decisions, shown, total, limit, offset}` envelope every
    `read_models.py` list endpoint uses, for real page controls.

    `allowed`/`resource` (`"dataset"` or `"agent"`, by whether
    `agent_run_id` is set) used to be filtered in the browser, on whatever
    one page's worth of rows happened to already be fetched -- harmless
    while every row was fetched at once, silently wrong the moment
    pagination means most rows never reach the browser to be filtered at
    all. Filtered here instead, the same principle `/datasets` already
    states: a console that fetches everything and hides most of it in the
    browser gets slower and, now, also wronger as the platform grows.
    """
    resource_sql = {
        None: "true",
        "dataset": "d.agent_run_id is null",
        "agent": "d.agent_run_id is not null",
    }.get(resource)
    if resource_sql is None:
        raise HTTPException(422, {"reasons": [f"unknown resource {resource!r}"]})

    where = (
        "where d.tenant_id = %(tenant)s"
        " and (%(principal)s::text is null or d.principal = %(principal)s)"
        " and (%(allowed)s::boolean is null or d.allowed = %(allowed)s)"
        f" and ({resource_sql})"
    )
    params = {
        "tenant": identity["tenant_id"], "principal": principal, "allowed": allowed,
    }

    # `active` is set on allowed grants only: whether the access is in effect
    # yet. On SeaweedFS that is a successful print started after the decision;
    # R2's credential works the moment it is minted.
    rows = db.all_rows(
        f"""select d.*,
                  case when d.phase = 'grant' and d.allowed then
                    coalesce(v.storage_backend <> 'seaweedfs'
                             or %(last_success)s::timestamptz > d.at, false)
                  end as active
             from access_decision d
             left join dataset_version v on v.id = d.dataset_version_id
           {where}
           order by d.at desc limit %(limit)s offset %(offset)s""",
        {**params, "last_success": grants.last_success_started_at(),
         "limit": limit, "offset": offset},
    )
    total = db.one(f"select count(*) as n from access_decision d {where}", params)
    return {
        "decisions": json.loads(json.dumps(rows, default=str)),
        "shown": len(rows),
        "total": total["n"],
        "limit": limit,
        "offset": offset,
    }


# --------------------------------------------------------------- promote --


def _perform_promotion(version_id: str, to_class: str, decided_by: str,
                       decided_by_kind: str, gate_evidence: dict,
                       grant_roles: list[str]) -> dict:
    """Record the class transition and grant the roles that follow from it.

    Shared by the workload-triggered endpoint and by a reviewer's decision at
    the gate, so there is one promotion in this codebase rather than two that
    can drift apart. It does not touch the version row, the storage prefix, or
    a single object. V3 records every object key and etag either side and
    asserts the sets are identical.

    Demotion is refused. Once a class has been widened, the people who could
    see it have seen it, so narrowing the label afterwards records a fiction.
    """
    order = {"RAW": 0, "UNDER_REVIEW": 1, "OPEN_FOR_ANNOTATION": 2, "OPEN_FOR_TRAINING": 3, "PUBLISHED": 4}
    version = db.one(
        "select * from version_class where dataset_version_id = %s", (version_id,)
    )
    if not version:
        raise HTTPException(404, "no such dataset version")

    current = version["current_class"]
    if order[to_class] <= order[current]:
        raise HTTPException(
            409,
            f"cannot move from {current} to {to_class}; promotion only widens access",
        )

    # version_class does not carry the backend, and the grants have to be made
    # where the bytes actually are.
    backend = db.one(
        "select storage_backend from dataset_version where id = %s", (version_id,)
    )["storage_backend"]

    db.execute(
        """insert into class_transition
             (id, dataset_version_id, from_class, to_class, decided_by,
              decided_by_kind, gate_evidence)
           values (%s, %s, %s, %s, %s, %s, %s)""",
        (_uuid(), version_id, current, to_class, decided_by,
         decided_by_kind, json.dumps(gate_evidence)),
    )

    granted = []
    for role in grant_roles:
        try:
            result = storage.grant_prefix(
                backend, role, version["storage_prefix"],
                f"promotion to {to_class}", version["tenant_id"])
            # On SeaweedFS nothing is written here: a role's read of this
            # version becomes a grant when it asks for a credential and the
            # compiled document includes it. Said as such, rather than listed
            # as though it were already in effect.
            granted.append(
                f"{role} may read this version from its next credential request"
                if backend == "seaweedfs" else result)
        except (seaweed.StorageUnavailable, r2.StorageUnavailable) as exc:
            # The transition has happened and the decision stands. A grant that
            # failed afterwards is a grant to retry, not a decision to unwind.
            granted.append(f"FAILED {role}: {exc}")

    # The new class can change what existing grants are justified, so the
    # document is compiled now, and a failure is reported, not hidden.
    if backend == "seaweedfs":
        try:
            grants.reconcile(trigger="promotion")
        except Exception:
            granted.append("storage permissions are being updated; the new "
                           "access takes effect once they are")

    return {
        "dataset_version_id": version_id,
        "from_class": current,
        "to_class": to_class,
        "storage_prefix": version["storage_prefix"],
        "bytes_moved": 0,
        "grants": granted,
    }


@app.post("/dataset-versions/{version_id}/promote")
def promote(version_id: str, body: models.PromotionIn) -> dict:
    """Promote a version to a less restricted class.

    This is the workload-triggered path, and it still trusts the identity its
    caller supplies, on purpose: workloads have no session to present and
    closing that is workload authentication, a separate piece of work.

    A human promotes through the gate decision endpoints instead, where the
    identity comes from their session and cannot be asserted about themselves.
    """
    return _perform_promotion(
        version_id, body.to_class, body.decided_by, body.decided_by_kind,
        body.gate_evidence, body.grant_roles,
    )


# ------------------------------------------------- records and deletion --


@app.post("/records", status_code=201)
def seal_record(body: models.RecordSealIn) -> dict:
    """Encrypt a record and store its wrapped key.

    The ciphertext is returned rather than stored. The control plane holds keys
    and metadata; bulk bytes belong in object storage, and keeping the two apart
    is what makes key destruction sufficient for deletion.
    """
    sealed = crypto.seal(body.tenant_id, body.record_id, body.plaintext.encode())
    db.execute(
        """insert into record_key (record_id, tenant_id, wrapped_key)
           values (%s, %s, %s)
           on conflict (record_id) do nothing""",
        (body.record_id, body.tenant_id, sealed.wrapped_key),
    )
    return {
        "record_id": body.record_id,
        "ciphertext": base64.b64encode(sealed.ciphertext).decode(),
    }


@app.post("/records/{record_id}/open")
def open_record(record_id: str, ciphertext_b64: str) -> dict:
    row = db.one(
        "select tenant_id, wrapped_key, destroyed_at from record_key where record_id = %s",
        (record_id,),
    )
    if not row:
        raise HTTPException(404, "no key for that record")
    try:
        plaintext = crypto.open(
            row["tenant_id"],
            record_id,
            base64.b64decode(ciphertext_b64),
            bytes(row["wrapped_key"]) if row["wrapped_key"] else None,
        )
    except DestroyedKeyError as exc:
        raise HTTPException(410, str(exc)) from exc
    return {"record_id": record_id, "plaintext": plaintext.decode()}


@app.delete("/records/{record_id}")
def destroy_record(record_id: str, body: models.RecordDestroyIn) -> dict:
    """Delete a record by destroying its key.

    Nothing is overwritten. The ciphertext stays in every sealed version that
    held it and stops being readable in all of them at once, and the tombstone
    keeps the fact of the record's existence so that erasure does not break the
    audit trail it was meant to serve.
    """
    row = db.execute(
        """update record_key
             set wrapped_key = null, destroyed_at = now()
           where record_id = %s and destroyed_at is null
           returning record_id""",
        (record_id,),
    )
    db.execute(
        """insert into tombstone (record_id, tenant_id, reason, requested_by)
           values (%s, %s, %s, %s)
           on conflict (record_id) do nothing""",
        (record_id, body.tenant_id, body.reason, body.requested_by),
    )
    return {
        "record_id": record_id,
        "key_destroyed": bool(row),
        "already_destroyed": not bool(row),
        "tombstoned": True,
    }


# ------------------------------------------------- the gate decision --


def _pending_gate(decision_id: str) -> dict:
    row = db.one("select * from gate_decision where id = %s", (decision_id,))
    if not row:
        raise HTTPException(404, "no such gate decision")
    if row["state"] != "pending":
        raise HTTPException(409, f"decision already {row['state']}")
    return row


def _gate_permission(row: dict, identity: dict) -> None:
    permitted, reasons = opa.may_decide_gate({
        "decider": {"id": identity["id"], "roles": identity["roles"]},
        "gate": {"triggered_by": row["triggered_by"]},
    })
    if not permitted:
        # Refused before the write is attempted, so the caller gets an
        # explanation rather than a constraint violation. The database check
        # remains the guarantee; this is the part that can say why.
        raise HTTPException(403, {"decided": False, "reasons": reasons})


@app.post("/gate-decisions/{decision_id}/promote", status_code=201)
def decide_gate_promote(decision_id: str, body: models.GateDecisionIn,
                        identity: dict = Depends(auth.current_session)) -> dict:
    """Clear the gate, and widen access.

    The identity comes from the session, never from the body. Promotion cannot
    be undone, so who did it has to be a fact the caller could not assert about
    themselves.
    """
    row = _pending_gate(decision_id)
    _gate_permission(row, identity)

    result = _perform_promotion(
        str(row["dataset_version_id"]), row["to_class"], identity["id"], "human",
        {
            "gate_decision": decision_id,
            "mlflow_run": row["score_card_id"],
            "machine_recommendation": row["recommendation"],
            "machine_reason": row["recommendation_reason"],
            "decided_because": body.reason,
            **row["metrics"],
        },
        body.grant_roles or ["annotation_tool"],
    )

    db.execute(
        """update gate_decision
             set state = 'promoted', decided_by = %s, decided_at = now(),
                 decision_reason = %s
           where id = %s and state = 'pending'""",
        (identity["id"], body.reason, decision_id),
    )
    return {"decided": True, "state": "promoted", **result}


@app.post("/gate-decisions/{decision_id}/refuse", status_code=201)
def decide_gate_refuse(decision_id: str, body: models.GateDecisionIn,
                       identity: dict = Depends(auth.current_session)) -> dict:
    """Refuse the gate. The version stays exactly where it is."""
    row = _pending_gate(decision_id)
    _gate_permission(row, identity)

    db.execute(
        """update gate_decision
             set state = 'refused', decided_by = %s, decided_at = now(),
                 decision_reason = %s
           where id = %s and state = 'pending'""",
        (identity["id"], body.reason, decision_id),
    )
    return {"decided": True, "state": "refused",
            "dataset_version_id": str(row["dataset_version_id"])}
