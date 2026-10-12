"""Add the agents of the Health story to the demonstration organisation, and walk them through the real approvals.

The organisation's own people do each step: the engineer registers an agent and its versions, a department's approver gives the department's approval and names the sponsor, the data
protection officer writes the risk levels and decides each agent's level with its first version, and the network architect is left with a pending request. Everything goes through the
control plane, never into the database, so every state shown here is one the platform would allow.

It adds, and does nothing to anything that is already there (each agent is looked up by name first):

  1. consultation-note-drafter       drafts a note from a consultation for a clinician to sign; uses no tools
     vendor-ambient-scribe           the same job done by a vendor, run outside the platform
  2. prior-authorization-assistant   prepares a request to an insurer; a second version that adds a submit tool and a payer address is left waiting for approval
  3. trial-matching-agent            suggests people who may fit a research study
  4. chest-pain-triage-agent         suggests how urgent a case is; has an incident and is suspended
  5. care-gap-finder                 lists people who missed a recommended check

It also writes the organisation's risk levels (once, and switches on holding agents to them) and classifies the agent that was already here, so that it still runs.

    .venv\\Scripts\\python.exe scripts/seed/seed-health-agents.py

Health is walkthrough data. Tests never use it.
"""

from __future__ import annotations

import sys
import time

import httpx

from seed_common import bearer_for, expect
from ports_config import PORTS  # noqa: E402

API = f"http://localhost:{PORTS['munitas_api_http']}"
TENANT = "health"

ENGINEER = "eng-devi"
OFFICER = "risk-okafor"
ARCHITECT = "arch-turner"
# Each department's approver, who is also its custodian.
APPROVER = {"Cardiology": "cust-hartley", "Oncology": "cust-mensah", "Orthopaedics": "cust-lindqvist", "Radiology": "cust-okonjo"}

LEVELS = [
    {"name": "Low", "description": "An agent whose output is read and checked by a person, and that reaches nothing outside the platform.", "framework_label": None, "second_approver": False, "blocks": False,
     "reconfirm_days": None},
    {"name": "Moderate", "description": "An agent whose output informs a decision a person makes.", "framework_label": None, "second_approver": False, "blocks": False, "reconfirm_days": 180},
    {"name": "High", "description": "An agent whose output can change what happens to a person, or that acts outside the platform.", "framework_label": "Example label: EU AI Act high-risk use",
     "second_approver": True, "blocks": False, "reconfirm_days": 90},
    {"name": "Not allowed", "description": "An agent the organisation will not run. No version of it can be deployed.", "framework_label": None, "second_approver": False, "blocks": True,
     "reconfirm_days": None},
]


def call(method: str, path: str, who: str, *allowed: int, doing: str, **kwargs) -> httpx.Response:
    r = httpx.request(method, f"{API}{path}", headers=bearer_for(who), timeout=60.0, **kwargs)
    expect(r, *allowed, doing=doing)
    return r


def department_id(name: str) -> str:
    org = call("GET", "/organisation", ENGINEER, 200, doing="reading the organisation", params={"tenant_id": TENANT}).json()
    return next(d["id"] for d in org["departments"] if d["name"] == name)


def existing_agents() -> dict[str, dict]:
    return {a["name"]: a for a in call("GET", "/agents", ENGINEER, 200, doing="listing agents", params={"limit": 200}).json()["agents"]}


def approval_for(agent_id: str, version: int) -> dict:
    return next(a for a in call("GET", f"/agents/{agent_id}/change-approvals", OFFICER, 200, doing="reading the changes").json()["approvals"] if a["version"] == version)


def register(name: str, dept: str, purpose: str, level: tuple[str, str, str], run_by: str = "platform") -> str:
    key, affected, why = level
    r = call("POST", "/agents/register", ENGINEER, 201, doing=f"registering {name}", json={
        "tenant_id": TENANT, "name": name, "department_id": department_id(dept), "registered_by": ENGINEER, "purpose": purpose, "run_by": run_by,
        "proposed_level": {"level_key": key, "affected": affected, "reasoning": why}})
    return r.json()["id"]


def version(agent: str, *, model: str, hosting: str, tools: list[str], hosts: list[str] | None = None) -> dict:
    body = {"code_hash": f"git:{int(time.time() * 1000)}", "source_path": "/checkout/agent", "model_id": model, "model_hosting": hosting, "tool_scope": tools,
            "declares_no_tools": not tools, "registered_by": ENGINEER, "requested_hosts": hosts or []}
    return call("POST", f"/agents/{agent}/versions", ENGINEER, 201, doing="registering a version", json=body).json()


