"""The routes for recordings locked per patient.

Thin on purpose: what is refused, and why, lives in `recordings`, which the checks call directly as well. A part is sent as the raw body of the
request, not as a form, so the platform never writes a plain copy of a recording to its own disk on the way in: each part (at most 8 MB) is held in
memory, locked, and handed on.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from crypto import NotOpenable
from crypto import stream

from . import auth, dag_pipelines, db, person_keys, recordings
from .person_errors import NoSuchVersion, Refused

router = APIRouter(tags=["recordings"])


class OpenUploadIn(BaseModel):
    file_name: str = Field(description="The recording, <record>.wav, or its answer key, <record>.truth.json")
    size_bytes: int = Field(description="The size of the file in bytes, stated before any part is sent")
    patient_kind: str = Field(description="What kind of identifier names the patient, such as a hospital chart number")
    patient_value: str = Field(description="The patient's identifier. Used to find the patient and never stored")


class OpenRecordingIn(BaseModel):
    principal: str
    tenant_id: str
    task_credential: str
    dataset_version_id: str
    record_id: str
    purpose: str
    kind: str = "audio"
    step: str | None = Field(default=None, description="The custom pipeline step asking, when one is. Such a step is given a recording only if its version was approved")


class StepRecordingsIn(BaseModel):
    principal: str
    tenant_id: str
    task_credential: str
    dataset_version_id: str
    step: str


def _service():
    try:
        return person_keys.key_service()
    except RuntimeError as exc:
        raise HTTPException(503, {"reasons": [f"the key service is not set up: {exc}"]}) from exc


def _answering(call):
    try:
        return call()
    except Refused as exc:
        raise HTTPException(400, {"reasons": exc.reasons}) from exc
    except recordings.NoSuchUpload as exc:
        raise HTTPException(404, "no such upload") from exc
    except recordings.PatientErased as exc:
        raise HTTPException(409, {"reasons": [str(exc)]}) from exc


async def _body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, {"reasons": [f"a part is at most {limit} bytes"]})
    chunks, total = [], 0
    async for piece in request.stream():
        total += len(piece)
        if total > limit:
            raise HTTPException(413, {"reasons": [f"a part is at most {limit} bytes"]})
        chunks.append(piece)
    return b"".join(chunks)


@router.post("/datasets/{dataset_id}/recording-uploads", status_code=201)
def open_upload(dataset_id: str, body: OpenUploadIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Open an upload of one recording (or its answer key) for one patient. It is sent in parts of at most 8 MB, each of which can be sent again.

    The patient's identifier finds their person code, made with a key the first time the identifier is seen. It is never stored.
    The upload stays open for a day.
    """
    service = _service()
    return _answering(lambda: recordings.open_upload(
        service, dataset_id=dataset_id, identity=identity, file_name=body.file_name, size_bytes=body.size_bytes,
        patient_kind=body.patient_kind, patient_value=body.patient_value))


