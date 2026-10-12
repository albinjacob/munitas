"""Overseeing an agent: whether it may start a run, a person's review of what it produced, and what an organisation can show about it.

Nothing here holds personal data. A review keeps hashes and times, never the text reviewed; the evidence pack keeps identifiers, counts, hashes and times. An agent the platform only
describes ("outside") has its facts recorded as declared by its owner and is never run.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from . import agent_chain, agent_changes, agent_risk, agent_sponsor, agent_words, db

LIVE = ("running", "awaiting_access", "awaiting_approval", "awaiting_activation")


def _iso(moment) -> str | None:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if moment else None


def sha256_of(value) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def suspension(agent_id: str) -> dict:
    """Whether an agent is suspended now, and by whom, when and why."""
    row = db.one(
        """select s.action, s.reason, s.acted_by, s.acted_at, who.label as acted_by_label from agent_suspension s left join directory who on who.id = s.acted_by
            where s.agent_id = %s order by s.acted_at desc, s.id desc limit 1""", (agent_id,))
    if not row:
        return {"suspended": False, "reason": None, "by": None, "by_label": None, "at": None}
    return {"suspended": row["action"] == "suspended", "reason": row["reason"], "by": row["acted_by"], "by_label": row["acted_by_label"], "at": _iso(row["acted_at"])}


def suspensions(agent_id: str) -> list[dict]:
    return [{"action": r["action"], "reason": r["reason"], "by": r["acted_by"], "by_label": r["acted_by_label"], "at": _iso(r["acted_at"])}
            for r in db.all_rows(
                """select s.*, who.label as acted_by_label from agent_suspension s left join directory who on who.id = s.acted_by
                    where s.agent_id = %s order by s.acted_at desc, s.id desc""", (agent_id,))]


def incidents(agent_id: str) -> list[dict]:
    return [{"id": str(r["id"]), "what": r["what"], "reported_onward": r["reported_onward"], "action_taken": r["action_taken"], "reported_by": r["reported_by"],
             "reported_by_label": r["reported_by_label"], "reported_at": _iso(r["reported_at"])}
            for r in db.all_rows(
                """select i.*, who.label as reported_by_label from agent_incident i left join directory who on who.id = i.reported_by
                    where i.agent_id = %s order by i.reported_at desc""", (agent_id,))]


def start_refusals(agent: dict) -> list[str]:
    """Why this agent may not start a run now, as sentences; empty when nothing stands in the way."""
    reasons: list[str] = []
    if agent.get("run_by") == "outside":
        reasons.append("This agent is run outside the platform. The platform keeps its record and does not start it.")
    state = suspension(str(agent["id"]))
    if state["suspended"]:
        reasons.append(f"This agent is suspended: {state['reason']}")
    standing = agent_risk.standing(agent)
    if standing["enforce"] and not standing["classified"]:
        reasons.append("This agent has no confirmed risk level yet. " + agent_words.sentence_start(agent_words.words_for("confirm_risk_level_roles")) + " confirms one after the person who registered it proposes it.")
    if standing["confirmed"] and standing["confirmed"]["blocks"]:
        reasons.append(f"This agent's level is {standing['confirmed']['level_name']}, which the organisation does not allow to run.")
    clock = agent_sponsor.reconfirm_clock(agent, standing)
    if standing["enforce"] and clock["overdue"]:
        reasons.append(f"This agent was due to be re-confirmed on {clock['due_at'][:10]}. Its sponsor, an approver of its department or " + agent_words.officer_words() + " re-confirms it.")
    return reasons


def halt_live_runs(agent_id: str, reason: str) -> int:
    """Mark every run of this agent that has not finished as halted, with the reason. Its read key lapses with its status."""
    marks = ", ".join(["%s"] * len(LIVE))
    with db.cursor(commit=True) as cur:
        cur.execute(f"update agent_run set status = 'halted', halted_reason = %s, ended_at = now() where agent_id = %s and status in ({marks})", (reason, agent_id, *LIVE))
        return cur.rowcount


def draft_hash_of(run: dict) -> str:
    """The hash of what the run produced, worked out here from its findings and never taken from the caller."""
    return sha256_of(run.get("findings") or [])


def draft_text_of(run: dict) -> str | None:
    """The text of what a run drafted, when its output is drafts with a note each: the notes, one after another. None for any other output, which the platform does not read."""
    for found in run.get("findings") or []:
        drafts = found.get("drafts") if isinstance(found, dict) else None
        if isinstance(drafts, list) and drafts and all(isinstance(d, dict) and isinstance(d.get("note"), str) for d in drafts):
            return "\n\n".join(d["note"] for d in drafts)
    return None


def record_review(run: dict, outcome: str, final_hash: str | None, reason: str | None, by: str) -> dict:
    """Keep a person's review of what a run produced: who, when, how long, and the hash of the draft and of what they ended with."""
    version = db.one("select model_id from agent_version where id = %s", (str(run["agent_version_id"]),))
    paused = run.get("ended_at") or run.get("awaiting_activation_since") or run["started_at"]
    seconds = max(0, int((datetime.now(timezone.utc) - paused).total_seconds()))
    row_id = str(uuid.uuid4())
    with db.cursor(commit=True) as cur:
        agent_chain.insert(cur, "output_review", str(run["agent_id"]), run["tenant_id"], {
            "id": row_id, "tenant_id": run["tenant_id"], "run_id": str(run["id"]), "agent_id": str(run["agent_id"]), "agent_version_id": str(run["agent_version_id"]),
            "model_id": version["model_id"] if version else "unknown", "outcome": outcome, "draft_hash": draft_hash_of(run), "final_hash": final_hash, "reason": reason,
            "reviewed_by": by, "seconds_taken": seconds})
    return {"id": row_id, "outcome": outcome, "seconds_taken": seconds}