def first_submission(agent: str, dept: str, level: str, why: str, number: int = 1) -> None:
    """The department approves its part, and the officer confirms the level and approves the version in one decision."""
    approval = approval_for(agent, number)
    call("POST", f"/agent-change-approvals/{approval['id']}/decisions", APPROVER[dept], 201, doing="the department's approval",
         json={"part": "department", "decision": "approved", "reason": "The department has looked at what the version is allowed to use and agrees."})
    call("POST", f"/agent-change-approvals/{approval['id']}/first-submission", OFFICER, 201, doing="the officer's decision on the first submission", json={"level_key": level, "reasoning": why})


def deploy(agent: str, version_id: str) -> None:
    call("POST", f"/agents/{agent}/deploy", ENGINEER, 201, doing="deploying", json={"agent_version_id": version_id})


def sponsor(agent: str, dept: str) -> None:
    person = APPROVER[dept]
    call("POST", f"/agents/{agent}/sponsor", person, 201, doing="naming the sponsor", json={"sponsor_id": person, "reason": f"The {dept} department answers for this agent."})


def settle_earlier_changes(agent: str, dept: str) -> None:
    """Decide the changes an agent had before agents were held to the levels, as the organisation would have.

    The agent that was here before this story had its versions registered when nothing was held to a level, so each of them still waits for a decision. All but the newest are approved,
    by the department and then by the officer. The newest asked to call a host that the network architect refused, so the department refuses it.
    """
    waiting = sorted((a for a in call("GET", f"/agents/{agent}/change-approvals", OFFICER, 200, doing="reading its earlier changes").json()["approvals"] if a["state"] == "waiting"), key=lambda a: a["version"])
    for i, a in enumerate(waiting):
        path = f"/agent-change-approvals/{a['id']}/decisions"
        if i == len(waiting) - 1 and len(waiting) > 1:
            call("POST", path, APPROVER[dept], 201, doing="refusing the newest earlier change", json={"part": "department", "decision": "refused", "reason": "The host it asks to call was not approved, so this version is not wanted."})
            continue
        call("POST", path, APPROVER[dept], 201, doing="the department's approval of an earlier change", json={"part": "department", "decision": "approved", "reason": "The department asked for this change and has looked at what it adds."})
        if a["needs_officer"]:
            call("POST", path, OFFICER, 201, doing="the officer's approval of an earlier change", json={"part": "officer", "decision": "approved", "reason": "Held at Moderate, and a person reviews every ranking."})


