"""What a custom pipeline step is given, through the platform and nowhere else.

A script step of an operator's own pipeline can say `reads_files: true` (it is given the run's files) and `writes_files: true` (what it writes
becomes a version that waits for review). Its container has no storage, no key and no way to reach anything but one small gateway, which forwards
only the routes in this file. The step holds a pass for this one step of this one run, and the platform asks four questions of every request, not
only the first:

  * is the pass real, unexpired, and made for a step (not for a run, a credential route, or anything else)?
  * is the pipeline run it belongs to still going?
  * does the version of the pipeline that run came from declare this step, for what it is now asking to do (read files, or write files)?
  * does an approver's approval of that version still stand?

A file is opened before it is read. Opening it is the one write to the key log, naming the step, the pipeline and its version, and it returns a
handle that every read of that file must carry, so a read cannot skip the opening. A person who was erased is refused (410) at the opening
and at any later read, because the person's key is checked every time. The key never leaves the platform.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from crypto import NotOpenable

from . import auth, dag_pipelines, db, development_samples, locked_files, step_outputs, task_credential
from .person_errors import NoSuchVersion
from .locked_file_routes import _body, _service

router = APIRouter(tags=["custom steps"])

PASS_SECONDS = 30 * 60
HANDLE_SECONDS = 30 * 60
MAX_RANGE = 16 * 1024 * 1024


class PassIn(BaseModel):
    principal: str
    tenant_id: str
    task_credential: str
    step: str


class FinishIn(PassIn):
    succeeded: bool


class OpenOutputIn(BaseModel):
    source_record: str | None = None
    size: int | None = None


def run_context(pipeline_run_id: str, tenant_id: str, step: str) -> dict:
    """The pipeline run behind a step, with the reasons it may not be given what it asks for, as a refusal. Asked on every request."""
    run = db.one("select tenant_id, ended_at, source_version_id, pipeline_version_id, triggered_by from pipeline_run where id = %s", (pipeline_run_id,))
    if not run or run["tenant_id"] != tenant_id:
        raise HTTPException(403, {"reasons": ["no such pipeline run for this organisation"]})
    if run["ended_at"] is not None:
        raise HTTPException(403, {"reasons": ["the pipeline run this step belongs to has ended"]})
    version = db.one("""select pv.id, pv.version, pv.dag_config, p.name from pipeline_version pv join pipeline p on p.id = pv.pipeline_id
                         where pv.id = %s""", (run["pipeline_version_id"],)) if run["pipeline_version_id"] else None
    declared = [x for x in (version["dag_config"]["steps"] if version else [])
                if x.get("name") == step and x.get("kind") == "script" and (x.get("reads_files") is True or x.get("writes_files") is True)]
    if not declared:
        raise HTTPException(403, {"reasons": [f"step {step!r} of this run does not declare that it is given the files or writes files"]})
    stage = dag_pipelines.version_stage(str(version["id"]), version["dag_config"])
    if stage == "retired":
        raise HTTPException(403, {"reasons": ["this version of the pipeline was retired"]})
    if stage == "development" and not development_samples.is_development_data(tenant_id, run["triggered_by"], str(run["source_version_id"]) if run["source_version_id"] else None):
        raise HTTPException(403, {"reasons": ["this version of the pipeline is in development, or was returned to it, and only a version in production may be given files that "
                                              "are not development data. The files this run reads are neither declared synthetic nor a development sample that is in force "
                                              "for the person who started it"]})
    return {"source_version_id": str(run["source_version_id"]) if run["source_version_id"] else None,
            "files": declared[0].get("reads_files") is True, "writes_files": declared[0].get("writes_files") is True,
            "label": f"custom step {step} of pipeline {version['name']} version {version['version']}"}


def _needs(step: dict, what: str) -> None:
    """The step must have declared the specific thing it is now asking to do."""
    if not step[what]:
        wording = "given the files" if what == "files" else "allowed to write files"
        raise HTTPException(403, {"reasons": [f"step {step['claim'].step!r} of this run does not declare that it is {wording}"]})
    if what == "files" and not step["source_version_id"]:
        raise HTTPException(403, {"reasons": ["this run was not started from a version of files"]})


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


@router.get("/step/v1/files")
def list_files(step: dict = Depends(_step_pass)) -> dict:
    """The files this step may be given: each one's id, its size when opened, and whether its person was erased. Lists; opens nothing."""
    _needs(step, "files")
    try:
        return {"files": locked_files.list_for_step(step["source_version_id"])}
    except NoSuchVersion as exc:
        raise HTTPException(404, {"reasons": [str(exc)]}) from exc


@router.post("/step/v1/files/{record_id}/open")
def open_file(record_id: str, step: dict = Depends(_step_pass)) -> dict:
    """Open one file to be read a range at a time. This is the opening that is written to the key log, once, naming the step.

    Returns the file's size and a handle that every read of it must carry. A person who was erased is refused (410).
    """
    _needs(step, "files")
    claim = step["claim"]
    try:
        size = locked_files.begin_file(_service(), version_id=step["source_version_id"], record_id=record_id, run=f"step_run {claim.task_id}",
                                          purpose=f"open a file for {step['label']}", plain_ok=True)
    except NoSuchVersion as exc:
        raise HTTPException(404, {"reasons": [str(exc)]}) from exc
    except locked_files.PersonErased as exc:
        raise HTTPException(410, {"reasons": [str(exc)]}) from exc
    step_outputs.note_opening(claim.tenant_id, claim.task_id, claim.step, step["source_version_id"], record_id)
    handle = task_credential.mint(principal=claim.principal, task_kind="step_read", task_id=claim.task_id, tenant_id=claim.tenant_id,
                                  ttl_seconds=HANDLE_SECONDS, step=claim.step, scope=record_id)
    return {"bytes": size, "handle": handle}


