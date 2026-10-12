r"""U198: an agent's first submission is decided once, an agent has a sponsor and is re-confirmed, the records are chained so a change shows, and the evidence pack says which outcomes its records support.

Run in the API container, which holds the API's code and reaches the database:

    docker compose exec -T munitas-api python /verify/v185_agent_oversight_more.py

Everything is made up here, in the test organisation: its own agents, versions and levels, named U198. This proves, per item:

  * a level proposed with an agent's registration is kept, and shown with the first version; the AI risk owner confirms the level and approves that version in one decision, both are
    recorded with the same reason, the department still decides its own part, nobody else may take the officer's decision, and it applies to a first version only;
  * an agent has a sponsor who has to be an approver of its owning department, who may assign one and who may not, and the registry says when there is none;
  * a level may ask for re-confirmation every so many days: the date is worked out from when the level was confirmed or the agent was last re-confirmed, an overdue agent is flagged and, when the
    organisation holds agents to its levels, cannot start a run, and re-confirming clears it; who may re-confirm is decided by policy;
  * every oversight record is in a chain: the check finds a record that was edited, one that was removed, one that was added outside the platform, and a link that was altered, and the
    chain is clean again once the change is undone;
  * the evidence pack carries the head of the chain and a table of which outcomes its records support, and the table cannot name a section the pack does not hold.
"""

from __future__ import annotations

import hashlib
import json
import sys
import uuid

sys.path.insert(0, "/verify")
sys.path.insert(0, "/app")

import pgdb  # noqa: E402
from app import agent_oversight  # noqa: E402
from common import (CANARY, CANARY_ADMIN, CUSTODIAN, ENGINEER, REVIEWER, RESEARCHER, RISK_OWNER, api, bearer_for, check, db,  # noqa: E402
                    fixture_tenant, heading, require_api, summary)


DEPARTMENT = "Verification"
made_agents: list[str] = []
made_runs: list[str] = []

LEVELS = [
    {"name": "Low", "description": "An agent whose output is checked by whoever reads it.", "framework_label": None, "second_approver": False, "blocks": False},
    {"name": "Moderate", "description": "An agent whose output informs a decision a person makes.", "framework_label": None, "second_approver": False, "blocks": False, "reconfirm_days": 100},
    {"name": "High", "description": "An agent whose output can change what happens to a person.", "framework_label": "Example framework: high impact", "second_approver": True, "blocks": False, "reconfirm_days": 1},
    {"name": "Prohibited", "description": "An agent the organisation will not run.", "framework_label": None, "second_approver": False, "blocks": True},
]


def reasons_of(response) -> str:
    try:
        body = response.json().get("detail", {})
        return " ".join(body.get("reasons", [])) if isinstance(body, dict) else str(body)
    except ValueError:
        return response.text[:200]


def department_id() -> str:
    with db() as conn:
        row = conn.execute("select id from department where tenant_id = %s and name = %s", (CANARY, DEPARTMENT)).fetchone()
    return str(row["id"])


def new_agent(label: str, **over) -> str:
    body = {"tenant_id": CANARY, "name": f"U198 {label} {uuid.uuid4().hex[:6]}", "department_id": department_id(), "registered_by": ENGINEER, "purpose": "verification fixture"}
    body.update(over)
    made = api("POST", "/agents/register", headers=bearer_for(ENGINEER), json=body)
    if made.status_code != 201:
        raise RuntimeError(f"register failed: {made.status_code} {made.text[:200]}")
    made_agents.append(made.json()["id"])
    return made.json()["id"]


def new_version(agent_id: str, **over):
    body = {"code_hash": f"git:{uuid.uuid4().hex}", "source_path": "/checkout/agent", "model_id": "qwen2.5:7b", "registered_by": ENGINEER}
    body.update(over)
    return api("POST", f"/agents/{agent_id}/versions", headers=bearer_for(ENGINEER), json=body)


def write_levels(enforce: bool, levels=LEVELS):
    return api("POST", "/risk-levels", headers=bearer_for(RISK_OWNER), json={"levels": levels, "enforce": enforce, "reason": "U198 fixture"})


def approval_of(agent_id: str, version: int, who: str = REVIEWER) -> dict:
    return next(x for x in api("GET", f"/agents/{agent_id}/change-approvals", headers=bearer_for(who)).json()["approvals"] if x["version"] == version)


