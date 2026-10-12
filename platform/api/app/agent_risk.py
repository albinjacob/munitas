"""Risk levels: the list an organisation writes, and the level each of its agents is held at.

The levels belong to the organisation, not to the platform and not to any one law. Each has a name, a description, an optional label saying which framework it corresponds to (typed by the
organisation), whether widening an agent at that level needs a second approver, and whether an agent at that level may be deployed at all. The list is in order from least to most
strict, and a change adds a version and edits nothing.

The person who registers an agent proposes a level; the AI risk owner confirms or changes it. An agent with no confirmed level is held, for every approval, at the strictest
level that is not blocked. When the organisation has turned enforcement on, such an agent cannot start a run.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import timezone

from . import agent_chain, db

BUILT_IN_LEVELS = [
    {"key": "low", "name": "Low", "description": "An agent whose output is checked by its reader and whose reach is small.", "framework_label": None, "second_approver": False, "blocks": False, "reconfirm_days": None},
    {"key": "moderate", "name": "Moderate", "description": "An agent whose output informs a decision a person makes.", "framework_label": None, "second_approver": False, "blocks": False, "reconfirm_days": None},
    {"key": "high", "name": "High", "description": "An agent whose output can change what happens to a person, or that acts outside the platform.", "framework_label": None, "second_approver": True, "blocks": False, "reconfirm_days": None},
    {"key": "not_allowed", "name": "Not allowed", "description": "An agent the organisation will not run. No version of it can be deployed.", "framework_label": None, "second_approver": False, "blocks": True, "reconfirm_days": None},
]


def _iso(moment) -> str | None:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if moment else None


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def check_levels(levels: list[dict]) -> tuple[list[dict], list[str]]:
    """The levels in the shape they are kept in, and what is wrong with them in words. The key of a level is made from its name."""
    problems: list[str] = []
    out: list[dict] = []
    seen: set[str] = set()
    for i, level in enumerate(levels or [], start=1):
        name = (level.get("name") or "").strip()
        if not name:
            problems.append(f"Level {i} has no name.")
            continue
        key = slug(name)
        if not key:
            problems.append(f"The name of level {i} needs letters or numbers.")
            continue
        if key in seen:
            problems.append(f"Two levels are called {name!r}. Each level needs its own name.")
            continue
        seen.add(key)
        description = (level.get("description") or "").strip()
        if not description:
            problems.append(f"{name} needs a description, so that whoever proposes a level can tell it from the others.")
        label = (level.get("framework_label") or "").strip() or None
        days = level.get("reconfirm_days")
        if days in ("", 0):
            days = None
        if days is not None and (isinstance(days, bool) or not isinstance(days, int) or days < 1 or days > 3650):
            problems.append(f"{name}: the days between re-confirmations must be a whole number from 1 to 3650, or empty for never.")
            days = None
        out.append({"key": key, "name": name, "description": description, "framework_label": label, "second_approver": bool(level.get("second_approver")), "blocks": bool(level.get("blocks")),
                    "reconfirm_days": days})
    if not out and not problems:
        problems.append("Write at least one level.")
    if out and all(level["blocks"] for level in out):
        problems.append("At least one level has to allow an agent to be deployed.")
    return out, problems


def current_list(tenant_id: str) -> dict:
    """The organisation's levels at their latest version, or the built-in list when it has written none."""
    row = db.one(
        """select l.version, l.levels, l.enforce, l.reason, l.set_by, l.set_at, who.label as set_by_label
             from agent_risk_level_list l left join directory who on who.id = l.set_by
            where l.tenant_id = %s order by l.version desc limit 1""", (tenant_id,))
    if not row:
        return {"version": None, "levels": BUILT_IN_LEVELS, "enforce": False, "reason": None, "set_by": None, "set_by_label": None, "set_at": None, "built_in": True}
    return {"version": row["version"], "levels": row["levels"], "enforce": row["enforce"], "reason": row["reason"], "set_by": row["set_by"], "set_by_label": row["set_by_label"],
            "set_at": _iso(row["set_at"]), "built_in": False}


def list_history(tenant_id: str) -> list[dict]:
    return [{"version": r["version"], "levels": r["levels"], "enforce": r["enforce"], "reason": r["reason"], "set_by": r["set_by"], "set_by_label": r["set_by_label"], "set_at": _iso(r["set_at"])}
            for r in db.all_rows(
                """select l.version, l.levels, l.enforce, l.reason, l.set_by, l.set_at, who.label as set_by_label
                     from agent_risk_level_list l left join directory who on who.id = l.set_by
                    where l.tenant_id = %s order by l.version desc""", (tenant_id,))]


