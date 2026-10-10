r"""U177: a person brings in a table in parts, and it is written by a worker, with limits that keep a few people from slowing everyone else down.

Runs on the host against the live platform, with a generated table of 100,000 rows (about 18 MB, so three parts of 8 MB) and its truth file:

    $env:MUNITAS_PG_DSN = "postgresql://munitas:munitas@localhost:5432/platform"
    $env:MUNITAS_VERIFY_KRATOS = "http://localhost:4433"
    $env:MUNITAS_S3_ENDPOINT = "http://localhost:8333"
    .venv\Scripts\python.exe verify\v169_table_upload.py

Checked one item at a time:

  * an upload is opened, reserves the next version, and takes its parts in any order; a part sent twice is the same as once; a part of the wrong size,
    or one that does not exist, is refused; the upload says which parts are missing; finishing with a part missing is refused and names it;
  * a finished upload is written by a worker, and the stored table agrees with the generator's truth file; the file's hash is in the version, the
    person who brought it in is on record, and the version starts at the most restricted level;
  * only the person who started an upload can send to it, finish it or cancel it, and another organisation cannot see it;
  * the storage permissions may be printed while an upload is open without hiding its key, and the upload is then written and sealed;
  * a cancelled upload leaves nothing in storage and gives its version number back; so does one that runs out of time; a table that is already being
    written can be cancelled, and no version appears;
  * a person may have three uploads open and no more, and the answer is 429 with a time to wait and the reason in words; a file past the organisation's
    largest is refused before anything is sent;
  * the platform takes a limited number of parts at the same moment and turns the rest away, not queued;
  * an organisation's tables are written two at a time and the others wait their turn, and all of them are written.

The limits per hour are not exercised here, because they would stop the person doing anything else for an hour.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

from common import ADMIN, API, CANARY, ENGINEER, S3_ENDPOINT, api, bearer_for, bucket_for, check, db, heading, require_api, s3_client, summary  # noqa: E402
from scripts.testdata import contracts, generate, tables, truth  # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix="munitas-u177-"))
PART = 8 * 1024 * 1024
TABLE = "vehicle_readings"
WAIT = 240


def read_table(version_id: str):
    from pyiceberg.table import StaticTable

    with db() as conn:
        ref = conn.execute("select metadata_location from iceberg_table_ref where dataset_version_id = %s", (version_id,)).fetchone()
    if not ref:
        return None
    props = {"s3.endpoint": S3_ENDPOINT, "s3.access-key-id": ADMIN[0], "s3.secret-access-key": ADMIN[1], "s3.region": "us-east-1", "s3.path-style-access": "true"}
    return StaticTable.from_metadata(ref["metadata_location"], properties=props).scan().to_arrow()


def main() -> int:
    require_api()
    try:
        run()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return summary("U177")


def run() -> None:
    headers = bearer_for(ENGINEER)
    department = api("GET", "/organisation", headers=headers).json()["departments"][0]["id"]
    tag = uuid.uuid4().hex[:8]
    spec = tables.SPECS[TABLE]
    contract_text = contracts.to_yaml(spec)
    folder = WORK / TABLE / "100000"
    generate.make(TABLE, 100_000, ("jsonl",), WORK, quiet=True)
    path = folder / "jsonl" / f"{TABLE}.jsonl"
    data = path.read_bytes()
    parts = [data[i:i + PART] for i in range(0, len(data), PART)]
    client, bucket = s3_client(*ADMIN), bucket_for(CANARY)

    def register(name: str) -> str:
        r = api("POST", "/datasets/register", headers=headers, json={"tenant_id": CANARY, "name": f"upload-{name}-{tag}", "department_id": department,
                                                                     "registered_by": ENGINEER, "provenance": "internal_regulated"})
        assert r.status_code == 201, f"register: HTTP {r.status_code} {r.text[:160]}"
        return r.json()["id"]

    def next_prefix(dataset: str) -> str:
        return api("GET", f"/datasets/{dataset}/next-version", params={"tenant_id": CANARY}).json()["storage_prefix"]

    def open_(dataset: str, size: int = len(data), name: str = f"{TABLE}.jsonl", who=headers, contract: str | None = None):
        return api("POST", f"/datasets/{dataset}/table-uploads", headers=who, json={"file_name": name, "size_bytes": size, "contract": contract or contract_text, "options": {}})

    def send(job: str, n: int, body: bytes, who=headers):
        return api("PUT", f"/table-uploads/{job}/parts/{n}", headers=who, content=body)

    def state(job: str, who=headers) -> dict:
        return api("GET", f"/table-uploads/{job}", headers=who).json()

    def wait(job: str, until=("sealed", "refused", "expired")) -> dict:
        started = time.monotonic()
        while time.monotonic() - started < WAIT:
            found = state(job)
            if found.get("status") in until:
                return found
            time.sleep(2)
        return state(job)

    def keys_under(prefix: str) -> list[str]:
        return [o["Key"] for o in client.list_objects_v2(Bucket=bucket, Prefix=prefix + "/").get("Contents", [])]

    def versions_of(dataset: str) -> int:
        with db() as conn:
            return conn.execute("select count(*) as n from dataset_version where dataset_id = %s", (dataset,)).fetchone()["n"]

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("An upload reserves the next version and takes its parts in any order")
    dataset = register("main")
    prefix_before = next_prefix(dataset)
    r = open_(dataset)
    first = r.json() if r.status_code == 201 else {}
    job = first.get("job_id", "")
    check("opening is accepted: three parts of 8 MB, open, and the version number is reserved",
          r.status_code == 201 and first.get("parts") == len(parts) == 3 and first.get("part_bytes") == PART and first.get("open") is True
          and first.get("missing") == [1, 2, 3] and next_prefix(dataset) != prefix_before, f"{r.status_code} {str(first)[:160]}")
    check("a person without a role that may bring data in is refused", api("POST", f"/datasets/{dataset}/table-uploads", headers=bearer_for("canary-researcher"),
          json={"file_name": f"{TABLE}.jsonl", "size_bytes": 10, "contract": contract_text, "options": {}}).status_code in (400, 403))
    other = bearer_for("canary-custodian")
    check("another person cannot see, send to, finish or cancel it",
          all(api(m, f"/table-uploads/{job}{tail}", headers=other, **({"content": b"x"} if m == "PUT" else {})).status_code == 404
              for m, tail in (("GET", ""), ("PUT", "/parts/1"), ("POST", "/finish"), ("POST", "/cancel"))))

    r = send(job, 2, parts[1])
    check("part 2 is accepted before part 1, and the status says what is missing", r.status_code == 200 and r.json()["received"] == [2] and r.json()["missing"] == [1, 3], r.text[:120])
    r = send(job, 1, parts[0][:-1])
    check("a part of the wrong size is refused with the size it should be", r.status_code == 400 and str(PART) in r.text, r.text[:120])
    r = send(job, 4, b"x")
    check("a part that does not exist is refused", r.status_code == 400 and "no part 4" in r.text, r.text[:120])
    send(job, 1, parts[0])
    r = send(job, 1, parts[0])
    check("a part sent twice is the same as once", r.status_code == 200 and r.json()["received"] == [1, 2], r.text[:120])
    r = api("POST", f"/table-uploads/{job}/finish", headers=headers)
    check("finishing with a part missing is refused, and names it", r.status_code == 400 and "part 3" in r.text and versions_of(dataset) == 0, r.text[:140])
    check("the status is the same after a refused finish, still open", state(job)["received"] == [1, 2] and state(job)["open"] is True)

    heading("A finished upload is written by a worker, and the stored table agrees with its truth file")
    send(job, 3, parts[2])
    r = api("POST", f"/table-uploads/{job}/finish", headers=headers)
    check("finishing is accepted at once, and the job is waiting for a worker", r.status_code == 202 and r.json()["open"] is False and r.json()["ready"] is True, r.text[:140])
    again = api("POST", f"/table-uploads/{job}/finish", headers=headers)
    check("finishing twice is the same as once", again.status_code == 202, again.text[:100])
    done = wait(job)
    check("the table is written and the version is sealed", done["status"] == "sealed" and done["outcome"] == "written" and done.get("version_id"), str(done)[:200])
    version = done.get("version_id", "")
    arrow = read_table(version)
    expected = json.loads((folder / "truth.json").read_text(encoding="utf-8"))
    problems = truth.compare(expected, truth.summarize(spec, arrow.select([c.name for c in spec.cols]).cast(spec.schema()))) if arrow is not None else ["no table"]
    check("the stored table of 100,000 rows agrees with its truth file", not problems, "; ".join(problems[:2]))
    with db() as conn:
        v = conn.execute("select visibility_class, object_manifest, record_count from dataset_version where id = %s", (version,)).fetchone()
        source = conn.execute("select locator, checksum, bytes, fetched_by from dataset_source where dataset_id = %s", (dataset,)).fetchall()
    file_entry = next((o for o in v["object_manifest"] if o["key"].endswith("/records.jsonl")), {})
    check("the file's hash and size are in the version, and are those of the file that was sent",
          file_entry.get("sha256") == hashlib.sha256(data).hexdigest() and file_entry.get("bytes") == len(data) == done["size_bytes"], str(file_entry)[:120])
    check("it starts at the most restricted level and says it holds 100,000 rows", v["visibility_class"] == "RAW" and v["record_count"] == 100_000, str(dict(v))[:100])
    ours = [s for s in source if s["locator"] == f"{TABLE}.jsonl"]
    check("who brought it in, which file, how big, and its hash, is on record", len(ours) == 1 and ours[0]["fetched_by"] == ENGINEER and ours[0]["bytes"] == len(data)
          and ours[0]["checksum"] == hashlib.sha256(data).hexdigest() and done["brought_in_by"] == ENGINEER, str([dict(s) for s in source])[:200])
    check("the progress of the writing ends as done, with every row", (done.get("progress") or {}).get("phase") == "done" and (done.get("progress") or {}).get("rows") == 100_000,
          str(done.get("progress")))

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("An update of the storage permissions while an upload is open does not hide the upload's key")
    # A long upload is open for minutes or hours, and the permissions are printed many times meanwhile. The upload's key goes into a print only when the upload is
    # finished, so a print made before that must not be taken for one that holds it, or the worker waits for a key that never comes.
    dataset_p = register("print")
    r = open_(dataset_p)
    job_p = r.json()["job_id"]
    for n, body in enumerate(parts, start=1):
        send(job_p, n, body)
    program = ("import sys; sys.path.insert(0, '/app'); from app import db, grants; db.pool.open(wait=True); "
               "print(grants.reconcile(trigger='manual'))")
    done = subprocess.run(["wsl.exe", "-d", "Munitas", "--", "docker", "exec", "munitas-munitas-api-1", "python", "-c", program], capture_output=True, text=True, timeout=120)
    check("the permissions are printed after the upload was opened and before it is finished", done.returncode == 0, (done.stdout + done.stderr)[-160:])
    time.sleep(2)
    api("POST", f"/table-uploads/{job_p}/finish", headers=headers)
    printed = wait(job_p)
    check("the upload is then written and sealed", printed["status"] == "sealed" and printed["outcome"] == "written", f"{printed['status']}: {printed.get('reason')}")

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("A cancelled upload leaves nothing, and gives its version number back")
    dataset2 = register("cancel")
    free = next_prefix(dataset2)
    r = open_(dataset2)
    job2 = r.json()["job_id"]
    send(job2, 1, parts[0])
    reserved_prefix = state(job2)["storage_prefix"]
    r = api("POST", f"/table-uploads/{job2}/cancel", headers=headers)
    check("cancelling is accepted and the upload ends as cancelled", r.status_code == 200 and r.json()["status"] == "refused" and r.json()["outcome"] == "cancelled", r.text[:140])
    check("nothing is left in storage, the number is free again, and no version was made",
          not keys_under(reserved_prefix) and next_prefix(dataset2) == free and versions_of(dataset2) == 0, str(keys_under(reserved_prefix))[:100])
    r = send(job2, 2, parts[1])
    check("nothing more can be sent to it", r.status_code == 400 and "no more can be sent" in r.text, r.text[:120])

    heading("An upload nobody finishes runs out of time and is thrown away")
    dataset3 = register("expire")
    r = open_(dataset3)
    job3 = r.json()["job_id"]
    send(job3, 1, parts[0])
    prefix3 = state(job3)["storage_prefix"]
    with db() as conn:
        conn.execute("update table_job set expires_at = now() - interval '1 minute' where id = %s", (job3,))
    time.sleep(8)
    found = state(job3)
    check("it is expired, with a reason that says the upload was not finished, and storage is clean",
          found["status"] == "expired" and "not finished" in (found["reason"] or "") and not keys_under(prefix3), f"{found['status']}: {found['reason']}")

    heading("A table that is already being written can be cancelled, and no version appears")
    dataset4 = register("running")
    r = open_(dataset4)
    job4 = r.json()["job_id"]
    for n, body in enumerate(parts, start=1):
        send(job4, n, body)
    api("POST", f"/table-uploads/{job4}/finish", headers=headers)
    time.sleep(1)
    r = api("POST", f"/table-uploads/{job4}/cancel", headers=headers)
    check("cancelling a table that is waiting or being written ends it as cancelled", r.status_code == 200 and r.json()["outcome"] == "cancelled", r.text[:140])
    time.sleep(25)
    check("no version appears afterwards, and nothing is left in storage", versions_of(dataset4) == 0 and not keys_under(state(job4)["storage_prefix"]),
          f"{versions_of(dataset4)} versions, {keys_under(state(job4)['storage_prefix'])[:2]}")

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("The limits turn a request away in words, with a time to wait")
    held = []
    for n in range(3):
        r = open_(register(f"limit{n}"), size=1000, name=f"{TABLE}.csv")
        held.append(r.json().get("job_id"))
    r = open_(register("limit-over"), size=1000, name=f"{TABLE}.csv")
    check("a fourth upload open at once is refused with 429, a time to wait, and the reason", r.status_code == 429 and int(r.headers.get("retry-after", 0)) > 0
          and "3 uploads open" in r.text, f"{r.status_code} {r.text[:140]}")
    for job_id in held:
        api("POST", f"/table-uploads/{job_id}/cancel", headers=headers)
    r = open_(register("big"), size=17 * 1024 ** 3, name=f"{TABLE}.csv")
    check("a file past the organisation's largest is refused before anything is sent", r.status_code == 400 and "16 GB" in r.text, f"{r.status_code} {r.text[:140]}")
    r = open_(register("ending"), size=1000, name=f"{TABLE}.xml")
    check("a file this route does not read is refused with what it does read", r.status_code == 400 and ".csv" in r.text, r.text[:140])
    r = open_(register("bad-contract"), size=1000, name=f"{TABLE}.csv", contract="not a contract")
    check("a contract that is not one is refused in words", r.status_code == 400, r.text[:140])

    heading("The platform takes a limited number of parts at once, and turns the rest away")
    spec_gate = importlib.util.spec_from_file_location("gate_under_test", ROOT / "platform" / "api" / "app" / "gate.py")
    gate = importlib.util.module_from_spec(spec_gate)
    spec_gate.loader.exec_module(gate)
    g = gate.Gate(total=2, per_key=1)
    results = []
    with g.enter("a", "all", "one"), g.enter("b", "all", "one"):
        for key in ("c", "a"):
            try:
                with g.enter(key, "the whole platform is busy", "this upload is busy"):
                    results.append("entered")
            except gate.Busy as busy:
                results.append(busy.what)
    check("past the total the part is turned away, and past one upload's own share too, each with its reason",
          results == ["the whole platform is busy", "the whole platform is busy"] or results == ["the whole platform is busy", "this upload is busy"], str(results))
    check("a place is given back when the request ends", g.in_flight() == 0)

    heading("An organisation's tables are written two at a time, and every one is written")
    dataset5 = register("queue")
    generate.make("ward_occupancy", 100_000, ("csv",), WORK / "ward", quiet=True)
    body = (WORK / "ward" / "ward_occupancy" / "100000" / "csv" / "ward_occupancy.csv").read_bytes()
    first_upload = api("POST", f"/datasets/{dataset5}/table-uploads", headers=headers, json={
        "file_name": "ward_occupancy.csv", "size_bytes": len(body), "contract": contracts.to_yaml(tables.SPECS["ward_occupancy"]), "options": {}}).json()
    api("POST", f"/table-uploads/{first_upload['job_id']}/cancel", headers=headers)
    with db() as conn:
        schema_id = conn.execute("select (request->>'schema_id') as s from table_job where id = %s", (first_upload["job_id"],)).fetchone()["s"]
    # Four tables from the platform's own route, which a person's limits do not count. The four files are put where the next four versions go, and
    # then the four jobs are made one straight after another, so all four are waiting together when the dispatcher next looks.
    first_prefix = next_prefix(dataset5)
    base = int(first_prefix.rsplit("/v", 1)[1])
    stem = first_prefix.rsplit("/v", 1)[0]
    keys = [f"{stem}/v{base + n}/records.csv" for n in range(4)]
    for key in keys:
        client.put_object(Bucket=bucket, Key=key, Body=body)
    jobs = []
    for key in keys:
        r = api("POST", "/dataset-versions", json={
            "tenant_id": CANARY, "dataset_id": dataset5, "schema_id": schema_id, "visibility_class": "RAW", "records_key": key, "record_count": 0,
            "object_manifest": [{"key": key, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}], "table_required": True,
            "table_mode": "background", "records_options": {}})
        assert r.status_code == 202, r.text[:200]
        jobs.append(r.json()["job_id"])
    peak, started = 0, time.monotonic()
    while time.monotonic() - started < 300:
        with db() as conn:
            rows = conn.execute("select status, dispatched_at is not null as given from table_job where id = any(%s::uuid[])", (jobs,)).fetchall()
        peak = max(peak, sum(1 for r in rows if r["status"] in ("pending", "running") and r["given"]))
        if all(r["status"] == "sealed" for r in rows):
            break
        time.sleep(0.5)
    check("two of the organisation's tables were handed out at once, and no more, while the others waited", peak == 2, f"the most at once was {peak}")
    check("all four were written and sealed", all(r["status"] == "sealed" for r in rows), str([r["status"] for r in rows]))


if __name__ == "__main__":
    sys.exit(main())
