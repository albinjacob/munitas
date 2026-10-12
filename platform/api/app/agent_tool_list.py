"""The approved tools list: tools an organisation has approved in advance, each with the highest risk level it is approved for.

A version of an agent that adds only tools on the list, at or below each entry's ceiling, does not wait for the people who would otherwise approve it. A tool that is "pre-approved" needs
nobody. A tool that needs "department approval only" waits for an approver of the department that owns the agent and not for the officer. Any other widening, and any tool that is not on the
list, is decided in the normal way.

What the list is: a record of what the organisation decided and who decided it. For uploaded code the names of tools are declarations by the owner, so an entry says what the owner of the tool
says it does. Hosts and data are controlled separately and are never covered by the list. Every change to the list is a new version with who made it, when and why; the earlier versions stay.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

from . import agent_risk, db

# How far an entry is approved, in the words the page uses.
TIERS = {1: "Pre-approved", 2: "Department approval only"}

# What a tool does, in the words the page uses. A tool that acts outside the platform with nobody in between is never put on the list: it is decided one case at a time.
REACH = {
    "reads": "Reads data and changes nothing",
    "adds_for_review": "Adds an item to a queue that a person works through",
    "creates_draft": "Creates a draft or a request that a person confirms",
    "hands_off": "Hands a record to a service that decides what happens next",
}

# Which kinds of tool each tier may hold. A tool that hands a record to a service, or creates something a person confirms, always has the department's say.
TIER_1_REACH = {"reads", "adds_for_review"}

MAX_ENTRIES = 100
LONGEST_REVIEW_DAYS = 400   # an entry has to be looked at again within about a year

_TOOL = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def _iso(moment) -> str | None:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if moment else None


def today() -> date:
    return datetime.now(timezone.utc).date()


def check_entries(entries: list[dict], levels: list[dict]) -> tuple[list[dict], list[str]]:
    """The entries in the shape they are kept in, and what is wrong with them in words."""
    problems: list[str] = []
    out: list[dict] = []
    seen: set[str] = set()
    keys = {lv["key"]: lv for lv in levels}
    if len(entries or []) > MAX_ENTRIES:
        problems.append(f"A list holds at most {MAX_ENTRIES} tools.")
    for i, entry in enumerate(entries or [], start=1):
        tool = (entry.get("tool") or "").strip()
        if not _TOOL.match(tool):
            problems.append(f"Tool {i}: a name is lower case letters, digits and underscores, starts with a letter and has at least three characters, for example read_dataset_version.")
            continue
        if tool in seen:
            problems.append(f"{tool} is on the list twice.")
            continue
        seen.add(tool)
        description = (entry.get("description") or "").strip()
        reason = (entry.get("reason") or "").strip()
        conditions = (entry.get("conditions") or "").strip()
        reach = entry.get("reach")
        tier = entry.get("tier")
        ceiling = entry.get("ceiling")
        if not description:
            problems.append(f"{tool} needs a description of what it does.")
        if not reason:
            problems.append(f"{tool} needs a reason for approving it in advance.")
        if reach not in REACH:
            problems.append(f"{tool}: say what it does. It is one of: {', '.join(REACH)}.")
        if tier not in TIERS:
            problems.append(f"{tool}: how far it is approved is 1 (pre-approved) or 2 (department approval only).")
        elif tier == 1 and reach in REACH and reach not in TIER_1_REACH:
            problems.append(f"{tool}: a tool that {REACH[reach][0].lower() + REACH[reach][1:]} always needs the department's approval, so it is tier 2.")
        if reach in REACH and reach != "reads" and not conditions:
            problems.append(f"{tool} needs the conditions it is approved under, for example who confirms what it creates.")
        level = keys.get(ceiling)
        if level is None:
            problems.append(f"{tool}: the highest level it is approved for must be one of the organisation's risk levels.")
        elif level["blocks"]:
            problems.append(f"{tool}: {level['name']} does not allow an agent to be deployed, so nothing can be approved up to it.")
        review = entry.get("review_by")
        try:
            review_on = date.fromisoformat(str(review))
        except ValueError:
            problems.append(f"{tool}: give the date it must be looked at again, as year-month-day.")
            review_on = None
        if review_on is not None:
            if review_on <= today():
                problems.append(f"{tool}: the date it must be looked at again is already past.")
            elif review_on > today() + timedelta(days=LONGEST_REVIEW_DAYS):
                problems.append(f"{tool}: it must be looked at again within {LONGEST_REVIEW_DAYS} days.")
        out.append({"tool": tool, "description": description, "reach": reach, "tier": tier, "ceiling": ceiling, "conditions": conditions, "reason": reason, "review_by": str(review)})
    if not out and not problems and entries:
        problems.append("Nothing was written.")
    return out, problems


def current_list(tenant_id: str) -> dict:
    """The organisation's approved tools at their latest version, or an empty list when it has written none."""
    row = db.one(
        """select l.version, l.entries, l.reason, l.set_by, l.set_at, who.label as set_by_label
             from agent_tool_list l left join directory who on who.id = l.set_by
            where l.tenant_id = %s order by l.version desc limit 1""", (tenant_id,))
    if not row:
        return {"version": None, "entries": [], "reason": None, "set_by": None, "set_by_label": None, "set_at": None}
    return {"version": row["version"], "entries": row["entries"], "reason": row["reason"], "set_by": row["set_by"], "set_by_label": row["set_by_label"], "set_at": _iso(row["set_at"])}


