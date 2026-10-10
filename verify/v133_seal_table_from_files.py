"""U133: a table is sealed from a contract file and a CSV file, and a bad file is refused with every problem listed.

The console has no way to send rows as JSON, so it sends two files: the privacy contract (YAML or JSON) and the rows (CSV). The server reads
them, turns each text value into the type the contract declares, and hands the result to the same sealing logic every other way of sealing a
table uses. This checks, one item at a time, with real sign-ins, on tables the check makes up for itself:

  * a contract in YAML and a contract in JSON both seal, and a number comes out a number, a true-or-false a true-or-false, a date a date;
  * a file that is not a contract at all, or a contract the platform refuses, is answered with what is wrong, and nothing is sealed;
  * a CSV with a missing, extra or repeated column, a value that does not fit its type, a missing required value and a row naming nobody is
    refused with every one of those listed together, each naming its row and column, and nothing is made or sealed;
  * a table over 20,000 rows or 5 MB, a file that is not UTF-8 text and an empty file are each refused with a plain message;
  * a researcher may not seal, and another organisation's person finds no such dataset.

    docker compose exec -T munitas-api python /verify/v133_seal_table_from_files.py
"""

from __future__ import annotations

import copy
import io
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

import httpx  # noqa: E402
import yaml  # noqa: E402

from common import API, CANARY, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402
from lifecycle_fixture import drop_org, make_org  # noqa: E402
from v128_person_keys_erasure import ENGINEER, CUSTODIAN, RESEARCHER, column, count, register_dataset, reasons_of, reset_keys  # noqa: E402


def contract() -> dict:
    return {
        "apiVersion": "v3.2.0", "kind": "DataContract", "id": "visits-files", "name": "visits-files", "version": "1.0.0", "status": "active",
        "schema": [{"id": "visits_obj", "name": "visits", "logicalType": "object", "properties": [
            {**column("visit_id", treatment="readable"), "primaryKey": True, "required": True},
            column("clinic_number", classification="direct", treatment="replaced by the person code", personIdentifier=True),
            column("patient_name", classification="direct", treatment="lock"),
            column("age", "integer", classification="quasi", treatment="readable"),
            column("weight_kg", "number", treatment="readable"),
            column("insured", "boolean", treatment="readable"),
            column("seen_on", "date", treatment="readable"),
        ]}],
        "customProperties": [
            {"property": "subject", "vendor": "munitas", "value": {"kind": "clinic number", "foundBy": "column", "column": "clinic_number"}},
            {"property": "erasable", "vendor": "munitas", "value": True},
        ],
    }


HEADER = "visit_id,clinic_number,patient_name,age,weight_kg,insured,seen_on"


def csv_rows(tag: str, n: int = 3) -> str:
    lines = [HEADER]
    for i in range(1, n + 1):
        lines.append(f"v{i:03d},F{i:03d}-{tag},Person{i:03d} Zzfiles{tag},{30 + i},{60.5 + i},{'true' if i % 2 else 'false'},2026-03-{i:02d}")
    return "\n".join(lines) + "\n"


def send(dataset: str, who: str, contract_text: str | bytes, rows_text: str | bytes, contract_name="contract.yaml", rows_name="rows.csv"):
    body = lambda x: x if isinstance(x, bytes) else x.encode("utf-8")  # noqa: E731
    return httpx.post(f"{API}/datasets/{dataset}/seal-table-files", headers=bearer_for(who), timeout=60.0,
                      files={"contract": (contract_name, body(contract_text), "application/x-yaml"), "rows": (rows_name, body(rows_text), "text/csv")})


