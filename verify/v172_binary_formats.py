r"""U182: a person brings in a table as a Parquet, Arrow, Feather, ORC, Avro or Excel file, and a worker reads it strictly.

Runs on the host against the live platform, with generated tables and their truth files (scripts/testdata). Ordinary size: tables of 5,000 and 20,000 rows.

    $env:MUNITAS_PG_DSN = "postgresql://munitas:munitas@localhost:5432/platform"
    $env:MUNITAS_VERIFY_KRATOS = "http://localhost:4433"
    $env:MUNITAS_S3_ENDPOINT = "http://localhost:8333"
    .venv\Scripts\python.exe verify\v172_binary_formats.py

The machine's .env needs MUNITAS_TABLE_UPLOAD_PER_HOUR and MUNITAS_TABLE_UPLOAD_REFUSED_PER_HOUR above the number of files below, or the platform
turns the later ones away (which it should: that is a different check, U177).

Checked one file at a time, and reported per file:

  * a clean file of each format is written by a worker, and the stored table agrees with the generator's truth file (count, nulls, smallest, largest,
    sums, the fingerprint of every row);
  * a faulty file is refused, with no version made, and the refusal names every row the generator planted (for a workbook, the row of the sheet too);
    a file whose columns are not the contract's, or are of the wrong type, is refused naming the column;
  * a hostile file (cut short, not the format its ending says, a workbook that expands to hundreds of times its size, a workbook with a macro, an Avro
    block that claims gigabytes) is refused in words, and a clean table sent afterwards is still written, so the worker is intact.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

from common import ADMIN, CANARY, ENGINEER, S3_ENDPOINT, api, bearer_for, check, db, heading, require_api, summary  # noqa: E402
from scripts.testdata import contracts, generate, tables, truth  # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix="munitas-u182-"))
WAIT = 180
CLEAN = [("fx_ticks", ["parquet", "parquet-small-groups", "arrow", "feather", "orc", "avro", "xlsx"]),
         ("ward_occupancy", ["parquet", "xlsx"]),
         ("vehicle_readings", ["parquet", "avro"])]
FAULTY = ["ward_occupancy", "fx_ticks"]


def main() -> int:
    require_api()
    try:
        run()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return summary("U182")


def read_table(version_id: str):
    from pyiceberg.table import StaticTable

    with db() as conn:
        ref = conn.execute("select metadata_location from iceberg_table_ref where dataset_version_id = %s", (version_id,)).fetchone()
    if not ref:
        return None
    props = {"s3.endpoint": S3_ENDPOINT, "s3.access-key-id": ADMIN[0], "s3.secret-access-key": ADMIN[1], "s3.region": "us-east-1", "s3.path-style-access": "true"}
    return StaticTable.from_metadata(ref["metadata_location"], properties=props).scan().to_arrow()


def run() -> None:
    headers = bearer_for(ENGINEER)
    department = api("GET", "/organisation", headers=headers).json()["departments"][0]["id"]
    tag = uuid.uuid4().hex[:6]

    def bring(table: str, path: Path, name: str | None = None) -> tuple[dict, str, float]:
        """Register a dataset, send the file as one upload, and wait. Returns the final status, the dataset id and the seconds it took."""
        spec = tables.SPECS[table]
        r = api("POST", "/datasets/register", headers=headers, json={"tenant_id": CANARY, "name": f"binfmt-{table}-{uuid.uuid4().hex[:8]}", "department_id": department,
                                                                     "registered_by": ENGINEER, "provenance": "internal_regulated"})
        assert r.status_code == 201, f"register: HTTP {r.status_code} {r.text[:160]}"
        dataset = r.json()["id"]
        data = path.read_bytes()
        started = time.monotonic()
        r = api("POST", f"/datasets/{dataset}/table-uploads", headers=headers, json={
            "file_name": name or path.name, "size_bytes": len(data), "contract": contracts.to_yaml(spec), "options": {}})
        if r.status_code != 201:
            return {"status": "not opened", "problems": [f"HTTP {r.status_code} {r.text[:300]}"]}, dataset, 0.0
        job = r.json()["job_id"]
        part = r.json()["part_bytes"]
        for n, at in enumerate(range(0, len(data), part), start=1):
            put = api("PUT", f"/table-uploads/{job}/parts/{n}", headers=headers, content=data[at:at + part])
            assert put.status_code == 200, f"part {n}: HTTP {put.status_code} {put.text[:160]}"
        fin = api("POST", f"/table-uploads/{job}/finish", headers=headers)
        assert fin.status_code == 202, f"finish: HTTP {fin.status_code} {fin.text[:160]}"
        status = {}
        while time.monotonic() - started < WAIT:
            status = api("GET", f"/table-uploads/{job}", headers=headers).json()
            if status.get("status") in ("sealed", "refused", "expired"):
                break
            time.sleep(1)
        return status, dataset, time.monotonic() - started

    def versions_of(dataset: str) -> int:
        with db() as conn:
            return conn.execute("select count(*) as n from dataset_version where dataset_id = %s", (dataset,)).fetchone()["n"]

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("A clean file of each format is written, and the stored table agrees with its truth file")
    for table, variants in CLEAN:
        spec = tables.SPECS[table]
        generate.make(table, 20_000, variants, WORK / "clean", quiet=True)
        folder = WORK / "clean" / table / "20000"
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        expected = json.loads((folder / "truth.json").read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            status, dataset, took = bring(table, folder / entry["path"])
            label = f"{table} as {entry['variant']}"
            if status.get("status") != "sealed":
                check(f"{label}: written", False, f"{status.get('status')}: {status.get('reason')} {(status.get('problems') or [])[:2]}")
                continue
            arrow = read_table(status["version_id"])
            problems = truth.compare(expected, truth.summarize(spec, arrow.select([c.name for c in spec.cols]).cast(spec.schema()))) if arrow is not None else ["no table"]
            check(f"{label}: 20,000 rows written in {took:.0f}s and the table agrees with its truth file", not problems and status.get("outcome") == "written"
                  and (status.get("progress") or {}).get("rows") == 20_000, "; ".join(problems[:2]) or str(status.get("progress")))

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("A faulty file is refused, with no version, naming every planted row or column")
    hostile_source = None
    for table in FAULTY:
        spec = tables.SPECS[table]
        generate.make(table, 5_000, ("parquet", "arrow", "orc", "avro", "xlsx", "csv"), WORK / "faults", quiet=True, faulty=True)
        folder = WORK / "faults" / table / "5000"
        answers = json.loads((folder / "faults" / "answers.json").read_text(encoding="utf-8"))
        hostile_source = (table, folder, answers["hostile"])
        for entry in answers["faults"]:
            if entry["format"] not in ("parquet", "arrow", "orc", "avro", "xlsx"):
                continue
            status, dataset, took = bring(table, folder / entry["file"])
            label = f"{table}, {entry['id']}"
            text = " | ".join(status.get("problems") or []) + " " + (status.get("reason") or "")
            refused = status.get("status") == "refused" and status.get("outcome") == "refused" and versions_of(dataset) == 0
            if entry["level"] == "row":
                rows = sorted(p["row"] for p in entry["planted"])
                lines = {p["row"]: p["line"] for p in entry["planted"] if "line" in p}
                named = [n for n in rows if f"row {n}:" in text or f"row {n} (" in text]
                wrong_line = [n for n in named if n in lines and f"(sheet row {lines[n]})" not in text]
                check(f"{label}: refused, no version, and all {len(rows)} planted rows named", refused and named == rows and not wrong_line,
                      f"{status.get('status')}/{status.get('outcome')}; named {named} of {rows}; wrong sheet row {wrong_line}; {text[:140]}")
            else:
                column = entry["planted"][0]["column"]
                check(f"{label}: refused, no version, and the column {column} is named", refused and column in text, f"{status.get('status')}; {text[:200]}")

    # ------------------------------------------------------------------------------------------------------------------------------
    heading("A hostile file is refused in words, and the worker carries on")
    table, folder, hostile = hostile_source
    for entry in hostile:
        status, dataset, took = bring(table, folder / entry["file"])
        text = " ".join(status.get("problems") or []) + " " + (status.get("reason") or "")
        check(f"{entry['id']}: refused in {took:.0f}s, no version, and it says why", status.get("status") == "refused" and versions_of(dataset) == 0 and len(text.strip()) > 20
              and took < 90, f"{status.get('status')}/{status.get('outcome')} {text[:160]}")
    spec = tables.SPECS[table]
    generate.make(table, 2_000, ("parquet",), WORK / "after", quiet=True)
    after = WORK / "after" / table / "2000"
    status, dataset, took = bring(table, after / "parquet" / f"{table}.parquet")
    expected = json.loads((after / "truth.json").read_text(encoding="utf-8"))
    arrow = read_table(status["version_id"]) if status.get("status") == "sealed" else None
    problems = truth.compare(expected, truth.summarize(spec, arrow.select([c.name for c in spec.cols]).cast(spec.schema()))) if arrow is not None else ["not written"]
    check("a clean table sent after all of them is written and agrees with its truth file", not problems, "; ".join(problems[:2]) + f" {status.get('reason')}")


if __name__ == "__main__":
    sys.exit(main())