@router.get("/step/v1/files/{record_id}/range")
def read_range(record_id: str, offset: int = Query(ge=0), length: int = Query(ge=0, le=MAX_RANGE), x_handle: str | None = Header(default=None),
               step: dict = Depends(_step_pass)) -> Response:
    """A range of an opened file, opened. The handle from opening it must come with the request, and the person's key is checked every time:
    a file being read stops the moment the person is erased (410)."""
    _needs(step, "files")
    claim = step["claim"]
    try:
        held = task_credential.verify(x_handle or "")
    except task_credential.InvalidTaskCredential as exc:
        raise HTTPException(403, {"reasons": [f"this file was not opened for this step: {exc}. Open it first"]}) from exc
    if held.task_kind != "step_read" or held.task_id != claim.task_id or held.step != claim.step or held.scope != record_id:
        raise HTTPException(403, {"reasons": ["this handle is for another file, step or run. Open this file first"]})
    try:
        size, data = locked_files.read_file_range(_service(), version_id=step["source_version_id"], record_id=record_id, run=f"step_run {claim.task_id}",
                                                     offset=offset, length=length, plain_ok=True)
    except NoSuchVersion as exc:
        raise HTTPException(404, {"reasons": [str(exc)]}) from exc
    except locked_files.PersonErased as exc:
        raise HTTPException(410, {"reasons": [str(exc)]}) from exc
    except NotOpenable as exc:
        raise HTTPException(409, {"reasons": [f"the stored file does not open: {exc}"]}) from exc
    return Response(content=data, media_type="application/octet-stream", headers={"x-plain-bytes": str(size)})


# The files a step writes ------------------------------------------------------------------------------------------------------------------------


def _refused(exc: step_outputs.Refused) -> HTTPException:
    return HTTPException(400, {"reasons": exc.reasons})


@router.post("/step/v1/outputs/{name}", status_code=201)
def open_output(name: str, body: OpenOutputIn | None = None, step: dict = Depends(_step_pass)) -> dict:
    """Open one file to be written, in parts of at most 8 MiB. Opening a name again replaces the earlier file of that name.

    A file made from a file says which one (`source_record`) and its size in bytes (`size`), and is then locked under that file's person as
    it arrives. Nothing is a version yet. The files a step finished are sealed together, "under review", when the step ends successfully, and thrown
    away when it fails.
    """
    _needs(step, "writes_files")
    claim = step["claim"]
    try:
        return step_outputs.open_output(tenant_id=claim.tenant_id, pipeline_run_id=claim.task_id, step=claim.step, name=name,
                                        source_record=body.source_record if body else None, size=body.size if body else None)
    except step_outputs.Refused as exc:
        raise _refused(exc) from exc


@router.put("/step/v1/outputs/{name}/parts/{part_number}")
async def put_output_part(name: str, part_number: int, request: Request, step: dict = Depends(_step_pass)) -> dict:
    """One part of an open file, sent as the raw body of the request (at most 8 MiB). Sending a part again replaces it."""
    _needs(step, "writes_files")
    claim = step["claim"]
    data = await _body(request, step_outputs.PART)
    service = _service()
    try:
        # In a worker thread, for the reason given on the locked file upload route: the write to storage must not hold up the other requests.
        return await run_in_threadpool(step_outputs.put_part, service=service, pipeline_run_id=claim.task_id, step=claim.step, name=name,
                                       part_number=part_number, data=data)
    except step_outputs.Refused as exc:
        raise _refused(exc) from exc


@router.post("/step/v1/outputs/{name}/complete")
def complete_output(name: str, step: dict = Depends(_step_pass)) -> dict:
    """Finish a file. Every part must be there. Returns the file's size and fingerprint as stored."""
    _needs(step, "writes_files")
    claim = step["claim"]
    try:
        return step_outputs.complete_output(pipeline_run_id=claim.task_id, step=claim.step, name=name, reads_files=bool(step["files"]))
    except step_outputs.Refused as exc:
        raise _refused(exc) from exc


@router.post("/internal/dag-step/finish", include_in_schema=False)
def finish_step(body: FinishIn, _worker: None = Depends(auth.worker_only)) -> dict:
    """The step's container has exited. If it succeeded, seal what it wrote as one version under review; if not, throw it all away.

    Asked by the sandbox worker with the pipeline run's own credential. When the approval of the step's version was withdrawn while it ran, nothing
    is sealed and the files are thrown away.
    """
    from . import main
    registered = db.one("select tenant_id from directory where id = %s", (body.principal,))
    if not registered or registered["tenant_id"] != body.tenant_id:
        raise HTTPException(403, {"reasons": ["no such workload is registered for this organisation"]})
    claim, _run, _inputs = main._resolve_pipeline_task(body.task_credential, body.principal, registered["tenant_id"])
    if claim.task_kind != "pipeline_run" or not main._live_task(claim, None):
        raise HTTPException(403, {"reasons": ["a step is finished through a pipeline run that is still running"]})
    try:
        context = run_context(claim.task_id, registered["tenant_id"], body.step)
        if not context["writes_files"]:
            raise HTTPException(403, {"reasons": [f"step {body.step!r} of this run does not declare that it is allowed to write files"]})
    except HTTPException as exc:
        step_outputs.finish(tenant_id=registered["tenant_id"], pipeline_run_id=claim.task_id, step=body.step, succeeded=False, principal=body.principal)
        return {"status": "refused", "files_written": 0, "reason": "; ".join(exc.detail["reasons"]) + ". Nothing the step wrote was kept"}
    return step_outputs.finish(tenant_id=registered["tenant_id"], pipeline_run_id=claim.task_id, step=body.step, succeeded=body.succeeded, principal=body.principal)
