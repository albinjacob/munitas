"""U130: sealing a table reports how many different people share each combination of its readable quasi-identifying columns, as advice and in counts.

A column such as age band or postcode names nobody, but a rare combination of them can single one person out. When a table is sealed the platform
counts the different people in each combination, reports those in a group smaller than the contract's threshold, and says what locking one column
would leave. This checks, one item at a time, with real sign-ins as each role, on tables the check makes up for itself (invented people whose
numbers were worked out by hand from how the rows were built):

  * the threshold in a contract is a whole number from 2 to 100, 5 when the contract says nothing;
  * the counts are exact: 25 people in 5 groups, 3 groups below 5 holding 7 people, and a person with two rows is one person;
  * the advice names the column whose locking helps, with what it would leave, and leaves out the column whose locking changes nothing;
  * the contract's own threshold is the one used, and a table with no readable quasi column reports that there was nothing to count;
  * the report holds counts and column names and no value, and says nothing a person who may not read the table must not know;
  * it is read by the owning department's approvers, the reviewer, the auditor and the data protection officer, and by nobody else;
  * only the owning department's approver decides what to do about it, once, with a reason, and the database refuses to edit the report;
  * a table with small groups is sealed all the same, and every seal makes a report of its own.

    docker compose exec -T munitas-api python /verify/v130_group_size_reports.py
"""

from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

import pgdb  # noqa: E402

from common import CANARY, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402
from lifecycle_fixture import drop_org, make_org  # noqa: E402
from v128_person_keys_erasure import ENGINEER, CUSTODIAN, RESEARCHER, column, count, register_dataset, reasons_of, reset_keys  # noqa: E402

ELSEWHERE, DPO, REVIEWER, AUDITOR = "canary-elsewhere", "canary-dpo", "canary-reviewer", "canary-auditor"


def contract(threshold=None, quasi_treatment="readable") -> dict:
    out = {
        "apiVersion": "v3.2.0", "kind": "DataContract", "id": "visits", "name": "visits-groups", "version": "1.0.0", "status": "active",
        "schema": [{"id": "visits_obj", "name": "visits", "logicalType": "object", "properties": [
            {**column("visit_id", treatment="readable"), "primaryKey": True, "required": True},
            column("clinic_number", classification="direct", treatment="replaced by the person code", personIdentifier=True),
            column("patient_name", classification="direct", treatment="lock"),
            column("postcode", classification="quasi", treatment=quasi_treatment),
            column("age_band", classification="quasi", treatment=quasi_treatment),
        ]}],
        "customProperties": [
            {"property": "subject", "vendor": "munitas", "value": {"kind": "clinic number", "foundBy": "column", "column": "clinic_number"}},
            {"property": "erasable", "vendor": "munitas", "value": True},
        ],
    }
    if threshold is not None:
        out["customProperties"].append({"property": "groupSizeThreshold", "vendor": "munitas", "value": threshold})
    return out


def rows(tag: str) -> list[dict]:
    """25 people in 5 groups. A: 10 people, B: 8, C: 3 (all 30-39); D: 1 person, E: 3 people with two rows each (both 40-49). 28 rows."""
    plan = [("A", "30-39", 10, 1), ("B", "30-39", 8, 1), ("C", "30-39", 3, 1), ("D", "40-49", 1, 1), ("E", "40-49", 3, 2)]
    out, n = [], 0
    for code, band, people, visits in plan:
        for p in range(people):
            for v in range(visits):
                n += 1
                out.append({"visit_id": f"v{n:03d}", "clinic_number": f"G{code}{p}-{tag}", "patient_name": f"Name{code}{p} Zzgroups{tag}",
                            "postcode": f"PC-{code}-{tag}", "age_band": band})
    return out


def seal(dataset: str, doc: dict, data: list[dict]):
    return api("POST", f"/datasets/{dataset}/seal-table", headers=bearer_for(ENGINEER), json={"contract": doc, "rows": data})


def get_report(who: str, version: str):
    return api("GET", f"/dataset-versions/{version}/group-size-report", headers=bearer_for(who))


