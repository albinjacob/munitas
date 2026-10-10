"""U174: a person brings in an ordinary table, in any of four plain-text layouts, and it is sealed as a table nobody can read yet.

A table that is not about people (it says it cannot be erased, and names nobody to find) used to crash the route that seals tables with an unexpected
error. It now seals like any other table, with nothing locked: the rows are written in the version's own folder and the same step that builds every
other table builds this one, so it can be queried once somebody is allowed to read it. The rows may be a CSV, a TSV, a JSON list or a JSONL file,
told apart by the file's ending. Whatever is wrong, nothing is sealed and every problem is listed together.

Checked here one item at a time, against the live platform, with an engineer of the test organisation:

  * the same three rows as a CSV, a TSV, a JSON list and a JSONL file each seal, say they are an ordinary table, and name no people;
  * each is a table in the platform's register, with three rows, and not a file that looks like one;
  * each starts at the most restricted level, and the engineer who brought it in cannot read it;
  * the same contract is one schema contract however many times it is used, and its types are the table builder's own;
  * a wrong type, a repeated key, a missing column, an extra column, a nested value, a bad line, an unknown ending and an empty file are each
    refused with the problem named, and nothing is sealed by a refusal;
  * a contract that says the table can be erased but names nobody, and one that says it cannot be erased but names a person, are refused in words,
    not with an unexpected error;
  * a table whose owner claimed it public is held at the most restricted level until an approver of the owning department agrees, then opens to the
    level claimed with the approver's decision recorded; a version sealed afterwards starts at the claimed level.

    docker compose exec -T munitas-api python /verify/v166_plain_tables.py
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import CANARY, CUSTODIAN, ENGINEER, api, bearer_for, check, db, heading, require_api, summary  # noqa: E402

PLAIN = """apiVersion: v3.2.0
kind: DataContract
id: ward-counts
name: ward-counts
version: 1.0.0
status: active
customProperties:
  - {property: erasable, vendor: munitas, value: false}
  - {property: notErasableReason, vendor: munitas, value: "Counts about wards, not about people."}
schema:
  - id: obj
    name: ward_counts
    logicalType: object
    properties:
      - {id: a, name: ward, logicalType: string, primaryKey: true, required: true, classification: none, customProperties: [{property: treatment, vendor: munitas, value: readable}]}
      - {id: b, name: admissions, logicalType: integer, classification: none, customProperties: [{property: treatment, vendor: munitas, value: readable}]}
      - {id: c, name: occupancy, logicalType: number, classification: none, customProperties: [{property: treatment, vendor: munitas, value: readable}]}
      - {id: d, name: open, logicalType: boolean, classification: none, customProperties: [{property: treatment, vendor: munitas, value: readable}]}
      - {id: e, name: counted_on, logicalType: date, classification: none, customProperties: [{property: treatment, vendor: munitas, value: readable}]}