def head_of(agent_id: str) -> str:
    """The newest hash in an agent's chain, read straight from the table."""
    with db() as conn:
        row = conn.execute("select chain_hash from agent_record_chain where agent_id = %s order by seq desc limit 1", (agent_id,)).fetchone()
    return row["chain_hash"] if row else "0" * 64


def sql(statement: str, params: tuple = ()) -> None:
    with db() as conn:
        conn.execute(statement, params)


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
        for table, trigger in (("agent_risk_record", "agent_risk_record_no_edit"), ("agent_incident", "agent_incident_no_edit"), ("agent_record_chain", "agent_record_chain_no_edit")):
            conn.execute(f"alter table {table} enable trigger {trigger}")


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    try:
        sql("delete from agent_risk_level_list where tenant_id = %s", (CANARY,))
        check("the organisation writes its levels, one of which asks for re-confirmation every day", write_levels(False).status_code == 201)
        listed = api("GET", "/risk-levels", headers=bearer_for(REVIEWER)).json()["current"]["levels"]
        check("the days between re-confirmations are kept with the level and empty where none is set", listed[2]["reconfirm_days"] == 1 and listed[0]["reconfirm_days"] is None, str(listed))
        for label, days, words in (("a negative number of days", -5, "whole number"), ("more than ten years", 99999, "whole number")):
            bad = write_levels(False, [{**LEVELS[0], "reconfirm_days": days}])
            check(f"{label} is refused in words", bad.status_code == 400 and words in reasons_of(bad), f"HTTP {bad.status_code}: {reasons_of(bad)[:100]}")

        # ------------------------------------------------------------------ first submission
        heading("U198: the first submission is decided once")
        proposed = {"level_key": "moderate", "affected": "The people the files are about", "reasoning": "A person reads every output"}
        bad_reg = api("POST", "/agents/register", headers=bearer_for(ENGINEER), json={"tenant_id": CANARY, "name": f"U198 bad {uuid.uuid4().hex[:5]}", "department_id": department_id(), "registered_by": ENGINEER, "purpose": "x", "proposed_level": {**proposed, "level_key": "nonsense"}})
        check("registering with a level that is not in the list is refused, and no agent is made", bad_reg.status_code == 400 and "not one of this organisation's risk levels" in reasons_of(bad_reg), reasons_of(bad_reg)[:100])
        a = new_agent("first", proposed_level=proposed)
        risk = api("GET", f"/agents/{a}/risk", headers=bearer_for(ENGINEER)).json()
        check("a level proposed with the registration is kept, with who and when, and waits for the officer", risk["proposal_waiting"]["level_key"] == "moderate" and risk["proposal_waiting"]["recorded_by"] == ENGINEER and risk["classified"] is False, str(risk["proposal_waiting"]))
        check("the purpose of the proposal is the purpose the agent was registered with", risk["proposal_waiting"]["intended_purpose"] == "verification fixture")
        check("an agent registered with no proposal has none", api("GET", f"/agents/{new_agent('plain')}/risk", headers=bearer_for(ENGINEER)).json()["proposal_waiting"] is None)

        write_levels(True)
        v1 = new_version(a, tool_scope=["read_dataset_version"], declares_no_tools=False)
        check("a first version that declares a tool is a widening", v1.status_code == 201 and v1.json()["widening"] is True, v1.text[:120])
        first = approval_of(a, 1)
        check("the first version carries the proposed level with it", first["first_submission"]["proposal"]["level_key"] == "moderate" and first["first_submission"]["confirmed"] is None, str(first["first_submission"])[:160])
        check("the agent has no confirmed level, so it is held at the strictest and the officer is needed", first["needs_officer"] is True and first["waiting_for"] == ["an approver of the department that owns the agent", "the AI risk owner"], str(first["waiting_for"]))
        v2 = new_version(a, tool_scope=["read_dataset_version", "summarise_counts"], declares_no_tools=False)
        later = approval_of(a, 2)
        check("a later version carries no proposal: its level is not decided with it", later["first_submission"] is None and v2.status_code == 201)
        decide = lambda who, aid, body: api("POST", f"/agent-change-approvals/{aid}/first-submission", headers=bearer_for(who), json=body)  # noqa: E731
        body = {"level_key": "high", "reasoning": "It names people in what it produces"}
        for who, label in ((ENGINEER, "the data engineer who submitted it"), (CUSTODIAN, "a department approver"), (REVIEWER, "the reviewer"), (CANARY_ADMIN, "the platform administrator")):
            refused = decide(who, first["id"], body)
            check(f"{label} cannot take the officer's decision", refused.status_code == 403, f"HTTP {refused.status_code}: {reasons_of(refused)[:100]}")
        check("a later version is refused in words", "not an agent's first version" in reasons_of(decide(RISK_OWNER, later["id"], body)))
        check("a level that is not in the list is refused", decide(RISK_OWNER, first["id"], {**body, "level_key": "nonsense"}).status_code == 400)
        check("a decision with no reason is refused", decide(RISK_OWNER, first["id"], {**body, "reasoning": ""}).status_code == 422)
        dept = api("POST", f"/agent-change-approvals/{first['id']}/decisions", headers=bearer_for(CUSTODIAN), json={"part": "department", "decision": "approved", "reason": "U198 fixture"})
        check("the department still gives its own approval, and it still waits for the officer", dept.status_code == 201 and dept.json()["approval"]["waiting_for"] == ["the AI risk owner"], dept.text[:160])
        done = decide(RISK_OWNER, first["id"], body)
        check("the officer confirms the level and approves the version in one decision", done.status_code == 201 and done.json()["approval"]["state"] == "approved", f"HTTP {done.status_code}: {done.text[:200]}")
        risk = api("GET", f"/agents/{a}/risk", headers=bearer_for(ENGINEER)).json()
        check("the level is confirmed, at the level the officer chose and not the one proposed", risk["classified"] and risk["confirmed"]["level_key"] == "high" and risk["confirmed"]["recorded_by"] == RISK_OWNER, str(risk["confirmed"])[:140])
        check("what is affected came from the proposal", risk["confirmed"]["affected"] == proposed["affected"])
        officer = next(d for d in done.json()["approval"]["decisions"] if d["part"] == "officer")
        check("both records carry the same reason, who and when", officer["reason"] == body["reasoning"] == risk["confirmed"]["reasoning"] and officer["decided_by"] == RISK_OWNER and officer["decided_at"])
        check("the approved first version can be deployed", api("POST", f"/agents/{a}/deploy", headers=bearer_for(ENGINEER), json={"agent_version_id": v1.json()["id"]}).status_code == 201)
        check("a second decision on it is refused", decide(RISK_OWNER, first["id"], body).status_code == 409)
        bare = new_agent("noproposal")
        vb = new_version(bare, tool_scope=["read_dataset_version"], declares_no_tools=False)
        ab = approval_of(bare, 1)
        need = decide(RISK_OWNER, ab["id"], {"level_key": "moderate", "reasoning": "x"})
        check("with no proposal the officer has to say who is affected", need.status_code == 400 and "Say who is affected" in reasons_of(need), reasons_of(need)[:100])
        alone = decide(RISK_OWNER, ab["id"], {"level_key": "moderate", "reasoning": "U198 fixture", "affected": "Whoever the notes are about"})
        check("and then decides without one", alone.status_code == 201 and alone.json()["approval"]["decisions"][0]["part"] == "officer", alone.text[:160])

        # ------------------------------------------------------------------ sponsor
        heading("U198: the sponsor of an agent")
        s = new_agent("sponsor")
        none = api("GET", f"/agents/{s}/sponsorship", headers=bearer_for(REVIEWER)).json()
        check("an agent starts with no sponsor, and the approvers who could be one are listed", none["sponsor"] is None and any(c["id"] == CUSTODIAN for c in none["candidates"]), str(none["candidates"]))
        check("the reviewer is told they may neither assign nor re-confirm, in words", none["may_assign"] is False and none["may_reconfirm"] is False and "may assign its sponsor" in " ".join(none["may_assign_reasons"]), str(none["may_assign_reasons"]))
        check("an agent with no sponsor is flagged in the registry", any(f["key"] == "no_sponsor" for r in api("GET", "/agent-registry", headers=bearer_for(REVIEWER)).json()["agents"] if r["id"] == s for f in r["flags"]))
        refused = api("POST", f"/agents/{s}/sponsor", headers=bearer_for(ENGINEER), json={"sponsor_id": CUSTODIAN, "reason": "x"})
        check("a data engineer cannot assign a sponsor, and is told who can", refused.status_code == 403 and "may assign its sponsor" in reasons_of(refused), reasons_of(refused)[:120])
        wrong = api("POST", f"/agents/{s}/sponsor", headers=bearer_for(CUSTODIAN), json={"sponsor_id": ENGINEER, "reason": "x"})
        check("the sponsor has to be an approver of the owning department", wrong.status_code == 400 and "approver of the department" in reasons_of(wrong), reasons_of(wrong)[:120])
        check("a reason is needed", api("POST", f"/agents/{s}/sponsor", headers=bearer_for(CUSTODIAN), json={"sponsor_id": CUSTODIAN, "reason": ""}).status_code == 422)
        made = api("POST", f"/agents/{s}/sponsor", headers=bearer_for(CUSTODIAN), json={"sponsor_id": CUSTODIAN, "reason": "U198 the department answers for it"})
        check("an approver of the department assigns the sponsor", made.status_code == 201 and made.json()["sponsor"]["sponsor_id"] == CUSTODIAN and made.json()["sponsor"]["assigned_by"] == CUSTODIAN, made.text[:140])
        both = api("POST", f"/agents/{s}/sponsor", headers=bearer_for(RISK_OWNER), json={"sponsor_id": CUSTODIAN, "reason": "U198 the officer confirms"})
        check("the AI risk owner may assign one too", both.status_code == 201)
        got = api("GET", f"/agents/{s}/sponsorship", headers=bearer_for(REVIEWER)).json()
        check("the newest is the current sponsor, who can act, and both stay in the history", got["sponsor"]["assigned_by"] == RISK_OWNER and got["sponsor_can_act"] is True and len(got["history"]) == 2, str(got["history"])[:200])
        check("the registry no longer flags it", not any(f["key"] == "no_sponsor" for r in api("GET", "/agent-registry", headers=bearer_for(REVIEWER)).json()["agents"] if r["id"] == s for f in r["flags"]))

        # ------------------------------------------------------------------ re-confirmation
        heading("U198: re-confirmation")
        r = new_agent("reconfirm")
        new_version(r, tool_scope=[], declares_no_tools=True)
        classify = api("POST", f"/agents/{r}/risk/confirmations", headers=bearer_for(RISK_OWNER), json={"level_key": "high", "reasoning": "U198 fixture", "affected": "Whoever it concerns"})
        check("an agent confirmed at the level that asks for it has a due date", classify.status_code == 201)
        clock = api("GET", f"/agents/{r}/sponsorship", headers=bearer_for(REVIEWER)).json()["reconfirm"]
        check("the clock runs from the day the level was confirmed, for as many days as the level says", clock["days"] == 1 and clock["last_at"] and clock["due_at"] and clock["overdue"] is False, str(clock))
        sql("alter table agent_risk_record disable trigger agent_risk_record_no_edit")
        sql("update agent_risk_record set recorded_at = now() - interval '3 days' where agent_id = %s", (r,))
        sql("alter table agent_risk_record enable trigger agent_risk_record_no_edit")
        late = api("GET", f"/agents/{r}/sponsorship", headers=bearer_for(REVIEWER)).json()["reconfirm"]
        check("three days on, it is overdue", late["overdue"] is True, str(late))
        check("and the registry flags it", any(f["key"] == "reconfirm_overdue" for x in api("GET", "/agent-registry", headers=bearer_for(REVIEWER)).json()["agents"] if x["id"] == r for f in x["flags"]))
        run = api("POST", f"/agents/{r}/runs", headers=bearer_for(ENGINEER), json={"purpose": "u198"})
        check("while the organisation holds agents to its levels, an overdue agent cannot start a run, and the reason says who can re-confirm", run.status_code == 409 and "due to be re-confirmed" in reasons_of(run) and "AI risk owner" in reasons_of(run), reasons_of(run)[:200])
        for who, label in ((REVIEWER, "the reviewer"), (ENGINEER, "a data engineer"), (CANARY_ADMIN, "the platform administrator")):
            refused = api("POST", f"/agents/{r}/reconfirmation", headers=bearer_for(who), json={"note": "x"})
            check(f"{label} cannot re-confirm an agent", refused.status_code == 403 and "may re-confirm it" in reasons_of(refused), f"HTTP {refused.status_code}: {reasons_of(refused)[:100]}")
        check("a re-confirmation with nothing said is refused", api("POST", f"/agents/{r}/reconfirmation", headers=bearer_for(CUSTODIAN), json={"note": ""}).status_code == 422)
        done_r = api("POST", f"/agents/{r}/reconfirmation", headers=bearer_for(CUSTODIAN), json={"note": "U198 still needed, still High"})
        check("an approver of the department re-confirms it", done_r.status_code == 201 and done_r.json()["reconfirmation"]["confirmed_by"] == CUSTODIAN, done_r.text[:140])
        check("it is no longer overdue, and the next due date is a day on", done_r.json()["reconfirm"]["overdue"] is False and done_r.json()["reconfirm"]["due_at"] > done_r.json()["reconfirm"]["last_at"], str(done_r.json()["reconfirm"]))
        check("the sponsor re-confirms too", api("POST", f"/agents/{s}/reconfirmation", headers=bearer_for(CUSTODIAN), json={"note": "U198 as sponsor"}).status_code == 201)
        check("the AI risk owner may", api("POST", f"/agents/{r}/reconfirmation", headers=bearer_for(RISK_OWNER), json={"note": "U198 officer"}).status_code == 201)
        soon = new_agent("soon")
        api("POST", f"/agents/{soon}/risk/confirmations", headers=bearer_for(RISK_OWNER), json={"level_key": "moderate", "reasoning": "U198 fixture", "affected": "Whoever it concerns"})
        fresh = api("GET", f"/agents/{soon}/sponsorship", headers=bearer_for(REVIEWER)).json()["reconfirm"]
        check("a clock of 100 days that has just started is not due soon", fresh["days"] == 100 and fresh["due_soon"] is False and fresh["overdue"] is False, str(fresh))
        sql("alter table agent_risk_record disable trigger agent_risk_record_no_edit")
        sql("update agent_risk_record set recorded_at = now() - interval '85 days' where agent_id = %s", (soon,))
        sql("alter table agent_risk_record enable trigger agent_risk_record_no_edit")
        near = api("GET", f"/agents/{soon}/sponsorship", headers=bearer_for(REVIEWER)).json()["reconfirm"]
        check("85 days in, in the last fifth, it is due soon and not yet overdue", near["due_soon"] is True and near["overdue"] is False, str(near))
        check("and the registry says so", any(f["key"] == "reconfirm_soon" for x in api("GET", "/agent-registry", headers=bearer_for(REVIEWER)).json()["agents"] if x["id"] == soon for f in x["flags"]))
        write_levels(False)
        sql("alter table agent_risk_record disable trigger agent_risk_record_no_edit")
        sql("update agent_risk_record set recorded_at = now() - interval '9 days' where agent_id = %s", (r,))
        sql("alter table agent_risk_record enable trigger agent_risk_record_no_edit")
        sql("delete from agent_reconfirmation where agent_id = %s", (r,))
        sql("delete from agent_record_chain where agent_id = %s and kind = 'reconfirmation'", (r,))
        quiet = api("POST", f"/agents/{r}/runs", headers=bearer_for(ENGINEER), json={"purpose": "u198"})
        check("when the organisation does not hold agents to its levels, being overdue stops nothing", "due to be re-confirmed" not in reasons_of(quiet), reasons_of(quiet)[:140])
        write_levels(True)

        # ------------------------------------------------------------------ the chain
        heading("U198: a change to a record shows")
        c = new_agent("chain")
        new_version(c, tool_scope=[], declares_no_tools=True)
        api("POST", f"/agents/{c}/risk/proposals", headers=bearer_for(ENGINEER), json={"level_key": "moderate", "intended_purpose": "x", "affected": "y", "reasoning": "z"})
        api("POST", f"/agents/{c}/incidents", headers=bearer_for(ENGINEER), json={"what": "U198 the first thing that happened"})
        api("POST", f"/agents/{c}/incidents", headers=bearer_for(ENGINEER), json={"what": "U198 the second thing that happened"})
        api("POST", f"/agents/{c}/suspension", headers=bearer_for(CUSTODIAN), json={"reason": "U198 stop"})
        verified = api("GET", f"/agents/{c}/record-chain", headers=bearer_for(RISK_OWNER))
        check("the officer can check an agent's chain", verified.status_code == 200, f"HTTP {verified.status_code}: {verified.text[:140]}")
        clean = verified.json()
        check("a chain nobody touched is clean, and counts what it covers", clean["ok"] is True and clean["problems"] == [] and clean["checked"] >= 5, str(clean)[:200])
        check("its head is what the evidence pack will carry", clean["head"] == head_of(c) and len(clean["head"]) == 64)
        for who, label in ((REVIEWER, "the reviewer"), (RESEARCHER, "a researcher"), (ENGINEER, "a data engineer")):
            blocked = api("GET", f"/agents/{c}/record-chain", headers=bearer_for(who))
            check(f"{label} cannot check the chain", blocked.status_code == 403 and "evidence pack" in reasons_of(blocked), f"HTTP {blocked.status_code}")
        check("a department approver can", api("GET", f"/agents/{c}/record-chain", headers=bearer_for(CUSTODIAN)).status_code == 200)

        sql("alter table agent_incident disable trigger agent_incident_no_edit")
        sql("update agent_incident set what = 'U198 changed afterwards' where agent_id = %s and what like %s", (c, "%first thing%"))
        edited = api("GET", f"/agents/{c}/record-chain", headers=bearer_for(RISK_OWNER)).json()
        check("an edited record is found, and the platform says what", edited["ok"] is False and any(p["kind"] == "incident" and "no longer says what it said" in p["what"] for p in edited["problems"]), str(edited["problems"])[:240])
        sql("update agent_incident set what = 'U198 the first thing that happened' where agent_id = %s and what = 'U198 changed afterwards'", (c,))
        sql("alter table agent_incident enable trigger agent_incident_no_edit")
        check("once the change is undone the chain is clean again", api("GET", f"/agents/{c}/record-chain", headers=bearer_for(RISK_OWNER)).json()["ok"] is True)

        sql("delete from agent_incident where agent_id = %s and what like %s", (c, "%second thing%"))
        gone = api("GET", f"/agents/{c}/record-chain", headers=bearer_for(RISK_OWNER)).json()
        check("a record that was removed is found", gone["ok"] is False and any("is gone" in p["what"] for p in gone["problems"]), str(gone["problems"])[:240])

        d = new_agent("chain2")
        api("POST", f"/agents/{d}/incidents", headers=bearer_for(ENGINEER), json={"what": "U198 recorded properly"})
        check("a second agent's chain is separate and clean", api("GET", f"/agents/{d}/record-chain", headers=bearer_for(RISK_OWNER)).json()["ok"] is True)
        sql("insert into agent_incident (id, tenant_id, agent_id, what, reported_by) values (%s, %s, %s, 'U198 slipped in with SQL', %s)", (str(uuid.uuid4()), CANARY, d, ENGINEER))
        added = api("GET", f"/agents/{d}/record-chain", headers=bearer_for(RISK_OWNER)).json()
        check("a record added outside the platform is found", added["ok"] is False and any("added outside the platform" in p["what"] for p in added["problems"]), str(added["problems"])[:240])

        e = new_agent("chain3")
        api("POST", f"/agents/{e}/incidents", headers=bearer_for(ENGINEER), json={"what": "U198 one"})
        api("POST", f"/agents/{e}/incidents", headers=bearer_for(ENGINEER), json={"what": "U198 two"})
        sql("alter table agent_record_chain disable trigger agent_record_chain_no_edit")
        sql("update agent_record_chain set record_hash = %s where agent_id = %s and seq = (select min(seq) from agent_record_chain where agent_id = %s)", ("0" * 64, e, e))
        sql("alter table agent_record_chain enable trigger agent_record_chain_no_edit")
        altered = api("GET", f"/agents/{e}/record-chain", headers=bearer_for(RISK_OWNER)).json()
        check("an altered link is found", altered["ok"] is False and any("does not match" in p["what"] or "no longer says" in p["what"] for p in altered["problems"]), str(altered["problems"])[:240])
        check("and it breaks the links after it, not only itself", len(altered["problems"]) >= 1)
        for table, trigger in (("agent_incident", "agent_incident_no_edit"), ("agent_record_chain", "agent_record_chain_no_edit")):
            with db() as conn:
                try:
                    conn.execute(f"update {table} set tenant_id = tenant_id")
                    refused = False
                except pgdb.errors.DatabaseError as exc:
                    refused = "never edited" in str(exc)
            check(f"the database still refuses an edit of {table}", refused)

        # ------------------------------------------------------------------ reviewing a finished run
        heading("U198: reviewing what a finished run produced")
        w = new_agent("review")
        wv = new_version(w, tool_scope=[], declares_no_tools=True).json()["id"]

        def finished(status: str = "succeeded", findings=None, ended: str = "90 seconds") -> str:
            run_id = str(uuid.uuid4())
            with db() as conn:
                conn.execute(
                    f"""insert into agent_run (id, tenant_id, agent_id, agent_version_id, status, purpose, requested_by, execution_mode, findings, ended_at)
                        values (%s, %s, %s, %s, %s, 'U198 fixture', %s, 'sandboxed', %s, now() - interval '{ended}')""",
                    (run_id, CANARY, w, wv, status, ENGINEER, json.dumps(findings if findings is not None else [])))
            made_runs.append(run_id)
            return run_id

        notes = [{"document": "t-1.txt", "note": "DRAFT NOTE for t-1.txt\nPlan:\n  - Book a follow up in three weeks. (line 10)"}]
        draft_output = [{"agent": "x", "drafts": notes}]
        review = lambda who, run_id, **body: api("POST", f"/agents/runs/{run_id}/review", headers=bearer_for(who), json=body)  # noqa: E731
        r1 = finished(findings=draft_output)
        check("the person who started the run cannot review what it produced, and is told why", review(ENGINEER, r1, outcome="accepted").status_code == 403 and "cannot be reviewed by the person who started it" in reasons_of(review(ENGINEER, r1, outcome="accepted")))
        check("a run that is still running has nothing to review", review(REVIEWER, finished("running"), outcome="accepted").status_code == 400)
        waiting_run = review(REVIEWER, finished("awaiting_approval", draft_output), outcome="accepted")
        check("a run at its approval step is approved or refused there, and the platform says so", waiting_run.status_code == 400 and "approve or refuse it there" in reasons_of(waiting_run), reasons_of(waiting_run))
        none_made = review(REVIEWER, finished("succeeded", []), outcome="accepted")
        check("a run that produced nothing has nothing to review", none_made.status_code == 400 and "produced nothing" in reasons_of(none_made), reasons_of(none_made))
        check("an outcome that is none of the three is refused", review(REVIEWER, r1, outcome="liked").status_code == 400)
        check("a run of nobody's is a 404", review(REVIEWER, str(uuid.uuid4()), outcome="accepted").status_code == 404)
        ok = review(REVIEWER, r1, outcome="accepted")
        check("somebody else accepts it", ok.status_code == 201 and ok.json()["review"]["outcome"] == "accepted" and ok.json()["review"]["seconds_taken"] >= 90, f"HTTP {ok.status_code}: {ok.text[:140]}")
        with db() as conn:
            row = conn.execute("select * from agent_output_review where run_id = %s", (r1,)).fetchone()
        check("the draft's hash is worked out by the platform from the run's output, and nothing from the caller is in it", row["draft_hash"] == agent_oversight.sha256_of(draft_output) and row["final_hash"] is None and row["reviewed_by"] == REVIEWER, str(row["draft_hash"]))
        check("it is in the agent's chain", api("GET", f"/agents/{w}/record-chain", headers=bearer_for(RISK_OWNER)).json()["ok"] is True)
        check("a run is reviewed once", review(CUSTODIAN, r1, outcome="refused", reason="x").status_code == 409)

        r2 = finished(findings=draft_output)
        check("changing it needs the text the reviewer ended with", review(REVIEWER, r2, outcome="changed").status_code == 400 and "text you ended with" in reasons_of(review(REVIEWER, r2, outcome="changed")))
        same = review(REVIEWER, r2, outcome="changed", final_text=notes[0]["note"] + "\n")
        check("sending the draft back unchanged is not a change, and the platform says to accept it", same.status_code == 400 and "Accept it instead" in reasons_of(same), reasons_of(same))
        edited = "DRAFT NOTE for t-1.txt\nPlan:\n  - Book a follow up in two weeks."
        changed = review(REVIEWER, r2, outcome="changed", final_text=edited)
        check("a change is recorded", changed.status_code == 201 and changed.json()["review"]["outcome"] == "changed", f"HTTP {changed.status_code}: {changed.text[:140]}")
        with db() as conn:
            row2 = conn.execute("select * from agent_output_review where run_id = %s", (r2,)).fetchone()
            everything = json.dumps({k: str(v) for k, v in row2.items()})
        check("the hash of the text the reviewer ended with is kept", row2["final_hash"] == "sha256:" + hashlib.sha256(edited.encode()).hexdigest(), str(row2["final_hash"]))
        check("the text itself is kept nowhere in the record", "two weeks" not in everything and "Book a follow up" not in everything)

        r3 = finished(findings=draft_output)
        check("refusing needs a reason", review(CUSTODIAN, r3, outcome="refused").status_code == 400 and "Say why" in reasons_of(review(CUSTODIAN, r3, outcome="refused")))
        refused_it = review(CUSTODIAN, r3, outcome="refused", reason="U198 the plan is not what was said")
        check("a refusal is recorded with its reason, and the run itself stays as it finished", refused_it.status_code == 201, refused_it.text[:140])
        with db() as conn:
            check("the run is still succeeded", conn.execute("select status from agent_run where id = %s", (r3,)).fetchone()["status"] == "succeeded")
        other = finished(findings=[{"agent": "x", "anything": {"a": 1}}])
        any_text = review(RISK_OWNER, other, outcome="changed", final_text="Whatever the reviewer ended with")
        check("output the platform does not read as drafts can still be changed, with no comparison", any_text.status_code == 201, any_text.text[:140])
        figures = api("GET", f"/agents/{w}/oversight", headers=bearer_for(REVIEWER)).json()["review_figures"]
        check("the figures count them for the agent: one accepted, two changed, one refused, by four people of whom three are different", (figures["reviewed"], figures["accepted_unchanged"], figures["changed"], figures["refused"]) == (4, 1, 2, 1) and figures["different_people"] == 3, str(figures))

        # ------------------------------------------------------------------ the pack
        heading("U198: the evidence pack carries the chain and the mapping")
        made_pack = api("GET", f"/agents/{a}/evidence-pack", headers=bearer_for(RISK_OWNER))
        check("the officer makes a pack", made_pack.status_code == 200, f"HTTP {made_pack.status_code}: {made_pack.text[:200]}")
        pack = made_pack.json()["pack"]
        check("it carries the head of the chain and says whether the chain is clean", pack["integrity"]["ok"] is True and pack["integrity"]["head"] == head_of(a) and pack["integrity"]["checked"] >= 6, str(pack["integrity"])[:200])
        check("it carries the sponsorship section, empty for an agent with no sponsor", pack["sponsorship"]["sponsor"] is None and pack["sponsorship"]["history"] == [])
        mp = pack["framework_mapping"]
        check("the mapping says it is not a statement of compliance, and names its sources", "not a statement" in mp["about"] and len(mp["sources"]) == 2 and all("2026-10-11" in x for x in mp["sources"]))
        by_outcome = {row["outcome"]: row for row in mp["rows"]}
        check("every outcome names a place in each framework", all(row["nist_ai_rmf"] and row["eu_ai_act"] for row in mp["rows"]) and len(mp["rows"]) == 12)
        check("every section a row says supports it is in the pack", all(s in pack for row in mp["rows"] for s in row["supported_by"]), str([s for row in mp["rows"] for s in row["supported_by"] if s not in pack]))
        check("a row that names sections, all of them in the pack, is marked as records", by_outcome["A person can stop the agent"]["provided"] == "records" and by_outcome["A person can stop the agent"]["all_present_in_this_pack"] is True)
        check("outputs tested for quality are marked not provided, with nothing named as support", by_outcome["Outputs are tested for quality"]["provided"] == "not provided" and by_outcome["Outputs are tested for quality"]["supported_by"] == [])
        check("telling the people affected is marked not provided", by_outcome["The people affected, and workers, are told that the agent is used"]["provided"] == "not provided")
        check("outside agents are marked as declared and not checked", by_outcome["Outside and third-party agents are identified"]["provided"] == "declared")
        check("the mapping never claims what the pack lacks: a row marked records has its sections", all(row["supported_by"] and row["all_present_in_this_pack"] for row in mp["rows"] if row["provided"] == "records"))
        check("ISO/IEC 42001 is said to be left out, not silently skipped", "ISO/IEC 42001 is not mapped" in mp["about"])
    finally:
        teardown()
    return summary("U198")


if __name__ == "__main__":
    sys.exit(main())
