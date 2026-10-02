"""U104: a table is handed over as the rows for named people, never whole, and the people are named by the custodian.

A demand usually asks about one person, and a table holds thousands. Handing over the whole table would send
everybody else's data out with it. This puts a table and a folder of files under a legal hold, asks for the table
filtered to some people, and checks every step from the side it concerns: that only a table can be filtered and only by
a plain column, that the values are named by the custodian and never seen by a platform administrator, that the
custodian can count what a filter matches before confirming, and, most importantly, that the package holds exactly the
matching rows and nothing else of that table: not the original records, not the Iceberg table that holds every row. The
package is opened with the recipient's own tool.

    docker compose exec -T munitas-api python /verify/v104_legal_export_filter.py
"""

from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, "/client")

from common import api, bearer_for, check, db, fixture_tabular_version, heading, require_api, summary, tabular_rows  # noqa: E402
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, export_body, finish_org, hold_in_force, make_org, seal_data  # noqa: E402
import open_legal_package  # noqa: E402


def reasons(r: httpx.Response) -> str:
    try:
        body = r.json()["detail"]
        return " ".join(body["reasons"]) if isinstance(body, dict) else str(body)
    except Exception:
        return r.text[:200]


def wait_ready(export_id: str, seconds: int = 120) -> dict:
    end = time.monotonic() + seconds
    while True:
        with db() as conn:
            row = conn.execute("select status, failure from legal_export where id = %s", (export_id,)).fetchone()
        if row["status"] in ("ready", "failed") or time.monotonic() > end:
            return row
        time.sleep(2)


