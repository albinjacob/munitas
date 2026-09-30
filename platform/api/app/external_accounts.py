"""A registered human's own external credential.

Separate from `ingest.py` for the reason its own docstring gives for keeping
that file to itself: a reviewer asking "where can a secret enter this
platform" should have one file to check, the same as "where do bytes enter
it" has one file to check. This is the only module that accepts a token.

The token itself never leaves this module in the clear. It is sealed with
`crypto.EnvelopeCrypto` (the same envelope scheme `record_key` already uses
for data at rest) the moment it arrives, and nothing here logs it, returns
it, or writes it anywhere unencrypted.
"""

from __future__ import annotations

import uuid

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from crypto import EnvelopeCrypto

from . import config, db
from .auth import current_session

router = APIRouter(tags=["external-accounts"])

crypto = EnvelopeCrypto()

HUGGINGFACE = "https://huggingface.co"


def _record_id(directory_id: str) -> str:
    return f"hf-token/{directory_id}"


def _directory(directory_id: str) -> dict:
    row = db.one("select id, tenant_id, kind from directory where id = %s", (directory_id,))
    if not row:
        raise HTTPException(404, {"reasons": [f"{directory_id!r} is not in the directory"]})
    return row


def _require_own(directory_id: str, session: dict) -> None:
    """Refuse unless the signed-in session is this exact person.

    A HuggingFace token is personal, not tenant-shared data: nothing else in
    this platform reads or writes it except that one person and the worker
    fetching on their behalf. Matching on `tenant_id` the way most other
    endpoints do would still let anyone else in the same organisation plant,
    remove, or ask whether a colleague's account is connected.
    """
    if session["id"] != directory_id:
        raise HTTPException(
            403,
            {"reasons": ["this is somebody else's HuggingFace connection"]},
        )


class ConnectHuggingFace(BaseModel):
    token: str = Field(min_length=1)


@router.post("/directory/{directory_id}/huggingface-token", status_code=201)
def connect_huggingface(
    directory_id: str,
    body: ConnectHuggingFace,
    session: dict = Depends(current_session),
) -> dict:
    """Connect a registered human's own HuggingFace access token.

    Validated before anything is stored: HuggingFace's own `whoami-v2` is
    asked whether the token works, so a typo or an expired token is refused
    here, on the spot, rather than surfacing later as a confusing fetch
    failure. Reconnecting replaces the previous token rather than keeping
    both, since only one HuggingFace account can be connected at a time.
    """
    _require_own(directory_id, session)
    person = _directory(directory_id)
    if person["kind"] != "human":
        raise HTTPException(
            400,
            {"reasons": [
                "a workload acts through its own registered identity and "
                "standing leases, not a personal HuggingFace account"
            ]},
        )

    try:
        who = httpx.get(
            f"{HUGGINGFACE}/api/whoami-v2",
            headers={"Authorization": f"Bearer {body.token}"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            502, {"reasons": [f"could not reach HuggingFace to check that token: {exc}"]}
        ) from exc
    if who.status_code != 200:
        raise HTTPException(
            422,
            {"reasons": [
                f"HuggingFace did not accept that token (HTTP {who.status_code}); "
                "check it was copied in full and has not expired"
            ]},
        )
    hf_username = who.json().get("name") or "unknown"

    sealed = crypto.seal(person["tenant_id"], _record_id(directory_id), body.token.encode())

    db.execute(
        """insert into external_credential
             (id, directory_id, provider, hf_username, ciphertext, wrapped_key)
           values (%s, %s, 'huggingface', %s, %s, %s)
           on conflict (directory_id, provider) do update set
             hf_username = excluded.hf_username,
             ciphertext = excluded.ciphertext,
             wrapped_key = excluded.wrapped_key,
             connected_at = now(),
             last_used_at = null""",
        (str(uuid.uuid4()), directory_id, hf_username, sealed.ciphertext, sealed.wrapped_key),
    )

    return {"connected": True, "hf_username": hf_username}


@router.delete("/directory/{directory_id}/huggingface-token")
def disconnect_huggingface(
    directory_id: str, session: dict = Depends(current_session)
) -> dict:
    """Disconnect. Absent already is not an error."""
    _require_own(directory_id, session)
    db.execute(
        "delete from external_credential where directory_id = %s and provider = 'huggingface'",
        (directory_id,),
    )
    return {"connected": False}


@router.get("/directory/{directory_id}/huggingface-token")
def huggingface_status(
    directory_id: str, session: dict = Depends(current_session)
) -> dict:
    """Whether this person has a HuggingFace account connected. Never the token."""
    _require_own(directory_id, session)
    row = db.one(
        """select hf_username, connected_at from external_credential
           where directory_id = %s and provider = 'huggingface'""",
        (directory_id,),
    )
    if not row:
        return {"connected": False, "hf_username": None, "connected_at": None}
    return {
        "connected": True,
        "hf_username": row["hf_username"],
        "connected_at": row["connected_at"].isoformat(),
    }


@router.get("/directory/{directory_id}/huggingface-token/secret")
def huggingface_secret(
    directory_id: str, x_worker_token: str | None = Header(default=None)
) -> dict:
    """The plaintext token, for the worker to use while building a fetch.

    The one place in this codebase a token round-trips out of this module.
    Called only by `worker/hf_ingest_activities.py`, a process with no
    Kratos session to present, so it is gated the same way the two
    `download_code` endpoints (`agent_upload.py`, `dag_pipelines.py`) gate
    their own worker-only reads: a shared secret, not a directory identity.
    Previously not gated at all, which meant anyone who could reach this API
    could read any connected person's live HuggingFace token by guessing
    their directory id -- a real credential, usable on HuggingFace itself,
    not just platform data.
    """
    if not config.WORKER_TOKEN or x_worker_token != config.WORKER_TOKEN:
        raise HTTPException(403, {"reasons": ["missing or wrong worker token"]})
    return {"token": token_for(directory_id)}


def token_for(directory_id: str) -> str | None:
    """The plaintext token for a fetch, if this person has one connected.

    Decrypted here and only here, then handed to whichever caller is about
    to build a HuggingFace request with it. Never logged, never written
    anywhere beyond that request.
    """
    row = db.one(
        """select ec.ciphertext, ec.wrapped_key, d.tenant_id
           from external_credential ec
           join directory d on d.id = ec.directory_id
           where ec.directory_id = %s and ec.provider = 'huggingface'""",
        (directory_id,),
    )
    if not row:
        return None
    plaintext = crypto.open(
        row["tenant_id"], _record_id(directory_id), row["ciphertext"], row["wrapped_key"]
    )
    db.execute(
        """update external_credential set last_used_at = now()
           where directory_id = %s and provider = 'huggingface'""",
        (directory_id,),
    )
    return plaintext.decode()
