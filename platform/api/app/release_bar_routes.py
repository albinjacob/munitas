"""Release bars: reading them, setting them, and judging a waiting result under one.

Anybody signed in may read their own organisation's release bars, so a reviewer can choose one and everyone can see what a result was held to. Only the data protection officer creates a bar or
adds a version to it; the platform refuses anybody else and says why. Nothing is ever edited: a change is a new version, with who made it and why.
"""

from __future__ import annotations

import gate_bars
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import auth, db, gate_purposes, opa

router = APIRouter(tags=["release bars"])


class BarsIn(BaseModel):
    overall_recall: float | None = Field(default=None, description="The least share of identifiers that survived transcription and were removed, from 0 to 1. Empty for no overall limit.")
    types: dict[str, dict] = Field(default_factory=dict, description="For a kind of identifier: `recall` (the least share removed) and `max_leaks` (the most left in the clear). Either may be empty.")


class NewBarIn(BaseModel):
    name: str = Field(min_length=1, max_length=120, description="What the organisation calls the bar, for example the audience a result is released to. It cannot be changed afterwards.")
    bars: BarsIn
    reason: str = Field(min_length=1, description="Why the bar is set this way.")


class NewVersionIn(BaseModel):
    bars: BarsIn
    reason: str = Field(min_length=1, description="Why the bar is changed.")


def _may_set(identity: dict) -> tuple[bool, list[str]]:
    return opa.may_set_release_bar({"person": {"id": identity["id"], "roles": identity["roles"]}})


def _checked(bars: BarsIn) -> dict:
    wanted = {"overall_recall": bars.overall_recall, "types": bars.types}
    problems = gate_bars.check_bars(wanted)
    if problems:
        raise HTTPException(400, {"reasons": problems})
    return wanted


@router.get("/release-bars")
def release_bars(identity: dict = Depends(auth.current_session)) -> dict:
    """The release bars of the caller's organisation, each at its latest version, with the Platform default bar, the kinds of identifier a bar can limit, and whether the caller may set bars. `aliases` names the labels a run keeps its counts under that stand for one of those kinds."""
    allowed, reasons = _may_set(identity)
    return {"release_bars": gate_purposes.list_bars(identity["tenant_id"]), "platform_default": gate_purposes.default_bar(), "kinds": gate_purposes.kinds(), "aliases": gate_bars.ALIASES,
            "may_set": allowed, "may_set_reasons": reasons}


@router.get("/release-bars/{bar_id}/versions")
def release_bar_versions(bar_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """Every version of one release bar, newest first, each with the limits as they were, who set it, when and why."""
    found = gate_purposes.latest(identity["tenant_id"], bar_id)
    if not found:
        raise HTTPException(404, "no such release bar")
    return {"release_bar": found, "versions": gate_purposes.history(identity["tenant_id"], bar_id)}


@router.post("/release-bars", status_code=201)
def create_release_bar(body: NewBarIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Create a release bar, at version 1. Only the data protection officer may. The name is the organisation's own and is never changed afterwards."""
    allowed, reasons = _may_set(identity)
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    wanted = _checked(body.bars)
    try:
        made = gate_purposes.create(identity["tenant_id"], body.name, wanted, body.reason, identity["id"])
    except gate_purposes.NameTaken as exc:
        raise HTTPException(409, {"reasons": [str(exc)]})
    return {"release_bar": made}


@router.post("/release-bars/{bar_id}/versions", status_code=201)
def change_release_bar(bar_id: str, body: NewVersionIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Change a release bar by adding a version to it. Only the data protection officer may. Earlier versions stay as they were, and a decision already made keeps the version it was judged by."""
    allowed, reasons = _may_set(identity)
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    wanted = _checked(body.bars)
    made = gate_purposes.add_version(identity["tenant_id"], bar_id, wanted, body.reason, identity["id"])
    if not made:
        raise HTTPException(404, "no such release bar")
    return {"release_bar": made}


def resolve(tenant_id: str, bar_id: str | None) -> dict | None:
    """The chosen release bar of an organisation at its latest version, or None for the platform's built-in one. A bar the organisation does not have is a 404."""
    if not bar_id:
        return None
    found = gate_purposes.latest(tenant_id, bar_id)
    if not found:
        raise HTTPException(404, "no such release bar")
    return found


@router.get("/gate-decisions/{decision_id}/judgement")
def judge_waiting_result(decision_id: str, release_bar_id: str | None = None, identity: dict = Depends(auth.current_session)) -> dict:
    """What the recommendation for a result waiting for a decision would be under a release bar (the Platform default when none is named), with the bar and the reason. Nothing is recorded."""
    row = db.one("select * from gate_decision where id = %s", (decision_id,))
    if not row or row["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such gate decision")
    try:
        return gate_purposes.judge(row, resolve(identity["tenant_id"], release_bar_id))
    except gate_purposes.NotJudgeable as exc:
        raise HTTPException(409, {"reasons": [str(exc)]})