def oversight_figures(agent_id: str, start=None, end=None) -> dict:
    """How the people who review what an agent produces have decided, counted for the agent and never by person."""
    clauses, params = ["agent_id = %s"], [agent_id]
    if start:
        clauses.append("reviewed_at >= %s")
        params.append(start)
    if end:
        clauses.append("reviewed_at <= %s")
        params.append(end)
    where = " and ".join(clauses)
    row = db.one(
        f"""select count(*) as total,
                   count(*) filter (where outcome = 'accepted') as accepted,
                   count(*) filter (where outcome = 'changed') as changed,
                   count(*) filter (where outcome = 'refused') as refused,
                   count(distinct reviewed_by) as people,
                   percentile_cont(0.5) within group (order by seconds_taken) as median_seconds
              from agent_output_review where {where}""", tuple(params))
    return {"reviewed": row["total"], "accepted_unchanged": row["accepted"], "changed": row["changed"], "refused": row["refused"], "different_people": row["people"],
            "median_seconds": round(float(row["median_seconds"])) if row["median_seconds"] is not None else None}


FRAMEWORK_SOURCES = [
    "NIST AI Risk Management Framework, core functions and categories (Govern, Map, Measure, Manage), read on 2026-10-11 from airc.nist.gov.",
    "Regulation (EU) 2024/1689, Articles 9, 12, 14 and 26 as published, read on 2026-10-11 from artificialintelligenceact.eu. Check against the Official Journal.",
]

# Which outcome each framework asks for, which sections of this pack support it, and how. `provided` is "records" (the platform keeps records that support the outcome),
# "declared" (the platform keeps what the owner says and does not check it) or "not provided". Whether the sections are in the pack is worked out when the pack is made, not stated here.
FRAMEWORK_ROWS = [
    ("Somebody is named as accountable for the agent", "GOVERN 2", "Article 26 (oversight assigned to people with the competence and authority)", ["agent", "sponsorship"], "records",
     "The pack names the owning department, who registered the agent and its sponsor."),
    ("The agent is categorised and its effect on people is stated", "MAP 2, MAP 5", "Article 9 (identifying and evaluating risks)", ["risk_level"], "records",
     "The level, who is affected and the reasoning are the organisation's own words and judgement. The platform records who confirmed them and when."),
    ("Risks are tracked over time", "MEASURE 3, MANAGE 4", "Article 9 (a continuous, repeated process)", ["reconfirmations", "versions", "incidents"], "records",
     "Re-confirmations, each version with what it changed, and incidents."),
    ("Outputs are tested for quality", "MEASURE 2", "Article 9 (testing)", [], "not provided",
     "Munitas limits what an agent can read and reach. It does not test whether an output is right."),
    ("Events are recorded automatically", "MANAGE 4", "Article 12 (record-keeping)", ["runs", "deployments", "access_decisions", "integrity"], "records",
     "Each run has a start and an end. The oversight records are chained so that a change can be seen. Article 12 lists what logs must capture for some systems. This does not claim to meet it."),
    ("People overseeing the agent can understand its output and decide not to use it", "GOVERN 2", "Article 14 (human oversight)", ["human_oversight"], "records",
     "A person accepts, changes or refuses each output, and the counts show how often. Counted for the agent, never for a person."),
    ("A person can stop the agent", "none named", "Article 14 (intervene or stop), Article 26 (suspend use)", ["suspensions"], "records",
     "Suspension, with who, when and why, and the runs it halted."),
    ("Incidents are recorded and reported onward", "MANAGE 4", "Article 26 (report to the provider and the authorities)", ["incidents"], "records",
     "The platform records what the organisation says it did and whom it told. It sends nothing to anybody."),
    ("Changes are managed across the agent's life", "MANAGE 1, MANAGE 2", "Article 9 (lifecycle)", ["versions"], "records",
     "Every version, what it changed against the one before it, and who approved a widening."),
    ("Outside and third-party agents are identified", "GOVERN 6, MANAGE 3", "none named", ["agent", "declared_and_enforced"], "declared",
     "An agent run outside the platform is recorded as such. Its tools and model are what its owner declares, not checked."),
    ("Logs are kept for a stated period", "none named", "Article 26 (at least six months where the organisation controls the logs)", ["integrity"], "records",
     "The platform does not delete these records on a schedule. The organisation sets how long it keeps a pack."),
    ("The people affected, and workers, are told that the agent is used", "none named", "Article 26", [], "not provided",
     "This is the organisation's duty. Munitas does not tell anybody."),
]