@router.get("/recording-uploads/{upload_id}")
def upload_status(upload_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """Which parts have arrived and which are missing, so an upload that was cut off carries on from where it stopped."""
    return _answering(lambda: recordings.status(upload_id, identity))


@router.put("/recording-uploads/{upload_id}/parts/{part_number}")
async def send_part(upload_id: str, part_number: int, request: Request, identity: dict = Depends(auth.current_session)) -> dict:
    """Send one part, as the raw body of the request. Sending a part again replaces it."""
    service = _service()
    data = await _body(request, stream.PART)
    return _answering(lambda: recordings.receive_part(service, upload_id=upload_id, part_number=part_number, data=data, identity=identity))


@router.post("/recording-uploads/{upload_id}/complete")
def complete_upload(upload_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """Finish the upload. Every part must have arrived and the stored size must be exactly what a locked file of that size is."""
    return _answering(lambda: recordings.complete(upload_id, identity))


@router.delete("/recording-uploads/{upload_id}")
def abort_upload(upload_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """Give up on an upload. What was sent is dropped."""
    return _answering(lambda: recordings.abort(upload_id, identity))


@router.post("/datasets/{dataset_id}/seal-recordings", status_code=201)
def seal_recordings(dataset_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """Seal the locked uploads as a version the de-identification pipeline can read. Pressing this is the check: a refusal seals nothing."""
    _service()
    return _answering(lambda: recordings.seal_recordings(dataset_id=dataset_id, identity=identity))


@router.get("/dataset-versions/{version_id}/recordings")
def recordings_summary(version_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """How many locked recordings a sealed version holds, how many patients they are about, and how many belong to patients who were erased.

    Counts only. A version that holds no locked recordings answers 404.
    """
    try:
        return recordings.summary(version_id, identity)
    except NoSuchVersion as exc:
        raise HTTPException(404, "no locked recordings here") from exc


@router.get("/internal/dataset-versions/{version_id}/locked-recordings", include_in_schema=False)
def is_locked(version_id: str, _worker: None = Depends(auth.worker_only)) -> dict:
    """Whether a version's recordings are locked, so a pipeline run knows to ask for them opened instead of reading them from storage."""
    return {"locked": recordings.is_locked_version(version_id)}


def _authorise(principal: str, tenant_id: str, task_credential: str, version_id: str, step: str | None):
    """Is this a real task of this organisation, launched with this version as an input, and still running? And, when a custom step is asking, was
    its version approved to be given the recordings, and does it declare that it wants them?

    Returns the verified claim and a label naming the step for the key log.
    """
    from . import main  # the one check of "is this task real, and is this version one of its inputs"
    registered = db.one("select tenant_id from directory where id = %s", (principal,))
    if not registered or registered["tenant_id"] != tenant_id:
        raise HTTPException(403, {"reasons": ["no such workload is registered for this organisation"]})
    claim, _run, inputs = main._resolve_pipeline_task(task_credential, principal, registered["tenant_id"])
    if version_id not in inputs:
        raise HTTPException(403, {"reasons": ["this recording's version is outside the inputs this task was launched for"]})
    if not main._live_task(claim, None):
        raise HTTPException(403, {"reasons": ["the task this was requested for is no longer running"]})
    version = db.one("select tenant_id from dataset_version where id = %s", (version_id,))
    if not version or version["tenant_id"] != registered["tenant_id"]:
        raise HTTPException(404, "no such version")
    label = None
    if step is not None:
        if claim.task_kind != "pipeline_run":
            raise HTTPException(403, {"reasons": ["only a step of a pipeline run is given recordings as a custom step"]})
        pipeline = db.one("""select pv.id, pv.version, pv.dag_config, p.name from pipeline_run r
                               join pipeline_version pv on pv.id = r.pipeline_version_id join pipeline p on p.id = pv.pipeline_id
                              where r.id = %s""", (claim.task_id,))
        declared = [x for x in (pipeline["dag_config"]["steps"] if pipeline else []) if x.get("name") == step and x.get("kind") == "script" and x.get("recordings") is True]
        if not declared:
            raise HTTPException(403, {"reasons": [f"step {step!r} of this run does not declare that it is given the recordings"]})
        if not dag_pipelines.is_approved(str(pipeline["id"])):
            raise HTTPException(403, {"reasons": ["this version of the pipeline has not been approved to be given the recordings, or its approval was withdrawn"]})
        label = f"custom step {step} of pipeline {pipeline['name']} version {pipeline['version']}"
    return claim, label


@router.post("/internal/dag-step/recordings", include_in_schema=False)
def step_recordings(body: StepRecordingsIn, _worker: None = Depends(auth.worker_only)) -> dict:
    """The recordings a custom pipeline step is to be given: each one's id, its size, and whether its patient was erased. Lists; opens nothing."""
    _authorise(body.principal, body.tenant_id, body.task_credential, body.dataset_version_id, body.step)
    try:
        return {"recordings": recordings.list_for_step(body.dataset_version_id)}
    except NoSuchVersion as exc:
        raise HTTPException(404, {"reasons": [str(exc)]}) from exc


@router.post("/internal/recordings/open", include_in_schema=False)
def open_recording(body: OpenRecordingIn, _worker: None = Depends(auth.worker_only)):
    """One recording, opened for a pipeline run, as a stream. Only a run that was launched with this version as an input, and is still running.

    Each opening is written to the key log with the run named, and with the custom step named when one asked. A patient who was erased is
    refused (410), and the run skips the recording.
    """
    claim, label = _authorise(body.principal, body.tenant_id, body.task_credential, body.dataset_version_id, body.step)
    try:
        size, pieces = recordings.open_recording(_service(), version_id=body.dataset_version_id, record_id=body.record_id,
                                                 run=f"{claim.task_kind} {claim.task_id}",
                                                 purpose=f"open a recording for {label or 'a pipeline run'}: {body.purpose}",
                                                 kind=body.kind, plain_ok=label is not None)
    except NoSuchVersion as exc:
        raise HTTPException(404, {"reasons": [str(exc)]}) from exc
    except recordings.PatientErased as exc:
        raise HTTPException(410, {"reasons": [str(exc)]}) from exc

    def body_stream():
        try:
            yield from pieces
        except NotOpenable:
            # The status line has gone already, so the only honest thing left is to end the body short. The reader compares what it got with the
            # size it was told, and a short body is refused.
            return

    return StreamingResponse(body_stream(), media_type="audio/wav", headers={"x-plain-bytes": str(size), "content-length": str(size)})
