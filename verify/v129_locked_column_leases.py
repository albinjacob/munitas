"""U129: locked columns are read in full only by the owning department's approvers, and by anybody else only through a lease, which an auditor can see.

A table sealed under a privacy contract has columns locked under each person's own key. A data engineer devising a rule, a reviewer and an
auditor may all need to see real values, and the way to let them is a lease: a purpose, an approver who is not the person asking, a short
life and a row cap, each read logged. This checks, one item at a time, with real sign-ins as each role, on a table the check makes up for
itself (thirty invented people):

  * without a lease nobody but the owning department's approvers reads the locked columns: an engineer, a reviewer, an auditor, a data
    protection officer, a researcher and another department's custodian are each refused with a reason, and each refusal is recorded;
  * a request needs a purpose, at most 7 days and at most 20 rows, and a custodian or a researcher cannot ask;
  * only an approver of the owning department decides it, never the person asking, and neither the data protection officer nor another
    department's custodian; the database refuses a self-approval even when the API is bypassed;
  * an approved lease opens a fixed sample of the rows and nothing else: the same rows on every read, locked values opened, the lease named
    on the access record and in the key log;
  * a person who was erased inside the sample shows the erased marker;
  * a lease that has run out, or been ended by its holder or by the department's approver, opens nothing;
  * the privacy auditor reads the evidence, which holds no name, value or identifier from the table, and cannot read the data without a
    lease; the data protection officer reads it too and nobody else does;
  * another organisation's people find nothing.

    docker compose exec -T munitas-api python /verify/v129_locked_column_leases.py
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

import pgdb  # noqa: E402

from common import CANARY, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402
from lifecycle_fixture import drop_org, make_org  # noqa: E402
from v128_person_keys_erasure import (ENGINEER, CUSTODIAN, RESEARCHER, count, good_contract, identity,  # noqa: E402
                                       register_dataset, reasons_of, reset_keys)

ELSEWHERE, DPO, REVIEWER, AUDITOR = "canary-elsewhere", "canary-dpo", "canary-reviewer", "canary-auditor"
PEOPLE = 30


def table(tag: str) -> tuple[list[dict], dict[str, str]]:
    """Thirty invented people, one row each. Returns the rows and the clinic number of each visit."""
    rows, numbers = [], {}
    for i in range(1, PEOPLE + 1):
        visit = f"v{i:03d}"
        numbers[visit] = f"M{i:03d}-{tag}"
        rows.append({"visit_id": visit, "clinic_number": numbers[visit], "patient_name": f"Person{i:03d} Zzexample{tag}",
                     "diagnosis": f"dx-{i:03d}-{tag}", "clinic": "north" if i % 2 else "south", "age": 20 + i})
    return rows, numbers


def lease_row(lease_id: str) -> dict:
    with db() as conn:
        return conn.execute("select * from locked_column_lease where id = %s", (lease_id,)).fetchone()


def ask(who: str, version: str, **kw):
    body = {"dataset_version_id": version, "purpose": "devise the rule for locking postcodes", "days": 3, "rows": 5, **kw}
    return api("POST", "/locked-column-leases", headers=bearer_for(who), json=body)


def read(who: str, version: str):
    return api("GET", f"/dataset-versions/{version}/person-rows", headers=bearer_for(who))


def decide(who: str, lease: str, verb: str = "approve"):
    return api("POST", f"/locked-column-leases/{lease}/{verb}", headers=bearer_for(who), json={"note": "checked"})


def main() -> int:
    require_api()
    from app import db as app_db
    if app_db.pool.closed:
        app_db.pool.open()
    fixture_tenant(CANARY)
    from app import person_keys

    # Starts from none of the test organisation's keys: an earlier run of the neighbouring check may have made them under another master.
    reset_keys()
    tag = uuid.uuid4().hex[:6]
    rows, numbers = table(tag)
    dataset = register_dataset(ENGINEER)
    sealed = api("POST", f"/datasets/{dataset}/seal-table", headers=bearer_for(ENGINEER), json={"contract": good_contract(), "rows": rows})
    check("a table of thirty people is sealed", sealed.status_code == 201 and sealed.json()["people"] == PEOPLE, f"{sealed.status_code} {reasons_of(sealed)[:80]}")
    version = sealed.json()["id"]
    names = {r["patient_name"] for r in rows}
    service = person_keys.key_service()

    heading("Without a lease")
    refused_before = count("select count(*) as n from access_decision where tenant_id = %s and purpose like 'read locked columns%%' and not allowed", (CANARY,))
    for who in (ENGINEER, REVIEWER, AUDITOR, DPO, RESEARCHER, ELSEWHERE):
        r = read(who, version)
        check(f"{who} is refused, with a reason", r.status_code == 403 and "lease" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:90]}")
    check("each refusal is recorded", count("select count(*) as n from access_decision where tenant_id = %s and purpose like 'read locked columns%%' and not allowed", (CANARY,)) == refused_before + 6)
    r = read(CUSTODIAN, version)
    body = r.json() if r.status_code == 200 else {}
    check("the owning department's approver reads every row, with the locked columns opened",
          r.status_code == 200 and body.get("mode") == "full" and body.get("count") == PEOPLE and {x["patient_name"] for x in body["rows"]} == names, f"{r.status_code} {reasons_of(r)[:80]}")

    heading("Asking for a lease")
    check("a request with no purpose is refused", ask(ENGINEER, version, purpose="  ").status_code == 403)
    r = ask(ENGINEER, version, days=8)
    check("a request for more than 7 days is refused, and says so", r.status_code == 403 and "between 1 and 7 days" in reasons_of(r), reasons_of(r))
    r = ask(ENGINEER, version, rows=21)
    check("a request for more than 20 rows is refused, and says so", r.status_code == 403 and "between 1 and 20 rows" in reasons_of(r), reasons_of(r))
    check("a researcher cannot ask", ask(RESEARCHER, version).status_code == 403)
    check("the department's own custodian cannot ask: they already read in full", ask(CUSTODIAN, version).status_code == 403)
    check("a table that does not exist is not found", ask(ENGINEER, str(uuid.uuid4())).status_code == 404)
    r = ask(ENGINEER, version)
    check("a data engineer asks, and the request waits", r.status_code == 201 and r.json()["status"] == "requested", f"{r.status_code} {reasons_of(r)[:80]}")
    lease = r.json()["id"]
    r = ask(ENGINEER, version)
    check("a second request while one waits is refused", r.status_code == 409, f"{r.status_code} {reasons_of(r)[:80]}")
    check("asking opens nothing: the engineer is still refused", read(ENGINEER, version).status_code == 403)
    mine = api("GET", "/locked-column-leases", headers=bearer_for(ENGINEER)).json()
    check("the engineer sees the request among their own", any(x["id"] == lease for x in mine["mine"]))
    check("the owning department's approver sees it waiting for them",
          any(x["id"] == lease for x in api("GET", "/locked-column-leases", headers=bearer_for(CUSTODIAN)).json()["to_decide"]))
    check("another department's custodian does not",
          not any(x["id"] == lease for x in api("GET", "/locked-column-leases", headers=bearer_for(ELSEWHERE)).json()["to_decide"]))

    heading("Deciding it")
    check("the person who asked cannot approve it", decide(ENGINEER, lease).status_code == 403)
    r = decide(ELSEWHERE, lease)
    check("another department's custodian cannot, and the refusal says why", r.status_code == 403 and "department that owns this data" in reasons_of(r), reasons_of(r))
    r = decide(DPO, lease)
    check("the data protection officer cannot", r.status_code == 403 and "data custodian" in reasons_of(r), reasons_of(r))
    try:
        with db() as conn:
            conn.execute("update locked_column_lease set status = 'approved', decided_by = principal, expires_at = now() + interval '1 day' where id = %s", (lease,))
        direct = "accepted"
    except pgdb.errors.CheckViolation:
        direct = "refused"
    check("the database refuses a self-approval even when the API is bypassed", direct == "refused", direct)
    r = decide(CUSTODIAN, lease)
    approved = r.json() if r.status_code == 200 else {}
    ends = datetime.fromisoformat(approved["expires_at"].replace("Z", "+00:00")) if approved else None
    check("the owning department's approver approves it, and it ends 3 days from now",
          r.status_code == 200 and approved["status"] == "approved" and approved["decided_by"] == CUSTODIAN
          and ends is not None and timedelta(days=2, hours=23) < ends - datetime.now(timezone.utc) <= timedelta(days=3), f"{r.status_code} {reasons_of(r)[:80]}")
    check("a request is decided once", decide(CUSTODIAN, lease).status_code == 409)
    check("a person who holds a lease cannot ask for a second", ask(ENGINEER, version).status_code == 409)

    heading("Reading under the lease")
    r = read(ENGINEER, version)
    first = r.json() if r.status_code == 200 else {}
    shown = first.get("rows", [])
    check("the engineer now reads, and only a sample: 5 rows of 30", r.status_code == 200 and first.get("mode") == "sample" and first.get("count") == 5, f"{r.status_code} {reasons_of(r)[:80]}")
    check("the locked columns are opened in that sample", len(shown) == 5 and all(x["patient_name"] in names for x in shown))
    check("the identifier column holds a person code, never the identifier", all(x["clinic_number"].startswith("T-") for x in shown))
    check("the lease is named in the answer", first.get("lease_id") == lease)
    again = read(ENGINEER, version).json()
    check("a second read opens exactly the same rows", [x["visit_id"] for x in again["rows"]] == [x["visit_id"] for x in shown])
    check("the sample is not the first five rows, which would be no sample at all", [x["visit_id"] for x in shown] != ["v001", "v002", "v003", "v004", "v005"])
    with db() as conn:
        logged = conn.execute("select allowed, purpose, locked_column_lease_id from access_decision where tenant_id = %s and principal = %s and allowed "
                              "and locked_column_lease_id = %s order by at desc limit 1", (CANARY, ENGINEER, lease)).fetchone()
        keyed = conn.execute("select purpose from key_use_log where tenant_id = %s and actor = %s and action = 'unwrap' and purpose like %s limit 1",
                             (CANARY, ENGINEER, f"%{lease}%")).fetchone()
    check("the read is on the access record with the lease and the purpose", logged is not None and "devise the rule" in logged["purpose"], str(logged))
    check("and every key opened for it is in the key log with the lease named", keyed is not None, str(keyed))

    heading("A person erased inside the sample")
    visit = shown[0]["visit_id"]
    out = person_keys.erase_person(service, CANARY, "clinic number", numbers[visit], identity(CUSTODIAN, ["data_custodian"]), "asked to be forgotten")
    after = {x["visit_id"]: x for x in read(ENGINEER, version).json()["rows"]}
    check("the erasure destroyed the key", out["key_destroyed"] is True)
    check("their locked columns now read as erased, inside the lease's sample", isinstance(after[visit]["patient_name"], dict) and "erased_on" in after[visit]["patient_name"])
    check("the rest of the sample is untouched", all(isinstance(after[v]["patient_name"], str) for v in after if v != visit))

    heading("A lease that has ended")
    with db() as conn:
        conn.execute("update locked_column_lease set expires_at = now() - interval '1 minute' where id = %s", (lease,))
    check("a lease that has run out opens nothing", read(ENGINEER, version).status_code == 403)
    r = ask(ENGINEER, version, purpose="a second look after the first ran out")
    check("and a new request can then be made", r.status_code == 201, f"{r.status_code} {reasons_of(r)[:80]}")
    second = r.json()["id"]
    check("approved", decide(CUSTODIAN, second).status_code == 200)
    check("the second lease reads the same rows as the first", [x["visit_id"] for x in read(ENGINEER, version).json()["rows"]] == [x["visit_id"] for x in shown])
    r = api("POST", f"/locked-column-leases/{second}/revoke", headers=bearer_for(ELSEWHERE))
    check("another department's custodian cannot end it", r.status_code == 403, f"{r.status_code}")
    r = api("POST", f"/locked-column-leases/{second}/revoke", headers=bearer_for(ENGINEER))
    check("its holder ends it", r.status_code == 200 and r.json()["status"] == "revoked", f"{r.status_code} {reasons_of(r)[:80]}")
    check("an ended lease opens nothing", read(ENGINEER, version).status_code == 403)
    check("ending it twice is refused", api("POST", f"/locked-column-leases/{second}/revoke", headers=bearer_for(ENGINEER)).status_code == 409)
    third = ask(ENGINEER, version, purpose="a third, ended by the approver").json()["id"]
    decide(CUSTODIAN, third)
    r = api("POST", f"/locked-column-leases/{third}/revoke", headers=bearer_for(CUSTODIAN))
    check("the department's approver can end a lease too", r.status_code == 200 and read(ENGINEER, version).status_code == 403, f"{r.status_code}")
    declined = ask(ENGINEER, version, purpose="one to decline").json()["id"]
    r = decide(CUSTODIAN, declined, "decline")
    check("a request can be declined, and a declined request opens nothing",
          r.status_code == 200 and r.json()["status"] == "declined" and read(ENGINEER, version).status_code == 403)

    heading("The privacy auditor")
    check("without a lease the auditor cannot read the data", read(AUDITOR, version).status_code == 403)
    r = ask(AUDITOR, version, purpose="verify that locking holds for a sample", rows=3, days=1)
    check("the auditor asks for a lease like anybody else", r.status_code == 201, f"{r.status_code} {reasons_of(r)[:80]}")
    check("the department's approver approves it", decide(CUSTODIAN, r.json()["id"]).status_code == 200)
    r = read(AUDITOR, version)
    check("the auditor then reads their three rows and no more", r.status_code == 200 and r.json()["count"] == 3 and r.json()["mode"] == "sample", f"{r.status_code}")
    before = count("select count(*) as n from access_decision where tenant_id = %s and principal = %s and purpose = 'read audit evidence' and allowed", (CANARY, AUDITOR))
    r = api("GET", "/audit/locked-columns", headers=bearer_for(AUDITOR))
    ev = r.json() if r.status_code == 200 else {}
    check("the auditor reads the evidence", r.status_code == 200 and all(k in ev for k in ("reads", "key_uses", "leases", "contracts", "erasures")), f"{r.status_code} {reasons_of(r)[:80]}")
    check("the evidence shows the reads and refusals above",
          any(x["allowed"] for x in ev.get("reads", [])) and any(not x["allowed"] for x in ev.get("reads", [])))
    check("it shows who made, opened and destroyed keys", {"make_person", "unwrap", "destroy"} <= {x["action"] for x in ev.get("key_uses", [])})
    check("it shows the leases with their purposes and who decided them", any(x["purpose"].startswith("devise the rule") and x["decided_by"] == CUSTODIAN for x in ev.get("leases", [])))
    check("it shows the treatment of each column in the contract", any(c["name"] == "patient_name" and c["treatment"] == "lock" for k in ev.get("contracts", []) for c in k["treatments"]))
    with db() as conn:
        mine = conn.execute("select d.name, v.version, v.schema_id from dataset_version v join dataset d on d.id = v.dataset_id where v.id = %s", (version,)).fetchone()
    under = [k for k in ev.get("contracts", []) if k["schema_id"] == str(mine["schema_id"])]
    check("each contract names the tables sealed under it: the table above is named, with its version, under its own contract",
          len(under) == 1 and f'{mine["name"]}, version {mine["version"]}' in under[0]["tables"] and under[0]["table_count"] >= 1 and len(under[0]["tables"]) <= 5,
          str(under)[:240])
    check("it shows the erasure", any(x["record_id"].startswith("person:") for x in ev.get("erasures", [])))
    text = json.dumps(ev)
    leaked = [w for w in (list(names)[:10] + [numbers[k] for k in list(numbers)[:10]] + ["Zzexample", f"dx-001-{tag}"]) if w in text]
    check("and holds no name, diagnosis or identifier from the table", not leaked, str(leaked[:3]))
    check("the auditor's own read of the evidence is recorded",
          count("select count(*) as n from access_decision where tenant_id = %s and principal = %s and purpose = 'read audit evidence' and allowed", (CANARY, AUDITOR)) == before + 1)
    check("the data protection officer reads the evidence too", api("GET", "/audit/locked-columns", headers=bearer_for(DPO)).status_code == 200)
    for who in (CUSTODIAN, ENGINEER, REVIEWER, RESEARCHER):
        check(f"{who} does not", api("GET", "/audit/locked-columns", headers=bearer_for(who)).status_code == 403)

    heading("Beside the existing leases")
    schema = api("GET", "/openapi.json").json()["components"]["schemas"]
    check("the existing lease request still has its own description in the API reference, and mine has another",
          "justification" in schema.get("LeaseRequestIn", {}).get("properties", {})
          and "rows" in schema.get("LockedColumnLeaseRequestIn", {}).get("properties", {}))
    check("a request for an ordinary lease on files is unaffected: it still needs a justification",
          api("POST", "/leases/requests", headers=bearer_for(RESEARCHER), json={"dataset_version_id": version, "principal": RESEARCHER, "purpose": "x"}).status_code in (400, 422))

    heading("Another organisation")
    org = make_org()
    try:
        outsider = org.bearer("custodian")
        check("another organisation's custodian finds no such table", api("GET", f"/dataset-versions/{version}/person-rows", headers=outsider).status_code == 404)
        r = api("POST", "/locked-column-leases", headers=bearer_for(org.people["dpo"]), json={"dataset_version_id": version, "purpose": "look", "days": 1, "rows": 1})
        check("and cannot ask for a lease on it", r.status_code == 404, str(r.status_code))
        check("nor decide one", api("POST", f"/locked-column-leases/{lease}/approve", headers=outsider, json={}).status_code == 404)
        check("nor see the evidence of another organisation", api("GET", "/audit/locked-columns", headers=bearer_for(org.people["dpo"])).json()["reads"] == [])
    finally:
        drop_org(org)
    reset_keys()
    return summary("v129")


if __name__ == "__main__":
    sys.exit(main())