def framework_mapping(pack: dict) -> dict:
    """Which records in this pack support which outcome of NIST's framework and of the EU's law. Whether each supporting section is in the pack is checked here, so the table cannot say more than the pack holds."""
    rows = []
    for outcome, nist, eu, sections, provided, note in FRAMEWORK_ROWS:
        present = [s for s in sections if s in pack]
        rows.append({"outcome": outcome, "nist_ai_rmf": nist, "eu_ai_act": eu, "supported_by": sections, "all_present_in_this_pack": len(present) == len(sections) and bool(sections),
                     "provided": provided if (provided != "records" or len(present) == len(sections)) else "not provided", "note": note})
    return {
        "about": "Which records in this pack support which outcome of two frameworks. It is not a statement that the agent or the organisation complies with either. ISO/IEC 42001 is not mapped.",
        "sources": FRAMEWORK_SOURCES, "rows": rows}


def _version_row(v: dict, approvals: dict) -> dict:
    approval = approvals.get(str(v["id"]))
    return {
        "version": v["version"], "id": str(v["id"]), "registered_at": _iso(v["created_at"]), "registered_by": v["registered_by"], "code_hash": v["code_hash"], "model_id": v["model_id"],
        "model_hosting": v["model_hosting"] or "not declared", "tools": {"declared": v["tools_declared"], "list": list(v["tool_scope"] or [])}, "hosts": list(v["requested_hosts"] or []),
        "data_access": v["data_access"], "executes": "in the platform's sandbox" if v["code_object_key"] else "as the platform's own code",
        "changes": approval["changes"] if approval else None,
        "approval": ({"state": approval["state"], "needs_officer": approval["needs_officer"], "decisions": approval["decisions"]} if approval else None),
    }


