"""The routes of the notebook sign-in: the notebook's three (start, ask whether approved, renew), the person's four in the console (code, sign-in, approval, and
the list with its Revoke), and nothing else. The rules are in `notebook_sign_in`.

The notebook's routes take no session: the notebook has none yet. What they take is what only that notebook holds (the device code it was given), and they are
limited by address. The person's routes take the console session, and so does the list, which an auditor or the data protection officer may read for the whole
organisation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import auth, notebook_sign_in

router = APIRouter(tags=["notebook sign-in"])

AUDITORS = {"dpo", "privacy_auditor"}


class StartIn(BaseModel):
    label: str = Field(default="a notebook", max_length=80, description="What the notebook calls itself, for example the machine's name. Shown to the person who approves it.")


class PollIn(BaseModel):
    device_code: str


class RenewIn(BaseModel):
    refresh_token: str


class CodeIn(BaseModel):
    device_challenge: str
    user_code: str = Field(min_length=4, max_length=32)


class ChallengeIn(BaseModel):
    login_challenge: str


class DecisionIn(BaseModel):
    consent_challenge: str
    approve: bool


@router.post("/notebook/v1/sign-in", status_code=201)
def start(body: StartIn, request: Request) -> dict:
    """A notebook asks to sign in. Returns the code to show the person, the address to open, and the device code to ask with. Nobody is identified yet."""
    return notebook_sign_in.start(request, body.label)


@router.post("/notebook/v1/sign-in/token")
def poll(body: PollIn) -> JSONResponse:
    """The notebook asks whether the person has approved. 200 with the pass once they have; 400 with `authorization_pending`, `slow_down`, `access_denied` or `expired_token`."""
    status, payload = notebook_sign_in.poll(body.device_code)
    return JSONResponse(payload, status_code=status)


@router.post("/notebook/v1/sign-in/refresh")
def renew(body: RenewIn) -> JSONResponse:
    """The notebook renews its pass with the renewal token it holds. The old renewal token stops working, and using it again ends the sign-in."""
    status, payload = notebook_sign_in.renew(body.refresh_token)
    return JSONResponse(payload, status_code=status)


@router.post("/notebook-sign-in/code")
def code(body: CodeIn, identity: dict = Depends(auth.current_session)) -> dict:
    """The person types the code the notebook is showing. Only a code this platform started is accepted, and only a few wrong ones."""
    return notebook_sign_in.accept_code(identity, body.device_challenge, body.user_code)


@router.post("/notebook-sign-in/login")
def login(body: ChallengeIn, identity: dict = Depends(auth.current_session)) -> dict:
    """The sign-in step: the console session is the sign-in. Refused when the person is not the one who typed the code."""
    return notebook_sign_in.login(identity, body.login_challenge)


@router.get("/notebook-sign-in/consent")
def consent(consent_challenge: str, identity: dict = Depends(auth.current_session)) -> dict:
    """What the person is asked to approve: the notebook, where it is, and what it will and will not be able to do."""
    return notebook_sign_in.consent_details(identity, consent_challenge)


@router.post("/notebook-sign-in/consent")
def decide(body: DecisionIn, identity: dict = Depends(auth.current_session)) -> dict:
    """The person approves or refuses. Both are recorded with who and when."""
    return notebook_sign_in.decide(identity, body.consent_challenge, body.approve)


@router.get("/notebook-sign-ins")
def mine(everyone: bool = False, identity: dict = Depends(auth.current_session)) -> dict:
    """The person's own notebook sign-ins, newest first. `everyone=true` lists the whole organisation's, for the data protection officer and the privacy auditor only."""
    if everyone and not (AUDITORS & set(identity["roles"])):
        raise HTTPException(403, {"reasons": ["only the data protection officer and the privacy auditor read everybody's notebook sign-ins"]})
    return {"sign_ins": notebook_sign_in.list_for(identity, everyone)}


@router.post("/notebook-sign-ins/revoke-all")
def revoke_all(identity: dict = Depends(auth.current_session)) -> dict:
    """The person ends every notebook sign-in they have, at once. A notebook's own pass cannot call this: only the console can."""
    return notebook_sign_in.revoke_all(identity)


@router.post("/notebook-sign-ins/{sign_in_id}/revoke")
def revoke(sign_in_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """The person ends their notebook sign-in. The pass and the renewal token stop at once."""
    return notebook_sign_in.revoke(identity, sign_in_id)
