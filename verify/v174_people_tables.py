r"""U184: a table about people is brought in as a table, with the identifying columns locked, whatever its size; one person can still be erased.

Runs on the host against the live platform, with generated tables of people (scripts/testdata): 10,000 patients and 5,000 admissions.

    $env:MUNITAS_PG_DSN = "postgresql://munitas:munitas@localhost:5432/platform"
    $env:MUNITAS_VERIFY_KRATOS = "http://localhost:4433"
    $env:MUNITAS_S3_ENDPOINT = "http://localhost:8333"
    .venv\Scripts\python.exe verify\v174_people_tables.py

Checked one item at a time:

  * the file is checked whole before any key is made: a file whose last row names nobody is refused naming that row, makes no key and no version, and leaves
    no file in storage;
  * a table of 10,000 people is written by a worker, with the readable columns as they were sent, the locked columns stored as locked text, the person code
    in its own column, and the file that was sent not kept (not in the version's list of files, and not in storage);
  * the people are counted once for the table: ten thousand keys made, and the key log holds a few lines for the batches, not a line for each person;
  * a second table about the same people (a Parquet file) finds the same people and makes no new key for them;
  * the group-size report recorded for the table is the one worked out from the data, number for number, advice included;
  * the department's approver reads every row, a page at a time, and every locked value opened is the value that was sent; each page is one line in the key log;
  * a person with a lease reads the same few rows the small route would give, found without reading the table whole;
  * erasing one person destroys one key: that person's locked cells read as erased, every other person reads as before, and the table is not rewritten.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("MUNITAS_PG_DSN", "postgresql://munitas:munitas@localhost:5432/platform")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS", "http://localhost:4433")
os.environ.setdefault("MUNITAS_S3_ENDPOINT", "http://localhost:8333")

from common import ADMIN, CANARY, ENGINEER, S3_ENDPOINT, api, bearer_for, bucket_for, check, db, heading, require_api, s3_client, summary  # noqa: E402
from scripts.testdata import contracts, generate, tables  # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix="munitas-u184-"))
RESEARCHER, CUSTODIAN = "canary-researcher", "canary-custodian"
WAIT = 240
PEOPLE = 10_000
ADMISSIONS = 5_000


def main() -> int:
    require_api()
    try:
        run()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return summary("U184")


def count(sql: str, args: tuple = ()) -> int:
    with db() as conn:
        return int(conn.execute(sql, args).fetchone()["n"])


def stored(version_id: str):
    from pyiceberg.table import StaticTable

    with db() as conn:
        ref = conn.execute("select metadata_location from iceberg_table_ref where dataset_version_id = %s", (version_id,)).fetchone()
    props = {"s3.endpoint": S3_ENDPOINT, "s3.access-key-id": ADMIN[0], "s3.secret-access-key": ADMIN[1], "s3.region": "us-east-1", "s3.path-style-access": "true"}
    return StaticTable.from_metadata(ref["metadata_location"], properties=props).scan().to_arrow()


def plain(value):
    """A value as it is locked and opened again: dates and times as text, decimals as text, the rest as it is."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    return value


def expected_report(rows: list[dict], codes: list[str], columns: list[str], threshold: int) -> dict:
    """The group-size report, worked out here from the data, the way group_sizes.compute works it out in memory."""
    def counted(cols: list[str]) -> dict:
        groups: dict[tuple, set] = {}
        if cols:
            for code, row in zip(codes, rows):
                groups.setdefault(tuple(json.dumps(plain(row[c]), sort_keys=True) for c in cols), set()).add(code)
        small = [p for p in groups.values() if len(p) < threshold]
        return {"groups": len(groups), "smallest_group": min((len(p) for p in groups.values()), default=None), "groups_below": len(small),
                "people_in_small_groups": len(set().union(*small)) if small else 0}

    found = counted(columns)
    advice = []
    if found["people_in_small_groups"]:
        for c in columns:
            after = counted([x for x in columns if x != c])["people_in_small_groups"]
            if after < found["people_in_small_groups"]:
                advice.append({"lock": c, "people_in_small_groups_after": after})
        advice.sort(key=lambda a: (a["people_in_small_groups_after"], a["lock"]))
    return {**found, "people": len(set(codes)), "advice": advice}