def evidence_pack(agent: dict, start: datetime, end: datetime, made_by: str) -> dict:
    """Everything the platform can back about one agent over a period, with no personal data in it."""
    aid = str(agent["id"])
    approvals = {a["agent_version_id"]: a for a in agent_changes.for_agent(agent["tenant_id"], aid)}
    versions = [v for v in db.all_rows("select * from agent_version where agent_id = %s order by version", (aid,))]
    deployments = db.all_rows(
        """select d.agent_version_id, v.version, d.deployed_by, d.deployed_at, d.note from agent_deployment d join agent_version v on v.id = d.agent_version_id
            where d.agent_id = %s and d.deployed_at <= %s order by d.deployed_at""", (aid, end))
    runs = db.all_rows(
        """select r.id, v.version, r.requested_by, r.started_at, r.ended_at, r.status, r.tool_calls, r.dataset_version_id, r.approved_by, r.approved_at, r.halted_reason, r.execution_mode
             from agent_run r join agent_version v on v.id = r.agent_version_id
            where r.agent_id = %s and r.started_at >= %s and r.started_at <= %s order by r.started_at""", (aid, start, end))
    ran_versions = {r["version"] for r in runs}
    in_period_deployments = [d for d in deployments if d["deployed_at"] >= start]
    active_at_start = [d for d in deployments if d["deployed_at"] < start][-1:] if deployments else []
    active_numbers = {d["version"] for d in in_period_deployments + active_at_start} | ran_versions
    decided = db.all_rows(
        """select access_decision.allowed, count(*) as n from access_decision where principal = %s and at >= %s and at <= %s group by allowed""", (agent["principal_id"], start, end))
    dataset_versions = sorted({str(r["dataset_version_id"]) for r in runs if r["dataset_version_id"]})
    releases = []
    for dv in dataset_versions:
        for g in db.all_rows(
                """select g.to_class, g.state, g.decided_by, g.decided_at, g.standard_name, g.standard_version, g.release_recommendation
                     from gate_decision g where g.dataset_version_id = %s and g.state <> 'pending' order by g.decided_at""", (dv,)):
            releases.append({"dataset_version_id": dv, "to_class": g["to_class"], "decision": g["state"], "decided_by": g["decided_by"], "decided_at": _iso(g["decided_at"]),
                             "standard": g["standard_name"], "standard_version": g["standard_version"], "judged": g["release_recommendation"]})
    risk = agent_risk.standing(agent)
    sponsor = agent_sponsor.state(agent, risk)
    pack = {
        "about": "Evidence the platform holds about one agent over a period. It holds identifiers, counts, hashes and times, and no personal data.",
        "period": {"from": _iso(start), "to": _iso(end)},
        "agent": {"id": aid, "name": agent["name"], "purpose": agent["purpose"], "department_id": str(agent["department_id"]) if agent.get("department_id") else None,
                  "registered_by": agent["registered_by"], "registered_at": _iso(agent["created_at"]), "run_by": agent["run_by"], "runtime_identity": agent["principal_id"]},
        "risk_level": {"confirmed": risk["confirmed"], "history": risk["history"], "levels_in_force": agent_risk.current_list(agent["tenant_id"])},
        "versions": [_version_row(v, approvals) for v in versions if v["version"] in active_numbers or start <= v["created_at"] <= end],
        "deployments": [{"version": d["version"], "deployed_by": d["deployed_by"], "deployed_at": _iso(d["deployed_at"]), "note_given": bool(d["note"])} for d in in_period_deployments],
        "active_at_start": {"version": active_at_start[0]["version"], "deployed_at": _iso(active_at_start[0]["deployed_at"])} if active_at_start else None,
        "runs": [{"id": str(r["id"]), "version": r["version"], "requested_by": r["requested_by"], "started_at": _iso(r["started_at"]), "ended_at": _iso(r["ended_at"]), "status": r["status"],
                  "tool_calls": r["tool_calls"], "dataset_version_id": str(r["dataset_version_id"]) if r["dataset_version_id"] else None, "approved_by": r["approved_by"],
                  "approved_at": _iso(r["approved_at"]), "halted_reason": r["halted_reason"], "executed": r["execution_mode"]} for r in runs],
        "access_decisions": {"allowed": sum(d["n"] for d in decided if d["allowed"]), "refused": sum(d["n"] for d in decided if not d["allowed"])},
        "human_oversight": oversight_figures(aid, start, end),
        "suspensions": [s for s in suspensions(aid) if start <= datetime.fromisoformat(s["at"].replace("Z", "+00:00")) <= end],
        "incidents": [i for i in incidents(aid) if start <= datetime.fromisoformat(i["reported_at"].replace("Z", "+00:00")) <= end],
        "release_decisions_for_data_read": releases,
        "sponsorship": {**sponsor, "history": agent_sponsor.sponsorships(aid)},
        "reconfirmations": [c for c in agent_sponsor.reconfirmations(aid) if start <= datetime.fromisoformat(c["confirmed_at"].replace("Z", "+00:00")) <= end],
        "integrity": {**agent_chain.verify(aid), "about": "The records of this agent are chained: each carries a hash of what it said when written and of the one before it. `ok` is true when nothing was changed, removed or added outside the platform. Keep this head hash with the pack: a later pack that does not extend it shows that the chain was rewritten."},
        "declared_and_enforced": {
            "declared_by_the_owner": "Which tools a version uses, which model it uses and where the model runs are stated by the owner when the version is registered. The platform keeps them and shows them as stated.",
            "enforced_by_the_platform": [
                "A run can read only the dataset versions it was started on, and only what the agent's own identity may read. This is checked by the platform on every request.",
                "A version that asks to call hosts outside the platform cannot be deployed until a network architect has approved them.",
                "Where a version runs in the platform's sandbox, its code has no capabilities beyond the sandbox, a read-only file system and only the network the platform gives it.",
                "A version that widens an agent is held for approval when the organisation has turned enforcement on.",
            ],
            "not_prevented": "Access limits stop reads and unapproved hosts. They do not stop an output that is wrong or incomplete. That is a question of evaluating the output.",
        },
        "made_by": made_by,
        "made_at": _iso(datetime.now(timezone.utc)),
    }
    pack["framework_mapping"] = framework_mapping(pack)
    return pack
