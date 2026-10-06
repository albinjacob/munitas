"""What a custom pipeline step is given, through the platform and nowhere else.

A script step of an operator's own pipeline can say `recordings: true`. Its container has no storage, no key and no way to reach anything but one
small gateway, which forwards only the routes in this file. The step holds a pass for this one step of this one run, and the platform asks four
questions of every request, not only the first:

  * is the pass real, unexpired, and made for a step (not for a run, a credential route, or anything else)?
  * is the pipeline run it belongs to still going?
  * does the version of the pipeline that run came from declare this step as one that is given the recordings?
  * does an approver's approval of that version still stand?

A recording is opened before it is read. Opening it is the one write to the key log, naming the step, the pipeline and its version, and it returns a
handle that every read of that recording must carry, so a read cannot skip the opening. A patient who was erased is refused (410) at the opening
and at any later read, because the patient's key is checked every time. The key never leaves the platform.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from crypto import NotOpenable

from . import auth, dag_pipelines, db, recordings, task_credential
from .person_errors import NoSuchVersion
from .recording_routes import _service

router = APIRouter(tags=["custom steps"])

PASS_SECONDS = 30 * 60
HANDLE_SECONDS = 30 * 60
MAX_RANGE = 16 * 1024 * 1024


class PassIn(BaseModel):
    principal: str
    tenant_id: str
    task_credential: str
    step: str


def run_context(pipeline_run_id: str, tenant_id: str, step: str) -> dict:
    """The pipeline run behind a step, with the reasons it may not be given the recordings, as a refusal. Asked on every request."""
    run = db.one("select tenant_id, ended_at, source_version_id, pipeline_version_id from pipeline_run where id = %s", (pipeline_run_id,))
    if not run or run["tenant_id"] != tenant_id:
        raise HTTPException(403, {"reasons": ["no such pipeline run for this organisation"]})
    if run["ended_at"] is not None:
        raise HTTPException(403, {"reasons": ["the pipeline run this step belongs to has ended"]})
    version = db.one("""select pv.id, pv.version, pv.dag_config, p.name from pipeline_version pv join pipeline p on p.id = pv.pipeline_id
                         where pv.id = %s""", (run["pipeline_version_id"],)) if run["pipeline_version_id"] else None
    declared = [x for x in (version["dag_config"]["steps"] if version else [])
                if x.get("name") == step and x.get("kind") == "script" and x.get("recordings") is True]
    if not declared:
        raise HTTPException(403, {"reasons": [f"step {step!r} of this run does not declare that it is given the recordings"]})
    if not dag_pipelines.is_approved(str(version["id"])):
        raise HTTPException(403, {"reasons": ["this version of the pipeline has not been approved to be given the recordings, or its approval was withdrawn"]})
    if not run["source_version_id"]:
        raise HTTPException(403, {"reasons": ["this run was not started from a version of recordings"]})
    return {"source_version_id": str(run["source_version_id"]), "label": f"custom step {step} of pipeline {version['name']} version {version['version']}"}


@router.post("/internal/dag-step/pass", include_in_schema=False)
def mint_pass(body: PassIn, _worker: None = Depends(auth.worker_only)) -> dict:
    """A pass for one step of one run, for the sandbox worker to hand to the step's container. Only for a step that the platform would then serve."""
    from . import main  # the one check of "is this task real and still running"
    registered = db.one("select tenant_id from directory where id = %s", (body.principal,))
    if not registered or registered["tenant_id"] != body.tenant_id:
        raise HTTPException(403, {"reasons": ["no such workload is registered for this organisation"]})
    claim, _run, _inputs = main._resolve_pipeline_task(body.task_credential, body.principal, registered["tenant_id"])
    if claim.task_kind != "pipeline_run" or not main._live_task(claim, None):
        raise HTTPException(403, {"reasons": ["a step's pass is made from a pipeline run that is still running"]})
    run_context(claim.task_id, registered["tenant_id"], body.step)
    token = task_credential.mint(principal=body.principal, task_kind="step_run", task_id=claim.task_id, tenant_id=registered["tenant_id"],
                                 ttl_seconds=PASS_SECONDS, step=body.step)
    return {"pass": token, "expires_in": PASS_SECONDS}