"""

CSV = "ward,admissions,occupancy,open,counted_on\nA,12,0.85,true,2026-03-31\nB,30,0.92,true,2026-03-31\nC,7,0.4,false,2026-03-31\n"
TSV = CSV.replace(",", "\t")
ROWS = [
    {"ward": "A", "admissions": 12, "occupancy": 0.85, "open": True, "counted_on": "2026-03-31"},
    {"ward": "B", "admissions": 30, "occupancy": 0.92, "open": True, "counted_on": "2026-03-31"},
    {"ward": "C", "admissions": 7, "occupancy": 0.4, "open": False, "counted_on": "2026-03-31"},
]
JSON_LIST = json.dumps(ROWS)
JSONL = "\n".join(json.dumps(r) for r in ROWS) + "\n"


def contract_with(extra_properties: str = "", erasable_lines: str | None = None) -> str:
    text = PLAIN
    if erasable_lines is not None:
        head, tail = text.split("schema:\n", 1)
        head = head.split("customProperties:")[0]
        text = head + erasable_lines + "schema:\n" + tail
    return text


def register(headers: dict, department: str, name: str) -> str:
    r = api("POST", "/datasets/register", headers=headers, json={
        "tenant_id": CANARY, "name": name, "department_id": department, "registered_by": ENGINEER, "provenance": "internal_regulated"})
    assert r.status_code == 201, f"register: HTTP {r.status_code} {r.text[:160]}"
    return r.json()["id"]


def seal(headers: dict, dataset: str, contract: str, rows: str, filename: str) -> "httpx.Response":
    return api("POST", f"/datasets/{dataset}/seal-table-files", headers=headers,
               files={"contract": ("contract.yaml", contract, "text/yaml"), "rows": (filename, rows, "text/plain")})


def versions_of(dataset: str) -> int:
    with db() as conn:
        return conn.execute("select count(*) as n from dataset_version where dataset_id = %s", (dataset,)).fetchone()["n"]


def main() -> int:
    require_api()
    headers = bearer_for(ENGINEER)
    department = api("GET", "/organisation", headers=headers).json()["departments"][0]["id"]
    tag = uuid.uuid4().hex[:8]

    heading("U174: the same rows in four layouts each seal as an ordinary table")
    sealed: dict[str, dict] = {}
    for label, rows, filename in (("a CSV", CSV, "rows.csv"), ("a TSV", TSV, "rows.tsv"), ("a JSON list", JSON_LIST, "rows.json"), ("a JSONL file", JSONL, "rows.jsonl")):
        dataset = register(headers, department, f"plain-{filename.split('.')[1]}-{tag}")
        r = seal(headers, dataset, PLAIN, rows, filename)
        body = r.json() if r.status_code == 201 else {}
        check(f"{label} seals", r.status_code == 201, f"HTTP {r.status_code} {r.text[:200] if r.status_code != 201 else ''}")
        check(f"{label} says it is an ordinary table with three rows and no people",
              body.get("plain") is True and body.get("records") == 3 and body.get("people") is None and body.get("group_size_report") is None,
              f"plain={body.get('plain')} records={body.get('records')} people={body.get('people')}")
        sealed[label] = {"dataset": dataset, **body}

    heading("U174: each is a table in the platform's register, at the most restricted level")
    for label, found in sealed.items():
        version = found.get("id")
        with db() as conn:
            ref = conn.execute("select record_count from iceberg_table_ref where dataset_version_id = %s", (version,)).fetchone()
            klass = conn.execute("select visibility_class from dataset_version where id = %s", (version,)).fetchone()
        check(f"{label} is a table, not a file that looks like one", bool(ref) and ref["record_count"] == 3, f"rows in the register: {ref['record_count'] if ref else 'none'}")
        check(f"{label} starts at the most restricted level", bool(klass) and klass["visibility_class"] == "RAW", str(klass["visibility_class"] if klass else None))
        denied = api("GET", f"/dataset-versions/{version}/person-rows", headers=headers)
        check(f"{label} cannot be read by the person who brought it in", denied.status_code in (403, 404), f"HTTP {denied.status_code}")

    heading("U174: one contract is one schema contract, in the table builder's own types")
    with db() as conn:
        schemas = conn.execute("select distinct schema_id from dataset_version where id = any(%s::uuid[])", ([f["id"] for f in sealed.values()],)).fetchall()
        fields = conn.execute("select fields from schema_contract where id = %s", (schemas[0]["schema_id"],)).fetchone()["fields"] if schemas else []
    check("the four versions share one schema contract", len(schemas) == 1, f"{len(schemas)}")
    check("its types are int, float, bool, a real date and text",
          {f["name"]: f["type"] for f in fields} == {"ward": "string", "admissions": "int", "occupancy": "float", "open": "bool", "counted_on": "date"},
          str({f["name"]: f["type"] for f in fields}))
    with db() as conn:
        reason = conn.execute("select not_erasable_reason from schema_contract where id = %s", (schemas[0]["schema_id"],)).fetchone()["not_erasable_reason"] if schemas else None
    check("the reason the table cannot be erased is kept with its schema", reason == "Counts about wards, not about people.", str(reason))

    heading("U174: what is wrong is named, and nothing is sealed by a refusal")
    dataset = register(headers, department, f"plain-refused-{tag}")
    cases = [
        ("a whole number that is not one", PLAIN, CSV.replace("12,0.85", "twelve,0.85"), "rows.csv", "row 1 (line 2): admissions is not a whole number"),
        ("a repeated key", PLAIN, CSV.replace("B,30", "A,30"), "rows.csv", "row 2 (line 3): the key (ward) repeats row 1"),
        ("a missing column", PLAIN, "ward,admissions\nA,1\n", "rows.csv", "has no column occupancy"),
        ("an extra column", PLAIN, CSV.replace("counted_on", "counted_on,extra").replace("2026-03-31\n", "2026-03-31,x\n"), "rows.csv", "extra"),
        ("a true or false that is not one", PLAIN, CSV.replace("true", "maybe", 1), "rows.csv", "true or false"),
        ("a date that is not a date", PLAIN, CSV.replace("2026-03-31", "31/03/2026", 1), "rows.csv", "not a date"),
        ("a nested value in a JSON list", PLAIN, json.dumps([{**ROWS[0], "ward": {"x": 1}}]), "rows.json", "nested value"),
        ("a number where the contract says text", PLAIN, json.dumps([{**ROWS[0], "ward": 7}]), "rows.json", "the contract says text"),
        ("a JSON object that is not a list", PLAIN, json.dumps(ROWS[0]), "rows.json", "one list of rows"),
        ("a bad line in a JSONL file", PLAIN, JSONL + "{not json}\n", "rows.jsonl", "row 4: it is not valid JSON"),
        ("a layout this route does not read", PLAIN, "<rows/>", "rows.xml", "does not read"),
        ("an empty file", PLAIN, "", "rows.csv", "empty"),
    ]
    for label, contract, rows, filename, expect in cases:
        before = versions_of(dataset)
        r = seal(headers, dataset, contract, rows, filename)
        reasons = " ".join(r.json().get("detail", {}).get("reasons", [])) if r.status_code == 400 else r.text[:160]
        check(f"{label} is refused with the problem named", r.status_code == 400 and expect in reasons, f"HTTP {r.status_code}: {reasons[:150]}")
        check(f"and the refusal seals nothing ({label})", versions_of(dataset) == before, f"{before} -> {versions_of(dataset)}")

    heading("U174: a contract that is neither a table of people nor an ordinary table is refused in words")
    erasable_no_subject = PLAIN.replace("{property: erasable, vendor: munitas, value: false}", "{property: erasable, vendor: munitas, value: true}")
    r = seal(headers, dataset, erasable_no_subject, CSV, "rows.csv")
    reasons = " ".join(r.json().get("detail", {}).get("reasons", [])) if r.status_code == 400 else r.text[:160]
    check("one that can be erased but names nobody is refused, not a crash", r.status_code == 400 and "how to find the person" in reasons, f"HTTP {r.status_code}: {reasons[:150]}")
    with_subject = PLAIN.replace('value: "Counts about wards, not about people."}', 'value: "Counts."}\n  - {property: subject, vendor: munitas, value: {kind: ward-number, foundBy: column, column: ward}}')
    r = seal(headers, dataset, with_subject, CSV, "rows.csv")
    reasons = " ".join(r.json().get("detail", {}).get("reasons", [])) if r.status_code == 400 else r.text[:160]
    check("one that cannot be erased but names a person is refused, not a crash", r.status_code == 400 and "cannot be erased and also says" in reasons, f"HTTP {r.status_code}: {reasons[:150]}")
    check("neither sealed anything", versions_of(dataset) == 0, f"{versions_of(dataset)} versions")

    heading("U174: a claim about a table is held at the most restrictive level until the approver agrees, then opens")
    verification = next(d["id"] for d in api("GET", "/organisation", headers=headers).json()["departments"] if d["name"] == "Verification")
    r = api("POST", "/datasets/register", headers=headers, json={
        "tenant_id": CANARY, "name": f"plain-public-{tag}", "department_id": verification, "registered_by": ENGINEER,
        "provenance": "internal_regulated", "declared_class": "PUBLISHED"})
    public = r.json()["id"]
    r = seal(headers, public, PLAIN, CSV, "rows.csv")
    version = r.json().get("id") if r.status_code == 201 else None
    check("a table with a public claim seals", r.status_code == 201, f"HTTP {r.status_code}")

    def level() -> str | None:
        with db() as conn:
            row = conn.execute("select current_class from version_class where dataset_version_id = %s", (version,)).fetchone() if version else None
        return row["current_class"] if row else None

    def reads_by_role(who: dict) -> bool:
        found = api("POST", "/access-preview", headers=who, json={"version_ids": [version]}).json()["versions"][version]
        return found["mark"] == "role"

    researcher = bearer_for("canary-researcher")
    custodian = bearer_for(CUSTODIAN)
    check("until the approver agrees it is held at the most restricted level", level() == "RAW", str(level()))
    check("and a researcher cannot read it by role", not reads_by_role(researcher), "mark is not role")
    waiting = api("GET", "/datasets/awaiting-confirmation", headers=custodian, params={"custodian": CUSTODIAN}).json()["items"]
    check("the claim waits in the approver's queue", any(i["id"] == public for i in waiting), f"{len(waiting)} waiting")

    agreed = api("POST", f"/datasets/{public}/confirm-classification", headers=custodian, json={"confirmed_by": CUSTODIAN})
    body = agreed.json() if agreed.status_code == 200 else {}
    check("the approver agrees, and one version is raised", agreed.status_code == 200 and len(body.get("raised", [])) == 1, f"HTTP {agreed.status_code} {str(body)[:100]}")
    check("it now has the level that was claimed", level() == "PUBLISHED", str(level()))
    check("and a researcher can read it by role", reads_by_role(researcher), "mark is role")
    with db() as conn:
        moved = conn.execute("select decided_by, decided_by_kind, at from class_transition where dataset_version_id = %s", (version,)).fetchall()
    check("the decision is recorded: who agreed, and when", len(moved) == 1 and moved[0]["decided_by"] == CUSTODIAN and moved[0]["decided_by_kind"] == "human" and moved[0]["at"] is not None,
          f"{len(moved)} recorded")
    waiting = api("GET", "/datasets/awaiting-confirmation", headers=custodian, params={"custodian": CUSTODIAN}).json()["items"]
    check("the claim has left the queue", not any(i["id"] == public for i in waiting), f"{len(waiting)} waiting")

    r = seal(headers, public, PLAIN, CSV.replace("A,12", "A,13"), "rows.csv")
    second = r.json().get("id") if r.status_code == 201 else None
    with db() as conn:
        row = conn.execute("select current_class from version_class where dataset_version_id = %s", (second,)).fetchone() if second else None
    check("a version sealed after the approver agreed starts at the claimed level", bool(row) and row["current_class"] == "PUBLISHED", str(row["current_class"] if row else None))

    return summary("U174")


if __name__ == "__main__":
    sys.exit(main())
