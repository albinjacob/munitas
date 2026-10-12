"""Overseeing agents: suspending one, reporting an incident, the registry of every agent, the evidence pack, and refusing what a run produced.

Anybody signed in may read their own organisation's registry. Who may suspend, resume or report is decided by the policy engine and the refusal says why. The evidence pack holds
identifiers, counts, hashes and times, never the content an agent read or wrote.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import agent_changes, agent_chain, agent_oversight, agent_risk, agent_sponsor, auth, db, opa
from .agent_risk_routes import agent_of, department_of

router = APIRouter(tags=["agent oversight"])

STALE_DAYS = 90


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, description="Why, which is kept with the act.")


class IncidentIn(BaseModel):
    what: str = Field(min_length=1, description="What happened. It holds no personal data.")
    reported_onward: str | None = Field(default=None, description="Whom the organisation has told, if anyone, such as a provider or an authority. The platform sends nothing.")
    action_taken: str | None = Field(default=None, description="What was done about it.")


def _person(identity: dict, agent: dict) -> dict:
    return {"person": {"id": identity["id"], "roles": identity["roles"]}, "department": department_of(agent)}


@router.get("/agents/{agent_id}/oversight")
def agent_oversight_view(agent_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """Whether an agent is suspended and the history of that, its incidents, how the people who review its output have decided (counted for the agent, never by person), and whether the caller may suspend, resume or report."""
    agent = agent_of(identity, agent_id)
    standing = agent_risk.standing(agent)
    may_suspend, suspend_reasons = opa.may_suspend_agent(_person(identity, agent))
    may_resume, resume_reasons = opa.may_resume_agent({**_person(identity, agent), "requires_officer": bool(standing["held_at"]["second_approver"])})
    may_report, report_reasons = opa.may_report_incident(_person(identity, agent))
    return {
        "suspension": agent_oversight.suspension(agent_id), "suspension_history": agent_oversight.suspensions(agent_id), "incidents": agent_oversight.incidents(agent_id),
        "review_figures": agent_oversight.oversight_figures(agent_id),
        "may_suspend": may_suspend, "may_suspend_reasons": suspend_reasons, "may_resume": may_resume, "may_resume_reasons": resume_reasons,
        "may_report": may_report, "may_report_reasons": report_reasons,
    }


@router.post("/agents/{agent_id}/suspension", status_code=201)
def suspend(agent_id: str, body: ReasonIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Suspend an agent: no new run starts, and every run of it that has not finished is halted, with the reason. Who may is decided by policy."""
    agent = agent_of(identity, agent_id)
    allowed, reasons = opa.may_suspend_agent(_person(identity, agent))
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    if agent_oversight.suspension(agent_id)["suspended"]:
        raise HTTPException(409, {"reasons": ["this agent is already suspended"]})
    with db.cursor(commit=True) as cur:
        agent_chain.insert(cur, "suspension", agent_id, agent["tenant_id"], {"id": str(uuid.uuid4()), "tenant_id": agent["tenant_id"], "agent_id": agent_id, "action": "suspended", "reason": body.reason, "acted_by": identity["id"]})
    halted = agent_oversight.halt_live_runs(agent_id, f"the agent was suspended: {body.reason}")
    return {"suspension": agent_oversight.suspension(agent_id), "runs_halted": halted}


