"""Sponsors, re-confirmation and the check of an agent's record chain: the routes.

Anybody signed in may read who sponsors an agent and when it is due for re-confirmation. An approver of the department that owns the agent, or the AI risk owner, assigns the sponsor, who
has to be an approver of that department. The sponsor, an approver or the AI risk owner re-confirms the agent. Checking the chain is for the same people as the evidence pack.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import agent_chain, agent_risk, agent_sponsor, auth, opa
from .agent_risk_routes import agent_of, department_of

router = APIRouter(tags=["agent oversight"])


class SponsorIn(BaseModel):
    sponsor_id: str = Field(description="The person who will answer for the agent. They have to be an approver of the department that owns it.")
    reason: str = Field(min_length=1, description="Why this person.")


class ReconfirmIn(BaseModel):
    note: str = Field(min_length=1, description="What was checked: that the agent is still needed and still held at the right level.")


def _asked(identity: dict, agent: dict, sponsor: str | None) -> dict:
    return {"person": {"id": identity["id"], "roles": identity["roles"]}, "department": department_of(agent), "sponsor": sponsor or ""}


@router.get("/agents/{agent_id}/sponsorship")
def agent_sponsorship(agent_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """Who sponsors an agent and whether that person can still act, every assignment with who made it and when, the re-confirmations, when the agent is due for the next one under its level, the approvers of its department who could be sponsor, and whether the caller may assign a sponsor or re-confirm."""
    agent = agent_of(identity, agent_id)
    standing = agent_risk.standing(agent)
    current = agent_sponsor.state(agent, standing)
    sponsor_id = current["sponsor"]["sponsor_id"] if current["sponsor"] else None
    may_assign, assign_reasons = opa.may_assign_sponsor(_asked(identity, agent, sponsor_id))
    may_reconfirm, reconfirm_reasons = opa.may_reconfirm_agent(_asked(identity, agent, sponsor_id))
    return {**current, "history": agent_sponsor.sponsorships(agent_id), "reconfirmations": agent_sponsor.reconfirmations(agent_id), "candidates": agent_sponsor.approvers_of(agent),
            "may_assign": may_assign, "may_assign_reasons": assign_reasons, "may_reconfirm": may_reconfirm, "may_reconfirm_reasons": reconfirm_reasons}


@router.post("/agents/{agent_id}/sponsor", status_code=201)
def assign_sponsor(agent_id: str, body: SponsorIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Assign the sponsor of an agent. Only an approver of the department that owns the agent or the AI risk owner may. The sponsor has to be an approver of that department, so the person who answers for the agent is always somebody who can act on it."""
    agent = agent_of(identity, agent_id)
    current = agent_sponsor.state(agent)["sponsor"]
    allowed, reasons = opa.may_assign_sponsor(_asked(identity, agent, current["sponsor_id"] if current else None))
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    if body.sponsor_id not in {a["id"] for a in agent_sponsor.approvers_of(agent)}:
        raise HTTPException(400, {"reasons": ["The sponsor has to be an approver of the department that owns the agent. Choose one of them."]})
    return {"sponsor": agent_sponsor.assign(agent, body.sponsor_id, body.reason, identity["id"])}


@router.post("/agents/{agent_id}/reconfirmation", status_code=201)
def reconfirm_agent(agent_id: str, body: ReconfirmIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Re-confirm an agent: say that it is still needed and still held at the right level. Its sponsor, an approver of the department that owns it or the AI risk owner may. It restarts the clock its level sets."""
    agent = agent_of(identity, agent_id)
    current = agent_sponsor.state(agent)["sponsor"]
    allowed, reasons = opa.may_reconfirm_agent(_asked(identity, agent, current["sponsor_id"] if current else None))
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    return {"reconfirmation": agent_sponsor.reconfirm(agent, body.note, identity["id"]), **agent_sponsor.state(agent)}


@router.get("/agents/{agent_id}/record-chain")
def record_chain(agent_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """Check that the oversight records of an agent have not been changed. The platform recomputes the chain from what it holds now and says what no longer matches: a record that was edited, one that was removed, one that was added outside the platform, or a link that was altered. For the people who may make the evidence pack."""
    agent = agent_of(identity, agent_id)
    allowed, reasons = opa.may_make_evidence_pack({"person": {"id": identity["id"], "roles": identity["roles"]}, "department": department_of(agent)})
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    return agent_chain.verify(agent_id)