def main() -> int:
    require_api()
    from app import db as app_db
    if app_db.pool.closed:
        app_db.pool.open()
    fixture_tenant(CANARY)
    from app import privacy_contract

    reset_keys()
    tag = uuid.uuid4().hex[:6]
    data = rows(tag)

    heading("The threshold in a contract")
    check("a contract that says nothing uses 5", privacy_contract.group_size_threshold(contract()) == 5)
    for bad in (1, 0, 101, "five", 2.5, True):
        problems = privacy_contract.check(contract(threshold=bad))
        check(f"a threshold of {bad!r} is refused", any("groupSizeThreshold" in p for p in problems), str(problems)[:90])
    for good in (2, 5, 100):
        check(f"a threshold of {good} is accepted", privacy_contract.check(contract(threshold=good)) == [], str(privacy_contract.check(contract(threshold=good))))
    check("the columns counted are the quasi ones left readable, and no others",
          privacy_contract.counted_columns(contract()) == ["postcode", "age_band"] and privacy_contract.counted_columns(contract(quasi_treatment="lock")) == [])

    heading("Sealing a table with small groups")
    dataset = register_dataset(ENGINEER)
    r = seal(dataset, contract(), data)
    check("it is sealed all the same: the report is advice", r.status_code == 201 and r.json()["records"] == 28 and r.json()["people"] == 25, f"{r.status_code} {reasons_of(r)[:80]}")
    version, report = r.json()["id"], r.json()["group_size_report"]
    check("the seal's answer carries the report", report["dataset_version_id"] == version)
    check("it counted the two readable quasi columns, with the contract's default threshold",
          report["columns"] == ["postcode", "age_band"] and report["threshold"] == 5, str(report["columns"]))
    check("25 people in 5 groups", report["people"] == 25 and report["groups"] == 5, f"{report['people']} people, {report['groups']} groups")
    check("the smallest group is one person", report["smallest_group"] == 1)
    check("3 groups are below 5", report["groups_below"] == 3)
    check("7 people sit in them: 3 + 1 + 3, and the six rows of three people count as three", report["people_in_small_groups"] == 7, str(report["people_in_small_groups"]))
    check("the advice is to lock the postcode, which would leave 4 people in a small group",
          report["advice"] == [{"lock": "postcode", "people_in_small_groups_after": 4}], str(report["advice"]))
    check("it does not advise locking the age band, which changes nothing", all(a["lock"] != "age_band" for a in report["advice"]))
    text = json.dumps(report)
    check("the report holds no value: no postcode, no name, no identifier", not any(w in text for w in (f"PC-A-{tag}", f"Zzgroups{tag}", f"GA0-{tag}", "30-39", "40-49")), text[:100])

    heading("Who reads it")
    for who in (CUSTODIAN, REVIEWER, AUDITOR, DPO):
        r = get_report(who, version)
        check(f"{who} reads it", r.status_code == 200 and r.json()["people_in_small_groups"] == 7, f"{r.status_code} {reasons_of(r)[:80]}")
    for who in (ENGINEER, RESEARCHER, ELSEWHERE):
        r = get_report(who, version)
        check(f"{who} is refused, with a reason", r.status_code == 403 and "group-size report" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:80]}")
    check("a table that has none is not found", get_report(CUSTODIAN, str(uuid.uuid4())).status_code == 404)

    heading("What the owner decides")
    body = {"decision": "will_change", "note": "Postcode will be locked in the next contract version."}
    for who in (ENGINEER, REVIEWER, AUDITOR, DPO, ELSEWHERE):
        r = api("POST", f"/dataset-versions/{version}/group-size-report/decision", headers=bearer_for(who), json=body)
        check(f"{who} may not decide", r.status_code == 403, f"{r.status_code} {reasons_of(r)[:80]}")
    r = api("POST", f"/dataset-versions/{version}/group-size-report/decision", headers=bearer_for(CUSTODIAN), json={"decision": "will_change", "note": "  "})
    check("a decision needs a reason", r.status_code == 400, f"{r.status_code} {reasons_of(r)[:80]}")
    r = api("POST", f"/dataset-versions/{version}/group-size-report/decision", headers=bearer_for(CUSTODIAN), json={"decision": "ignore", "note": "x"})
    check("and is one of the two words", r.status_code == 400, f"{r.status_code} {reasons_of(r)[:80]}")
    r = api("POST", f"/dataset-versions/{version}/group-size-report/decision", headers=bearer_for(CUSTODIAN), json=body)
    check("the owning department's approver decides, and it is on the record",
          r.status_code == 200 and r.json()["decision"] == "will_change" and r.json()["decided_by"] == CUSTODIAN and r.json()["decided_at"], f"{r.status_code} {reasons_of(r)[:80]}")
    r = api("POST", f"/dataset-versions/{version}/group-size-report/decision", headers=bearer_for(CUSTODIAN), json={"decision": "accepted", "note": "changed my mind"})
    check("it is decided once", r.status_code == 409, f"{r.status_code} {reasons_of(r)[:80]}")
    try:
        with db() as conn:
            conn.execute("update group_size_report set people_in_small_groups = 0 where dataset_version_id = %s", (version,))
        edited = "accepted"
    except pgdb.errors.CheckViolation:
        edited = "refused"
    check("the database refuses an edit to what was counted, even when the API is bypassed", edited == "refused", edited)
    try:
        with db() as conn:
            conn.execute("update group_size_report set decision = 'accepted', decided_by = 'x', decided_at = now(), decision_note = 'y' where dataset_version_id = %s", (version,))
        redecided = "accepted"
    except pgdb.errors.CheckViolation:
        redecided = "refused"
    check("and a second decision, the same way", redecided == "refused", redecided)

    heading("The contract's own threshold, and nothing to count")
    dataset2 = register_dataset(ENGINEER)
    r = seal(dataset2, contract(threshold=3), rows(tag + "b"))
    small = r.json()["group_size_report"] if r.status_code == 201 else {}
    check("with a threshold of 3 only the one person alone is in a small group",
          small.get("threshold") == 3 and small.get("groups_below") == 1 and small.get("people_in_small_groups") == 1 and small.get("smallest_group") == 1, str(small)[:120])
    check("and locking the postcode would leave nobody", small.get("advice") == [{"lock": "postcode", "people_in_small_groups_after": 0}], str(small.get("advice")))
    dataset3 = register_dataset(ENGINEER)
    r = seal(dataset3, contract(quasi_treatment="lock"), rows(tag + "c"))
    none = r.json()["group_size_report"] if r.status_code == 201 else {}
    check("a table whose quasi columns are all locked has nothing to count, and says so",
          r.status_code == 201 and none.get("columns") == [] and none.get("groups") == 0 and none.get("smallest_group") is None
          and none.get("people_in_small_groups") == 0 and none.get("advice") == [], f"{r.status_code} {str(none)[:100]}")

    heading("Every seal makes its own")
    before = count("select count(*) as n from group_size_report where tenant_id = %s", (CANARY,))
    r = seal(dataset, contract(), rows(tag + "d"))
    check("sealing the same shape again makes a new version with a report of its own",
          r.status_code == 201 and r.json()["id"] != version and count("select count(*) as n from group_size_report where tenant_id = %s", (CANARY,)) == before + 1)
    check("a refused table makes none", seal(register_dataset(ENGINEER), contract(), data + [{**data[0], "visit_id": "v900", "clinic_number": ""}]).status_code == 400
          and count("select count(*) as n from group_size_report where tenant_id = %s", (CANARY,)) == before + 1)

    heading("Seen by the auditor")
    ev = api("GET", "/audit/locked-columns", headers=bearer_for(AUDITOR)).json()
    mine = [g for g in ev.get("group_sizes", []) if g["dataset_version_id"] == version]
    check("the audit evidence lists the report, with its decision", len(mine) == 1 and mine[0]["people_in_small_groups"] == 7 and mine[0]["decision"] == "will_change", str(mine)[:100])
    check("and still holds no value from any table", not any(w in json.dumps(ev) for w in (f"PC-A-{tag}", f"Zzgroups{tag}", f"GA0-{tag}")))

    heading("Another organisation")
    org = make_org()
    try:
        check("another organisation's custodian finds no such table", api("GET", f"/dataset-versions/{version}/group-size-report", headers=org.bearer("custodian")).status_code == 404)
        check("nor can they decide on it", api("POST", f"/dataset-versions/{version}/group-size-report/decision", headers=org.bearer("custodian"), json=body).status_code == 404)
    finally:
        drop_org(org)
    reset_keys()
    return summary("v130")


if __name__ == "__main__":
    sys.exit(main())