@router.post("/agents/{agent_id}/resumption", status_code=201)
def resume(agent_id: str, body: ReasonIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Let a suspended agent start runs again. Where the agent's level asks for a second approver, only the AI risk owner may."""
    agent = agent_of(identity, agent_id)
    standing = agent_risk.standing(agent)
    allowed, reasons = opa.may_resume_agent({**_person(identity, agent), "requires_officer": bool(standing["held_at"]["second_approver"])})
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    if not agent_oversight.suspension(agent_id)["suspended"]:
        raise HTTPException(409, {"reasons": ["this agent is not suspended"]})
    with db.cursor(commit=True) as cur:
        agent_chain.insert(cur, "suspension", agent_id, agent["tenant_id"], {"id": str(uuid.uuid4()), "tenant_id": agent["tenant_id"], "agent_id": agent_id, "action": "resumed", "reason": body.reason, "acted_by": identity["id"]})
    return {"suspension": agent_oversight.suspension(agent_id)}


@router.post("/agents/{agent_id}/incidents", status_code=201)
def report_incident(agent_id: str, body: IncidentIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Record an incident about an agent: what happened, whom the organisation has told, and what was done. The platform sends nothing to anybody."""
    agent = agent_of(identity, agent_id)
    allowed, reasons = opa.may_report_incident(_person(identity, agent))
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    with db.cursor(commit=True) as cur:
        agent_chain.insert(cur, "incident", agent_id, agent["tenant_id"], {"id": str(uuid.uuid4()), "tenant_id": agent["tenant_id"], "agent_id": agent_id, "what": body.what,
                                                                           "reported_onward": body.reported_onward, "action_taken": body.action_taken, "reported_by": identity["id"]})
    return {"incidents": agent_oversight.incidents(agent_id)}


class ReviewIn(BaseModel):
    outcome: str = Field(description="`accepted` (as it was), `changed` (the reviewer ended with different text) or `refused`.")
    final_text: str | None = Field(default=None, description="For `changed`: the text the reviewer ended with. The platform keeps its hash and never the text.")
    reason: str | None = Field(default=None, description="Why. Needed for `refused`.")


@router.post("/agents/runs/{run_id}/review", status_code=201)
def review_finished_run(run_id: str, body: ReviewIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Review what a finished run produced: accept it as it was, change it, or refuse it. A person other than the one who started the run does it, once per run. The platform keeps who, when, how long it took, the hash of the draft (worked out from the run's output, never taken from the caller) and, for a change, the hash of the text the reviewer ended with. It never keeps the text. A run that waits at an approval step is approved or refused there instead."""
    run = db.one("select * from agent_run where id = %s", (run_id,))
    if not run or run["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such run")
    if body.outcome not in ("accepted", "changed", "refused"):
        raise HTTPException(400, {"reasons": ["`outcome` is accepted, changed or refused"]})
    if run["status"] == "awaiting_approval":
        raise HTTPException(400, {"reasons": ["this run waits at its approval step: approve or refuse it there"]})
    if run["status"] != "succeeded" or not (run["findings"] or []):
        raise HTTPException(400, {"reasons": [f"this run is {run['status']}" + (" and produced nothing" if run["status"] == "succeeded" else "") + ", so there is nothing to review"]})
    if identity["id"] == run["requested_by"]:
        raise HTTPException(403, {"reasons": ["what a run produced cannot be reviewed by the person who started it; somebody else has to look at it"]})
    if db.one("select id from agent_output_review where run_id = %s", (run_id,)):
        raise HTTPException(409, {"reasons": ["this run's output has already been reviewed"]})
    final_hash = None
    if body.outcome == "refused" and not (body.reason or "").strip():
        raise HTTPException(400, {"reasons": ["Say why it is refused."]})
    if body.outcome == "changed":
        text = (body.final_text or "").strip()
        if not text:
            raise HTTPException(400, {"reasons": ["Give the text you ended with."]})
        draft = agent_oversight.draft_text_of(run)
        if draft is not None and text == draft.strip():
            raise HTTPException(400, {"reasons": ["That is the draft as it was. Accept it instead."]})
        final_hash = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    done = agent_oversight.record_review(run, body.outcome, final_hash, (body.reason or "").strip() or None, identity["id"])
    return {"review": done}


@router.post("/agents/runs/{run_id}/refuse", status_code=201)
def refuse_run(run_id: str, body: ReasonIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Refuse what a run produced while it waits for approval. The run stops, the reason is kept, and the review is recorded with who, when and how long it took."""
    run = db.one("select * from agent_run where id = %s", (run_id,))
    if not run or run["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such run")
    if run["status"] != "awaiting_approval":
        raise HTTPException(400, {"reasons": [f"this run is {run['status']}, so there is nothing waiting to be refused"]})
    review = agent_oversight.record_review(run, "refused", None, body.reason, identity["id"])
    db.execute("update agent_run set status = 'halted', halted_reason = %s, ended_at = now() where id = %s and status = 'awaiting_approval'", (f"refused: {body.reason}", run_id))
    return {"run_id": run_id, "status": "halted", "review": review}


def _flags(agent: dict, standing: dict, active: dict | None, newest: dict | None, widening_waiting: int, last_run, suspended: bool, sponsor: dict) -> list[dict]:
    flags: list[dict] = []
    if sponsor["sponsor"] is None:
        flags.append({"key": "no_sponsor", "label": "No sponsor"})
    elif not sponsor["sponsor_can_act"]:
        flags.append({"key": "sponsor_cannot_act", "label": "Its sponsor can no longer act"})
    if sponsor["reconfirm"]["overdue"]:
        flags.append({"key": "reconfirm_overdue", "label": "Re-confirmation is overdue"})
    elif sponsor["reconfirm"]["due_soon"]:
        flags.append({"key": "reconfirm_soon", "label": "Re-confirmation is due soon"})
    if suspended:
        flags.append({"key": "suspended", "label": "Suspended"})
    if not standing["classified"]:
        flags.append({"key": "no_level", "label": "No confirmed risk level"})
    if active and not active["tools_declared"]:
        flags.append({"key": "tools_not_declared", "label": "Tools not declared"})
    if widening_waiting:
        flags.append({"key": "widening_waiting", "label": "A change waits for approval"})
    if active and newest and newest["version"] > active["version"]:
        flags.append({"key": "behind", "label": "A newer version has not been deployed"})
    if active and active["model_hosting"] == "outside":
        flags.append({"key": "model_outside", "label": "Its model runs outside the platform"})
    if agent["run_by"] == "outside":
        flags.append({"key": "run_outside", "label": "Run outside the platform"})
    # An agent that has not run for a long time, or that was registered a long time ago and never has. A brand-new agent that has not run yet is not idle.
    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)
    if (last_run is not None and last_run < cutoff) or (last_run is None and agent["created_at"] < cutoff):
        flags.append({"key": "idle", "label": f"No run in {STALE_DAYS} days"})
    return flags


def _tools_text(version: dict | None) -> dict:
    if not version:
        return {"state": "no version", "list": []}
    if not version["tools_declared"]:
        return {"state": "not declared", "list": []}
    return {"state": "uses no tools" if not version["tool_scope"] else "declared", "list": list(version["tool_scope"] or [])}


@router.get("/agent-registry")
def agent_registry(identity: dict = Depends(auth.current_session)) -> dict:
    """Every agent of the organisation in one list: its department, owner, purpose, confirmed risk level, active version, tools as declared, model and where it runs, who runs it, its last run, what waits for approval, and what needs attention."""
    rows = db.all_rows(
        """select a.*, dept.name as department_name, who.label as registered_by_label from agent a
             left join department dept on dept.id = a.department_id left join directory who on who.id = a.registered_by
            where a.tenant_id = %s order by lower(a.name)""", (identity["tenant_id"],))
    out = []
    for agent in rows:
        aid = str(agent["id"])
        standing = agent_risk.standing(agent)
        active = db.one("select v.* from agent_active_version av join agent_version v on v.id = av.agent_version_id where av.agent_id = %s", (aid,))
        newest = db.one("select * from agent_version where agent_id = %s order by version desc limit 1", (aid,))
        waiting = sum(1 for a in agent_changes.for_agent(identity["tenant_id"], aid) if a["state"] == "waiting")
        last = db.one("select max(started_at) as at from agent_run where agent_id = %s", (aid,))
        suspension = agent_oversight.suspension(aid)
        sponsor = agent_sponsor.state(agent, standing)
        confirmed = standing["confirmed"]
        out.append({
            "id": aid, "name": agent["name"], "department_name": agent["department_name"], "registered_by": agent["registered_by"], "registered_by_label": agent["registered_by_label"],
            "purpose": agent["purpose"], "run_by": agent["run_by"],
            "risk_level": ({"name": confirmed["level_name"], "framework_label": confirmed["framework_label"], "confirmed_at": confirmed["recorded_at"]} if confirmed else None),
            "proposal_waiting": bool(standing["proposal_waiting"]),
            "active_version": active["version"] if active else None, "newest_version": newest["version"] if newest else None,
            "tools": _tools_text(active), "model_id": active["model_id"] if active else None, "model_hosting": (active["model_hosting"] or "not declared") if active else None,
            "hosts": list(active["requested_hosts"] or []) if active else [], "last_run_at": agent_oversight._iso(last["at"]) if last and last["at"] else None,
            "changes_waiting": waiting, "suspended": suspension["suspended"],
            "sponsor": sponsor["sponsor"]["sponsor_label"] if sponsor["sponsor"] else None, "reconfirm_due_at": sponsor["reconfirm"]["due_at"],
            "flags": _flags(agent, standing, active, newest, waiting, last["at"] if last else None, suspension["suspended"], sponsor),
        })
    return {"agents": out, "stale_after_days": STALE_DAYS, "enforce": agent_risk.current_list(identity["tenant_id"])["enforce"]}


@router.get("/agents/{agent_id}/evidence-pack")
def evidence_pack(agent_id: str, start: str | None = None, end: str | None = None, identity: dict = Depends(auth.current_session)) -> dict:
    """Everything the platform can back about one agent over a period (the last 90 days unless dates are given), for an approver of its department, the AI risk owner, the data protection officer, a privacy auditor or a platform administrator: identity, risk level history, every version that ran with what it changed and who approved, deployments, runs, access decisions counted, how reviewers decided, suspensions, incidents, the release decision of the data it read, and which facts are declared by the owner and which the platform enforces. It holds no personal data. Making it is recorded, with a hash of what was handed over."""
    agent = agent_of(identity, agent_id)
    allowed, reasons = opa.may_make_evidence_pack(_person(identity, agent))
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    try:
        to = datetime.fromisoformat(end.replace("Z", "+00:00")) if end else datetime.now(timezone.utc)
        since = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else to - timedelta(days=90)
    except ValueError:
        raise HTTPException(400, {"reasons": ["dates are written like 2026-10-01 or 2026-10-01T09:00:00Z"]})
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if to.tzinfo is None:
        to = to.replace(tzinfo=timezone.utc)
    if since >= to:
        raise HTTPException(400, {"reasons": ["the start of the period has to be before its end"]})
    pack = agent_oversight.evidence_pack(agent, since, to, identity["id"])
    pack_hash = agent_oversight.sha256_of(pack)
    db.execute("insert into agent_evidence_export (id, tenant_id, agent_id, period_from, period_to, pack_hash, exported_by) values (%s, %s, %s, %s, %s, %s, %s)",
               (str(uuid.uuid4()), agent["tenant_id"], agent_id, since, to, pack_hash, identity["id"]))
    return {"pack": pack, "pack_hash": pack_hash}