def save_list(tenant_id: str, levels: list[dict], enforce: bool, reason: str, by: str) -> dict:
    row = db.one("select coalesce(max(version), 0) as v from agent_risk_level_list where tenant_id = %s", (tenant_id,))
    version = row["v"] + 1
    db.execute("insert into agent_risk_level_list (tenant_id, version, levels, enforce, reason, set_by) values (%s, %s, %s, %s, %s, %s)",
               (tenant_id, version, json.dumps(levels), enforce, reason, by))
    return current_list(tenant_id)


def strictest_allowed(levels: list[dict]) -> dict:
    """The last level in the list that lets an agent be deployed: what an agent with no confirmed level is held at."""
    allowed = [level for level in levels if not level["blocks"]]
    return allowed[-1] if allowed else levels[-1]


def _record_shown(r: dict) -> dict:
    return {"id": str(r["id"]), "kind": r["kind"], "level_key": r["level_key"], "level_name": r["level_name"], "framework_label": r["framework_label"], "second_approver": r["second_approver"],
            "blocks": r["blocks"], "list_version": r["list_version"], "intended_purpose": r["intended_purpose"], "affected": r["affected"], "reasoning": r["reasoning"],
            "recorded_by": r["recorded_by"], "recorded_by_label": r.get("recorded_by_label"), "recorded_at": _iso(r["recorded_at"])}


def records(agent_id: str) -> list[dict]:
    """Every proposal and decision about an agent's level, newest first."""
    return [_record_shown(r) for r in db.all_rows(
        """select r.*, who.label as recorded_by_label from agent_risk_record r left join directory who on who.id = r.recorded_by
            where r.agent_id = %s order by r.recorded_at desc, r.id""", (agent_id,))]


def standing(agent: dict) -> dict:
    """Where an agent stands: its confirmed level, any proposal waiting for the officer, and the level an approval holds it to."""
    rows = records(str(agent["id"]))
    confirmed = next((r for r in rows if r["kind"] == "confirmed"), None)
    newest_proposal = next((r for r in rows if r["kind"] == "proposed"), None)
    waiting = newest_proposal if newest_proposal and (not confirmed or newest_proposal["recorded_at"] > confirmed["recorded_at"]) else None
    levels = current_list(agent["tenant_id"])
    strict = strictest_allowed(levels["levels"])
    return {
        "classified": confirmed is not None,
        "confirmed": confirmed,
        "proposal_waiting": waiting,
        "held_at": {"key": confirmed["level_key"], "name": confirmed["level_name"], "second_approver": confirmed["second_approver"], "blocks": confirmed["blocks"]} if confirmed
                   else {"key": strict["key"], "name": strict["name"], "second_approver": strict["second_approver"], "blocks": False},
        "held_at_because": "its confirmed level" if confirmed else "no level has been confirmed, so it is held at the strictest level that allows deployment",
        "enforce": levels["enforce"],
        "history": rows,
    }


def record(agent: dict, kind: str, level: dict, list_version: int | None, purpose: str, affected: str, reasoning: str, by: str, cur=None) -> dict:
    """Keep a proposal or a decision about an agent's level, in the agent's chain. Pass `cur` to do it inside a transaction the caller owns."""
    values = {"id": str(uuid.uuid4()), "tenant_id": agent["tenant_id"], "agent_id": str(agent["id"]), "kind": kind, "level_key": level["key"], "level_name": level["name"],
              "framework_label": level.get("framework_label"), "second_approver": level["second_approver"], "blocks": level["blocks"], "list_version": list_version,
              "intended_purpose": purpose, "affected": affected, "reasoning": reasoning, "recorded_by": by}
    if cur is None:
        with db.cursor(commit=True) as own:
            row = agent_chain.insert(own, "risk_record", str(agent["id"]), agent["tenant_id"], values)
    else:
        row = agent_chain.insert(cur, "risk_record", str(agent["id"]), agent["tenant_id"], values)
    who = db.one("select label from directory where id = %s", (by,))
    return _record_shown({**row, "recorded_by_label": who["label"] if who else None})


def level_named(tenant_id: str, key: str) -> tuple[dict, int | None] | None:
    current = current_list(tenant_id)
    for level in current["levels"]:
        if level["key"] == key:
            return level, current["version"]
    return None
