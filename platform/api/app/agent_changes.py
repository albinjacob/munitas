"""What a new version of an agent changes, and whether it has to be approved before it can run.

When a version is registered the platform compares it with the version before it (or with nothing, for the first) and writes down each change and whether it widens what the agent can
do. A widening is approved by an approver of the department that owns the agent, and also by the AI risk owner where the agent's level asks for a second approver. A
narrowing is recorded and needs nobody. The state of an approval is worked out from the decisions made, so nothing is ever updated.

Tools are named by the owner. The platform knows what its own tools do and holds an unknown tool at the strictest kind until it knows better: it cannot tell from a name whether a
tool only reads or sends something out.
"""

from __future__ import annotations

import json
import uuid
from datetime import timezone

from . import agent_chain, agent_risk, agent_tool_list, agent_words, db

# What the platform's own tools do. Anything else is a tool the platform does not know.
KNOWN_TOOLS = {
    "read_dataset_version": ("reads_more", "reads the details of a dataset version"),
    "summarise_counts": ("reads_more", "reads the counts of a dataset version"),
    "list_review_queue": ("reads_more", "reads the items waiting for review"),
    "fetch_url": ("reaches_host", "fetches a web address"),
}

# Strictest first. The approval screen puts the strictest kind first and says it in these words.
KINDS = {
    "acts_outside": (1, "Acts outside the platform, or may"),
    "reaches_host": (2, "Reaches a host outside the platform"),
    "not_declared": (2, "No longer says which tools it uses"),
    "reads_more": (3, "Reads more"),
    "new_model": (4, "Uses a different model"),
    "narrows": (9, "Narrower than before"),
}


def _iso(moment) -> str | None:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if moment else None


def _facts(version: dict | None) -> dict:
    """What a version declares, with nothing for no version."""
    if not version:
        return {"tools": set(), "declared": True, "model": None, "hosting": None, "hosts": set(), "data": "none"}
    return {
        "tools": set(version.get("tool_scope") or []),
        "declared": bool(version.get("tools_declared")),
        "model": version.get("model_id"),
        "hosting": version.get("model_hosting"),
        "hosts": set(version.get("requested_hosts") or []),
        "data": version.get("data_access") or "none",
    }


def _change(kind: str, what: str, widens: bool = True, tool: str | None = None) -> dict:
    change = {"kind": kind, "rank": KINDS[kind][0], "label": KINDS[kind][1], "what": what, "widens": widens}
    if tool:   # the name of a tool this change adds, so that the approved tools list can say whether it covers the change
        change["tool"] = tool
    return change


def diff(previous: dict | None, new: dict) -> list[dict]:
    """The changes of `new` against `previous` (None for a first version), strictest first. Each has a kind, its rank, a label, what changed in words, and whether it widens."""
    before, after = _facts(previous), _facts(new)
    changes: list[dict] = []

    for tool in sorted(after["tools"] - before["tools"]):
        kind, does = KNOWN_TOOLS.get(tool, ("acts_outside", ""))
        what = f"Adds the tool {tool}, which {does}." if does else f"Adds the tool {tool}. The platform does not know what it does, so it is treated as one that may act outside the platform."
        changes.append(_change(kind, what, tool=tool))
    for tool in sorted(before["tools"] - after["tools"]):
        changes.append(_change("narrows", f"Removes the tool {tool}.", widens=False))

    if before["declared"] and not after["declared"] and previous is not None:
        changes.append(_change("not_declared", "The earlier version said which tools it uses. This one does not say, so nothing limits the tools it calls."))
    if not before["declared"] and after["declared"] and previous is not None and not after["tools"]:
        changes.append(_change("narrows", "Now says it uses no tools.", widens=False))

    for host in sorted(after["hosts"] - before["hosts"]):
        changes.append(_change("reaches_host", f"Asks to call {host}. A network architect approves hosts separately."))
    for host in sorted(before["hosts"] - after["hosts"]):
        changes.append(_change("narrows", f"No longer asks to call {host}.", widens=False))

    if before["data"] == "none" and after["data"] == "copy":
        changes.append(_change("reads_more", "Is given a copy of the data it is run on, which the earlier version was not."))
    if before["data"] == "copy" and after["data"] == "none":
        changes.append(_change("narrows", "Is no longer given a copy of the data it is run on.", widens=False))

    if previous is not None and before["model"] != after["model"]:
        changes.append(_change("new_model", f"Uses the model {after['model']} where the earlier version used {before['model']}."))
    if after["hosting"] == "outside" and before["hosting"] != "outside":
        changes.append(_change("new_model", "Its model runs outside the platform, where the platform cannot tell when it changes."))

    changes.sort(key=lambda c: (c["rank"], c["what"]))
    return changes


