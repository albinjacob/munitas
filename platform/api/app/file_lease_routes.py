"""The routes for leases on real files: asking for one, deciding a request, ending a lease, and what a version's page needs to show.

Thin on purpose. Who may do what, and why a request is refused, live in `file_leases` and the policy.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import auth, file_leases
from .person_errors import AlreadyDecided, LeaseConflict, NoSuchLease, NoSuchVersion, NotPermitted, Refused

router = APIRouter(tags=["file leases"])


class FileLeaseRequestIn(BaseModel):
    kind: str = Field(description="'console' to listen in the page, or 'notebook' to read through the client library")
    purpose: str = Field(description="What the files are for. Recorded with the lease.")
    records: int = Field(description="How many files the lease opens")
    hours: int = Field(description="How many hours the lease lasts")


class FileLeaseDecisionIn(BaseModel):
    note: str | None = None
    records: int | None = Field(default=None, description="An approver may lower the number of files asked for, never raise it")
    hours: int | None = Field(default=None, description="An approver may lower the hours asked for, never raise them")


def _refusals(call):
    try:
        return call()
    except NotPermitted as exc:
        raise HTTPException(403, {"reasons": exc.reasons}) from exc
    except (LeaseConflict, AlreadyDecided) as exc:
        raise HTTPException(409, {"reasons": exc.reasons}) from exc
    except Refused as exc:
        raise HTTPException(400, {"reasons": exc.reasons}) from exc
    except (NoSuchVersion, NoSuchLease) as exc:
        raise HTTPException(404, "no such version or lease") from exc


@router.get("/dataset-versions/{version_id}/file-lease")
def file_lease_of_version(version_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """What a version's page needs about leases on its files: this person's own lease on it, the limits, and whether anybody can decide a request."""
    return _refusals(lambda: file_leases.for_version(identity, version_id))


@router.post("/dataset-versions/{version_id}/file-leases", status_code=201)
def request_file_lease(version_id: str, body: FileLeaseRequestIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Ask to open a few of a version's real files for a few hours. An approver of the owning department who is not the person asking decides."""
    return _refusals(lambda: file_leases.request_lease(identity, version_id, body.kind, body.purpose, body.records, body.hours))


@router.get("/file-leases")
def list_file_leases(identity: dict = Depends(auth.current_session)) -> dict:
    """The person's own leases, the requests waiting for them as an approver of the department that owns the files, and the leases in force there."""
    return file_leases.lists(identity)


@router.post("/file-leases/{lease_id}/approve")
def approve_file_lease(lease_id: str, body: FileLeaseDecisionIn | None = None, identity: dict = Depends(auth.current_session)) -> dict:
    """Approve a request. The platform draws the files and fixes them. An approver may grant fewer files or fewer hours than were asked for."""
    body = body or FileLeaseDecisionIn()
    return _refusals(lambda: file_leases.decide(identity, lease_id, True, body.note, body.records, body.hours))


@router.post("/file-leases/{lease_id}/decline")
def decline_file_lease(lease_id: str, body: FileLeaseDecisionIn | None = None, identity: dict = Depends(auth.current_session)) -> dict:
    """Decline a request, with a note saying why."""
    body = body or FileLeaseDecisionIn()
    return _refusals(lambda: file_leases.decide(identity, lease_id, False, body.note))


@router.post("/file-leases/{lease_id}/end")
def end_file_lease(lease_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """End a lease early: the department's approver, or the person it was granted to."""
    return _refusals(lambda: file_leases.end(identity, lease_id))