def main() -> int:
    require_api()
    org = make_org()
    priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
    try:
        files = seal_data(org)
        rows = tabular_rows(6)
        table = fixture_tabular_version(org.id, rows=rows, dataset_name="appointments")
        records, custodian = org.bearer("dpo"), org.bearer("custodian")
        hold_id = hold_in_force(org, priya, ravi)
        both = [files["dataset_id"], table["dataset_id"]]

        heading("What can be filtered, and by what")
        scope = api("GET", "/legal-exports/scope", params={"hold_id": hold_id}, headers=priya).json()["datasets"]
        by_name = {d["name"]: d for d in scope}
        check("the choice of datasets says which are tables and by which plain columns they can be filtered",
              by_name["appointments"]["tabular"] and "record_id" in by_name["appointments"]["columns"]
              and "tags" not in by_name["appointments"]["columns"] and not by_name["records"]["tabular"], str(by_name["appointments"]["columns"]))
        r = api("POST", "/legal-exports", headers=priya, json=export_body(hold_id, both, filters=[{"dataset_id": files["dataset_id"], "column": "record_id"}]))
        check("a folder of files cannot be filtered", r.status_code == 422 and "not stored as a table" in reasons(r), reasons(r)[:100])
        r = api("POST", "/legal-exports", headers=priya, json=export_body(hold_id, both, filters=[{"dataset_id": table["dataset_id"], "column": "no_such_column"}]))
        check("nor by a column that does not exist, and the refusal lists the columns that can be used",
              r.status_code == 422 and "record_id" in reasons(r), reasons(r)[:120])
        r = api("POST", "/legal-exports", headers=priya, json=export_body(hold_id, both, filters=[{"dataset_id": table["dataset_id"], "column": "tags"}]))
        check("nor by a column that holds a list", r.status_code == 422, f"{r.status_code}")
        r = api("POST", "/legal-exports", headers=priya, json=export_body(hold_id, [files["dataset_id"]], filters=[{"dataset_id": table["dataset_id"], "column": "record_id"}]))
        check("a dataset that is filtered must be one of the datasets named", r.status_code == 422, reasons(r)[:100])

        heading("The custodian names the people, and sees only counts")
        made = api("POST", "/legal-exports", headers=priya, json=export_body(hold_id, both, filters=[{"dataset_id": table["dataset_id"], "column": "record_id"}]))
        check("a table filtered by one plain column is accepted", made.status_code == 201 and made.json()["filters"][0]["column"] == "record_id", reasons(made))
        export_id = made.json()["id"]
        api("POST", f"/legal-exports/{export_id}/approve", headers=ravi, json={"approve": True, "note": "ok"}).raise_for_status()
        values = ["rec-1", "rec-3", "rec-nobody"]
        r = api("POST", f"/legal-exports/{export_id}/confirm", headers=records, json={"approve": True, "note": "no values"})
        check("the custodian cannot confirm without naming the people",
              r.status_code == 403 and "names the values" in reasons(r) and table["dataset_id"] in reasons(r), reasons(r)[:120])
        r = api("POST", f"/legal-exports/{export_id}/filter-preview", headers=priya, json={"values": {table["dataset_id"]: values}})
        check("a platform administrator cannot ask what a filter matches", r.status_code == 403, f"{r.status_code}")
        r = api("POST", f"/legal-exports/{export_id}/filter-preview", headers=custodian, json={"values": {table["dataset_id"]: values}})
        check("nor the organisation's own data custodian", r.status_code == 403, f"{r.status_code}")
        r = api("POST", f"/legal-exports/{export_id}/filter-preview", headers=records, json={"values": {table["dataset_id"]: values}})
        result = r.json()["results"][0] if r.status_code == 200 else {}
        check("the custodian is told how many rows match, out of how many, and which value matched none",
              result.get("rows_matched") == 2 and result.get("rows_total") == 6 and result.get("unmatched_values") == ["rec-nobody"], str(result))
        check("and the answer holds no row", "transcript" not in r.text and "synthetic" not in r.text)

        r = api("POST", f"/legal-exports/{export_id}/confirm", headers=records, json={"approve": True, "note": "These two patients", "values": {table["dataset_id"]: values}})
        check("the custodian confirms, with the values", r.status_code == 200 and r.json()["status"] == "confirmed", reasons(r))
        check("what comes back holds no value", "rec-1" not in r.text and "rec-3" not in r.text)
        row = wait_ready(export_id)
        check("the package is built", row["status"] == "ready", f"{row['status']} {row['failure'] or ''}")

        heading("A platform administrator never sees the people named")
        listing = api("GET", "/legal-exports", params={"hold_id": hold_id}, headers=priya)
        mine = next(e for e in listing.json()["exports"] if e["id"] == export_id)
        check("the listing names the filter and the counts and no value",
              mine["filters"] == [{"dataset_id": table["dataset_id"], "column": "record_id"}]
              and mine["filter_results"][table["dataset_id"]]["rows_matched"] == 2
              and "filter_values" not in mine and "rec-1" not in listing.text and "rec-3" not in listing.text, str(mine["filter_results"]))
        manifest = api("GET", f"/legal-exports/{export_id}/manifest", headers=priya)
        check("nor does the manifest list", "rec-1" not in manifest.text)

        heading("The package holds the rows, and only the rows")
        passphrase = api("POST", f"/legal-exports/{export_id}/passphrase", headers=records).json()["passphrase"]
        link = api("POST", f"/legal-exports/{export_id}/links", headers=priya).json()
        package = Path(tempfile.mkdtemp()) / "p.mlep"
        package.write_bytes(api("GET", link["download_path"]).content)
        opened = Path(tempfile.mkdtemp())
        report = open_legal_package.open_package(package, passphrase, opened, api("GET", "/legal-exports/signing-key").json()["public_key"])
        check("the package opens and every file matches its fingerprint", report["ok"] and report["signed"], "; ".join(report["problems"]))
        paths = sorted(p.relative_to(opened).as_posix() for p in (opened / "data").rglob("*") if p.is_file())
        tabled = [p for p in paths if "appointments" in p]
        check("the table is one file, the filtered rows", tabled == ["data/appointments/v1/v1.filtered.csv"], str(tabled))
        check("no original records and no Iceberg file of that table are in the package",
              not any("records.json" in p or "iceberg" in p or p.endswith(".parquet") or "metadata" in p for p in tabled)
              and "synthetic transcript number 0" not in "".join((opened / p).read_text(errors="ignore") for p in paths if p.startswith("data/appointments")))
        check("the folder of files, which was not filtered, is whole", sum(1 for p in paths if p.startswith("data/records/")) == 2)
        got = list(csv.DictReader(io.StringIO((opened / "data/appointments/v1/v1.filtered.csv").read_text())))
        check("the rows are exactly the two named, and the other four are not there",
              sorted(r["record_id"] for r in got) == ["rec-1", "rec-3"], str([r["record_id"] for r in got]))
        check("every column of those rows is kept, with a list and a dictionary written as JSON",
              got and json.loads(got[0]["tags"]) == ["a", "b1"] and json.loads(got[0]["detail"])["i"] == 1 and got[0]["transcript"] == "synthetic transcript number 1", str(got[0])[:120])
        entry = next(f for f in report["manifest"]["files"] if f["path"].endswith(".filtered.csv"))
        check("the manifest says it is filtered: the column, how many values, how many rows matched of how many, and the sealed original",
              entry["filtered_by"]["column"] == "record_id" and entry["filtered_by"]["values"] == 3
              and entry["filtered_by"]["rows_matched"] == 2 and entry["filtered_by"]["rows_total"] == 6
              and entry["filtered_by"]["source_records_sha256"] == table["records_sha256"], str(entry["filtered_by"]))
        custody = json.loads((opened / "chain-of-custody.json").read_text())
        check("the chain of custody records the filter and the values the custodian named",
              custody["filters"][0]["values_named_by_the_custodian"] == values and custody["filters"][0]["rows_matched"] == 2, str(custody["filters"])[:140])

        heading("A filter that matches nobody still produces an honest file")
        empty = api("POST", "/legal-exports", headers=priya, json=export_body(hold_id, [table["dataset_id"]], demand_reference="KB-2026-004600",
                                                                             filters=[{"dataset_id": table["dataset_id"], "column": "record_id"}])).json()["id"]
        api("POST", f"/legal-exports/{empty}/approve", headers=ravi, json={"approve": True, "note": "ok"}).raise_for_status()
        api("POST", f"/legal-exports/{empty}/confirm", headers=records, json={"approve": True, "values": {table["dataset_id"]: ["nobody-at-all"]}}).raise_for_status()
        check("the second export is built", wait_ready(empty)["status"] == "ready")
        pass2 = api("POST", f"/legal-exports/{empty}/passphrase", headers=records).json()["passphrase"]
        link2 = api("POST", f"/legal-exports/{empty}/links", headers=priya).json()
        package2 = Path(tempfile.mkdtemp()) / "q.mlep"
        package2.write_bytes(api("GET", link2["download_path"]).content)
        opened2 = Path(tempfile.mkdtemp())
        report2 = open_legal_package.open_package(package2, pass2, opened2)
        lines = (opened2 / "data/appointments/v1/v1.filtered.csv").read_text().strip().splitlines()
        check("it holds the header and no rows, and the manifest says nothing matched",
              report2["ok"] and len(lines) == 1 and "record_id" in lines[0]
              and next(f for f in report2["manifest"]["files"] if f["path"].endswith(".csv"))["filtered_by"]["rows_matched"] == 0, str(lines)[:100])

        heading("What a deletion keeps")
        finish_org(org, priya, ravi)
        with db() as conn:
            record = conn.execute("select exports from tenant_deletion_record where original_tenant_id = %s", (org.id,)).fetchone()
        check("the record counts the filtered datasets and holds no value",
              record and sum(e["filtered_datasets"] for e in record["exports"]) == 2 and "rec-1" not in json.dumps(record["exports"]),
              str(record["exports"])[:150] if record else "no record")
    finally:
        finish_org(org, priya, ravi)
        drop_org(org)
    return summary("U104")


if __name__ == "__main__":
    sys.exit(main())
