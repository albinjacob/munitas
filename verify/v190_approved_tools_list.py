r"""U205: an organisation approves tools in advance, and a version that adds only those tools does not wait for the people who would otherwise decide it.

Run in the API container, which holds the API's code and reaches the database:

    docker compose exec -T munitas-api python /verify/v190_approved_tools_list.py

Everything is made up here, in the test organisation: its own agents, versions, levels and list, named U205. This proves, per item:

  * who may write the list: only the AI risk owner; the data protection officer, a data engineer, a data custodian and the reviewer are refused with the reason; anybody signed in may read it;
  * what an entry must say: a name, a description, what the tool does, how far it is approved, the highest risk level it is approved for, the conditions (unless it only reads), a reason and
    a date to look at it again; each mistake is refused in words, and nothing is written when one entry is wrong;
  * every change is a new version with who, when and why, and the earlier versions stay;
  * a version that adds only pre-approved tools, for an agent held at or below each ceiling, is approved in advance and nobody is asked, and the record says which entry and which version of the list;
    a first version is never covered, because its level is decided with it;
  * a tool approved only up to a lower level does not cover an agent held higher; a tool that is not on the list is decided in the normal way, with the officer where the level asks for one;
  * a tool that needs the department's approval asks the department and not the officer, and a mix of the two asks the department only;
  * an entry past its review date approves nothing;
  * the AI risk owner gives the officer's part of a change, writes the risk levels and confirms an agent's level; the data protection officer does none of these, can still stop an agent and
    read its evidence, and cannot resume an agent whose level asks for a second approver.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, timedelta

sys.path.insert(0, "/verify")
sys.path.insert(0, "/app")

from common import (CANARY, CANARY_ADMIN, CUSTODIAN, ENGINEER, OVERSIGHT, REVIEWER, RISK_OWNER, api, bearer_for, check, db,  # noqa: E402
                    fixture_tenant, heading, require_api, summary)

DEPARTMENT = "Verification"
made_agents: list[str] = []

LEVELS = [
    {"name": "Low", "description": "An agent whose output is checked by whoever reads it.", "framework_label": None, "second_approver": False, "blocks": False},
    {"name": "Moderate", "description": "An agent whose output informs a decision a person makes.", "framework_label": None, "second_approver": False, "blocks": False},
    {"name": "High", "description": "An agent whose output can change what happens to a person.", "framework_label": "Example framework: high impact", "second_approver": True, "blocks": False},
    {"name": "Prohibited", "description": "An agent the organisation will not run.", "framework_label": None, "second_approver": False, "blocks": True},
]


def in_days(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def entry(tool: str, **over) -> dict:
    base = {"tool": tool, "description": "Reads the notes already filed for a person.", "reach": "reads", "tier": 1, "ceiling": "high", "conditions": "",
            "reason": "It changes nothing and the data it reads is already reachable.", "review_by": in_days(200)}
    base.update(over)
    return base


READ = entry("u205_read_notes")
QUEUE = entry("u205_queue_review", description="Adds an item to the queue a reviewer works through.", reach="adds_for_review", ceiling="moderate", conditions="A reviewer opens every item.", reason="A person works through the queue.")
DRAFT = entry("u205_draft_request", description="Creates a draft request that a person confirms.", reach="creates_draft", tier=2, ceiling="high", conditions="A named person confirms every draft.", reason="Nothing leaves until a person confirms.")


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


def new_agent(label: str) -> str:
    body = {"tenant_id": CANARY, "name": f"U205 {label} {uuid.uuid4().hex[:6]}", "department_id": department_id(), "registered_by": ENGINEER, "purpose": "verification fixture"}
    made = api("POST", "/agents/register", headers=bearer_for(ENGINEER), json=body)
    if made.status_code != 201:
        raise RuntimeError(f"register failed: {made.status_code} {made.text[:200]}")
    made_agents.append(made.json()["id"])
    return made.json()["id"]


def new_version(agent_id: str, tools: list[str]):
    body = {"code_hash": f"git:{uuid.uuid4().hex}", "source_path": "/checkout/agent", "model_id": "qwen2.5:7b", "registered_by": ENGINEER, "tool_scope": tools, "declares_no_tools": not tools}
    made = api("POST", f"/agents/{agent_id}/versions", headers=bearer_for(ENGINEER), json=body)
    if made.status_code != 201:
        raise RuntimeError(f"version failed: {made.status_code} {made.text[:200]}")
    return made.json()


def approval_of(agent_id: str, version: int) -> dict:
    return next(x for x in api("GET", f"/agents/{agent_id}/change-approvals", headers=bearer_for(REVIEWER)).json()["approvals"] if x["version"] == version)


def write_list(entries: list[dict], who: str = RISK_OWNER, reason: str = "U205 fixture"):
    return api("POST", "/agent-tool-list", headers=bearer_for(who), json={"entries": entries, "reason": reason})


def teardown() -> None:
    with db() as conn:
        for agent in made_agents:
            for table in ("agent_record_chain", "agent_evidence_export", "agent_incident", "agent_suspension", "agent_risk_record", "agent_sponsorship", "agent_reconfirmation"):
                conn.execute(f"delete from {table} where agent_id = %s", (agent,))
            conn.execute("delete from agent_change_decision where approval_id in (select id from agent_change_approval where agent_id = %s)", (agent,))
            conn.execute("delete from agent_change_approval where agent_id = %s", (agent,))
        conn.execute("delete from agent_tool_list where tenant_id = %s", (CANARY,))
        conn.execute("delete from agent_risk_level_list where tenant_id = %s", (CANARY,))
        for table, trigger in (("agent_risk_record", "agent_risk_record_no_edit"), ("agent_incident", "agent_incident_no_edit"), ("agent_record_chain", "agent_record_chain_no_edit")):
            conn.execute(f"alter table {table} enable trigger {trigger}")


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    try:
        with db() as conn:
            conn.execute("delete from agent_tool_list where tenant_id = %s", (CANARY,))
            conn.execute("delete from agent_risk_level_list where tenant_id = %s", (CANARY,))
        check("the officer writes the risk levels the list is measured against", api("POST", "/risk-levels", headers=bearer_for(RISK_OWNER), json={"levels": LEVELS, "enforce": False, "reason": "U205 fixture"}).status_code == 201)

        heading("U205: who writes the list")
        seen = api("GET", "/agent-tool-list", headers=bearer_for(REVIEWER))
        body = seen.json()
        check("anybody signed in reads the list, and an organisation with none has none", seen.status_code == 200 and body["current"]["entries"] == [] and body["current"]["version"] is None, str(body.get("current"))[:120])
        check("the reviewer is told they may not write it", body["may_manage"] is False and body["may_manage_reasons"], str(body["may_manage_reasons"]))
        check("the AI risk owner is told they may", api("GET", "/agent-tool-list", headers=bearer_for(RISK_OWNER)).json()["may_manage"] is True)
        check("a person who is not signed in is refused", api("GET", "/agent-tool-list", headers={}).status_code == 401)
        for who, label in ((OVERSIGHT, "the data protection officer"), (ENGINEER, "a data engineer"), (CUSTODIAN, "a data custodian"), (REVIEWER, "the reviewer"), (CANARY_ADMIN, "the platform administrator")):
            refused = write_list([READ], who)
            check(f"{label} cannot write the list", refused.status_code == 403 and reasons_of(refused), f"HTTP {refused.status_code}: {reasons_of(refused)[:100]}")

        heading("U205: what an entry must say")
        for label, bad_entry, words in (
            ("a name with capitals", entry("U205Read"), "lower case"),
            ("a tool that hands a record on, as pre-approved", entry("u205_hand_off", reach="hands_off", conditions="x"), "tier 2"),
            ("a tool that creates something, with no conditions", entry("u205_make_draft", reach="creates_draft", tier=2), "conditions"),
            ("no description", entry("u205_nothing", description=""), "description"),
            ("no reason", entry("u205_nothing", reason=""), "reason"),
            ("a level the organisation does not have", entry("u205_nothing", ceiling="nonsense"), "risk levels"),
            ("a level that does not allow deployment", entry("u205_nothing", ceiling="prohibited"), "deployed"),
            ("a review date already past", entry("u205_nothing", review_by=in_days(-1)), "already past"),
            ("a review date years away", entry("u205_nothing", review_by=in_days(900)), "within"),
            ("a review date that is not a date", entry("u205_nothing", review_by="soon"), "year-month-day"),
            ("a kind of tool that does not exist", entry("u205_nothing", reach="deletes"), "what it does"),
        ):
            bad = write_list([READ, bad_entry])
            check(f"{label} is refused in words", bad.status_code == 400 and words in reasons_of(bad), f"HTTP {bad.status_code}: {reasons_of(bad)[:120]}")
        twice = write_list([READ, READ])
        check("a tool listed twice is refused", twice.status_code == 400 and "twice" in reasons_of(twice), reasons_of(twice)[:100])
        check("a list with no reason is refused", api("POST", "/agent-tool-list", headers=bearer_for(RISK_OWNER), json={"entries": [READ], "reason": ""}).status_code == 422)
        check("nothing was written by any of those", api("GET", "/agent-tool-list", headers=bearer_for(REVIEWER)).json()["current"]["version"] is None)

        heading("U205: every change is a new version")
        first = write_list([READ, QUEUE, DRAFT])
        check("the AI risk owner writes the list", first.status_code == 201 and first.json()["current"]["version"] == 1, f"HTTP {first.status_code}: {first.text[:140]}")
        second = write_list([READ, QUEUE, DRAFT], reason="U205 second version")
        page = api("GET", "/agent-tool-list", headers=bearer_for(REVIEWER)).json()
        check("a second write is version 2 and the first stays in the history", second.status_code == 201 and page["current"]["version"] == 2 and [h["version"] for h in page["history"]][:2] in ([2, 1], [1, 2]), str(page["history"])[:200])
        check("each version keeps who wrote it, when and why", page["current"]["set_by"] == RISK_OWNER and page["current"]["set_at"] and page["current"]["reason"] == "U205 second version", str(page["current"])[:200])
        check("the entries are returned with whether each is overdue", all(e["overdue"] is False for e in page["current"]["entries"]) and {e["tool"] for e in page["current"]["entries"]} == {READ["tool"], QUEUE["tool"], DRAFT["tool"]})
        with db() as conn:
            try:
                conn.execute("update agent_tool_list set reason = 'changed' where tenant_id = %s", (CANARY,))
                edited = True
            except Exception:   # noqa: BLE001  (the trigger refuses an edit)
                edited = False
        check("the database refuses an edit of a version of the list", edited is False)

        heading("U205: what the list saves the approvers")
        a = new_agent("main")
        v1 = new_version(a, [])
        check("a first version that uses no tools needs nothing", v1["widening"] is False)
        first_agent = new_agent("first")
        new_version(first_agent, [READ["tool"]])
        first_approval = approval_of(first_agent, 1)
        check("a first version is never covered by the list, even when every tool it declares is on it: its level is decided with it", first_approval["state"] == "waiting" and first_approval["needs_officer"] is True and not any(c.get("covered_by") for c in first_approval["changes"]),
              f"{first_approval['state']} officer={first_approval['needs_officer']}")
        v2 = new_version(a, [READ["tool"]])
        ap2 = approval_of(a, v2["version"])
        check("a version that adds only a pre-approved tool is approved in advance", ap2["state"] == "approved_in_advance" and ap2["needs_officer"] is False, f"{ap2['state']} officer={ap2['needs_officer']}")
        covered = [c for c in ap2["changes"] if c.get("covered_by")]
        check("the record says which entry covered it and which version of the list", covered and covered[0]["covered_by"]["tool"] == READ["tool"] and covered[0]["covered_by"]["list_version"] == 2 and covered[0]["covered_by"]["set_by_label"], str(ap2["changes"])[:240])
        check("nobody was asked, so nobody is waited for", not ap2.get("waiting_for"), str(ap2.get("waiting_for")))

        v3 = new_version(a, [READ["tool"], QUEUE["tool"]])
        ap3 = approval_of(a, v3["version"])
        check("a tool approved only up to Moderate does not cover an agent held at High", ap3["state"] == "waiting" and ap3["needs_officer"] is True and not any(c.get("covered_by") for c in ap3["changes"]), f"{ap3['state']} officer={ap3['needs_officer']}")

        v4 = new_version(a, [READ["tool"], DRAFT["tool"]])
        ap4 = approval_of(a, v4["version"])
        check("a tool that needs the department's approval waits for the department alone", ap4["state"] == "waiting" and ap4["needs_officer"] is False and ap4["waiting_for"] == ["an approver of the department that owns the agent"], f"{ap4['state']} {ap4.get('waiting_for')}")
        check("and the record says it was covered", any(c.get("covered_by", {}).get("tier") == 2 for c in ap4["changes"]))
        done = api("POST", f"/agent-change-approvals/{ap4['id']}/decisions", headers=bearer_for(CUSTODIAN), json={"part": "department", "decision": "approved", "reason": "U205 fixture"})
        check("the department approves and the version is approved", done.status_code == 201 and done.json()["approval"]["state"] == "approved", f"HTTP {done.status_code}: {done.text[:140]}")

        v5 = new_version(a, [READ["tool"], DRAFT["tool"], "u205_not_listed"])
        ap5 = approval_of(a, v5["version"])
        check("a tool that is not on the list is decided in the normal way, with the officer", ap5["state"] == "waiting" and ap5["needs_officer"] is True and not any(c.get("covered_by") for c in ap5["changes"]), f"{ap5['state']} officer={ap5['needs_officer']}")

        heading("U205: the officer's part")
        dep = api("POST", f"/agent-change-approvals/{ap5['id']}/decisions", headers=bearer_for(CUSTODIAN), json={"part": "department", "decision": "approved", "reason": "U205 fixture"})
        check("the department approves its part", dep.status_code == 201 and dep.json()["approval"]["state"] == "waiting", f"HTTP {dep.status_code}: {dep.text[:120]}")
        check("the reviewer cannot give the officer's part", api("POST", f"/agent-change-approvals/{ap5['id']}/decisions", headers=bearer_for(REVIEWER), json={"part": "officer", "decision": "approved", "reason": "x"}).status_code == 403)
        off = api("POST", f"/agent-change-approvals/{ap5['id']}/decisions", headers=bearer_for(RISK_OWNER), json={"part": "officer", "decision": "approved", "reason": "U205 fixture"})
        check("the AI risk owner gives the officer's part and the version is approved", off.status_code == 201 and off.json()["approval"]["state"] == "approved", f"HTTP {off.status_code}: {off.text[:140]}")

        v6 = new_version(a, [READ["tool"], DRAFT["tool"], "u205_not_listed", "u205_other_unlisted"])
        ap6 = approval_of(a, v6["version"])
        check("the department approves its part of the next change", api("POST", f"/agent-change-approvals/{ap6['id']}/decisions", headers=bearer_for(CUSTODIAN), json={"part": "department", "decision": "approved", "reason": "x"}).status_code == 201)
        refused_officer = api("POST", f"/agent-change-approvals/{ap6['id']}/decisions", headers=bearer_for(OVERSIGHT), json={"part": "officer", "decision": "approved", "reason": "x"})
        check("the data protection officer cannot give the officer's part, and the refusal names the AI risk owner", refused_officer.status_code == 403 and "the AI risk owner" in reasons_of(refused_officer), f"HTTP {refused_officer.status_code}: {reasons_of(refused_officer)[:120]}")
        check("the AI risk owner gives it", api("POST", f"/agent-change-approvals/{ap6['id']}/decisions", headers=bearer_for(RISK_OWNER), json={"part": "officer", "decision": "approved", "reason": "x"}).status_code == 201)

        heading("U205: what the data protection officer keeps and what moved to the AI risk owner")
        refused_levels = api("POST", "/risk-levels", headers=bearer_for(OVERSIGHT), json={"levels": LEVELS, "enforce": False, "reason": "x"})
        check("the data protection officer cannot write the risk levels", refused_levels.status_code == 403 and "the AI risk owner" in reasons_of(refused_levels), f"HTTP {refused_levels.status_code}: {reasons_of(refused_levels)[:120]}")
        refused_confirm = api("POST", f"/agents/{a}/risk/confirmations", headers=bearer_for(OVERSIGHT), json={"level_key": "high", "reasoning": "x"})
        check("or confirm the level of an agent", refused_confirm.status_code == 403 and "the AI risk owner" in reasons_of(refused_confirm), f"HTTP {refused_confirm.status_code}: {reasons_of(refused_confirm)[:120]}")
        check("the data protection officer can still stop an agent", api("POST", f"/agents/{a}/suspension", headers=bearer_for(OVERSIGHT), json={"reason": "U205 fixture"}).status_code == 201)
        refused_resume = api("POST", f"/agents/{a}/resumption", headers=bearer_for(OVERSIGHT), json={"reason": "x"})
        check("but cannot resume an agent whose level asks for a second approver", refused_resume.status_code == 403 and "the AI risk owner" in reasons_of(refused_resume), f"HTTP {refused_resume.status_code}: {reasons_of(refused_resume)[:120]}")
        check("the AI risk owner resumes it", api("POST", f"/agents/{a}/resumption", headers=bearer_for(RISK_OWNER), json={"reason": "U205 fixture"}).status_code == 201)
        from app import agent_words  # noqa: E402  (the container holds the API's code)
        check("a decision given by somebody who held a role that no longer gives the officer's part is described by the role they held",
              agent_words.officer_role_of(["dpo"]) == "the data protection officer" and agent_words.officer_role_of(["ai_risk_owner"]) == "the AI risk owner" and agent_words.officer_role_of([]) == "the AI risk owner",
              str([agent_words.officer_role_of(["dpo"]), agent_words.officer_role_of(["ai_risk_owner"]), agent_words.officer_role_of([])]))
        check("the data protection officer can still read the evidence of an agent", api("GET", f"/agents/{a}/evidence-pack", headers=bearer_for(OVERSIGHT)).status_code == 200)

        heading("U205: an entry past its review date approves nothing")
        with db() as conn:
            conn.execute("insert into agent_tool_list (tenant_id, version, entries, reason, set_by) select tenant_id, 3, %s::jsonb, 'U205 overdue fixture', %s from agent_tool_list where tenant_id = %s and version = 2",
                         (__import__("json").dumps([{**READ, "review_by": in_days(-3)}]), RISK_OWNER, CANARY))
        shown = api("GET", "/agent-tool-list", headers=bearer_for(REVIEWER)).json()["current"]
        check("the list shows the entry as overdue", shown["version"] == 3 and shown["entries"][0]["overdue"] is True, str(shown)[:160])
        b = new_agent("overdue")
        new_version(b, [])
        vb = new_version(b, [READ["tool"]])
        apb = approval_of(b, vb["version"])
        check("a version that adds it is decided in the normal way", apb["state"] == "waiting" and apb["needs_officer"] is True and not any(c.get("covered_by") for c in apb["changes"]), f"{apb['state']} officer={apb['needs_officer']}")

        heading("U205: when the organisation holds agents to its levels, an approval in advance lets the version be deployed")
        check("the officer turns on holding agents to the levels", api("POST", "/risk-levels", headers=bearer_for(RISK_OWNER), json={"levels": LEVELS, "enforce": True, "reason": "U205 fixture"}).status_code == 201)
        deploy = lambda version_id: api("POST", f"/agents/{a}/deploy", headers=bearer_for(ENGINEER), json={"agent_version_id": version_id})  # noqa: E731
        check("a version approved in advance is deployed", deploy(v2["id"]).status_code == 201)
        blocked = deploy(v3["id"])
        check("a version that is still waiting is refused", blocked.status_code == 409, f"HTTP {blocked.status_code}: {reasons_of(blocked)[:120]}")
    finally:
        teardown()
    return summary("U205")


if __name__ == "__main__":
    sys.exit(main())