def _step_pass(authorization: str | None = Header(default=None)) -> dict:
    """The step behind a request: its pass is real and made for a step, and its run, version and approval are all in order right now."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, {"reasons": ["a step presents the pass it was given as a bearer token"]})
    try:
        claim = task_credential.verify(authorization[7:].strip())
    except task_credential.InvalidTaskCredential as exc:
        raise HTTPException(401, {"reasons": [f"pass rejected: {exc}"]}) from exc
    if claim.task_kind != "step_run" or not claim.step:
        raise HTTPException(401, {"reasons": ["this is not a step's pass"]})
    return {"claim": claim, **run_context(claim.task_id, claim.tenant_id, claim.step)}


@router.get("/step/v1/recordings")
def list_recordings(step: dict = Depends(_step_pass)) -> dict:
    """The recordings this step may be given: each one's id, its size when opened, and whether its patient was erased. Lists; opens nothing."""
    try:
        return {"recordings": recordings.list_for_step(step["source_version_id"])}
    except NoSuchVersion as exc:
        raise HTTPException(404, {"reasons": [str(exc)]}) from exc


@router.post("/step/v1/recordings/{record_id}/open")
def open_recording(record_id: str, step: dict = Depends(_step_pass)) -> dict:
    """Open one recording to be read a range at a time. This is the opening that is written to the key log, once, naming the step.

    Returns the recording's size and a handle that every read of it must carry. A patient who was erased is refused (410).
    """
    claim = step["claim"]
    try:
        size = recordings.begin_recording(_service(), version_id=step["source_version_id"], record_id=record_id, run=f"step_run {claim.task_id}",
                                          purpose=f"open a recording for {step['label']}", plain_ok=True)
    except NoSuchVersion as exc:
        raise HTTPException(404, {"reasons": [str(exc)]}) from exc
    except recordings.PatientErased as exc:
        raise HTTPException(410, {"reasons": [str(exc)]}) from exc
    handle = task_credential.mint(principal=claim.principal, task_kind="step_read", task_id=claim.task_id, tenant_id=claim.tenant_id,
                                  ttl_seconds=HANDLE_SECONDS, step=claim.step, scope=record_id)
    return {"bytes": size, "handle": handle}


@router.get("/step/v1/recordings/{record_id}/range")
def read_range(record_id: str, offset: int = Query(ge=0), length: int = Query(ge=0, le=MAX_RANGE), x_handle: str | None = Header(default=None),
               step: dict = Depends(_step_pass)) -> Response:
    """A range of an opened recording, opened. The handle from opening it must come with the request, and the patient's key is checked every time:
    a recording being read stops the moment the patient is erased (410)."""
    claim = step["claim"]
    try:
        held = task_credential.verify(x_handle or "")
    except task_credential.InvalidTaskCredential as exc:
        raise HTTPException(403, {"reasons": [f"this recording was not opened for this step: {exc}. Open it first"]}) from exc
    if held.task_kind != "step_read" or held.task_id != claim.task_id or held.step != claim.step or held.scope != record_id:
        raise HTTPException(403, {"reasons": ["this handle is for another recording, step or run. Open this recording first"]})
    try:
        size, data = recordings.read_recording_range(_service(), version_id=step["source_version_id"], record_id=record_id, run=f"step_run {claim.task_id}",
                                                     offset=offset, length=length, plain_ok=True)
    except NoSuchVersion as exc:
        raise HTTPException(404, {"reasons": [str(exc)]}) from exc
    except recordings.PatientErased as exc:
        raise HTTPException(410, {"reasons": [str(exc)]}) from exc
    except NotOpenable as exc:
        raise HTTPException(409, {"reasons": [f"the stored recording does not open: {exc}"]}) from exc
    return Response(content=data, media_type="application/octet-stream", headers={"x-plain-bytes": str(size)})
