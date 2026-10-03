"""The storage key a pipeline worker reads its own earlier steps' output with.

WHY

A pipeline step reads what the step before it wrote: the records and the audio of a version it produced a moment ago, in its
own organisation's bucket. That read is not a lease and not a person's request, and it has no version of its own to ask
`/credentials` about at every point, so the worker holds a key for it. That key used to be ONE key, set in configuration, the
same for every organisation, and the policy gave it Read and List on every bucket: whoever held it could read every
organisation's data. It is now one key per organisation (`grants.tenant_role_key`), and each opens that organisation's bucket and
what the register justifies in it, and nothing of any other organisation. This endpoint is how a worker gets the one for the
organisation whose data it is processing.

WHO MAY ASK

A task presenting its signed credential gets the key of the organisation that credential names, and cannot ask for another's.
The worker token alone, with an organisation named, is also accepted for the steps that have no task (the scoring step reads
the answer key a run left); it is what the worker could always do with the static key, no more. The key does not change, so a
worker may keep it for as long as it runs.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from . import config, db, grants, logs, task_credential

log = logs.get_logger("pipeline_keys")
router = APIRouter()

ROLE = "pipeline_action"
KINDS = ("action_run", "pipeline_run", "huggingface_fetch_job")


@router.post("/storage-keys/pipeline")
def pipeline_storage_key(
    tenant_id: str | None = Query(default=None),
    x_worker_token: str | None = Header(default=None),
    x_task_credential: str | None = Header(default=None),
):
    if x_task_credential:
        try:
            claim = task_credential.verify(x_task_credential)
        except task_credential.InvalidTaskCredential as exc:
            raise HTTPException(403, {"reasons": [f"task credential rejected: {exc}"]}) from exc
        if claim.task_kind not in KINDS:
            raise HTTPException(403, {"reasons": ["this credential is not for a pipeline task"]})
        if tenant_id and tenant_id != claim.tenant_id:
            raise HTTPException(403, {"reasons": ["a task is given the key of its own organisation and no other"]})
        tenant = claim.tenant_id
    elif config.WORKER_TOKEN and x_worker_token == config.WORKER_TOKEN and tenant_id:
        tenant = tenant_id
    else:
        raise HTTPException(403, {"reasons": ["a task credential, or the worker token and the organisation, is required"]})

    if not grants.per_tenant(ROLE):
        raise HTTPException(409, {"reasons": ["the policy does not give the pipeline a key per organisation"]})
    provision = db.one("select bucket, created_at from tenant_storage_provision where tenant_id = %s and backend = 'seaweedfs'", (tenant,))
    if not provision:
        raise HTTPException(404, {"reasons": ["this organisation has no storage yet, so there is nothing for the key to open"]})
    # The key exists in the live permissions once a print that began after the bucket was provisioned has succeeded.
    if not grants.is_active(provision["created_at"]):
        try:
            grants.reconcile(trigger="request")
        except Exception:  # noqa: BLE001 - the same answer the credential endpoints give
            wait = grants.MAX_RETRY_SECONDS
            return JSONResponse(status_code=202, headers={"Retry-After": str(wait)}, content={
                "active": False, "retry_after_seconds": wait,
                "reasons": ["the key is made and takes effect once storage permissions are updated, which the platform is retrying"]})
    access_key, secret_key = grants.tenant_role_key(ROLE, tenant)
    return {"active": True, "access_key": access_key, "secret_key": secret_key, "endpoint": config.S3_ENDPOINT, "bucket": provision["bucket"]}
