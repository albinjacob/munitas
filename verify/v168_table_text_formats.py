r"""U176: a table that arrives as text, in any layout, is checked strictly and written as a table, the same way at any size.

Runs on the host against the live platform, with the generated test tables (scripts/testdata) as the data and their truth files as the answers:

    $env:MUNITAS_PG_DSN = "postgresql://munitas:munitas@localhost:5432/platform"
    $env:MUNITAS_VERIFY_KRATOS = "http://localhost:4433"
    $env:MUNITAS_S3_ENDPOINT = "http://localhost:8333"
    .venv\Scripts\python.exe verify\v168_table_text_formats.py

At 100,000 rows or fewer, so it takes a minute or two and never belongs to the large tier. Checked one item at a time:

  * every text layout of a table with a composite key and decimals (comma, semicolon, pipe and tab separated, with a byte-order mark, in Windows-1252,
    quoted, gzip and zstd, JSON lines and a JSON list) is sealed through the person route as a table, and the stored table, read back, has the row
    count, totals, smallest and largest values, keys and key fingerprint the generator wrote down, with real dates, timestamps and decimals;
  * a table with no key, and tables with one key, are sealed the same way;
  * a table of 100,000 rows goes through a table job in the worker and comes back the same;
  * a faulty file is refused, whichever way it arrives, naming the rows and the lines the answers file says were damaged and none other, and
    seals nothing and leaves no table behind;
  * a file declared with the wrong delimiter is refused for its header, not read as one column;
  * the reason a table cannot be erased is kept with its schema, and the snapshot's row count is the count of rows read.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

from common import ADMIN, CANARY, ENGINEER, S3_ENDPOINT, api, bearer_for, bucket_for, check, db, heading, require_api, s3_client, summary  # noqa: E402
from scripts.testdata import contracts, generate, tables, truth  # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix="munitas-u176-"))
WAIT_SECONDS = 240
ALL_TEXT = ("csv", "csv-quoted", "csv-bom", "csv-cp1252", "csv-semicolon", "csv-pipe", "tsv", "csv-gzip", "csv-zstd", "jsonl", "jsonl-gzip", "jsonl-zstd", "json")


def read_table(version_id: str):
    """The stored table of a version, read back through Iceberg with the administrator's own key, and its snapshot's properties."""
    import pyarrow as pa  # noqa: F401
    from pyiceberg.table import StaticTable

    with db() as conn:
        ref = conn.execute("select metadata_location, record_count from iceberg_table_ref where dataset_version_id = %s", (version_id,)).fetchone()
    if not ref:
        return None, None, None
    props = {"s3.endpoint": S3_ENDPOINT, "s3.access-key-id": ADMIN[0], "s3.secret-access-key": ADMIN[1], "s3.region": "us-east-1", "s3.path-style-access": "true"}
    table = StaticTable.from_metadata(ref["metadata_location"], properties=props)
    return table.scan().to_arrow(), dict(table.current_snapshot().summary.additional_properties), table.schema()


def disagreements(spec, folder: Path, arrow) -> list[str]:
    expected = json.loads((folder / "truth.json").read_text(encoding="utf-8"))
    return truth.compare(expected, truth.summarize(spec, arrow.select([c.name for c in spec.cols]).cast(spec.schema())))


def numbers(problems: list[str]) -> tuple[list[int], dict[int, int]]:
    rows, lines = [], {}
    for text in problems:
        found = re.match(r"row (\d+)(?: \(line (\d+)\))?:", text)
        if found:
            rows.append(int(found.group(1)))
            if found.group(2):
                lines[int(found.group(1))] = int(found.group(2))
    return rows, lines


def main() -> int:
    require_api()
    try:
        run()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return summary("U176")


def run() -> None:
    headers = bearer_for(ENGINEER)
    department = api("GET", "/organisation", headers=headers).json()["departments"][0]["id"]
    tag = uuid.uuid4().hex[:8]
    datasets: dict[str, str] = {}

    def dataset_of(name: str) -> str:
        if name not in datasets:
            r = api("POST", "/datasets/register", headers=headers, json={
                "tenant_id": CANARY, "name": f"textfmt-{name}-{tag}", "department_id": department, "registered_by": ENGINEER, "provenance": "internal_regulated"})
            assert r.status_code == 201, f"register: HTTP {r.status_code} {r.text[:160]}"
            datasets[name] = r.json()["id"]
        return datasets[name]

    def versions_of(dataset: str) -> int:
        with db() as conn:
            return conn.execute("select count(*) as n from dataset_version where dataset_id = %s", (dataset,)).fetchone()["n"]

    def upload(table: str, path: Path, filename: str, options: dict, contract_text: str | None = None, dataset: str | None = None):
        spec = tables.SPECS[table]
        data = {}
        if options.get("delimiter", ",") != ",":
            data["delimiter"] = options["delimiter"]
        if options.get("encoding", "utf-8") != "utf-8":
            data["encoding"] = options["encoding"]
        files = {"contract": ("contract.yaml", contract_text or contracts.to_yaml(spec), "text/yaml"), "rows": (filename, path.read_bytes(), "application/octet-stream")}
        return api("POST", f"/datasets/{dataset or dataset_of(table)}/seal-table-files", headers=headers, files=files, data=data)

    corpus: dict[tuple[str, int], Path] = {}

    def made(table: str, rows: int, variants, faulty: bool = False) -> Path:
        key = (table, rows)
        if key not in corpus or faulty:
            generate.make(table, rows, variants, WORK, quiet=True, faulty=faulty)
            corpus[key] = WORK / table / str(tables.SPECS[table].fixed_rows or rows)
        return corpus[key]

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("Every text layout of a table with a composite key, decimals and milliseconds is sealed as a table that reads back to its truth")
    for table in ("fx_ticks", "ward_occupancy"):
        spec, folder = tables.SPECS[table], made(table, 1000, ALL_TEXT)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            r = upload(table, folder / entry["path"], f"{table}.{entry['extension']}", entry["options"])
            body = r.json() if r.status_code == 201 else {}
            arrow, props, _ = read_table(body.get("id", "")) if body else (None, None, None)
            problems = disagreements(spec, folder, arrow) if arrow is not None else [f"HTTP {r.status_code}: {r.text[:160]}"]
            check(f"{table} as {entry['variant']}: sealed, {body.get('records', 0):,} rows, and the stored table agrees with its truth file",
                  r.status_code == 201 and body.get("plain") is True and body.get("records") == 1000 and not problems, "; ".join(problems[:2]))

    heading("Tables with one key and with none")
    for table, variants in (("diagnosis_codes", ("csv", "jsonl")), ("merchant_categories", ("csv", "tsv")), ("vehicle_readings", ("csv", "jsonl-gzip", "json"))):
        spec, folder = tables.SPECS[table], made(table, 1000, variants)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            r = upload(table, folder / entry["path"], f"{table}.{entry['extension']}", entry["options"])
            body = r.json() if r.status_code == 201 else {}
            arrow, _, _ = read_table(body.get("id", "")) if body else (None, None, None)
            problems = disagreements(spec, folder, arrow) if arrow is not None else [f"HTTP {r.status_code}: {r.text[:160]}"]
            check(f"{table} ({len(spec.key_columns())} keys) as {entry['variant']}: sealed and agrees with its truth file", r.status_code == 201 and not problems, "; ".join(problems[:2]))

    heading("What was kept: real types, the reason, and the count")
    folder = made("fx_ticks", 1000, ("csv",))
    r = upload("fx_ticks", folder / "csv" / "fx_ticks.csv", "fx_ticks.csv", {})
    version = r.json()["id"]
    arrow, props, schema = read_table(version)
    kinds = {f.name: str(f.field_type) for f in schema.fields}
    check("the stored table has a timestamp, decimals and text, not text for all of them",
          kinds == {"pair": "string", "ticked_at": "timestamp", "bid": "decimal(12, 6)", "ask": "decimal(12, 6)", "venue": "string"}, str(kinds))
    check("the snapshot's row count is the count of rows read", props.get("munitas.record-count") == "1000" and props.get("munitas.records-format") == "csv", str({k: v for k, v in props.items() if "record" in k}))
    with db() as conn:
        found = conn.execute("select s.not_erasable_reason, s.primary_key from schema_contract s join dataset_version v on v.schema_id = s.id where v.id = %s", (version,)).fetchone()
    check("the reason the table cannot be erased and both key columns are kept with the schema",
          found["not_erasable_reason"] == "Market prices, not about any person." and list(found["primary_key"]) == ["pair", "ticked_at"], str(dict(found)))

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("A table of 100,000 rows goes through a table job in the worker and comes back the same")
    spec = tables.SPECS["ward_occupancy"]
    big = made("ward_occupancy", 100_000, ("csv", "csv-gzip"))
    dataset = dataset_of("ward_occupancy")
    with db() as conn:
        schema_id = conn.execute("select schema_id from dataset_version where dataset_id = %s order by version limit 1", (dataset,)).fetchone()["schema_id"]
    client = s3_client(*ADMIN)
    bucket = bucket_for(CANARY)

    def next_prefix() -> str:
        return api("GET", f"/datasets/{dataset}/next-version", params={"tenant_id": CANARY}).json()["storage_prefix"]

    def start_job(path: Path, name: str, options: dict) -> dict:
        key = f"{next_prefix()}/records.{name}"
        body = path.read_bytes()
        client.put_object(Bucket=bucket, Key=key, Body=body)
        r = api("POST", "/dataset-versions", json={
            "tenant_id": CANARY, "dataset_id": dataset, "schema_id": str(schema_id), "visibility_class": "RAW", "records_key": key,
            "object_manifest": [{"key": key, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}], "record_count": 0,
            "table_required": True, "table_mode": "background", "records_options": options})
        assert r.status_code == 202, f"job: HTTP {r.status_code} {r.text[:200]}"
        started = time.monotonic()
        while time.monotonic() - started < WAIT_SECONDS:
            state = api("GET", f"/table-jobs/{r.json()['job_id']}", headers=headers).json()
            if state["status"] in ("sealed", "refused", "expired"):
                return {**state, "key": key, "bytes": len(body)}
            time.sleep(2)
        return {**api("GET", f"/table-jobs/{r.json()['job_id']}", headers=headers).json(), "key": key}

    for name, label in (("csv", "a CSV"), ("csv.gz", "a gzip-compressed CSV")):
        path = big / ("csv" if name == "csv" else "csv-gzip") / f"ward_occupancy.{name}"
        done = start_job(path, name, {})
        arrow, props, _ = read_table(done.get("version_id") or "") if done.get("version_id") else (None, None, None)
        problems = disagreements(spec, big, arrow) if arrow is not None else [f"job {done.get('status')}: {done.get('reason')} {done.get('problems')}"]
        check(f"{label} of 100,000 rows ({done.get('bytes', 0) // 1000:,} KB) is written by the worker, sealed, and agrees with its truth file",
              done.get("status") == "sealed" and not problems and props.get("munitas.record-count") == "100000", "; ".join(problems[:2]))

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("A faulty file is refused, naming the rows and lines damaged and no others, and seals nothing")
    spec = tables.SPECS["ward_occupancy"]
    folder = made("ward_occupancy", 20_000, ("csv", "tsv", "jsonl", "json"), faulty=True)
    answers = json.loads((folder / "faults" / "answers.json").read_text(encoding="utf-8"))["faults"]
    refused = dataset_of("ward_occupancy-refused")
    wrong, count = [], 0
    for answer in answers:
        before = versions_of(refused)
        r = upload("ward_occupancy", folder / answer["file"], Path(answer["file"]).name, {"delimiter": answer.get("delimiter", ",")}, dataset=refused)
        problems = r.json().get("detail", {}).get("reasons", []) if r.status_code == 400 else [f"HTTP {r.status_code}"]
        rows, lines = numbers(problems)
        planted = sorted(p["row"] for p in answer["planted"] if "row" in p)
        want = {p["row"]: p["line"] for p in answer["planted"] if "line" in p}
        count += 1
        if r.status_code != 400 or versions_of(refused) != before:
            wrong.append(f"{answer['id']}: HTTP {r.status_code}")
        elif answer["level"] == "row" and (sorted(set(rows)) != planted or any(lines.get(n, want[n]) != want[n] for n in planted if n in want)):
            wrong.append(f"{answer['id']}: wanted rows {planted}, got {rows}")
        elif answer["level"] == "file" and not problems:
            wrong.append(f"{answer['id']}: no problem named")
    check(f"{count} faulty files of 20,000 rows are each refused naming exactly the rows and lines the answers list, and nothing is sealed", not wrong, "; ".join(wrong[:3]))

    folder = made("ward_occupancy", 100_000, ("csv",), faulty=True)
    answers = json.loads((folder / "faults" / "answers.json").read_text(encoding="utf-8"))["faults"]
    wrong, count = [], 0
    for answer in (a for a in answers if a["id"] in ("csv-bad-integer", "csv-repeated-key", "csv-short-row", "csv-invalid-byte", "csv-missing-column", "csv-empty-key")):
        done = start_job(folder / answer["file"], "csv", {})
        problems = done.get("problems") or []
        rows, lines = numbers(problems)
        planted = sorted(p["row"] for p in answer["planted"] if "row" in p)
        want = {p["row"]: p["line"] for p in answer["planted"] if "line" in p}
        count += 1
        folder_left = [o["Key"] for o in client.list_objects_v2(Bucket=bucket, Prefix=done["storage_prefix"] + "/").get("Contents", []) if "/iceberg/" in o["Key"]]
        if done.get("status") != "refused" or folder_left:
            wrong.append(f"{answer['id']}: job {done.get('status')}, {len(folder_left)} table files left")
        elif answer["level"] == "row" and (sorted(set(rows)) != planted or any(lines.get(n, want[n]) != want[n] for n in planted if n in want)):
            wrong.append(f"{answer['id']}: wanted rows {planted}, got {rows}")
    check(f"{count} faulty files of 100,000 rows in a worker job end refused, with exactly the rows and lines listed and no table left behind", not wrong, "; ".join(wrong[:3]))

    heading("A file declared with the wrong delimiter is refused for its header")
    folder = made("ward_occupancy", 1000, ALL_TEXT)
    r = upload("ward_occupancy", folder / "csv-semicolon" / "ward_occupancy.csv", "ward_occupancy.csv", {}, dataset=refused)
    reasons = " ".join(r.json().get("detail", {}).get("reasons", [])) if r.status_code == 400 else r.text[:120]
    check("a semicolon file read as comma separated is refused: its header does not match", r.status_code == 400 and "header" in reasons, reasons[:140])


if __name__ == "__main__":
    sys.exit(main())
