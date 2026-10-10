"""U165: the way to ask for a version follows what the version holds, and the platform never takes an approval that leads to nothing.

A version of locked files cannot be handed over whole: what storage holds of it is bytes nobody outside the platform can read. A table with locked
columns can be handed over in its readable columns only. So one platform answer (`/dataset-versions/{id}/readability`) says, for each version, what is
locked and what permission to read the whole of it is worth, and everything else follows it: the request for the whole version is refused when it would
read nothing, an old request waiting for a decision is not approved, the access mark of a version of locked files says what the person holds of it and not
what their role covers, and the file door no longer tells a person without a role that their role is what stops them.

Checked here one item at a time, against the live platform, on a version of three locked files, a table of thirty people with locked columns, and a
version with nothing locked: the answer for each, what is refused and what is allowed, that nothing is written for a refusal, that a request already
waiting is refused at approval and can still be rejected, what the mark says before and after a permission to open files, and the reasons the file door
gives. The agent route that files a request for an agent is exercised by the neighbouring check for agents.

    docker compose exec -T munitas-api python /verify/v158_ways_to_ask.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

import httpx  # noqa: E402

from common import API, CANARY, api, bearer_for, check, db, fixture_contract, fixture_tenant, fixture_version, heading, require_api, summary  # noqa: E402
from v128_person_keys_erasure import CUSTODIAN, ENGINEER, RESEARCHER, good_contract, register_dataset, reset_keys  # noqa: E402
from v137_locked_files import make_wav, upload_whole  # noqa: E402
from v140_custom_steps_and_files import reasons_of  # noqa: E402
from v51_agent_access_request import register_agent, seed_review_queue  # noqa: E402
from v157_file_leases import ask as ask_for_files, decide as decide_files  # noqa: E402

ROLE_SENTENCE = "holds no role that may open files"


def readability(version: str, who: str = RESEARCHER) -> httpx.Response:
    return api("GET", f"/dataset-versions/{version}/readability", headers=bearer_for(who))


def ask_whole(version: str, who: str = RESEARCHER, **over) -> httpx.Response:
    body = {"tenant_id": CANARY, "principal": who, "dataset_version_id": version, "purpose": "measure recall", "justification": "needs real values", "ttl_hours": 4, **over}
    return api("POST", "/leases/requests", json=body, headers=bearer_for(who))


def requests_for(version: str, who: str = RESEARCHER) -> int:
    with db() as conn:
        return conn.execute("select count(*) as n from lease_request where principal = %s and dataset_version_id = %s", (who, version)).fetchone()["n"]


def mark_of(version: str, who: str = RESEARCHER) -> dict:
    r = api("POST", "/access-preview", json={"version_ids": [version]}, headers=bearer_for(who))
    return r.json()["versions"][version]


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    from app import db as app_db
    if app_db.pool.closed:
        app_db.pool.open()
    tag = uuid.uuid4().hex[:8]

    heading("Three versions: locked files, a table with locked columns, and nothing locked")
    dataset = register_dataset(ENGINEER)
    for i in range(3):
        upload_whole(dataset, f"w-{i + 1}.wav", make_wav(40_000 + i * 3_000), f"W-{tag}-{i}")
    files = api("POST", f"/datasets/{dataset}/seal-files", headers=bearer_for(ENGINEER))
    check("three locked files are sealed", files.status_code == 201, files.text[:140])
    locked_files = files.json()["id"]

    rows = [{"visit_id": f"v{i:03d}", "clinic_number": f"M{i:03d}-{tag}", "patient_name": f"Person{i:03d} Zzexample{tag}", "diagnosis": f"dx-{i:03d}",
             "clinic": "north" if i % 2 else "south", "age": 20 + i} for i in range(1, 31)]
    table = api("POST", f"/datasets/{register_dataset(ENGINEER)}/seal-table", headers=bearer_for(ENGINEER), json={"contract": good_contract(), "rows": rows})
    check("a table of thirty people with locked columns is sealed", table.status_code == 201, f"{table.status_code} {table.text[:140]}")
    locked_columns = table.json()["id"]

    plain = fixture_version(CANARY, fixture_contract())["id"]
    check("a version with nothing locked is sealed", bool(plain))

    heading("What each version holds")
    f = readability(locked_files).json()
    check("locked files: shape files, a whole-version permission reads nothing, three files, and the answer says so in words",
          f["shape"] == "files" and f["reads"] == "nothing" and f["locked_files"] == 3 and "ask to open a few" in (f["says"] or ""), str(f)[:240])
    c = readability(locked_columns).json()
    check("a table with locked columns: shape columns, a whole-version permission reads the readable columns, and the answer says so",
          c["shape"] == "columns" and c["reads"] == "readable_columns" and c["locked_columns"] > 0 and c["readable_columns"] > 0 and "locked columns" in (c["says"] or ""), str(c)[:240])
    p = readability(plain).json()
    check("nothing locked: shape plain, everything is read, and there is nothing to say", p["shape"] == "plain" and p["reads"] == "all" and p["says"] is None, str(p)[:200])
    check("the answer holds counts and a sentence, never a file name or a person",
          set(f) == {"version_id", "shape", "reads", "locked_files", "locked_columns", "readable_columns", "says"} and not any(k in str(f).lower() for k in ("w-1", "person_code", ".wav")), str(f)[:200])
    held = api("GET", f"/dataset-versions/{locked_files}/holds", headers=bearer_for(RESEARCHER)).json()
    check("what the version page says it holds agrees: three files, not a table of rows, and the list that describes them is not counted as a file",
          held["files"] == 3 and held["table"] is None and "table" not in held["formats"] and held["kind"] == "audio", str(held)[:260])
    check("a version that does not exist is not found", readability(str(uuid.uuid4())).status_code == 404)
    check("with no session there is no answer", httpx.get(f"{API}/dataset-versions/{locked_files}/readability", timeout=30.0).status_code == 401)

    heading("Asking for the whole version")
    before = requests_for(locked_files)
    refused = ask_whole(locked_files)
    check("asking for the whole of a version of locked files is refused, in words, with the way that works",
          refused.status_code == 400 and "ask to open them on the version's page" in reasons_of(refused) and "every file in this version is locked" in reasons_of(refused),
          f"{refused.status_code} {reasons_of(refused)[:200]}")
    check("a refusal writes nothing: no request is waiting for anybody", requests_for(locked_files) == before)
    allowed = ask_whole(locked_columns)
    check("asking for the whole of a table with locked columns is allowed: its readable columns are worth having", allowed.status_code == 201, f"{allowed.status_code} {allowed.text[:160]}")
    check("and a request is waiting", requests_for(locked_columns) == 1)
    check("asking for a version with nothing locked is allowed, as before", ask_whole(plain).status_code == 201)

    heading("A request that was already waiting is not approved either")
    waiting = str(uuid.uuid4())
    with db() as conn:
        conn.execute("insert into lease_request (id, tenant_id, principal, dataset_version_id, purpose, justification, standing, requested_ttl_hours) "
                     "values (%s, %s, %s, %s, 'listen', 'listen', false, 4)", (waiting, CANARY, RESEARCHER, locked_files))
        conn.commit()
    queue = api("GET", f"/lease-requests?custodian={CUSTODIAN}&limit=500", headers=bearer_for(CUSTODIAN)).json()["lease_requests"]
    shown = {r["id"]: r.get("gives") for r in queue}
    check("the approver is told in the queue that approving it would give nothing readable, and what to do",
          "refused" in (shown.get(waiting) or "") and "ask to open a few" in (shown.get(waiting) or ""), str(shown.get(waiting))[:200])
    columns_request = next((r for r in queue if r["dataset_version_id"] == locked_columns and r["principal"] == RESEARCHER), None)
    check("for a table with locked columns the queue says approving gives the readable columns only",
          columns_request is not None and "readable columns only" in (columns_request.get("gives") or ""), str(columns_request and columns_request.get("gives"))[:200])
    everyone = api("GET", f"/lease-requests?principal={RESEARCHER}&limit=500", headers=bearer_for(CUSTODIAN)).json()["lease_requests"]
    plain_request = next((r for r in everyone if r["dataset_version_id"] == plain), None)
    check("and for a version with nothing locked it says nothing extra", plain_request is not None and plain_request.get("gives") is None)
    approved = api("POST", f"/leases/requests/{waiting}/approve", json={}, headers=bearer_for(CUSTODIAN))
    check("approving it is refused, with the same words", approved.status_code == 400 and "every file in this version is locked" in reasons_of(approved), f"{approved.status_code} {reasons_of(approved)[:160]}")
    with db() as conn:
        granted = conn.execute("select count(*) as n from access_lease where principal = %s and dataset_version_id = %s", (RESEARCHER, locked_files)).fetchone()["n"]
        state = conn.execute("select state from lease_request where id = %s", (waiting,)).fetchone()["state"]
    check("no permission was made and the request is still waiting", granted == 0 and state == "pending")
    rejected = api("POST", f"/leases/requests/{waiting}/reject", json={"reason": "ask to open a few files instead"}, headers=bearer_for(CUSTODIAN))
    check("the approver can still reject it, with a reason", rejected.status_code in (200, 201), f"{rejected.status_code} {rejected.text[:140]}")

    heading("The mark of a version of locked files says what the person holds of it")
    m = mark_of(locked_files)
    check("before anything is asked: it says to ask for a few files, and says the version is locked files", m["mark"] == "ask" and m.get("locked") is True, str(m))
    m_cols = mark_of(locked_columns)
    check("a table with locked columns is not marked as locked files", not m_cols.get("locked"), str(m_cols))
    asked = ask_for_files(locked_files, RESEARCHER, records=2)
    check("a request for two files is made", asked.status_code == 201, f"{asked.status_code} {asked.text[:140]}")
    m = mark_of(locked_files)
    check("while it waits the mark says asked and waiting", m["mark"] == "pending" and m.get("locked") is True, str(m))
    granted = decide_files(asked.json()["id"], CUSTODIAN, "approve", records=2, note="two files")
    check("an approver allows two files", granted.status_code == 200, f"{granted.status_code} {granted.text[:140]}")
    m = mark_of(locked_files)
    check("in force the mark is a permission with an end, on locked files", m["mark"] == "lease" and m.get("locked") is True and bool(m["until"]), str(m))
    decide_files(asked.json()["id"], RESEARCHER, "end")

    heading("The file door gives one reason, and it is not about the person's role")
    listed = api("GET", f"/dataset-versions/{locked_files}/records", headers=bearer_for(RESEARCHER)).json()
    check("a researcher is told to ask an approver, and is not told their role stops them",
          listed["may_open"] is False and len(listed["reasons"]) == 1 and "has allowed it" in listed["reasons"][0] and ROLE_SENTENCE not in " ".join(listed["reasons"]), str(listed["reasons"])[:240])

    heading("An agent started on the data: the request filed for it follows the same rule")
    schema_id = fixture_contract(CANARY)
    with db() as conn:
        department = conn.execute("select id from department where tenant_id = %s and name = 'Verification'", (CANARY,)).fetchone()["id"]
    agent_id, agent_principal = register_agent(str(department))

    def runs_of_agent() -> int:
        with db() as conn:
            return conn.execute("select count(*) as n from agent_run where agent_id = %s", (agent_id,)).fetchone()["n"]

    def requests_of_agent(version: str) -> int:
        with db() as conn:
            return conn.execute("select count(*) as n from lease_request where principal = %s and dataset_version_id = %s", (agent_principal, version)).fetchone()["n"]

    pre = api("GET", f"/agents/{agent_id}/access-preflight", params={"dataset_version_id": locked_files}, headers=bearer_for(ENGINEER)).json()
    check("before anybody presses start, the preflight says asking for access would give the agent nothing readable",
          pre["needs_access_request"] is True and len(pre["cannot_be_granted"]) == 1 and "nothing that can be read" in pre["cannot_be_granted"][0], str(pre)[:240])
    on_files = api("POST", f"/agents/{agent_id}/runs", json={"purpose": "rank the files", "dataset_version_id": locked_files, "request_access": True},
                   headers=bearer_for(ENGINEER))
    check("starting an agent on locked files and asking for access for it is refused, with the reason and not the way for a person",
          on_files.status_code == 400 and "every file in this version is locked" in reasons_of(on_files) and "nothing that can be read" in reasons_of(on_files)
          and "version's page" not in reasons_of(on_files), f"{on_files.status_code} {reasons_of(on_files)[:200]}")
    check("nothing was filed for the agent and no run was parked", requests_of_agent(locked_files) == 0 and runs_of_agent() == 0)
    queue = seed_review_queue(schema_id)
    pre_ok = api("GET", f"/agents/{agent_id}/access-preflight", params={"dataset_version_id": queue["id"]}, headers=bearer_for(ENGINEER)).json()
    check("and for a version with nothing locked the preflight has nothing to add", pre_ok["needs_access_request"] is True and pre_ok["cannot_be_granted"] == [], str(pre_ok)[:200])
    on_readable = api("POST", f"/agents/{agent_id}/runs", json={"purpose": "rank the review queue", "dataset_version_id": queue["id"], "request_access": True},
                      headers=bearer_for(ENGINEER))
    check("the same start on a version with nothing locked still files the request and parks the run", on_readable.status_code == 202 and requests_of_agent(queue["id"]) == 1,
          f"{on_readable.status_code} {on_readable.text[:160]}")

    return summary("U165")


if __name__ == "__main__":
    sys.exit(main())
