"""Sponsors and re-confirmation: who answers for an agent on the business side, and when somebody last said it is still needed.

The sponsor of an agent is an approver of the department that owns it, so the person who answers for the agent is always somebody who can act on it. The newest assignment is the current
sponsor; if that person stops being an approver of the department (they left, or lost the role), the agent shows that its sponsor can no longer act, and somebody assigns another.

A risk level may ask for re-confirmation every so many days (`reconfirm_days`). The clock starts when the level was confirmed or the agent was last re-confirmed, whichever is later. Nothing is
refused because of an agent with no sponsor. A re-confirmation that is overdue stops a run only for an organisation that holds its agents to its levels.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from . import agent_chain, agent_risk, db

SOON_DAYS = 30


def _iso(moment) -> str | None:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if moment else None


def approvers_of(agent: dict) -> list[dict]:
    """The people who answer for the agent's department now, with their names, in the order they were added."""
    if not agent.get("department_id"):
        return []
    row = db.one("select active_department_approvers(%s) as a", (str(agent["department_id"]),))
    ids = list(row["a"] or []) if row else []
    if not ids:
        return []
    labels = {r["id"]: r["label"] for r in db.all_rows("select id, label from directory where id = any(%s)", (ids,))}
    return [{"id": i, "label": labels.get(i, i)} for i in ids]


def sponsorships(agent_id: str) -> list[dict]:
    return [{"id": str(r["id"]), "sponsor_id": r["sponsor_id"], "sponsor_label": r["sponsor_label"], "assigned_by": r["assigned_by"], "assigned_by_label": r["assigned_by_label"],
             "reason": r["reason"], "assigned_at": _iso(r["assigned_at"])}
            for r in db.all_rows(
                """select s.*, sp.label as sponsor_label, ab.label as assigned_by_label from agent_sponsorship s
                     left join directory sp on sp.id = s.sponsor_id left join directory ab on ab.id = s.assigned_by
                    where s.agent_id = %s order by s.assigned_at desc, s.id""", (agent_id,))]


def reconfirmations(agent_id: str) -> list[dict]:
    return [{"id": str(r["id"]), "confirmed_by": r["confirmed_by"], "confirmed_by_label": r["confirmed_by_label"], "note": r["note"], "confirmed_at": _iso(r["confirmed_at"])}
            for r in db.all_rows(
                """select c.*, who.label as confirmed_by_label from agent_reconfirmation c left join directory who on who.id = c.confirmed_by
                    where c.agent_id = %s order by c.confirmed_at desc, c.id""", (agent_id,))]


def reconfirm_clock(agent: dict, standing: dict | None = None, now: datetime | None = None) -> dict:
    """When this agent was last confirmed, how often its level asks for it again, when it is due, and whether it is overdue or due soon."""
    now = now or datetime.now(timezone.utc)
    standing = standing or agent_risk.standing(agent)
    confirmed = standing["confirmed"]
    last_re = db.one("select max(confirmed_at) as at from agent_reconfirmation where agent_id = %s", (str(agent["id"]),))
    starts = [m for m in (datetime.fromisoformat(confirmed["recorded_at"].replace("Z", "+00:00")) if confirmed else None, last_re["at"] if last_re else None) if m]
    base = max(starts) if starts else None
    days = None
    if confirmed:
        level = next((lv for lv in agent_risk.current_list(agent["tenant_id"])["levels"] if lv["key"] == confirmed["level_key"]), None)
        days = level.get("reconfirm_days") if level else None
    due = base + timedelta(days=days) if base and days else None
    # Due soon is the last fifth of the period, and never more than SOON_DAYS, so a short clock is not "soon" from the day it starts.
    window = timedelta(days=min(SOON_DAYS, max(1, (days or 0) // 5)))
    return {"days": days, "last_at": _iso(base), "due_at": _iso(due), "overdue": bool(due and now > due), "due_soon": bool(due and now <= due <= now + window)}


def state(agent: dict, standing: dict | None = None) -> dict:
    """The current sponsor, whether that person can still act, and the re-confirmation clock."""
    history = sponsorships(str(agent["id"]))
    current = history[0] if history else None
    can_act = bool(current and any(a["id"] == current["sponsor_id"] for a in approvers_of(agent)))
    return {"sponsor": current, "sponsor_can_act": can_act, "reconfirm": reconfirm_clock(agent, standing)}


def assign(agent: dict, sponsor_id: str, reason: str, by: str) -> dict:
    with db.cursor(commit=True) as cur:
        agent_chain.insert(cur, "sponsorship", str(agent["id"]), agent["tenant_id"], {
            "id": str(uuid.uuid4()), "tenant_id": agent["tenant_id"], "agent_id": str(agent["id"]), "sponsor_id": sponsor_id, "assigned_by": by, "reason": reason})
    return sponsorships(str(agent["id"]))[0]


def reconfirm(agent: dict, note: str, by: str) -> dict:
    with db.cursor(commit=True) as cur:
        agent_chain.insert(cur, "reconfirmation", str(agent["id"]), agent["tenant_id"], {
            "id": str(uuid.uuid4()), "tenant_id": agent["tenant_id"], "agent_id": str(agent["id"]), "confirmed_by": by, "note": note})
    return reconfirmations(str(agent["id"]))[0]