def list_history(tenant_id: str) -> list[dict]:
    return [{"version": r["version"], "entries": r["entries"], "reason": r["reason"], "set_by": r["set_by"], "set_by_label": r["set_by_label"], "set_at": _iso(r["set_at"])}
            for r in db.all_rows(
                """select l.version, l.entries, l.reason, l.set_by, l.set_at, who.label as set_by_label
                     from agent_tool_list l left join directory who on who.id = l.set_by
                    where l.tenant_id = %s order by l.version desc""", (tenant_id,))]


def save_list(tenant_id: str, entries: list[dict], reason: str, by: str) -> dict:
    row = db.one("select coalesce(max(version), 0) as v from agent_tool_list where tenant_id = %s", (tenant_id,))
    version = row["v"] + 1
    db.execute("insert into agent_tool_list (tenant_id, version, entries, reason, set_by) values (%s, %s, %s, %s, %s)", (tenant_id, version, json.dumps(entries), reason, by))
    return current_list(tenant_id)


def is_overdue(entry: dict) -> bool:
    """An entry whose review date has passed no longer approves anything."""
    try:
        return date.fromisoformat(entry["review_by"]) < today()
    except (KeyError, ValueError):
        return True


def coverage(agent: dict, changes: list[dict], standing: dict | None = None) -> dict:
    """Whether the approved tools list covers everything a version widens, and what it saves the approvers.

    Covered means every widening is a tool added, and each tool is on the list, not overdue for review, and approved up to the level the agent is held at or a stricter one. Then, if every
    tool is pre-approved the version needs nobody (`inside`), and if any needs the department only the officer's part is not asked for (`department_only`). Anything else is decided in the
    normal way. Each tool added gets a `covered_by` note naming the entry and the version of the list, so the record says why nobody was asked.
    """
    widening = [c for c in changes if c.get("widens")]
    result = {"all_covered": False, "inside": False, "department_only": False, "list_version": None}
    if not widening or any(not c.get("tool") for c in widening):
        return result
    standing = standing or agent_risk.standing(agent)
    levels = agent_risk.current_list(agent["tenant_id"])["levels"]
    order = {lv["key"]: i for i, lv in enumerate(levels)}
    held = standing["held_at"]
    if held["key"] not in order or held.get("blocks"):
        return result
    listed = current_list(agent["tenant_id"])
    entries = {e["tool"]: e for e in listed["entries"]}
    notes: list[tuple[dict, dict]] = []
    for change in widening:
        entry = entries.get(change["tool"])
        if entry is None or is_overdue(entry) or entry.get("tier") not in TIERS:
            return result
        if entry["ceiling"] not in order or order[held["key"]] > order[entry["ceiling"]]:
            return result
        notes.append((change, entry))
    tiers = {entry["tier"] for _, entry in notes}
    ceiling_name = {lv["key"]: lv["name"] for lv in levels}
    for change, entry in notes:
        change["covered_by"] = {
            "tool": entry["tool"], "tier": entry["tier"], "tier_name": TIERS[entry["tier"]], "ceiling": entry["ceiling"], "ceiling_name": ceiling_name.get(entry["ceiling"], entry["ceiling"]),
            "conditions": entry["conditions"], "reason": entry["reason"], "list_version": listed["version"], "set_by_label": listed["set_by_label"], "set_at": listed["set_at"]}
    return {"all_covered": True, "inside": tiers == {1}, "department_only": 2 in tiers, "list_version": listed["version"]}
