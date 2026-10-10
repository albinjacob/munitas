"""The routes through which a notebook lists and opens the few real files a person was allowed to open there.

A person who holds a lease made for a notebook (`file_leases`, kind `notebook`) asks the platform for a files token. The token names the person and the one
version, lasts at most an hour and never beyond the lease, and opens these routes and nothing else: it is refused wherever a session, a worker or a run
credential is wanted, so a token left in a notebook cannot read tables, ask for a storage credential or change a decision.

Every request asks again whether the lease is in force, so ending a lease, or its time running out, stops what a notebook is reading at its next request. The
files are listed and opened exactly as in the console (the same door, `record_door`, told this is a notebook): the same files the lease names and no others,
unlocked inside the platform, every opening written to the same read log with the way it was opened. The bytes are read through the stream route with the
handle an opening returns, from the start or by range, so a library can read a file as a file.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException

from . import auth, db, file_leases, locked_files, record_door, task_credential
from .person_errors import NoSuchVersion, NotPermitted
from .record_routes import _service

router = APIRouter(tags=["notebook files"])

TOKEN_SECONDS = 60 * 60
NOT_LEASED = ("these files are opened in a notebook only when an approver of the department that owns them has allowed it. Ask for it on the version's page, "
              "and choose that you will open them in a notebook")


def _version(version_id: str, tenant_id: str) -> None:
    try:
        record_door._version(version_id, tenant_id)
    except NoSuchVersion as exc:
        raise HTTPException(404, "no such version") from exc


@router.post("/dataset-versions/{version_id}/files-token", status_code=201)
def files_token(version_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """A token for reading the files a notebook lease opens: one version, at most an hour, never past the lease.

    Refused (403) when the caller holds no notebook lease in force on this version. Asked again to renew it; nothing is recorded until a file is opened.
    """
    _version(version_id, identity["tenant_id"])
    lease = file_leases.active(identity["id"], identity["tenant_id"], version_id, "notebook")
    if not lease:
        raise HTTPException(403, {"reasons": [NOT_LEASED]})
    left = int((lease["expires_at"] - datetime.now(timezone.utc)).total_seconds())
    if left < 1:
        raise HTTPException(403, {"reasons": [NOT_LEASED]})
    seconds = min(TOKEN_SECONDS, left)
    # A token made by a notebook that signed in carries that sign-in, and stops with it: ending the sign-in, or its running out, ends what it opened as well.
    token = task_credential.mint(principal=identity["id"], task_kind="notebook_files", task_id=str(lease["id"]), tenant_id=identity["tenant_id"],
                                 ttl_seconds=seconds, scope=version_id, step=identity.get("via_notebook_sign_in"))
    return {"token": token, "expires_in": seconds, "files": len(lease["records"] or []),
            "lease_ends": lease["expires_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}


def _notebook(authorization: str | None = Header(default=None)) -> tuple[task_credential.TaskClaim, dict]:
    """The files token in the request, checked: real, unexpired, made for a notebook, and for a person still in post in their own organisation."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, {"reasons": ["send the files token as a bearer token"]})
    try:
        claim = task_credential.verify(authorization[7:].strip())
    except task_credential.InvalidTaskCredential as exc:
        raise HTTPException(401, {"reasons": [f"this files token is not good: {exc}. Ask for a new one"]}) from exc
    if claim.task_kind != "notebook_files" or not claim.scope:
        raise HTTPException(401, {"reasons": ["this is not a files token"]})
    person = auth.identity_for(claim.principal)
    if not person or person.get("ended_at"):
        raise HTTPException(403, {"reasons": ["the person this token was made for is no longer in post"]})
    closed = auth.closed_refusal(person.get("phase"), person["roles"])
    if closed:
        raise HTTPException(403, {"reasons": [closed]})
    if person["tenant_id"] != claim.tenant_id:
        raise HTTPException(403, {"reasons": ["this token is for another organisation"]})
    if claim.step and not db.one("select 1 as x from notebook_sign_in where id = %s and status = 'approved' and expires_at > now()", (claim.step,)):
        raise HTTPException(401, {"reasons": ["the notebook sign-in this token came from has ended. Sign in again from the notebook"]})
    return claim, person


@router.get("/notebook/v1/files")
def list_files(context: tuple = Depends(_notebook)) -> dict:
    """The files the caller's notebook lease opens, with their sizes and whether the person each is about was erased. Opens nothing, so nothing is logged."""
    claim, person = context
    try:
        listed = record_door.list_records(person, claim.scope, via="notebook")
    except NoSuchVersion as exc:
        raise HTTPException(404, "no such version") from exc
    if not listed["may_open"]:
        raise HTTPException(403, {"reasons": [NOT_LEASED]})
    return {"version_id": listed["version_id"], "files": listed["records"], "total": listed["total"], "lease": listed.get("lease")}


@router.post("/notebook/v1/files/{record_id}/open")
def open_file(record_id: str, context: tuple = Depends(_notebook)) -> dict:
    """Open one file. This is the opening that is logged, and for a locked file written to the key log. Returns its size and a handle to read it with.

    Refused (403) when the lease does not name this file or is no longer in force, and (410) when the person the file is about was erased.
    """
    claim, person = context
    try:
        return record_door.open_record(_service(), person, claim.scope, record_id, via="notebook")
    except NotPermitted as exc:
        raise HTTPException(403, {"reasons": exc.reasons}) from exc
    except locked_files.PersonErased as exc:
        raise HTTPException(410, {"reasons": [str(exc)]}) from exc
    except (NoSuchVersion, record_door.NoSuchRecord) as exc:
        raise HTTPException(404, "no such version or file") from exc
