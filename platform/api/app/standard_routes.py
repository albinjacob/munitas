"""De-identification standards: reading them, setting them, and judging a waiting result under one.

Anybody signed in may read their own organisation's de-identification standards, so a reviewer can choose one and everyone can see what a result was held to. Only the data protection officer creates a standard or
adds a version to it; the platform refuses anybody else and says why. Nothing is ever edited: a change is a new version, with who made it and why.
"""

from __future__ import annotations

import gate_standards
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import auth, db, deidentification_standards, opa

router = APIRouter(tags=["de-identification standards"])


class LimitsIn(BaseModel):
    overall_recall: float | None = Field(default=None, description="The least share of identifiers that survived transcription and were removed, from 0 to 1. Empty for no overall limit.")
    types: dict[str, dict] = Field(default_factory=dict, description="For a kind of identifier: `recall` (the least share removed) and `max_leaks` (the most left in the clear). Either may be empty.")


class NewStandardIn(BaseModel):
    name: str = Field(min_length=1, max_length=120, description="What the organisation calls the standard, for example the audience a result is released to. It cannot be changed afterwards.")
    limits: LimitsIn
    reason: str = Field(min_length=1, description="Why the standard is set this way.")


class NewVersionIn(BaseModel):
    limits: LimitsIn
    reason: str = Field(min_length=1, description="Why the standard is changed.")


def _may_set(identity: dict) -> tuple[bool, list[str]]:
    return opa.may_set_standard({"person": {"id": identity["id"], "roles": identity["roles"]}})


def _checked(limits: LimitsIn) -> dict:
    wanted = {"overall_recall": limits.overall_recall, "types": limits.types}
    problems = gate_standards.check_limits(wanted)
    if problems:
        raise HTTPException(400, {"reasons": problems})
    return wanted


@router.get("/deidentification-standards", summary="List de-identification standards")
def list_standards(identity: dict = Depends(auth.current_session)) -> dict:
    """The de-identification standards of the caller's organisation, each at its latest version, with the Platform default standard, the kinds of identifier a standard can limit, and whether the caller may set standards. `aliases` names the labels a run keeps its counts under that stand for one of those kinds."""
    allowed, reasons = _may_set(identity)
    return {"standards": deidentification_standards.list_standards(identity["tenant_id"]), "platform_default": deidentification_standards.default_standard(), "kinds": deidentification_standards.kinds(), "aliases": gate_standards.ALIASES,
            "may_set": allowed, "may_set_reasons": reasons}


@router.get("/deidentification-standards/{standard_id}/versions", summary="Versions of a de-identification standard")
def standard_versions(standard_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """Every version of one de-identification standard, newest first, each with the limits as they were, who set it, when and why."""
    found = deidentification_standards.latest(identity["tenant_id"], standard_id)
    if not found:
        raise HTTPException(404, "no such de-identification standard")
    return {"standard": found, "versions": deidentification_standards.history(identity["tenant_id"], standard_id)}


@router.post("/deidentification-standards", status_code=201, summary="Create a de-identification standard")
def create_standard(body: NewStandardIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Create a de-identification standard, at version 1. Only the data protection officer may. The name is the organisation's own and is never changed afterwards."""
    allowed, reasons = _may_set(identity)
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    wanted = _checked(body.limits)
    try:
        made = deidentification_standards.create(identity["tenant_id"], body.name, wanted, body.reason, identity["id"])
    except deidentification_standards.NameTaken as exc:
        raise HTTPException(409, {"reasons": [str(exc)]})
    return {"standard": made}


@router.post("/deidentification-standards/{standard_id}/versions", status_code=201, summary="Change a de-identification standard")
def change_standard(standard_id: str, body: NewVersionIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Change a de-identification standard by adding a version to it. Only the data protection officer may. Earlier versions stay as they were, and a decision already made keeps the version it was judged by."""
    allowed, reasons = _may_set(identity)
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    wanted = _checked(body.limits)
    made = deidentification_standards.add_version(identity["tenant_id"], standard_id, wanted, body.reason, identity["id"])
    if not made:
        raise HTTPException(404, "no such de-identification standard")
    return {"standard": made}


def resolve(tenant_id: str, standard_id: str | None) -> dict | None:
    """The chosen de-identification standard of an organisation at its latest version, or None for the platform's built-in one. A standard the organisation does not have is a 404."""
    if not standard_id:
        return None
    found = deidentification_standards.latest(tenant_id, standard_id)
    if not found:
        raise HTTPException(404, "no such de-identification standard")
    return found


@router.get("/gate-decisions/{decision_id}/judgement")
def judge_waiting_result(decision_id: str, standard_id: str | None = None, identity: dict = Depends(auth.current_session)) -> dict:
    """What the recommendation for a result waiting for a decision would be under a de-identification standard (the Platform default when none is named), with the standard and the reason. Nothing is recorded."""
    row = db.one("select * from gate_decision where id = %s", (decision_id,))
    if not row or row["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such gate decision")
    try:
        return deidentification_standards.judge(row, resolve(identity["tenant_id"], standard_id))
    except deidentification_standards.NotJudgeable as exc:
        raise HTTPException(409, {"reasons": [str(exc)]})
