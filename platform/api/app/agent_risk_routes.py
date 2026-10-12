"""Risk levels and the approval of a widening: the routes.

Anybody signed in may read their own organisation's levels and approvals. Only the AI risk owner writes the list of levels or confirms the level of an agent; the person who
registers an agent proposes its level. A version that widens an agent is approved by an approver of the department that owns the agent, and by the AI risk owner where the level asks for it.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pgdb import errors as pg_errors
from pydantic import BaseModel, Field

from . import agent_chain, agent_changes, agent_risk, agent_words, auth, db, opa

router = APIRouter(tags=["agent oversight"])


def agent_of(identity: dict, agent_id: str) -> dict:
    """An agent of the caller's organisation, or a 404."""
    try:
        uuid.UUID(str(agent_id))
    except ValueError:
        raise HTTPException(404, "no such agent")
    agent = db.one("select * from agent where id = %s", (agent_id,))
    if not agent or agent["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such agent")
    return agent


def department_approvers(agent: dict) -> list[str]:
    if not agent.get("department_id"):
        return []
    row = db.one("select active_department_approvers(%s) as a", (str(agent["department_id"]),))
    return list(row["a"] or []) if row else []


def department_of(agent: dict) -> dict:
    name = None
    if agent.get("department_id"):
        row = db.one("select name from department where id = %s", (str(agent["department_id"]),))
        name = row["name"] if row else None
    approvers = department_approvers(agent)
    # The policy compares people by id and says them by name, so a refusal reads as a sentence about people.
    names = [r["label"] for r in db.all_rows("select label from directory where id = any(%s) order by label", (approvers,))] if approvers else []
    return {"name": name or "no department", "approvers": approvers, "approver_names": names}


class LevelIn(BaseModel):
    name: str = Field(description="What the organisation calls the level.")
    description: str = Field(description="What an agent at this level is like, so whoever proposes a level can tell it from the others.")
    framework_label: str | None = Field(default=None, description="Which framework, law or standard this level corresponds to, in the organisation's own words. Optional.")
    second_approver: bool = Field(default=False, description="Whether widening an agent at this level needs the AI risk owner as well as the department.")
    blocks: bool = Field(default=False, description="Whether an agent at this level may not be deployed at all.")
    reconfirm_days: int | None = Field(default=None, description="How many days may pass before somebody has to re-confirm an agent at this level. Empty for never.")


class RiskLevelsIn(BaseModel):
    levels: list[LevelIn] = Field(description="The levels in order from least to most strict.")
    enforce: bool = Field(default=False, description="Whether an agent needs a confirmed level to start a run, and a version that widens an agent needs approval before it is deployed.")
    reason: str = Field(min_length=1, description="Why the levels are written this way.")


@router.get("/risk-levels")
def risk_levels(identity: dict = Depends(auth.current_session)) -> dict:
    """The risk levels of the caller's organisation at their latest version, every earlier version, and whether the caller may write them. An organisation that has written none uses the built-in list, which says so."""
    allowed, reasons = opa.may_manage_risk_levels({"person": {"id": identity["id"], "roles": identity["roles"]}})
    return {"current": agent_risk.current_list(identity["tenant_id"]), "history": agent_risk.list_history(identity["tenant_id"]), "may_manage": allowed, "may_manage_reasons": reasons,
            "manage_words": agent_words.words_for("manage_risk_levels_roles"), "confirm_words": agent_words.words_for("confirm_risk_level_roles")}


