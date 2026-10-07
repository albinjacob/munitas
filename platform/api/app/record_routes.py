"""The routes through which a person lists, opens and plays the files of a version.

Thin on purpose. Why a person may open them, how a locked file is unlocked and what is logged live in `record_door` and the policy.

The stream route takes no session. It takes the handle that opening a file returned, which names the person and the one file, lasts minutes, and is
checked again on every request. A sound player in a page cannot send a header, which is why the handle is in the address.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import auth, person_keys, recordings, record_door, task_credential
from .person_errors import NoSuchVersion, NotPermitted

router = APIRouter(tags=["record files"])


def _service():
    try:
        return person_keys.key_service()
    except RuntimeError as exc:
        raise HTTPException(503, {"reasons": [f"the key service is not set up: {exc}"]}) from exc


def _handle(handle: str) -> task_credential.TaskClaim:
    """The handle in the address, checked: real, unexpired and made for opening a file. Whether it still opens anything is asked by the route."""
    try:
        return record_door.check_handle(handle)
    except record_door.HandleRefused as exc:
        raise HTTPException(exc.status, {"reasons": exc.reasons}) from exc


@router.get("/dataset-versions/{version_id}/records")
def list_records(version_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """The files of a version, whether the caller may open them, and why not. Opens nothing, so nothing is logged."""
    try:
        return record_door.list_records(identity, version_id)
    except NoSuchVersion as exc:
        raise HTTPException(404, "no such version") from exc


@router.post("/dataset-versions/{version_id}/records/{record_id}/open")
def open_record(version_id: str, record_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """Open one file. This is the opening that is logged, and for a locked file written to the key log. Returns its size and a handle to stream it with.

    Refused (403) when the caller may not open this version's files, and (410) when the patient the file is about was erased.
    """
    try:
        return record_door.open_record(_service(), identity, version_id, record_id)
    except NotPermitted as exc:
        raise HTTPException(403, {"reasons": exc.reasons}) from exc
    except recordings.PatientErased as exc:
        raise HTTPException(410, {"reasons": [str(exc)]}) from exc
    except (NoSuchVersion, record_door.NoSuchRecord) as exc:
        raise HTTPException(404, "no such version or file") from exc


@router.get("/record-stream/{handle}")
def stream(handle: str, request: Request, _claim: task_credential.TaskClaim = Depends(_handle)):
    """The bytes of the file the handle was made for, from the start or from a range (`Range: bytes=a-b`), unlocked inside the platform as they are sent.

    Asked again on every request: the person is still in post, the reason they may open it still holds, the patient was not erased.
    """
    try:
        status, headers, body = record_door.serve(_service(), handle, request.headers.get("range"))
    except record_door.HandleRefused as exc:
        raise HTTPException(exc.status, {"reasons": exc.reasons}) from exc
    except NoSuchVersion as exc:
        raise HTTPException(404, "no such version") from exc
    return StreamingResponse(body, status_code=status, headers=headers)
