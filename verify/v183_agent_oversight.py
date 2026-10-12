r"""U195: an organisation holds its agents to risk levels it writes, a version that widens an agent waits for approval, an agent can be suspended, and the evidence about an agent can be made.

Run in the API container, which holds the API's code and reaches the database:

    docker compose exec -T munitas-api python /verify/v183_agent_oversight.py

Everything is made up here, in the test organisation: its own agents, versions and runs, named U195. This proves, per item:

  * who writes the risk levels (the AI risk owner alone, in the words of the role table) and that a list which is not well formed is refused in words;
  * a version is compared with the one before it: what widens and what narrows, the strictest kind first, an unknown tool held at the strictest kind, and "uses no tools" stored
    as a statement and not the same as saying nothing;
  * who proposes and who confirms a level, that an unconfirmed agent is held at the strictest level that allows deployment, and that a level which may not be deployed stops a deploy and a run;
  * while the organisation does not hold agents to its levels nothing is refused because of them; once it does, an unclassified agent cannot start a run and a widening cannot be deployed
    until the department and, where the level asks, the AI risk owner have approved it; nobody decides a version they submitted, in policy and in the table;
  * suspending an agent stops a run starting and halts one that has not finished, and who may resume depends on the level;
  * incidents, the review of what a run produced (counted for the agent and never for a person), the registry and its flags, and an agent run outside the platform;
  * the evidence pack: who may make it, that it holds no content of a run, that its hash is what was handed over, and that making it is recorded;
  * the database refuses an edit of any of these records with the API bypassed. That a version which declared no tools refuses every tool in the agent's own code is U196 (v184).
"""

from __future__ import annotations

import json
import sys
import uuid

sys.path.insert(0, "/verify")
sys.path.insert(0, "/app")

import pgdb  # noqa: E402
from common import (CANARY, CANARY_ADMIN, CUSTODIAN, ENGINEER, REVIEWER, RESEARCHER, RISK_OWNER, api, bearer_for, check, db,  # noqa: E402
                    fixture_tenant, heading, require_api, summary)

from app import agent_oversight  # noqa: E402

DEPARTMENT = "Verification"
PLANTED = "u195-secret-purpose-words"
made_agents: list[str] = []
made_runs: list[str] = []


def reasons_of(response) -> str:
    try:
        body = response.json().get("detail", {})
        return " ".join(body.get("reasons", [])) if isinstance(body, dict) else str(body)
    except ValueError:
        return response.text[:200]


def department_id() -> str:
    with db() as conn:
        row = conn.execute("select id from department where tenant_id = %s and name = %s", (CANARY, DEPARTMENT)).fetchone()
    if not row:
        raise RuntimeError(f"department {DEPARTMENT!r} is missing from {CANARY!r}: apply infra/postgres/seed-canary.sql")
    return str(row["id"])


def new_agent(label: str, **over) -> str:
    body = {"tenant_id": CANARY, "name": f"U195 {label} {uuid.uuid4().hex[:6]}", "department_id": department_id(), "registered_by": ENGINEER, "purpose": "verification fixture"}
    body.update(over)
    made = api("POST", "/agents/register", headers=bearer_for(ENGINEER), json=body)
    made.raise_for_status()
    made_agents.append(made.json()["id"])
    return made.json()["id"]


def new_version(agent_id: str, **over):
    body = {"code_hash": f"git:{uuid.uuid4().hex}", "source_path": "/checkout/agent", "model_id": "qwen2.5:7b", "registered_by": ENGINEER}
    body.update(over)
    return api("POST", f"/agents/{agent_id}/versions", headers=bearer_for(ENGINEER), json=body)


def deploy(agent_id: str, version_id: str, who: str = ENGINEER):
    return api("POST", f"/agents/{agent_id}/deploy", headers=bearer_for(who), json={"agent_version_id": version_id})


def write_levels(levels: list[dict], enforce: bool, reason: str = "U195 fixture"):
    return api("POST", "/risk-levels", headers=bearer_for(RISK_OWNER), json={"levels": levels, "enforce": enforce, "reason": reason})


LEVELS = [
    {"name": "Low", "description": "An agent whose output is checked by whoever reads it.", "framework_label": None, "second_approver": False, "blocks": False},
    {"name": "Moderate", "description": "An agent whose output informs a decision a person makes.", "framework_label": None, "second_approver": False, "blocks": False},
    {"name": "High", "description": "An agent whose output can change what happens to a person.", "framework_label": "Example framework: high impact", "second_approver": True, "blocks": False},
    {"name": "Prohibited", "description": "An agent the organisation will not run.", "framework_label": None, "second_approver": False, "blocks": True},
]