def run() -> None:
    engineer, custodian, researcher = bearer_for(ENGINEER), bearer_for(CUSTODIAN), bearer_for(RESEARCHER)
    department = next(d["id"] for d in api("GET", "/organisation", headers=engineer).json()["departments"] if d["name"] == "Verification")
    tag = uuid.uuid4().hex[:8]
    patients, admissions = tables.SPECS["patients"], tables.SPECS["admissions"]
    client, bucket = s3_client(*ADMIN), bucket_for(CANARY)

    def contract_for(spec) -> str:
        """The generated contract, with the kind of identifier made particular to this run, so that the people in it are new to the platform every time the
        check runs: the kind is part of how a person is found, and the same record numbers would otherwise find the people of the last run."""
        return contracts.to_yaml(spec).replace("medical record number", f"medical record number {tag}")

    def bring(spec, path: Path, name: str):
        dataset = api("POST", "/datasets/register", headers=engineer, json={"tenant_id": CANARY, "name": f"people-{spec.name}-{tag}-{uuid.uuid4().hex[:4]}",
                                                                           "department_id": department, "registered_by": ENGINEER, "provenance": "internal_regulated"}).json()["id"]
        body = path.read_bytes()
        r = api("POST", f"/datasets/{dataset}/table-uploads", headers=engineer, json={"file_name": name, "size_bytes": len(body), "contract": contract_for(spec), "options": {}})
        if r.status_code != 201:
            return {"status": "not opened", "problems": [f"HTTP {r.status_code} {r.text[:300]}"]}, dataset
        job, part = r.json()["job_id"], r.json()["part_bytes"]
        for n in range(1, -(-len(body) // part) + 1):
            assert api("PUT", f"/table-uploads/{job}/parts/{n}", headers=engineer, content=body[(n - 1) * part:n * part]).status_code == 200
        assert api("POST", f"/table-uploads/{job}/finish", headers=engineer).status_code == 202
        started, state = time.monotonic(), {}
        while time.monotonic() - started < WAIT:
            state = api("GET", f"/table-uploads/{job}", headers=engineer).json()
            if state["status"] in ("sealed", "refused", "expired"):
                break
            time.sleep(1)
        return state, dataset

    def pages(version: str, who, limit: int = 500) -> list[dict]:
        out, cursor = [], None
        while True:
            r = api("GET", f"/dataset-versions/{version}/person-rows", headers=who, params={"limit": limit, **({"cursor": cursor} if cursor else {})})
            assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
            body = r.json()
            out.append(body)
            cursor = body.get("next")
            if not cursor:
                return out

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("A file whose last row names nobody is refused before any key is made")
    generate.make("patients", 3_000, ("csv",), WORK / "bad", quiet=True)
    import csv
    source = WORK / "bad" / "patients" / "3000" / "csv" / "patients.csv"
    with open(source, newline="", encoding="utf-8") as handle:
        table_rows = list(csv.reader(handle))
    table_rows[-1][1] = ""
    with open(source, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(table_rows)
    keys_before = count("select count(*) as n from subject_key where tenant_id = %s", (CANARY,))
    state, dataset = bring(patients, source, "patients.csv")
    text = " | ".join(state.get("problems") or [])
    check("it is refused, naming the last row and saying it names nobody", state["status"] == "refused" and "row 3000" in text and "names nobody" in text, f"{state['status']}: {text[:200]}")
    check("no key was made, no version was made, and nothing is left in storage",
          count("select count(*) as n from subject_key where tenant_id = %s", (CANARY,)) == keys_before
          and count("select count(*) as n from dataset_version where dataset_id = %s", (dataset,)) == 0
          and not client.list_objects_v2(Bucket=bucket, Prefix=f"{state['storage_prefix']}/").get("Contents"), f"{keys_before} keys before")

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("A table of 10,000 people is written, locked, and the file that was sent is not kept")
    generate.make("patients", PEOPLE, ("csv", "parquet"), WORK / "good", quiet=True)
    folder = WORK / "good" / "patients" / str(PEOPLE)
    original = patients.build(0, PEOPLE, patients.context(PEOPLE, generate.SEED))
    original_rows = original.to_pylist()
    keys_before = count("select count(*) as n from subject_key where tenant_id = %s", (CANARY,))
    log_before = count("select coalesce(max(id), 0) as n from key_use_log")
    state, dataset = bring(patients, folder / "csv" / "patients.csv", "patients.csv")
    version = state.get("version_id")
    check("the table is written and sealed, about 10,000 people in 10,000 rows", state["status"] == "sealed" and state["about_people"] is True and state["people"] == PEOPLE
          and (state.get("progress") or {}).get("rows") == PEOPLE, f"{state['status']}: {state.get('reason')} {str(state.get('problems'))[:200]}")
    if state["status"] != "sealed":
        return
    check("ten thousand keys were made, one for each person", count("select count(*) as n from subject_key where tenant_id = %s", (CANARY,)) == keys_before + PEOPLE,
          f"{count('select count(*) as n from subject_key where tenant_id = %s', (CANARY,)) - keys_before} new")
    batch_lines = count("select count(*) as n from key_use_log where tenant_id = %s and id > %s and purpose like '%%lock a batch%%'", (CANARY, log_before))
    per_person = count("select count(*) as n from key_use_log where tenant_id = %s and id > %s and person_code is not null", (CANARY, log_before))
    check("the key log holds a few lines for the batches and none for single people", 0 < batch_lines <= 12 and per_person == 0, f"{batch_lines} lines for batches, {per_person} for single people")
    arrow = stored(version)
    rows_back = arrow.to_pylist()
    columns = arrow.column_names
    check("the table has the contract's columns and a last column holding the person code", columns == [c.name for c in patients.cols] + ["person_code"], str(columns))
    by_key = {r["registration_id"]: r for r in rows_back}
    readable = [c.name for c in patients.cols if c.treatment == "readable" or c.key]
    locked_cols = [c.name for c in patients.cols if c.treatment == "lock"]
    same = all(all(by_key[o["registration_id"]][c] == o[c] for c in readable) for o in original_rows)
    check("the readable columns are exactly as they were sent", same and len(by_key) == PEOPLE)
    sent_names = {str(o["full_name"]) for o in original_rows}
    cells = {str(r[c]) for r in rows_back for c in columns}
    check("every locked cell is locked text, and no name that was sent appears anywhere in the table",
          all(str(r[c]).startswith("locked:v1:") for r in rows_back for c in locked_cols) and not (sent_names & cells), "a locked cell is not text, or a name shows")
    check("the column that identified the person holds the person code", all(r["patient_number"] == r["person_code"] and str(r["person_code"]).startswith("T-") for r in rows_back))
    with db() as conn:
        v = conn.execute("select object_manifest, visibility_class, record_count from dataset_version where id = %s", (version,)).fetchone()
    check("the version lists the table's files and not the file that was sent, and starts at the most restricted level",
          not any("/records." in m["key"] for m in v["object_manifest"]) and any("/iceberg/" in m["key"] for m in v["object_manifest"]) and v["visibility_class"] == "RAW"
          and v["record_count"] == PEOPLE, str([m["key"][-40:] for m in v["object_manifest"]][:3]))
    prefix = state["storage_prefix"]
    check("the file that was sent is gone from storage", not any("/records." in o["Key"] for o in client.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/").get("Contents", [])))

    heading("The group-size report is the one the data gives")
    with db() as conn:
        report = conn.execute("select * from group_size_report where dataset_version_id = %s", (version,)).fetchone()
    mine = expected_report(original_rows, [by_key[o["registration_id"]]["person_code"] for o in original_rows],
                           [c.name for c in patients.cols if c.classification == "quasi" and c.treatment == "readable"], 5)
    got = {"groups": report["groups"], "smallest_group": report["smallest_group"], "groups_below": report["groups_below"],
           "people_in_small_groups": report["people_in_small_groups"], "people": report["people"], "advice": report["advice"]}
    check("groups, smallest group, small groups, people in them, people and advice all agree", got == mine, f"recorded {got} worked out {mine}")

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("A second table about the same people finds them and makes no new key")
    generate.make("admissions", ADMISSIONS, ("parquet",), WORK / "adm", quiet=True)
    adm_folder = WORK / "adm" / "admissions" / str(ADMISSIONS)
    adm_original = admissions.build(0, ADMISSIONS, admissions.context(ADMISSIONS, generate.SEED)).to_pylist()
    keys_before = count("select count(*) as n from subject_key where tenant_id = %s", (CANARY,))
    adm_state, _ = bring(admissions, adm_folder / "parquet" / "admissions.parquet", "admissions.parquet")
    check("an admissions file in Parquet is written and sealed", adm_state["status"] == "sealed" and adm_state["about_people"] is True,
          f"{adm_state['status']}: {adm_state.get('reason')} {str(adm_state.get('problems'))[:200]}")
    if adm_state["status"] == "sealed":
        adm_back = stored(adm_state["version_id"]).to_pylist()
        code_of = {o["patient_number"]: by_key[o["registration_id"]]["person_code"] for o in original_rows}
        wanted = {o["admission_id"]: code_of.get(o["patient_number"]) for o in adm_original}
        check("every admission carries the code of the same person in the patients table", all(wanted[r["admission_id"]] == r["person_code"] for r in adm_back) and None not in wanted.values())
        check("no new key was made for them", count("select count(*) as n from subject_key where tenant_id = %s", (CANARY,)) == keys_before,
              f"{count('select count(*) as n from subject_key where tenant_id = %s', (CANARY,)) - keys_before} new")
        adm_exact = {r["admission_id"]: r for r in adm_back}
        check("the readable columns, a decimal and times among them, are exactly as they were sent",
              all(adm_exact[o["admission_id"]][c] == o[c] for o in adm_original for c in ("ward_code", "admitted_at", "total_cost", "age_at_admission", "outcome")))

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("The department's approver reads every row, a page at a time, and the values are the ones that were sent")
    log_before = count("select coalesce(max(id), 0) as n from key_use_log")
    got_pages = pages(version, custodian)
    flat = [r for p in got_pages for r in p["rows"]]
    check("there are 20 pages of 500, the last one has no next page, and every row is there once", len(got_pages) == 20 and got_pages[-1]["next"] is None and len(flat) == PEOPLE
          and len({r["registration_id"] for r in flat}) == PEOPLE and all(p["mode"] == "full" and p["total"] == PEOPLE for p in got_pages), f"{len(got_pages)} pages, {len(flat)} rows")
    opened = {r["registration_id"]: r for r in flat}
    right = all(all(opened[o["registration_id"]][c] == plain(o[c]) for c in locked_cols) for o in original_rows)
    check("every locked value, opened, is the value that was sent", right)
    page_lines = count("select count(*) as n from key_use_log where tenant_id = %s and id > %s and action = 'unwrap' and purpose like '%%read locked columns%%'", (CANARY, log_before))
    check("each page is one line in the key log, carrying the count", page_lines == 20, f"{page_lines} lines")
    check("a person without the right to read is refused, and a bad cursor is refused in words",
          api("GET", f"/dataset-versions/{version}/person-rows", headers=researcher).status_code == 403
          and api("GET", f"/dataset-versions/{version}/person-rows", headers=custodian, params={"cursor": "not-a-place"}).status_code == 400)

    heading("A lease opens the same few rows the small route would give")
    lease = api("POST", "/locked-column-leases", headers=engineer, json={"dataset_version_id": version, "purpose": "check the sample", "days": 1, "rows": 7})
    check("the request is made", lease.status_code == 201, lease.text[:160])
    api("POST", f"/locked-column-leases/{lease.json()['id']}/approve", headers=custodian, json={"note": "checked"})
    sample = api("GET", f"/dataset-versions/{version}/person-rows", headers=engineer)
    ranked = sorted((o["registration_id"] for o in original_rows), key=lambda i: hashlib.sha256(f"{version}|{ENGINEER}|{i}".encode()).hexdigest())[:7]
    got_ids = [r["registration_id"] for r in sample.json()["rows"]] if sample.status_code == 200 else []
    check("the engineer reads exactly those 7 rows, opened", sample.status_code == 200 and sample.json()["mode"] == "sample" and set(got_ids) == set(ranked)
          and all(r["full_name"] == plain(next(o for o in original_rows if o["registration_id"] == r["registration_id"])["full_name"]) for r in sample.json()["rows"]), sample.text[:160])

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("Erasing one person destroys one key and leaves everyone else readable")
    with db() as conn:
        before = conn.execute("select content_hash, iceberg_snapshot_id from dataset_version where id = %s", (version,)).fetchone()
    victim = original_rows[4321]
    program = (f"import sys, json; sys.path.insert(0, '/app'); from app import db, person_keys; db.pool.open(wait=True); "
               f"r = person_keys.erase_person(person_keys.key_service(), {CANARY!r}, f'medical record number {tag}', {victim['patient_number']!r}, {{'id': 'u184'}}, 'check U184'); "
               f"print(json.dumps({{'found': r['found'], 'destroyed': r['key_destroyed']}}))")
    done = subprocess.run(["wsl.exe", "-d", "Munitas", "--", "docker", "exec", "munitas-munitas-api-1", "python", "-c", program], capture_output=True, text=True, timeout=120)
    check("the person's key is destroyed", '"destroyed": true' in done.stdout, (done.stdout + done.stderr)[-200:])
    after_pages = pages(version, custodian)
    after_rows = {r["registration_id"]: r for p in after_pages for r in p["rows"]}
    gone = after_rows[victim["registration_id"]]
    check("that person's locked cells read as erased, with the date", all(isinstance(gone[c], dict) and "erased_on" in gone[c] for c in locked_cols))
    others = all(all(after_rows[o["registration_id"]][c] == plain(o[c]) for c in locked_cols) for o in original_rows if o["registration_id"] != victim["registration_id"])
    check("everybody else reads as before, and the person's readable columns still read", others and after_rows[victim["registration_id"]]["town"] == victim["town"])
    with db() as conn:
        now = conn.execute("select content_hash, iceberg_snapshot_id from dataset_version where id = %s", (version,)).fetchone()
    check("the table was not rewritten: the version's fingerprint and snapshot are as they were", now == before)


if __name__ == "__main__":
    sys.exit(main())