def main() -> int:
    call("GET", "/health", ENGINEER, 200, doing="reaching the platform")
    levels = call("GET", "/risk-levels", OFFICER, 200, doing="reading the risk levels").json()["current"]
    if levels["built_in"]:
        call("POST", "/risk-levels", OFFICER, 201, doing="writing the risk levels", json={
            "levels": LEVELS, "enforce": True, "reason": "The organisation holds its agents to these levels from now on: each needs a level the AI risk owner has confirmed, and a change that widens an agent is approved first."})
        print("wrote the organisation's risk levels and switched on holding agents to them")
    else:
        print("risk levels already written, left as they are")

    have = existing_agents()

    # The agent that was here before this story: classified, so that it still runs now that agents are held to the levels.
    old = have.get("radiology-intake-triage")
    if old and not call("GET", f"/agents/{old['id']}/risk", OFFICER, 200, doing="reading its level").json()["classified"]:
        call("POST", f"/agents/{old['id']}/risk/confirmations", OFFICER, 201, doing="classifying the earlier agent", json={
            "level_key": "moderate", "affected": "The people whose intake documents it ranks for review", "reasoning": "Classified when the organisation began holding agents to its levels. A person reviews every ranking."})
        sponsor(old["id"], "Radiology")
        print("classified radiology-intake-triage and named its sponsor")
    if old:
        settle_earlier_changes(old["id"], "Radiology")

    made = []

    # 1. the note drafting agent, and the vendor's version of it
    if "consultation-note-drafter" not in have:
        a = register("consultation-note-drafter", "Cardiology", "Drafts a note from a consultation, for a clinician to read, change and sign.",
                     ("moderate", "The people the consultations are about", "A clinician reads and signs every note before it is used."))
        v = version(a, model="note-drafting-model:1", hosting="platform", tools=[])
        call("POST", f"/agents/{a}/risk/confirmations", OFFICER, 201, doing="the officer's level", json={"level_key": "high", "reasoning": "The notes name people and go into their records, so the agent is held at High."})
        deploy(a, v["id"])
        sponsor(a, "Cardiology")
        call("POST", f"/agents/{a}/reconfirmation", APPROVER["Cardiology"], 201, doing="re-confirming", json={"note": "Checked with the clinical lead: still needed, and still held at the right level."})
        made.append("consultation-note-drafter")
    if "vendor-ambient-scribe" not in have:
        a = register("vendor-ambient-scribe", "Cardiology", "A vendor's service that drafts consultation notes. Run by the vendor, outside the platform; the platform keeps its record.",
                     ("high", "The people the consultations are about", "The notes name people and the model runs outside the organisation."), run_by="outside")
        version(a, model="vendor-scribe", hosting="outside", tools=[])
        first_submission(a, "Cardiology", "high", "Run outside the platform, so what it uses is declared by the vendor and not checked. Held at High because the notes name people.")
        sponsor(a, "Cardiology")
        made.append("vendor-ambient-scribe")

    # 2. prior authorization: the first version is approved; the second adds a submit tool and the payer's address and is left waiting
    if "prior-authorization-assistant" not in have:
        a = register("prior-authorization-assistant", "Orthopaedics", "Prepares a request to an insurer for approval of a procedure, for a person to check before it is sent.",
                     ("high", "The people waiting for an insurer's decision", "A request that goes wrong delays a person's care."))
        v1 = version(a, model="authorization-model:1", hosting="platform", tools=["read_dataset_version"])
        first_submission(a, "Orthopaedics", "high", "The agent prepares requests about people's care, so it is held at High.")
        deploy(a, v1["id"])
        sponsor(a, "Orthopaedics")
        version(a, model="authorization-model:1", hosting="platform", tools=["read_dataset_version", "submit_authorization_request"], hosts=["api.payer.example"])
        made.append("prior-authorization-assistant (version 2 left waiting)")

    # 3. trial matching
    if "trial-matching-agent" not in have:
        a = register("trial-matching-agent", "Oncology", "Suggests people whose de-identified records may fit a research study, for a research coordinator to review.",
                     ("moderate", "The people whose records are screened", "A research coordinator reviews every suggestion, and the agent reads de-identified data only."))
        v = version(a, model="matching-model:1", hosting="platform", tools=["read_dataset_version", "summarise_counts"])
        first_submission(a, "Oncology", "moderate", "A research coordinator reviews every suggestion, so Moderate is enough.")
        deploy(a, v["id"])
        sponsor(a, "Oncology")
        call("POST", f"/agents/{a}/reconfirmation", APPROVER["Oncology"], 201, doing="re-confirming", json={"note": "The study is still open, and the level still fits."})
        made.append("trial-matching-agent")

    # 4. triage: held at High, with an incident, and suspended while it is looked at
    if "chest-pain-triage-agent" not in have:
        a = register("chest-pain-triage-agent", "Cardiology", "Suggests how urgent a case is, from what a person reports, for a nurse to decide.",
                     ("high", "People who report chest pain", "A wrong suggestion could delay care."))
        v = version(a, model="triage-model:1", hosting="platform", tools=["read_dataset_version", "list_review_queue"])
        first_submission(a, "Cardiology", "high", "A wrong suggestion could delay care, so the agent is held at High.")
        deploy(a, v["id"])
        sponsor(a, "Cardiology")
        call("POST", f"/agents/{a}/incidents", ENGINEER, 201, doing="reporting an incident", json={
            "what": "A suggestion ranked a case lower than the nurse judged it should be.", "reported_onward": "The clinical safety group was told.",
            "action_taken": "The agent was suspended while its rankings are reviewed."})
        call("POST", f"/agents/{a}/suspension", APPROVER["Cardiology"], 201, doing="suspending", json={"reason": "Suspended while its rankings are reviewed after a nurse disagreed with one."})
        made.append("chest-pain-triage-agent (suspended)")

    # 5. care gaps
    if "care-gap-finder" not in have:
        a = register("care-gap-finder", "Oncology", "Lists people who missed a recommended screening or follow-up, for a care team to contact.",
                     ("moderate", "The people who may have missed a check", "A care team reviews the list before anybody is contacted."))
        v = version(a, model="care-gap-model:1", hosting="platform", tools=["read_dataset_version", "summarise_counts"])
        first_submission(a, "Oncology", "moderate", "A care team reviews every list, so Moderate is enough.")
        deploy(a, v["id"])
        sponsor(a, "Oncology")
        made.append("care-gap-finder")

    print("added:", ", ".join(made) if made else "nothing: every agent was already here")
    registry = call("GET", "/agent-registry", OFFICER, 200, doing="reading the registry").json()["agents"]
    print(f"the registry now lists {len(registry)} agents")
    for row in registry:
        level = row["risk_level"]["name"] if row["risk_level"] else "not classified"
        print(f"  {row['name']:34} {row['department_name']:13} {level:15} sponsor {row['sponsor'] or 'none':10} flags: {', '.join(f['label'] for f in row['flags']) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