@router.post("/risk-levels", status_code=201)
def write_risk_levels(body: RiskLevelsIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Write the organisation's risk levels as a new version. Only the AI risk owner may. Earlier versions stay, and an agent keeps the level it was confirmed at."""
    allowed, reasons = opa.may_manage_risk_levels({"person": {"id": identity["id"], "roles": identity["roles"]}})
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    levels, problems = agent_risk.check_levels([lv.model_dump() for lv in body.levels])
    if problems:
        raise HTTPException(400, {"reasons": problems})
    return {"current": agent_risk.save_list(identity["tenant_id"], levels, body.enforce, body.reason, identity["id"])}


class ProposalIn(BaseModel):
    level_key: str = Field(description="The key of the level proposed, from the organisation's list.")
    intended_purpose: str = Field(min_length=1, description="What the agent is for.")
    affected: str = Field(min_length=1, description="Who is affected by what the agent produces.")
    reasoning: str = Field(min_length=1, description="Why this level.")


class ConfirmationIn(BaseModel):
    level_key: str = Field(description="The key of the level confirmed, from the organisation's list. It may differ from the one proposed.")
    reasoning: str = Field(min_length=1, description="Why this level.")
    intended_purpose: str | None = Field(default=None, description="What the agent is for. Taken from the proposal when there is one.")
    affected: str | None = Field(default=None, description="Who is affected by what the agent produces. Taken from the proposal when there is one.")


@router.get("/agents/{agent_id}/risk")
def agent_risk_standing(agent_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """Where an agent stands on risk: its confirmed level, any proposal waiting for the AI risk owner, the level its approvals are held at, every proposal and decision with who and when, the organisation's levels, and whether the caller may confirm."""
    agent = agent_of(identity, agent_id)
    allowed, reasons = opa.may_confirm_risk_level({"person": {"id": identity["id"], "roles": identity["roles"]}, "agent": {"registered_by": agent["registered_by"]}})
    return {**agent_risk.standing(agent), "levels": agent_risk.current_list(agent["tenant_id"]), "may_confirm": allowed, "may_confirm_reasons": reasons, "confirm_words": agent_words.words_for("confirm_risk_level_roles")}


@router.post("/agents/{agent_id}/risk/proposals", status_code=201)
def propose_level(agent_id: str, body: ProposalIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Propose a risk level for an agent. The AI risk owner confirms or changes it. Until it is confirmed the agent is held at the strictest level that allows deployment."""
    agent = agent_of(identity, agent_id)
    auth.require_code_registration_role(identity)
    found = agent_risk.level_named(agent["tenant_id"], body.level_key)
    if not found:
        raise HTTPException(400, {"reasons": [f"{body.level_key!r} is not one of this organisation's risk levels"]})
    level, list_version = found
    return {"record": agent_risk.record(agent, "proposed", level, list_version, body.intended_purpose, body.affected, body.reasoning, identity["id"])}


@router.post("/agents/{agent_id}/risk/confirmations", status_code=201)
def confirm_level(agent_id: str, body: ConfirmationIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Confirm or change the risk level of an agent. Only the AI risk owner may, and never for an agent the AI risk owner registered. Either way it is recorded with who and when."""
    agent = agent_of(identity, agent_id)
    allowed, reasons = opa.may_confirm_risk_level({"person": {"id": identity["id"], "roles": identity["roles"]}, "agent": {"registered_by": agent["registered_by"]}})
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    found = agent_risk.level_named(agent["tenant_id"], body.level_key)
    if not found:
        raise HTTPException(400, {"reasons": [f"{body.level_key!r} is not one of this organisation's risk levels"]})
    level, list_version = found
    standing = agent_risk.standing(agent)
    proposal = standing["proposal_waiting"] or next((r for r in standing["history"] if r["kind"] == "proposed"), None)
    purpose = (body.intended_purpose or "").strip() or (proposal["intended_purpose"] if proposal else agent["purpose"])
    affected = (body.affected or "").strip() or (proposal["affected"] if proposal else "")
    if not affected:
        raise HTTPException(400, {"reasons": ["Say who is affected by what this agent produces: no proposal gives it."]})
    return {"record": agent_risk.record(agent, "confirmed", level, list_version, purpose, affected, body.reasoning, identity["id"])}


class ChangeDecisionIn(BaseModel):
    part: str = Field(description="`department` for the approval of the department that owns the agent, or `officer` for the officer's part, which the AI risk owner gives.")
    decision: str = Field(description="`approved` or `refused`.")
    reason: str = Field(min_length=1, description="Why, which is kept with the decision.")


def _decider_input(identity: dict, approval: dict, agent: dict, part: str) -> dict:
    return {"part": part, "approver": {"id": identity["id"], "roles": identity["roles"]}, "approval": {"submitted_by": approval["submitted_by"]}, "department": department_of(agent)}


def _with_permissions(identity: dict, shown: dict, agent: dict) -> dict:
    """An approval with, for each part still to give, whether the caller may give it and, if not, why."""
    can: dict = {}
    if shown["state"] == "waiting":
        parts = ["department"] + (["officer"] if shown["needs_officer"] else [])
        done = {d["part"] for d in shown["decisions"] if d["decision"] == "approved"}
        for part in parts:
            if part in done:
                continue
            allowed, reasons = opa.may_decide_agent_change(_decider_input(identity, {"submitted_by": shown["submitted_by"]}, agent, part))
            can[part] = {"allowed": allowed, "reasons": reasons}
    return {**shown, "can_decide": can}


class FirstSubmissionIn(BaseModel):
    level_key: str = Field(description="The risk level confirmed, from the organisation's list. It may differ from the one proposed.")
    reasoning: str = Field(min_length=1, description="Why this level, and why the first version is approved. Kept with both decisions.")
    affected: str | None = Field(default=None, description="Who is affected by what the agent produces. Taken from the proposal when there is one.")


@router.post("/agent-change-approvals/{approval_id}/first-submission", status_code=201)
def decide_first_submission(approval_id: str, body: FirstSubmissionIn, identity: dict = Depends(auth.current_session)) -> dict:
    """The AI risk owner's one decision on an agent's first submission: confirm its risk level and approve its first version together. Both are recorded, in one step, with the same reason. The department's approval is still its own decision. Only for a first version, and only while the officer's part is still to give."""
    shown = agent_changes.one_approval(identity["tenant_id"], approval_id)
    if not shown:
        raise HTTPException(404, "no such approval")
    if shown["first_submission"] is None:
        raise HTTPException(409, {"reasons": ["this is not an agent's first version, so its level is not decided with it"]})
    if shown["state"] != "waiting" or not shown["needs_officer"] or any(d["part"] == "officer" for d in shown["decisions"]):
        raise HTTPException(409, {"reasons": ["there is no officer's decision waiting on this version"]})
    agent = db.one("select * from agent where id = %s", (shown["agent_id"],))
    allowed, reasons = opa.may_decide_agent_change(_decider_input(identity, {"submitted_by": shown["submitted_by"]}, agent, "officer"))
    if allowed:
        allowed, reasons = opa.may_confirm_risk_level({"person": {"id": identity["id"], "roles": identity["roles"]}, "agent": {"registered_by": agent["registered_by"]}})
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    found = agent_risk.level_named(agent["tenant_id"], body.level_key)
    if not found:
        raise HTTPException(400, {"reasons": [f"{body.level_key!r} is not one of this organisation's risk levels"]})
    level, list_version = found
    proposal = shown["first_submission"]["proposal"]
    purpose = proposal["intended_purpose"] if proposal else agent["purpose"]
    affected = (body.affected or "").strip() or (proposal["affected"] if proposal else "")
    if not affected:
        raise HTTPException(400, {"reasons": ["Say who is affected by what this agent produces: no proposal gives it."]})
    try:
        with db.cursor(commit=True) as cur:
            agent_risk.record(agent, "confirmed", level, list_version, purpose, affected, body.reasoning, identity["id"], cur=cur)
            agent_chain.insert(cur, "change_decision", shown["agent_id"], identity["tenant_id"], {
                "id": str(uuid.uuid4()), "approval_id": approval_id, "tenant_id": identity["tenant_id"], "part": "officer", "decision": "approved", "reason": body.reasoning,
                "submitted_by": shown["submitted_by"], "decided_by": identity["id"]})
    except pg_errors.CheckViolation as exc:
        raise HTTPException(403, {"decided": False, "reasons": ["the person who submitted a version cannot decide on it"]}) from exc
    return {"approval": _with_permissions(identity, agent_changes.one_approval(identity["tenant_id"], approval_id), agent)}


@router.get("/agent-change-approvals")
def change_approvals(state: str | None = None, identity: dict = Depends(auth.current_session)) -> dict:
    """Every version of the organisation's agents with what it changed against the one before it, newest first, with the state of its approval and, for a part still to give, whether the caller may give it. `state` limits the list to `waiting`, `approved`, `approved_in_advance` (every tool the version adds is on the approved tools list), `refused` or `not_needed`. `enforce` says whether the organisation holds agents to its risk levels: when it does not, a version that widens an agent is shown as waiting and nothing is refused because of it."""
    out = []
    agents_seen: dict = {}
    for shown in agent_changes.for_tenant(identity["tenant_id"]):
        if state and shown["state"] != state:
            continue
        agent = agents_seen.get(shown["agent_id"]) or db.one("select * from agent where id = %s", (shown["agent_id"],))
        agents_seen[shown["agent_id"]] = agent
        out.append(_with_permissions(identity, shown, agent))
    return {"approvals": out, "enforce": agent_risk.current_list(identity["tenant_id"])["enforce"]}


@router.get("/agents/{agent_id}/change-approvals")
def agent_change_approvals(agent_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """What each version of one agent changed against the one before it, newest first, with the state of its approval."""
    agent = agent_of(identity, agent_id)
    return {"approvals": [_with_permissions(identity, s, agent) for s in agent_changes.for_agent(identity["tenant_id"], agent_id)], "enforce": agent_risk.current_list(identity["tenant_id"])["enforce"]}


@router.post("/agent-change-approvals/{approval_id}/decisions", status_code=201)
def decide_change(approval_id: str, body: ChangeDecisionIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Approve or refuse a version that widens an agent, as the department that owns it or as the officer, which is the AI risk owner. Nobody decides a version they submitted. Any refusal refuses the version."""
    if body.part not in ("department", "officer") or body.decision not in ("approved", "refused"):
        raise HTTPException(400, {"reasons": ["`part` is department or officer, and `decision` is approved or refused"]})
    shown = agent_changes.one_approval(identity["tenant_id"], approval_id)
    if not shown:
        raise HTTPException(404, "no such approval")
    if shown["state"] != "waiting":
        raise HTTPException(409, {"reasons": [f"this approval is already {shown['state'].replace('_', ' ')}"]})
    if body.part == "officer" and not shown["needs_officer"]:
        raise HTTPException(409, {"reasons": ["this agent's level does not ask for the officer's approval"]})
    if any(d["part"] == body.part for d in shown["decisions"]):
        raise HTTPException(409, {"reasons": ["that part has already been decided"]})
    agent = db.one("select * from agent where id = %s", (shown["agent_id"],))
    allowed, reasons = opa.may_decide_agent_change(_decider_input(identity, {"submitted_by": shown["submitted_by"]}, agent, body.part))
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    try:
        with db.cursor(commit=True) as cur:
            agent_chain.insert(cur, "change_decision", shown["agent_id"], identity["tenant_id"], {
                "id": str(uuid.uuid4()), "approval_id": approval_id, "tenant_id": identity["tenant_id"], "part": body.part, "decision": body.decision, "reason": body.reason,
                "submitted_by": shown["submitted_by"], "decided_by": identity["id"]})
    except pg_errors.CheckViolation as exc:
        raise HTTPException(403, {"decided": False, "reasons": ["the person who submitted a version cannot decide on it"]}) from exc
    return {"approval": _with_permissions(identity, agent_changes.one_approval(identity["tenant_id"], approval_id), agent)}