def make_run(agent_id: str, version_id: str, status: str, findings: list | None = None, purpose: str = PLANTED) -> str:
    run_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """insert into agent_run (id, tenant_id, agent_id, agent_version_id, status, purpose, requested_by, execution_mode, findings)
               values (%s, %s, %s, %s, %s, %s, %s, 'native', %s)""",
            (run_id, CANARY, agent_id, version_id, status, purpose, ENGINEER, json.dumps(findings or [])))
    made_runs.append(run_id)
    return run_id


def teardown() -> None:
    with db() as conn:
        for run in made_runs:
            conn.execute("delete from agent_output_review where run_id = %s", (run,))
        for run in made_runs:
            conn.execute("delete from agent_run where id = %s", (run,))
        for agent in made_agents:
            for table in ("agent_record_chain", "agent_evidence_export", "agent_incident", "agent_suspension", "agent_risk_record", "agent_sponsorship", "agent_reconfirmation"):
                conn.execute(f"delete from {table} where agent_id = %s", (agent,))
            conn.execute("delete from agent_change_decision where approval_id in (select id from agent_change_approval where agent_id = %s)", (agent,))
            conn.execute("delete from agent_change_approval where agent_id = %s", (agent,))
        conn.execute("delete from agent_risk_level_list where tenant_id = %s", (CANARY,))


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    try:
        with db() as conn:
            conn.execute("delete from agent_risk_level_list where tenant_id = %s", (CANARY,))

        # ------------------------------------------------------------------ the levels
        heading("U195: who writes the risk levels")
        seen = api("GET", "/risk-levels", headers=bearer_for(REVIEWER))
        check("a reviewer can read the levels", seen.status_code == 200, f"HTTP {seen.status_code}")
        body = seen.json()
        check("an organisation with none uses the built-in list, and it says so", body["current"]["built_in"] is True and [lv["name"] for lv in body["current"]["levels"]] == ["Low", "Moderate", "High", "Not allowed"], str(body["current"]["levels"])[:120])
        check("holding agents to them is off until the officer turns it on", body["current"]["enforce"] is False)
        check("the reviewer is told they may not write them, in the words of the role table", body["may_manage"] is False and "only the AI risk owner may set the risk levels" in " ".join(body["may_manage_reasons"]), str(body["may_manage_reasons"]))
        check("a person who is not signed in is refused", api("GET", "/risk-levels", headers={}).status_code == 401)
        for who, label in ((ENGINEER, "a data engineer"), (CANARY_ADMIN, "the platform administrator"), (CUSTODIAN, "a data custodian"), (REVIEWER, "the reviewer")):
            refused = api("POST", "/risk-levels", headers=bearer_for(who), json={"levels": LEVELS, "enforce": False, "reason": "x"})
            check(f"{label} cannot write the levels", refused.status_code == 403 and "the AI risk owner" in reasons_of(refused), f"HTTP {refused.status_code}: {reasons_of(refused)[:90]}")
        for label, levels, words in (
            ("a level with no name", [{**LEVELS[0], "name": ""}], "no name"),
            ("two levels with one name", [LEVELS[0], {**LEVELS[1], "name": "low"}], "Two levels"),
            ("a level with no description", [{**LEVELS[0], "description": ""}], "description"),
            ("a list in which nothing may be deployed", [LEVELS[3]], "allow an agent to be deployed"),
            ("an empty list", [], "at least one level"),
        ):
            bad = write_levels(levels, False)
            check(f"{label} is refused with the reason", bad.status_code == 400 and words in reasons_of(bad), f"HTTP {bad.status_code}: {reasons_of(bad)[:100]}")
        check("a list with no reason is refused", api("POST", "/risk-levels", headers=bearer_for(RISK_OWNER), json={"levels": LEVELS, "enforce": False, "reason": ""}).status_code == 422)
        wrote = write_levels(LEVELS, False)
        check("the AI risk owner writes the levels", wrote.status_code == 201 and wrote.json()["current"]["version"] == 1, f"HTTP {wrote.status_code}: {wrote.text[:100]}")
        now = api("GET", "/risk-levels", headers=bearer_for(REVIEWER)).json()
        check("they are in force at version 1 with who set them", now["current"]["built_in"] is False and now["current"]["set_by"] == RISK_OWNER and [lv["key"] for lv in now["current"]["levels"]] == ["low", "moderate", "high", "prohibited"], str(now["current"])[:140])
        check("the framework label and the two settings are kept as written", now["current"]["levels"][2]["framework_label"] == "Example framework: high impact" and now["current"]["levels"][2]["second_approver"] and now["current"]["levels"][3]["blocks"])

        # ------------------------------------------------------------------ comparing versions
        heading("U195: a version is compared with the one before it")
        a = new_agent("main")
        v1 = new_version(a, tool_scope=[], declares_no_tools=True)
        check("a first version that says it uses no tools is stored as declared", v1.status_code == 201 and v1.json()["tools_declared"] is True and v1.json()["changes"] == [] and v1.json()["widening"] is False, f"HTTP {v1.status_code}: {v1.text[:140]}")
        v2 = new_version(a, tool_scope=["read_dataset_version"], declares_no_tools=False)
        check("adding a read tool the platform knows widens by reading more", v2.status_code == 201 and v2.json()["widening"] and [c["kind"] for c in v2.json()["changes"]] == ["reads_more"], v2.text[:160])
        v3 = new_version(a, tool_scope=["read_dataset_version", "send_report"])
        check("a tool the platform does not know is held at the strictest kind, first in the list", v3.json()["changes"][0]["kind"] == "acts_outside" and v3.json()["changes"][0]["rank"] == 1 and "does not know" in v3.json()["changes"][0]["what"], v3.text[:200])
        v4 = new_version(a)
        check("saying nothing about tools is stored as not declared, not as none", v4.json()["tools_declared"] is False, v4.text[:120])
        check("and it is shown as a widening against a version that did say", any(c["kind"] == "not_declared" and c["widens"] for c in v4.json()["changes"]), v4.text[:200])
        v5 = new_version(a, tool_scope=[], declares_no_tools=True)
        check("going from not saying to saying none narrows", v5.json()["widening"] is False and any(c["kind"] == "narrows" for c in v5.json()["changes"]), v5.text[:200])
        v6 = new_version(a, tool_scope=[], declares_no_tools=True, model_id="another-model:1b", model_hosting="outside", requested_hosts=["example.org"])
        kinds = {c["kind"] for c in v6.json()["changes"]}
        check("a new model, a model that runs outside, and a new host are each named", {"new_model", "reaches_host"} <= kinds, str(kinds))
        check("a model_hosting other than platform or outside is refused", new_version(a, tool_scope=[], declares_no_tools=True, model_hosting="moon").status_code == 400)
        detail = api("GET", f"/agents/{a}", headers=bearer_for(ENGINEER)).json()
        check("the agent's page returns what was declared for each version", all("tools_declared" in v and "model_hosting" in v for v in detail["versions"]) and detail["run_by"] == "platform")
        listed = api("GET", f"/agents/{a}/change-approvals", headers=bearer_for(REVIEWER)).json()["approvals"]
        check("the changes of an agent are listed, newest first, with the state of each", [x["version"] for x in listed] == [6, 5, 4, 3, 2, 1] and listed[-1]["state"] == "not_needed", str([(x["version"], x["state"]) for x in listed]))

        # ------------------------------------------------------------------ not held to the levels yet
        heading("U195: while the organisation does not hold agents to its levels, nothing is refused because of them")
        versions = {v["version"]: v["id"] for v in detail["versions"]}
        free = deploy(a, versions[3])
        check("a version that widens is deployed without an approval", free.status_code == 201, f"HTTP {free.status_code}: {free.text[:140]}")

        # ------------------------------------------------------------------ proposing and confirming a level
        heading("U195: proposing and confirming a level")
        risk = api("GET", f"/agents/{a}/risk", headers=bearer_for(ENGINEER)).json()
        check("a new agent has no confirmed level", risk["classified"] is False and risk["confirmed"] is None)
        check("it is held at the strictest level that allows deployment, and that asks for a second approver", risk["held_at"]["key"] == "high" and risk["held_at"]["second_approver"] is True, str(risk["held_at"]))
        proposal = {"level_key": "moderate", "intended_purpose": "Drafts a summary for a person to read", "affected": "The people the summaries are about", "reasoning": "A person reads every summary before it is used"}
        check("the reviewer cannot propose a level", api("POST", f"/agents/{a}/risk/proposals", headers=bearer_for(REVIEWER), json=proposal).status_code == 403)
        bad = api("POST", f"/agents/{a}/risk/proposals", headers=bearer_for(ENGINEER), json={**proposal, "level_key": "nonsense"})
        check("a level that is not in the list is refused", bad.status_code == 400 and "not one of this organisation's risk levels" in reasons_of(bad), reasons_of(bad)[:100])
        proposed = api("POST", f"/agents/{a}/risk/proposals", headers=bearer_for(ENGINEER), json=proposal)
        check("the data engineer who registered the agent proposes one", proposed.status_code == 201 and proposed.json()["record"]["level_name"] == "Moderate", f"HTTP {proposed.status_code}: {proposed.text[:120]}")
        risk = api("GET", f"/agents/{a}/risk", headers=bearer_for(ENGINEER)).json()
        check("the proposal waits for the officer and the agent is still unclassified", risk["classified"] is False and risk["proposal_waiting"]["level_key"] == "moderate")
        for who, label in ((ENGINEER, "the data engineer"), (CUSTODIAN, "a data custodian"), (REVIEWER, "the reviewer")):
            refused = api("POST", f"/agents/{a}/risk/confirmations", headers=bearer_for(who), json={"level_key": "moderate", "reasoning": "x"})
            check(f"{label} cannot confirm a level", refused.status_code == 403 and "the AI risk owner" in reasons_of(refused), f"HTTP {refused.status_code}: {reasons_of(refused)[:90]}")
        confirmed = api("POST", f"/agents/{a}/risk/confirmations", headers=bearer_for(RISK_OWNER), json={"level_key": "high", "reasoning": "It names people in what it produces"})
        check("the AI risk owner confirms a different level from the one proposed", confirmed.status_code == 201 and confirmed.json()["record"]["level_key"] == "high", f"HTTP {confirmed.status_code}: {confirmed.text[:140]}")
        check("what is affected is taken from the proposal", confirmed.json()["record"]["affected"] == proposal["affected"] and confirmed.json()["record"]["intended_purpose"] == proposal["intended_purpose"])
        risk = api("GET", f"/agents/{a}/risk", headers=bearer_for(ENGINEER)).json()
        check("the agent is now classified, and the history keeps both with who and when", risk["classified"] and risk["confirmed"]["level_name"] == "High" and len(risk["history"]) == 2 and risk["confirmed"]["recorded_by"] == RISK_OWNER, str(risk["history"])[:200])

        # ------------------------------------------------------------------ held to the levels
        heading("U195: once the organisation holds agents to its levels")
        held = write_levels(LEVELS, True, "U195: hold agents to the levels")
        check("the officer turns holding agents to the levels on, as a new version", held.status_code == 201 and held.json()["current"]["version"] == 2 and held.json()["current"]["enforce"] is True)
        b = new_agent("unclassified")
        refused = api("POST", f"/agents/{b}/runs", headers=bearer_for(ENGINEER), json={"purpose": PLANTED})
        check("an agent with no confirmed level cannot start a run, and the reason says what to do", refused.status_code == 409 and "no confirmed risk level" in reasons_of(refused), f"HTTP {refused.status_code}: {reasons_of(refused)[:120]}")

        v7 = new_version(a, tool_scope=["read_dataset_version", "mystery_tool"], declares_no_tools=False)
        check("a version that widens is registered with its approval waiting", v7.status_code == 201 and v7.json()["widening"] is True, v7.text[:120])
        v7_id = v7.json()["id"]
        blocked = deploy(a, v7_id)
        check("it cannot be deployed, and the reason names who it waits for", blocked.status_code == 409 and "widens what the agent can do" in reasons_of(blocked) and "AI risk owner" in reasons_of(blocked), reasons_of(blocked)[:200])
        approval = next(x for x in api("GET", f"/agents/{a}/change-approvals", headers=bearer_for(REVIEWER)).json()["approvals"] if x["version"] == v7.json()["version"])
        aid = approval["id"]
        check("the agent's level asks for the officer as well as the department", approval["state"] == "waiting" and approval["needs_officer"] is True and approval["waiting_for"] == ["an approver of the department that owns the agent", "the AI risk owner"], str(approval["waiting_for"]))
        check("the reviewer is told, for each part, whether they may give it", approval["can_decide"]["department"]["allowed"] is False and approval["can_decide"]["officer"]["allowed"] is False)
        cust = next(x for x in api("GET", "/agent-change-approvals", headers=bearer_for(CUSTODIAN)).json()["approvals"] if x["id"] == aid)
        check("and the department approver is told they may give the department's", cust["can_decide"]["department"]["allowed"] is True and cust["can_decide"]["officer"]["allowed"] is False)
        decide = lambda who, part, decision="approved": api("POST", f"/agent-change-approvals/{aid}/decisions", headers=bearer_for(who), json={"part": part, "decision": decision, "reason": "U195 fixture"})  # noqa: E731
        submitter = decide(ENGINEER, "department")
        check("the person who submitted the version cannot decide on it", submitter.status_code == 403 and "cannot decide on it" in reasons_of(submitter), f"HTTP {submitter.status_code}: {reasons_of(submitter)[:100]}")
        outsider = decide(RESEARCHER, "department")
        check("somebody outside the department cannot give its approval, and is told who can", outsider.status_code == 403 and "only an approver of" in reasons_of(outsider), reasons_of(outsider)[:140])
        check("the officer cannot give the department's approval either", decide(RISK_OWNER, "department").status_code == 403)
        wrong = decide(CUSTODIAN, "officer")
        check("a department approver cannot give the officer's approval", wrong.status_code == 403 and "the AI risk owner" in reasons_of(wrong), reasons_of(wrong)[:100])
        check("a part must be department or officer", api("POST", f"/agent-change-approvals/{aid}/decisions", headers=bearer_for(CUSTODIAN), json={"part": "other", "decision": "approved", "reason": "x"}).status_code == 400)
        first = decide(CUSTODIAN, "department")
        check("the department approver gives the department's approval", first.status_code == 201 and first.json()["approval"]["state"] == "waiting", f"HTTP {first.status_code}: {first.text[:140]}")
        check("it still waits, now for the officer alone", first.json()["approval"]["waiting_for"] == ["the AI risk owner"], str(first.json()["approval"]["waiting_for"]))
        check("and cannot be deployed", deploy(a, v7_id).status_code == 409)
        check("a part already decided is not decided twice", decide(CUSTODIAN, "department").status_code == 409)
        second = decide(RISK_OWNER, "officer")
        check("the officer gives theirs, and the version is approved", second.status_code == 201 and second.json()["approval"]["state"] == "approved", f"HTTP {second.status_code}: {second.text[:140]}")
        check("the decisions keep who and when", all(d["decided_by"] and d["decided_at"] for d in second.json()["approval"]["decisions"]) and {d["part"] for d in second.json()["approval"]["decisions"]} == {"department", "officer"})
        check("an approved version is deployed", deploy(a, v7_id).status_code == 201)
        check("a decision on an approval that is finished is refused", decide(RISK_OWNER, "officer").status_code == 409)

        v8 = new_version(a, tool_scope=["read_dataset_version", "mystery_tool", "another_unknown"], declares_no_tools=False)
        a8 = next(x for x in api("GET", f"/agents/{a}/change-approvals", headers=bearer_for(REVIEWER)).json()["approvals"] if x["version"] == v8.json()["version"])
        aid = a8["id"]
        refusal = decide(CUSTODIAN, "department", "refused")
        check("the department approver can refuse", refusal.status_code == 201 and refusal.json()["approval"]["state"] == "refused")
        blocked = deploy(a, v8.json()["id"])
        check("a refused version cannot be deployed, and the reason says it was refused", blocked.status_code == 409 and "refused" in reasons_of(blocked), reasons_of(blocked)[:120])

        v9 = new_version(a, tool_scope=["read_dataset_version"], declares_no_tools=False)
        check("a version that only removes a tool needs no approval", v9.json()["widening"] is False and deploy(a, v9.json()["id"]).status_code == 201, v9.text[:160])

        with db() as conn:
            try:
                conn.execute("insert into agent_change_decision (id, approval_id, tenant_id, part, decision, reason, submitted_by, decided_by) values (%s, %s, %s, 'officer', 'approved', 'x', %s, %s)",
                             (str(uuid.uuid4()), aid, CANARY, ENGINEER, ENGINEER))
                held_out = False
            except pgdb.errors.DatabaseError as exc:
                held_out = "no_self_decision" in str(exc)
        check("the table itself refuses a decision by the person who submitted the version, with the API bypassed", held_out)

        # ------------------------------------------------------------------ a level that may not be deployed
        heading("U195: a level that may not be deployed")
        c = new_agent("prohibited")
        vc = new_version(c, tool_scope=[], declares_no_tools=True)
        sealed = api("POST", f"/agents/{c}/risk/confirmations", headers=bearer_for(RISK_OWNER), json={"level_key": "prohibited", "reasoning": "The organisation will not run this", "affected": "Nobody, it is not run"})
        check("the officer confirms the level that may not be deployed", sealed.status_code == 201 and sealed.json()["record"]["blocks"] is True, sealed.text[:140])
        blocked = deploy(c, vc.json()["id"])
        check("no version of that agent can be deployed", blocked.status_code == 409 and "does not allow" in reasons_of(blocked), reasons_of(blocked)[:140])
        run = api("POST", f"/agents/{c}/runs", headers=bearer_for(ENGINEER), json={"purpose": PLANTED})
        check("and it cannot start a run", run.status_code == 409 and "does not allow" in reasons_of(run), reasons_of(run)[:140])

        # ------------------------------------------------------------------ suspension
        heading("U195: suspending an agent")
        running = make_run(a, versions[3], "running")
        waiting = make_run(a, versions[3], "awaiting_approval", findings=[{"document": "d1", "priority": "high", "reason": "x"}])
        for who, label in ((ENGINEER, "a data engineer"), (REVIEWER, "the reviewer")):
            refused = api("POST", f"/agents/{a}/suspension", headers=bearer_for(who), json={"reason": "x"})
            check(f"{label} cannot suspend an agent", refused.status_code == 403 and "may suspend or resume" in reasons_of(refused), f"HTTP {refused.status_code}: {reasons_of(refused)[:90]}")
        suspended = api("POST", f"/agents/{a}/suspension", headers=bearer_for(CUSTODIAN), json={"reason": "U195 a defect was reported"})
        check("an approver of the owning department suspends it", suspended.status_code == 201 and suspended.json()["suspension"]["suspended"] is True, f"HTTP {suspended.status_code}: {suspended.text[:120]}")
        check("the runs that had not finished are halted with the reason", suspended.json()["runs_halted"] >= 2, str(suspended.json()))
        with db() as conn:
            rows = {str(r["id"]): r for r in conn.execute("select id, status, halted_reason from agent_run where id = any(%s)", ([running, waiting],)).fetchall()}
        check("each shows halted with why", all(str(rows[r]["status"]) == "halted" and "suspended" in (rows[r]["halted_reason"] or "") for r in (running, waiting)), str(rows))
        refused = api("POST", f"/agents/{a}/runs", headers=bearer_for(ENGINEER), json={"purpose": PLANTED})
        check("a suspended agent cannot start a run, and the reason is the suspension's", refused.status_code == 409 and "suspended" in reasons_of(refused) and "a defect was reported" in reasons_of(refused), reasons_of(refused)[:140])
        check("it cannot be suspended twice", api("POST", f"/agents/{a}/suspension", headers=bearer_for(CUSTODIAN), json={"reason": "again"}).status_code == 409)
        view = api("GET", f"/agents/{a}/oversight", headers=bearer_for(CUSTODIAN)).json()
        check("the page shows it suspended, who and when", view["suspension"]["suspended"] and view["suspension"]["by"] == CUSTODIAN and view["suspension"]["at"] and view["may_suspend"] is True)
        check("the department approver may not resume it, because its level asks for a second approver", view["may_resume"] is False and "second approver" in " ".join(view["may_resume_reasons"]), str(view["may_resume_reasons"]))
        check("and resuming is refused in the same words", api("POST", f"/agents/{a}/resumption", headers=bearer_for(CUSTODIAN), json={"reason": "x"}).status_code == 403)
        resumed = api("POST", f"/agents/{a}/resumption", headers=bearer_for(RISK_OWNER), json={"reason": "U195 fixed"})
        check("the AI risk owner resumes it", resumed.status_code == 201 and resumed.json()["suspension"]["suspended"] is False, f"HTTP {resumed.status_code}: {resumed.text[:120]}")
        again = api("GET", f"/agents/{a}/oversight", headers=bearer_for(RISK_OWNER)).json()
        check("both acts stay in the history with who and why", [h["action"] for h in again["suspension_history"]] == ["resumed", "suspended"] and again["suspension_history"][1]["reason"] == "U195 a defect was reported")
        check("an agent that is not suspended cannot be resumed", api("POST", f"/agents/{a}/resumption", headers=bearer_for(RISK_OWNER), json={"reason": "x"}).status_code == 409)

        # ------------------------------------------------------------------ incidents
        heading("U195: incidents")
        check("a researcher cannot report an incident", api("POST", f"/agents/{a}/incidents", headers=bearer_for(RESEARCHER), json={"what": "x"}).status_code == 403)
        reported = api("POST", f"/agents/{a}/incidents", headers=bearer_for(ENGINEER), json={"what": "U195 the agent listed the wrong queue", "reported_onward": "The provider was told", "action_taken": "Suspended it"})
        check("a data engineer reports one", reported.status_code == 201 and reported.json()["incidents"][0]["reported_by"] == ENGINEER, f"HTTP {reported.status_code}: {reported.text[:120]}")
        check("it is kept with what, whom was told, what was done, who and when", reported.json()["incidents"][0]["reported_onward"] == "The provider was told" and reported.json()["incidents"][0]["reported_at"])
        check("an incident with nothing in it is refused", api("POST", f"/agents/{a}/incidents", headers=bearer_for(ENGINEER), json={"what": ""}).status_code == 422)

        # ------------------------------------------------------------------ review
        heading("U195: the review of what a run produced")
        refusal = api("POST", f"/agents/runs/{make_run(a, versions[3], 'awaiting_approval', findings=[{'document': 'd2', 'priority': 'low', 'reason': 'y'}])}/refuse", headers=bearer_for(REVIEWER), json={"reason": "The ranking was wrong"})
        check("a reviewer refuses what a run produced, and the run stops", refusal.status_code == 201 and refusal.json()["status"] == "halted" and refusal.json()["review"]["outcome"] == "refused", f"HTTP {refusal.status_code}: {refusal.text[:140]}")
        check("a run that is not waiting cannot be refused", api("POST", f"/agents/runs/{running}/refuse", headers=bearer_for(REVIEWER), json={"reason": "x"}).status_code == 400)
        check("a run of another organisation or none is a 404", api("POST", f"/agents/runs/{uuid.uuid4()}/refuse", headers=bearer_for(REVIEWER), json={"reason": "x"}).status_code == 404)
        draft_run = refusal.json()["run_id"]
        with db() as conn:
            review = conn.execute("select * from agent_output_review where run_id = %s", (draft_run,)).fetchone()
        expected = agent_oversight.sha256_of([{"document": "d2", "priority": "low", "reason": "y"}])
        check("the draft's hash is worked out by the platform from the run's findings", review["draft_hash"] == expected, review["draft_hash"])
        check("it keeps who reviewed, when, how long it took and the model", review["reviewed_by"] == REVIEWER and review["seconds_taken"] >= 0 and review["model_id"] == "qwen2.5:7b" and review["reason"] == "The ranking was wrong")
        for who, outcome, final in ((CUSTODIAN, "accepted", None), (RISK_OWNER, "changed", "sha256:edited")):
            extra = make_run(a, versions[3], "succeeded")
            with db() as conn:
                conn.execute(
                    """insert into agent_output_review (id, tenant_id, run_id, agent_id, agent_version_id, model_id, outcome, draft_hash, final_hash, reviewed_by, seconds_taken)
                       values (%s, %s, %s, %s, %s, 'qwen2.5:7b', %s, 'sha256:d', %s, %s, 30)""", (str(uuid.uuid4()), CANARY, extra, a, versions[3], outcome, final, who))
        figures = api("GET", f"/agents/{a}/oversight", headers=bearer_for(REVIEWER)).json()["review_figures"]
        check("the figures count accepted, changed and refused for the agent", (figures["reviewed"], figures["accepted_unchanged"], figures["changed"], figures["refused"]) == (3, 1, 1, 1), str(figures))
        check("and how many different people decided, never who", figures["different_people"] == 3 and set(figures) == {"reviewed", "accepted_unchanged", "changed", "refused", "different_people", "median_seconds"}, str(figures))

        # ------------------------------------------------------------------ outside, registry
        heading("U195: an agent run outside the platform, and the registry")
        newer = new_version(a, tool_scope=["read_dataset_version"], declares_no_tools=False)
        check("a newer version that is not deployed is registered", newer.status_code == 201, newer.text[:100])
        d = new_agent("outside", run_by="outside")
        refused = api("POST", f"/agents/{d}/runs", headers=bearer_for(ENGINEER), json={"purpose": PLANTED})
        check("an agent run outside the platform is never started", refused.status_code == 409 and "run outside the platform" in reasons_of(refused), reasons_of(refused)[:140])
        check("run_by must be platform or outside", api("POST", "/agents/register", headers=bearer_for(ENGINEER), json={"tenant_id": CANARY, "name": f"U195 bad {uuid.uuid4().hex[:5]}", "department_id": department_id(), "registered_by": ENGINEER, "purpose": "x", "run_by": "moon"}).status_code == 400)
        reg = api("GET", "/agent-registry", headers=bearer_for(REVIEWER))
        check("anybody signed in can read the registry", reg.status_code == 200, f"HTTP {reg.status_code}")
        rows = {r["id"]: r for r in reg.json()["agents"]}
        check("every agent made here is listed", all(x in rows for x in (a, b, c, d)))
        check("a classified agent shows its level and who answers for it", rows[a]["risk_level"]["name"] == "High" and rows[a]["registered_by"] == ENGINEER and rows[a]["department_name"] == DEPARTMENT, str(rows[a]["risk_level"]))
        check("an unclassified agent says so and is flagged", rows[b]["risk_level"] is None and any(f["key"] == "no_level" for f in rows[b]["flags"]))
        check("an agent with a newer version not deployed is flagged", any(f["key"] == "behind" for f in rows[a]["flags"]) and rows[a]["newest_version"] > rows[a]["active_version"], str(rows[a]["flags"]))
        check("an agent run outside the platform is flagged", any(f["key"] == "run_outside" for f in rows[d]["flags"]) and rows[d]["run_by"] == "outside")
        check("an agent registered just now that has not run yet is not called idle", not any(f["key"] == "idle" for f in rows[b]["flags"]))
        with db() as conn:
            conn.execute("update agent set created_at = now() - interval '200 days' where id = %s", (b,))
        old = next(r for r in api("GET", "/agent-registry", headers=bearer_for(REVIEWER)).json()["agents"] if r["id"] == b)
        check("one registered long ago that never ran is", any(f["key"] == "idle" for f in old["flags"]), str(old["flags"]))
        check("the registry says whether agents are held to the levels", reg.json()["enforce"] is True)
        check("what an active version is declared to use is shown as stated", rows[a]["tools"]["state"] in ("declared", "uses no tools", "not declared"), str(rows[a]["tools"]))

        # ------------------------------------------------------------------ evidence pack
        heading("U195: the evidence pack")
        for who, label in ((REVIEWER, "the reviewer"), (RESEARCHER, "a researcher"), (ENGINEER, "a data engineer")):
            refused = api("GET", f"/agents/{a}/evidence-pack", headers=bearer_for(who))
            check(f"{label} cannot make an evidence pack", refused.status_code == 403 and "evidence pack" in reasons_of(refused), f"HTTP {refused.status_code}: {reasons_of(refused)[:90]}")
        made = api("GET", f"/agents/{a}/evidence-pack", headers=bearer_for(RISK_OWNER))
        check("the AI risk owner makes one", made.status_code == 200, f"HTTP {made.status_code}: {made.text[:140]}")
        pack = made.json()["pack"]
        check("a department approver can make one too", api("GET", f"/agents/{a}/evidence-pack", headers=bearer_for(CUSTODIAN)).status_code == 200)
        check("its hash is the hash of what was handed over", made.json()["pack_hash"] == agent_oversight.sha256_of(pack), made.json()["pack_hash"])
        check("it covers the last 90 days when no dates are given", pack["period"]["from"] < pack["period"]["to"])
        check("it names the agent, its department and who registered it", pack["agent"]["id"] == a and pack["agent"]["registered_by"] == ENGINEER and pack["agent"]["run_by"] == "platform")
        check("it holds the level history", pack["risk_level"]["confirmed"]["level_name"] == "High" and len(pack["risk_level"]["history"]) == 2)
        by_version = {v["version"]: v for v in pack["versions"]}
        check("it holds each version with its tools as declared and what it changed", by_version[1]["tools"]["declared"] is True and by_version[3]["changes"][0]["kind"] == "acts_outside", str(by_version.get(3, {}))[:200])
        check("and who approved a widening, with when", by_version[7]["approval"]["state"] == "approved" and {d["part"] for d in by_version[7]["approval"]["decisions"]} == {"department", "officer"})
        check("and a refused one as refused", by_version[8]["approval"]["state"] == "refused")
        check("it holds the runs with who started and who approved", len(pack["runs"]) >= 4 and all("requested_by" in r for r in pack["runs"]))
        check("it holds how reviewers decided, counted for the agent", pack["human_oversight"]["reviewed"] == 3 and pack["human_oversight"]["different_people"] == 3)
        check("it holds the suspension and the incident", len(pack["suspensions"]) == 2 and len(pack["incidents"]) == 1)
        check("and says which facts are declared by the owner and which the platform enforces", "declared_by_the_owner" in pack["declared_and_enforced"] and len(pack["declared_and_enforced"]["enforced_by_the_platform"]) >= 3 and pack["declared_and_enforced"]["not_prevented"])
        text = json.dumps(pack)
        check("it holds no content of a run: the purpose a run was started with is not in it", PLANTED not in text)
        check("nor the findings a run produced", '"document"' not in text and '"priority"' not in text)
        check("the text of an incident is in it, because the organisation wrote that for the record and was told to leave personal data out", pack["incidents"][0]["what"] == "U195 the agent listed the wrong queue")
        with db() as conn:
            exports = conn.execute("select pack_hash, exported_by from agent_evidence_export where agent_id = %s order by exported_at", (a,)).fetchall()
        check("making a pack is recorded with who made it and the hash", len(exports) == 2 and exports[0]["exported_by"] == RISK_OWNER and exports[0]["pack_hash"] == made.json()["pack_hash"], str(exports))
        check("a period that ends before it starts is refused", api("GET", f"/agents/{a}/evidence-pack", headers=bearer_for(RISK_OWNER), params={"start": "2026-10-02", "end": "2026-10-01"}).status_code == 400)
        check("a date that is not a date is refused in words", "dates are written like" in reasons_of(api("GET", f"/agents/{a}/evidence-pack", headers=bearer_for(RISK_OWNER), params={"start": "yesterday"})))
        check("an agent of another organisation or none is a 404", api("GET", f"/agents/{uuid.uuid4()}/evidence-pack", headers=bearer_for(RISK_OWNER)).status_code == 404)

        # ------------------------------------------------------------------ the database
        heading("U195: nothing is edited, with the API bypassed")
        for label, sql, params in (
            ("a risk level record", "update agent_risk_record set reasoning = 'x' where agent_id = %s", (a,)),
            ("an approval decision", "update agent_change_decision set reason = 'x' where approval_id in (select id from agent_change_approval where agent_id = %s)", (a,)),
            ("a change record", "update agent_change_approval set widening = false where agent_id = %s", (a,)),
            ("a suspension", "update agent_suspension set reason = 'x' where agent_id = %s", (a,)),
            ("an incident", "update agent_incident set what = 'x' where agent_id = %s", (a,)),
            ("a review", "update agent_output_review set outcome = 'accepted' where agent_id = %s", (a,)),
            ("an evidence export", "update agent_evidence_export set pack_hash = 'x' where agent_id = %s", (a,)),
            ("the list of levels", "update agent_risk_level_list set enforce = false where tenant_id = %s", (CANARY,)),
        ):
            with db() as conn:
                try:
                    conn.execute(sql, params)
                    refused = False
                except pgdb.errors.DatabaseError as exc:
                    refused = "never edited" in str(exc)
            check(f"the database refuses an edit of {label}", refused)
    finally:
        teardown()
    return summary("U195")


if __name__ == "__main__":
    sys.exit(main())
