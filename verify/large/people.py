"""What the two large checks about people (L6 and L7) share: a table of patients of a given size is brought in, locked and read back.

Nothing here runs by itself. verify/run_large.py names the checks, says what they will do, and runs one only when it is told to.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import time

import httpx

import flow
from common import CANARY, ENGINEER, api, bearer_for, check, db, heading

CUSTODIAN = "canary-custodian"
TABLE = "patients"


def need(rows: int, minutes: str) -> dict:
    spec = flow.tables.SPECS[TABLE]
    return {"disk_bytes": flow.generate.estimate(spec, rows, ["csv"]), "minutes": minutes,
            "does": [f"makes {TABLE} with {rows:,} rows, each a different person, as one CSV file under {flow.DATA}",
                     "sends it, has the worker check it whole and then lock it a batch at a time through the platform, and reads the worker's memory all through",
                     f"leaves {rows:,} people's keys in the test organisation, which the canary tidy removes, and roughly {rows * 0.6 / 1e6:.1f} GB in the database (a guess, not measured)",
                     "reads the table back a page at a time (the first, and a page from the far end), a lease's sample, and erases one person",
                     "counts the lines the key log gained, and removes the made file at the end"]}


def codes_of(version_id: str) -> int:
    """How many different person codes the stored table holds, counted without holding them as text."""
    import numpy as np

    parts = []
    for batch in flow.stored_table(version_id).scan(selected_fields=("person_code",)).to_arrow_batch_reader():
        parts.append(np.array([int(c[2:], 16) for c in batch.column(0).to_pylist()], dtype=np.uint64))
    return int(len(np.unique(np.concatenate(parts)))) if parts else 0


def run(rows: int, label: str) -> None:
    headers, custodian = bearer_for(ENGINEER), bearer_for(CUSTODIAN)
    department = next(d["id"] for d in api("GET", "/organisation", headers=headers).json()["departments"] if d["name"] == "Verification")
    spec = flow.tables.SPECS[TABLE]
    # The kind of identifier is made particular to this run, so the people are new to the platform every time: the kind is part of how a person is found,
    # and the same record numbers would otherwise find the people of an earlier run.
    kind = f"medical record number {label} {int(time.time())}"
    contract = flow.contracts.to_yaml(spec).replace("medical record number", kind)
    folder = flow.make_files(TABLE, rows, ["csv"])
    try:
        path = folder / "csv" / f"{TABLE}.csv"
        with db() as conn:
            log_before = conn.execute("select coalesce(max(id), 0) as n from key_use_log").fetchone()["n"]
            keys_before = conn.execute("select count(*) as n from subject_key where tenant_id = %s", (CANARY,)).fetchone()["n"]
        heading(f"{rows:,} people brought in")
        dataset = flow.register(headers, department, TABLE)
        opened = flow.open_upload(headers, dataset, TABLE, path, contract=contract)
        check("the upload is opened", opened.status_code == 201, f"{opened.status_code} {opened.text[:200]}")
        if opened.status_code != 201:
            return
        job, part = opened.json()["job_id"], opened.json()["part_bytes"]
        phases: dict[str, float] = {}
        with flow.MemoryWatch() as memory, flow.Latency(headers) as latency:
            flow.send_parts(headers, job, path, part)
            flow.finish(headers, job)
            began = time.monotonic()
            progress, status, noted = [], {}, 0.0
            while time.monotonic() - memory.started < 6 * 3600:
                status = api("GET", f"/table-uploads/{job}", headers=headers).json()
                seen = status.get("progress") or {}
                progress.append((time.monotonic() - memory.started, int(seen.get("rows") or 0)))
                phases.setdefault(seen.get("phase") or "", time.monotonic())
                if time.monotonic() - noted > 60:
                    noted = time.monotonic()
                    flow.out(f"    {status.get('status')} {seen.get('phase', '')}: {int(seen.get('rows') or 0):,} rows after {time.monotonic() - began:.0f}s")
                if status.get("status") in ("sealed", "refused", "expired"):
                    break
                time.sleep(flow.POLL_SECONDS)
            finished = time.monotonic()
        check("the table is written, with every row and every person", status.get("status") == "sealed" and status.get("people") == rows
              and (status.get("progress") or {}).get("rows") == rows, f"{status.get('status')}: {status.get('reason')} {str(status.get('problems'))[:200]}")
        if status.get("status") != "sealed":
            return
        version = status["version_id"]
        if "writing" in phases:
            flow.out(f"    checking took {phases['writing'] - began:.0f}s; locking and writing took {finished - phases['writing']:.0f}s, "
                     f"{rows / max(1, finished - phases['writing']):,.0f} rows a second")
        ok, words = flow.level(flow.flatness(memory, progress, rows))
        check("the worker's memory stays level through both passes", ok, words)
        seen = latency.summary()
        check("an ordinary call stayed fast while it was locked and written", seen["calls"] > 20 and seen["p95"] < 2.0 and seen["worst"] < 15,
              f"{seen['calls']} calls, median {seen['median']:.2f}s, 95th {seen['p95']:.2f}s, worst {seen['worst']:.2f}s")
        distinct = codes_of(version)
        check("every row has a different person's code: as many codes as people", distinct == rows, f"{distinct:,} codes")
        with db() as conn:
            lines = conn.execute("select count(*) as n from key_use_log where tenant_id = %s and id > %s and purpose like '%%lock a batch%%'", (CANARY, log_before)).fetchone()["n"]
            per_person = conn.execute("select count(*) as n from key_use_log where tenant_id = %s and id > %s and person_code is not null", (CANARY, log_before)).fetchone()["n"]
            keys = conn.execute("select count(*) as n from subject_key where tenant_id = %s", (CANARY,)).fetchone()["n"] - keys_before
            before = conn.execute("select content_hash, iceberg_snapshot_id from dataset_version where id = %s", (version,)).fetchone()
        batches = -(-rows // 5000)
        check("one key for each person, and the key log holds a line for each batch and none for a person", keys == rows and per_person == 0 and lines <= 2 * batches + 4,
              f"{keys:,} keys, {lines:,} batch lines for {batches:,} batches, {per_person} single-person lines")

        heading("Reading it back")
        started = time.monotonic()
        first = api("GET", f"/dataset-versions/{version}/person-rows", headers=custodian, params={"limit": 200})
        took = time.monotonic() - started
        check("the first page of 200 rows is read in a few seconds", first.status_code == 200 and len(first.json()["rows"]) == 200 and took < 10, f"{first.status_code} in {took:.1f}s")
        from pyiceberg.table import StaticTable
        with db() as conn:
            ref = conn.execute("select metadata_location from iceberg_table_ref where dataset_version_id = %s", (version,)).fetchone()
        table = StaticTable.from_metadata(ref["metadata_location"], properties={"s3.endpoint": flow.S3_ENDPOINT, "s3.access-key-id": flow.ADMIN[0],
                                                                               "s3.secret-access-key": flow.ADMIN[1], "s3.region": "us-east-1", "s3.path-style-access": "true"})
        count_files = len(list(table.scan().plan_files()))
        far = base64.urlsafe_b64encode(json.dumps([count_files - 1, 0]).encode()).decode()
        started = time.monotonic()
        late = api("GET", f"/dataset-versions/{version}/person-rows", headers=custodian, params={"limit": 200, "cursor": far})
        took = time.monotonic() - started
        mismatches = 0
        if late.status_code == 200:
            for row in late.json()["rows"][:50]:
                index = int(row["registration_id"][3:]) - 1
                original = spec.build(index, 1, spec.context(rows, flow.generate.SEED)).to_pylist()[0]
                mismatches += int(row["full_name"] != original["full_name"] or row["postcode"] != original["postcode"])
        check(f"a page from the last of {count_files} data files is read just as fast, and its values are the ones that were sent",
              late.status_code == 200 and len(late.json()["rows"]) > 0 and took < 10 and mismatches == 0, f"{late.status_code} in {took:.1f}s, {mismatches} values differ")

        lease = api("POST", "/locked-column-leases", headers=headers, json={"dataset_version_id": version, "purpose": f"large check {label}", "days": 1, "rows": 20})
        api("POST", f"/locked-column-leases/{lease.json()['id']}/approve", headers=custodian, json={"note": "checked"})
        started = time.monotonic()
        sample = httpx.request("GET", f"{flow.API}/dataset-versions/{version}/person-rows", headers=headers, timeout=900)
        took = time.monotonic() - started
        ranked = sorted((f"REG{i:08d}" for i in range(1, rows + 1)), key=lambda i: hashlib.sha256(f"{version}|{ENGINEER}|{i}".encode()).hexdigest())[:20]
        got = {r["registration_id"] for r in sample.json()["rows"]} if sample.status_code == 200 else set()
        check(f"a lease's 20 rows are found in one pass over the key column, in {took:.0f}s, and are the ones the ranking gives", sample.status_code == 200 and got == set(ranked), f"{sample.status_code}")

        heading("Erasing one person")
        victim_index = rows // 3
        original = spec.build(victim_index, 1, spec.context(rows, flow.generate.SEED)).to_pylist()[0]
        program = (f"import sys, json; sys.path.insert(0, '/app'); from app import db, person_keys; db.pool.open(wait=True); "
                   f"r = person_keys.erase_person(person_keys.key_service(), {CANARY!r}, {kind!r}, {original['patient_number']!r}, {{'id': 'large check'}}, 'large check'); "
                   f"print(json.dumps({{'destroyed': r['key_destroyed']}}))")
        started = time.monotonic()
        done = subprocess.run(["wsl.exe", "-d", flow.DISTRO, "--", "docker", "exec", "munitas-munitas-api-1", "python", "-c", program], capture_output=True, text=True, timeout=300)
        took = time.monotonic() - started
        check(f"one person among {rows:,} is erased in {took:.1f}s by destroying one key", '"destroyed": true' in done.stdout, (done.stdout + done.stderr)[-200:])
        with db() as conn:
            now = conn.execute("select content_hash, iceberg_snapshot_id from dataset_version where id = %s", (version,)).fetchone()
        check("the table was not rewritten", now == before)
    finally:
        flow.remove(folder)
