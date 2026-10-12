"""The approved tools list: the routes.

Anybody signed in may read their own organisation's list. Only the AI risk owner writes it. A version of an agent that adds only listed tools, at or below each entry's ceiling, needs no
request, or only the approval of the department that owns the agent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import agent_risk, agent_tool_list, auth, opa

router = APIRouter(tags=["agent oversight"])


class ToolEntryIn(BaseModel):
    tool: str = Field(description="The name of the tool, as a version of an agent declares it: lower case letters, digits and underscores, for example `queue_summary_notification`.")
    description: str = Field(description="What the tool does, in plain words.")
    reach: str = Field(description="What kind of tool it is: `reads`, `adds_for_review`, `creates_draft` or `hands_off`.")
    tier: int = Field(description="How far it is approved: 1 for pre-approved, 2 for department approval only.")
    ceiling: str = Field(description="The key of the highest risk level it is approved for. An agent held at a stricter level is not covered.")
    conditions: str = Field(default="", description="What it is approved under, for example who confirms what it creates. Required unless the tool only reads.")
    reason: str = Field(description="Why it is approved in advance.")
    review_by: str = Field(description="The date it must be looked at again, as year-month-day. After it the entry approves nothing.")


class ToolListIn(BaseModel):
    entries: list[ToolEntryIn] = Field(description="Every tool on the list. It replaces the list, so include the entries that stay.")
    reason: str = Field(min_length=1, description="Why the list is written this way, or what changed.")


@router.get("/agent-tool-list")
def agent_tool_list_page(identity: dict = Depends(auth.current_session)) -> dict:
    """The approved tools of the caller's organisation at their latest version, every earlier version, the organisation's risk levels that an entry may be approved up to, what each tier and kind of tool means, and whether the caller may write the list."""
    allowed, reasons = opa.may_manage_tool_list({"person": {"id": identity["id"], "roles": identity["roles"]}})
    levels = agent_risk.current_list(identity["tenant_id"])["levels"]
    current = agent_tool_list.current_list(identity["tenant_id"])
    current = {**current, "entries": [{**e, "overdue": agent_tool_list.is_overdue(e)} for e in current["entries"]]}
    return {
        "current": current,
        "history": agent_tool_list.list_history(identity["tenant_id"]),
        "levels": [{"key": lv["key"], "name": lv["name"], "blocks": lv["blocks"]} for lv in levels],
        "tiers": {str(k): v for k, v in agent_tool_list.TIERS.items()},
        "reach": agent_tool_list.REACH,
        "tier_one_reach": sorted(agent_tool_list.TIER_1_REACH),
        "may_manage": allowed,
        "may_manage_reasons": reasons,
    }


@router.post("/agent-tool-list", status_code=201)
def write_agent_tool_list(body: ToolListIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Write the organisation's approved tools list as a new version. Only the AI risk owner may. Earlier versions stay. A change takes effect for versions registered afterwards: a version already registered keeps the decision it was given."""
    allowed, reasons = opa.may_manage_tool_list({"person": {"id": identity["id"], "roles": identity["roles"]}})
    if not allowed:
        raise HTTPException(403, {"decided": False, "reasons": reasons})
    levels = agent_risk.current_list(identity["tenant_id"])["levels"]
    entries, problems = agent_tool_list.check_entries([e.model_dump() for e in body.entries], levels)
    if problems:
        raise HTTPException(400, {"reasons": problems})
    return {"current": agent_tool_list.save_list(identity["tenant_id"], entries, body.reason.strip(), identity["id"])}