def previous_version(agent_id: str, version: int) -> dict | None:
    return db.one("select * from agent_version where agent_id = %s and version < %s order by version desc limit 1", (agent_id, version))


def record_version(agent: dict, version_id: str, submitted_by: str) -> dict:
    """Compare a version just registered with the one before it and write down what changed. Called by every way of registering a version."""
    new = db.one("select * from agent_version where id = %s", (version_id,))
    previous = previous_version(str(agent["id"]), new["version"])
    changes = diff(previous, new)
    widening = any(c["widens"] for c in changes)
    standing = agent_risk.standing(agent)
    # The approved tools list never covers a first version: the agent's level is decided together with it, by the AI risk owner, whatever tools it declares.
    covered = agent_tool_list.coverage(agent, changes, standing) if previous is not None else {"all_covered": False, "inside": False, "department_only": False, "list_version": None}   # adds a `covered_by` note to each tool change it covers
    needs_officer = standing["held_at"]["second_approver"] and not (covered["inside"] or covered["department_only"])
    approval_id = str(uuid.uuid4())
    with db.cursor(commit=True) as cur:
        agent_chain.insert(cur, "change_approval", str(agent["id"]), agent["tenant_id"], {
            "id": approval_id, "tenant_id": agent["tenant_id"], "agent_id": str(agent["id"]), "agent_version_id": version_id, "previous_version_id": str(previous["id"]) if previous else None,
            "changes": json.dumps(changes), "widening": widening, "needs_officer": needs_officer, "inside_envelope": True if (widening and covered["inside"]) else None, "submitted_by": submitted_by})
    return {"id": approval_id, "widening": widening, "changes": changes}


def decisions(approval_id: str) -> list[dict]:
    return [{"part": r["part"], "decision": r["decision"], "reason": r["reason"], "decided_by": r["decided_by"], "decided_by_label": r["decided_by_label"], "decided_at": _iso(r["decided_at"]),
             # Who the decision was for, in words: the department, or the role the person gave the officer's part as.
             "decided_as": "the department that owns the agent" if r["part"] == "department" else agent_words.officer_role_of(r["decided_by_roles"])}
            for r in db.all_rows(
                """select d.*, who.label as decided_by_label, who.roles as decided_by_roles from agent_change_decision d left join directory who on who.id = d.decided_by
                    where d.approval_id = %s order by d.decided_at""", (approval_id,))]


def state_of(approval: dict, made: list[dict]) -> str:
    """not_needed (nothing widened), approved_in_advance (every tool it adds is on the approved list), refused, approved, or waiting."""
    if not approval["widening"]:
        return "not_needed"
    if approval.get("inside_envelope"):
        return "approved_in_advance"   # every tool it adds is on the approved tools list, so nobody was asked
    if any(d["decision"] == "refused" for d in made):
        return "refused"
    parts = {d["part"] for d in made if d["decision"] == "approved"}
    if "department" in parts and (not approval["needs_officer"] or "officer" in parts):
        return "approved"
    return "waiting"