def versions_of(dataset: str) -> int:
    return count("select count(*) as n from dataset_version where dataset_id = %s", (dataset,))


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    tag = uuid.uuid4().hex[:6]
    as_yaml = yaml.safe_dump(contract(), sort_keys=False)

    heading("Sealing from two files")
    dataset = register_dataset(ENGINEER)
    r = send(dataset, ENGINEER, as_yaml, csv_rows(tag, 3))
    check("a contract in YAML and a CSV seal a table", r.status_code == 201 and r.json()["records"] == 3 and r.json()["people"] == 3, f"{r.status_code} {reasons_of(r)[:100]}")
    version = r.json()["id"] if r.status_code == 201 else ""
    check("the answer carries the group-size report, as sealing by the API does", "group_size_report" in r.json())
    got = api("GET", f"/dataset-versions/{version}/person-rows", headers=bearer_for(CUSTODIAN)).json()["rows"] if version else []
    first = next((x for x in got if x["visit_id"] == "v001"), {})
    check("a whole number comes out a whole number, a decimal a decimal, true-or-false a true-or-false, a date a date",
          first.get("age") == 31 and isinstance(first.get("age"), int) and first.get("weight_kg") == 61.5 and first.get("insured") is True
          and first.get("seen_on") == "2026-03-01", str(first))
    check("and the locked column opens for the department's approver", first.get("patient_name") == f"Person001 Zzfiles{tag}", str(first))
    dataset2 = register_dataset(ENGINEER)
    r = send(dataset2, ENGINEER, json.dumps(contract()), csv_rows(tag + "j", 2), contract_name="contract.json")
    check("the same contract as JSON seals too", r.status_code == 201 and r.json()["records"] == 2, f"{r.status_code} {reasons_of(r)[:100]}")
    r = send(register_dataset(ENGINEER), ENGINEER, as_yaml, "﻿" + csv_rows(tag + "b", 2))
    check("a CSV saved with a byte order mark, as spreadsheets do, is read", r.status_code == 201, f"{r.status_code} {reasons_of(r)[:100]}")
    r = send(register_dataset(ENGINEER), ENGINEER, as_yaml, csv_rows(tag + "c", 2).replace("\n", "\r\n"))
    check("and so is one with Windows line endings", r.status_code == 201, f"{r.status_code} {reasons_of(r)[:100]}")

    heading("A file that is not a contract, or a contract the platform refuses")
    bare = register_dataset(ENGINEER)
    before = (versions_of(bare), count("select count(*) as n from subject_lookup where tenant_id = %s", (CANARY,)))
    r = send(bare, ENGINEER, "this: is: not: [valid", csv_rows(tag))
    check("text that is neither YAML nor JSON is refused and says so", r.status_code == 400 and "contract" in reasons_of(r) and "YAML" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:120]}")
    r = send(bare, ENGINEER, "- just\n- a list\n", csv_rows(tag))
    check("a list instead of a contract is refused", r.status_code == 400 and "contract" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:120]}")
    broken = copy.deepcopy(contract())
    for prop in broken["schema"][0]["properties"]:
        if prop["name"] == "patient_name":
            prop["customProperties"][0]["value"] = "lokc"
    r = send(bare, ENGINEER, yaml.safe_dump(broken, sort_keys=False), csv_rows(tag))
    check("a contract with a misspelt treatment is refused with the column and the word", r.status_code == 400 and "patient_name" in reasons_of(r) and "lokc" in reasons_of(r), reasons_of(r)[:120])
    r = send(bare, ENGINEER, as_yaml, b"")
    check("an empty rows file is refused", r.status_code == 400 and "empty" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:120]}")
    r = send(bare, ENGINEER, as_yaml, HEADER + "\n")
    check("a rows file with a header and no rows is refused", r.status_code == 400 and "no rows" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:120]}")
    r = send(bare, ENGINEER, as_yaml, b"\xff\xfe\x00bad bytes")
    check("a file that is not UTF-8 text is refused and says so", r.status_code == 400 and "UTF-8" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:120]}")

    heading("A CSV with several things wrong at once")
    bad_header = "visit_id,clinic_number,patient_name,age,age,mystery,insured,seen_on"
    r = send(bare, ENGINEER, as_yaml, bad_header + "\nv1,F1,Name,30,30,x,true,2026-03-01\n")
    text = reasons_of(r)
    check("a missing column, an extra column and a repeated column are each listed",
          r.status_code == 400 and "weight_kg" in text and "mystery" in text and "age" in text and "more than once" in text, text[:260])
    rows = "\n".join([HEADER,
                      f"v001,F1-{tag},Name One,forty,60.5,true,2026-03-01",
                      f"v002,F2-{tag},Name Two,31,heavy,true,2026-03-02",
                      f"v003,F3-{tag},Name Three,32,61.0,maybe,2026-03-03",
                      f"v004,F4-{tag},Name Four,33,62.0,true,not a date",
                      f",F5-{tag},Name Five,34,63.0,false,2026-03-05",
                      f"v006,,Name Six,35,64.0,false,2026-03-06",
                      f"v007,F7-{tag},Name Seven,36,65.0"]) + "\n"
    r = send(bare, ENGINEER, as_yaml, rows)
    text = reasons_of(r)
    check("a value that is not a whole number is listed with its row and column", r.status_code == 400 and "v001" in text and "age" in text and "forty" in text, text[:300])
    check("so is a value that is not a number", "v002" in text and "weight_kg" in text and "heavy" in text)
    check("so is one that is not true or false", "v003" in text and "insured" in text and "maybe" in text)
    check("so is one that is not a date", "v004" in text and "seen_on" in text and "not a date" in text)
    check("a missing required value, a row naming nobody and a short row are each listed",
          "row 5" in text and "visit_id" in text and "v006" in text and "names nobody" in text and "v007" in text, text[:400])
    check("every one of them is in the same answer, so one attempt shows all of them", all(w in text for w in ("v001", "v002", "v003", "v004", "v006", "v007")))
    after = (versions_of(bare), count("select count(*) as n from subject_lookup where tenant_id = %s", (CANARY,)))
    check("nothing was sealed and nobody was made for any of those refusals", before == after, f"{before} -> {after}")

    heading("Size")
    big = HEADER + "\n" + "\n".join(f"v{i},G{i}-{tag},N{i},30,60.0,true,2026-03-01" for i in range(20_001)) + "\n"
    r = send(bare, ENGINEER, as_yaml, big)
    check("more than 20,000 rows is refused with a plain message", r.status_code == 400 and "20,000" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:140]}")
    r = send(bare, ENGINEER, as_yaml, HEADER + "\n" + "v1,F1,N,30,60.0,true,2026-03-01," + "x" * (5 * 1024 * 1024) + "\n")
    check("a file over 5 MB is refused with a plain message", r.status_code == 400 and "5 MB" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:140]}")
    check("neither sealed anything", versions_of(bare) == before[0])

    heading("Who")
    check("a researcher may not seal", send(bare, RESEARCHER, as_yaml, csv_rows(tag)).status_code == 403)
    org = make_org()
    try:
        r = httpx.post(f"{API}/datasets/{bare}/seal-table-files", headers=org.bearer("custodian"), timeout=30.0,
                       files={"contract": ("c.yaml", as_yaml.encode(), "application/x-yaml"), "rows": ("r.csv", csv_rows(tag).encode(), "text/csv")})
        check("another organisation's custodian finds no such dataset", r.status_code == 404, str(r.status_code))
    finally:
        drop_org(org)
    reset_keys()
    return summary("v133")


if __name__ == "__main__":
    sys.exit(main())