def waiting_for(approval: dict, made: list[dict]) -> list[str]:
    """The parts still to give their approval, in words."""
    parts = {d["part"] for d in made if d["decision"] == "approved"}
    out = []
    if "department" not in parts:
        out.append("an approver of the department that owns the agent")
    if approval["needs_officer"] and "officer" not in parts:
        out.append(agent_words.officer_words())
    return out


def first_submission(approval: dict) -> dict | None:
    """For a first version: the level proposed with it and whether a level is confirmed, so the officer decides both together. None for any later version."""
    if approval.get("previous_version_id") is not None:
        return None
    records = agent_risk.records(str(approval["agent_id"]))
    return {"proposal": next((r for r in records if r["kind"] == "proposed"), None), "confirmed": next((r for r in records if r["kind"] == "confirmed"), None)}


def shown(approval: dict) -> dict:
    made = decisions(str(approval["id"]))
    state = state_of(approval, made)
    return {
        "first_submission": first_submission(approval),
        "id": str(approval["id"]), "agent_id": str(approval["agent_id"]), "agent_name": approval.get("agent_name"), "department_name": approval.get("department_name"),
        "agent_version_id": str(approval["agent_version_id"]), "version": approval.get("version"), "previous_version": approval.get("previous_number"),
        "changes": approval["changes"], "widening": approval["widening"], "needs_officer": approval["needs_officer"], "inside_envelope": approval["inside_envelope"],
        "submitted_by": approval["submitted_by"], "submitted_by_label": approval.get("submitted_by_label"), "created_at": _iso(approval["created_at"]),
        "state": state, "waiting_for": waiting_for(approval, made) if state == "waiting" else [], "decisions": made, "officer_words": agent_words.officer_words(),
    }


_BASE = """select a.*, ag.name as agent_name, dept.name as department_name, v.version, pv.version as previous_number, who.label as submitted_by_label
             from agent_change_approval a
             join agent ag on ag.id = a.agent_id
             left join department dept on dept.id = ag.department_id
             join agent_version v on v.id = a.agent_version_id
             left join agent_version pv on pv.id = a.previous_version_id
             left join directory who on who.id = a.submitted_by
            where a.tenant_id = %s"""


def for_tenant(tenant_id: str) -> list[dict]:
    return [shown(r) for r in db.all_rows(_BASE + " order by a.created_at desc", (tenant_id,))]


def for_agent(tenant_id: str, agent_id: str) -> list[dict]:
    return [shown(r) for r in db.all_rows(_BASE + " and a.agent_id = %s order by v.version desc", (tenant_id, agent_id))]


def one_approval(tenant_id: str, approval_id: str) -> dict | None:
    try:
        uuid.UUID(str(approval_id))
    except ValueError:
        return None
    row = db.one(_BASE + " and a.id = %s", (tenant_id, approval_id))
    return shown(row) if row else None


def for_version(version_id: str) -> dict | None:
    row = db.one("select a.*, null::text as agent_name, null::text as department_name, null::int as version, null::int as previous_number, null::text as submitted_by_label "
                 "from agent_change_approval a where a.agent_version_id = %s", (version_id,))
    return shown(row) if row else None


def deploy_refusal(agent: dict, version_id: str) -> list[str]:
    """Why this version cannot be deployed because of its level or an approval, as sentences; empty when nothing stands in the way."""
    standing = agent_risk.standing(agent)
    reasons: list[str] = []
    if standing["confirmed"] and standing["confirmed"]["blocks"]:
        reasons.append(f"This agent's level is {standing['confirmed']['level_name']}, which the organisation does not allow to be deployed.")
    if standing["enforce"]:
        found = for_version(version_id)
        if found and found["state"] in ("waiting", "refused"):
            if found["state"] == "refused":
                reasons.append("This version widens what the agent can do and the widening was refused.")
            else:
                reasons.append("This version widens what the agent can do, and it waits for " + " and ".join(found["waiting_for"]) + ".")
    return reasons
