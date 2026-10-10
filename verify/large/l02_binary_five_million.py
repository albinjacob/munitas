r"""L2: tables of five million rows as Parquet, ORC, Arrow and Avro, and of a million as an Excel workbook, are written with the worker's memory level.

LARGE TIER. Never part of the ordinary suite. Run it with verify\run_large.py, which says what it will do, how long it takes and how much disk it needs
before it does anything.

The same checks as L1, for the binary layouts. A workbook holds at most 1,048,575 rows, so it is tried at a million. A binary layout is read a row group, a
stripe or a record batch at a time, so the worker's memory should follow the size of one of those, not of the table.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import flow  # noqa: E402
from common import ENGINEER, api, bearer_for, check, heading, require_api, summary  # noqa: E402

ID = "L2"
TITLE = "tables of five million rows as Parquet, ORC, Arrow and Avro, and a million as Excel, are written with the worker's memory level"
TABLE = "fx_ticks"
BIG = ("parquet", flow.scaled(5_000_000)), ("orc", flow.scaled(5_000_000)), ("arrow", flow.scaled(5_000_000)), ("avro", flow.scaled(5_000_000))
SHEET = ("xlsx", flow.scaled(1_000_000))


def need() -> dict:
    spec = flow.tables.SPECS[TABLE]
    disk = sum(flow.generate.estimate(spec, rows, [variant]) for variant, rows in BIG + (SHEET,))
    return {"disk_bytes": disk, "minutes": "40 to 120 (not measured yet)",
            "does": [f"makes {TABLE} with 5,000,000 rows as Parquet, ORC, Arrow and Avro, and with 1,000,000 rows as an Excel workbook, one layout at a time under {flow.DATA}",
                     "sends each file in parts, has the worker write it, and reads the worker's memory every few seconds",
                     "compares each stored table with its truth file, and removes the made files after each layout"]}


def main() -> int:
    require_api()
    headers = bearer_for(ENGINEER)
    department = api("GET", "/organisation", headers=headers).json()["departments"][0]["id"]
    for variant, rows in BIG + (SHEET,):
        heading(f"{TABLE}, {rows:,} rows, {variant}")
        folder = flow.make_files(TABLE, rows, [variant])
        try:
            path = folder / variant / f"{TABLE}.{flow.generate.formats.VARIANTS[variant].extension}"
            result = flow.bring_in(headers, department, TABLE, path, rows)
            check(f"{variant}: written, with every row", result.get("status") == "sealed" and (result.get("progress") or {}).get("rows") == rows,
                  f"{result.get('status')}: {result.get('reason')} {str(result.get('problems'))[:200]}")
            if result.get("status") != "sealed":
                continue
            flow.out(f"    sent in {result['sent_seconds']:.0f}s, written in {result['written_seconds']:.0f}s")
            problems = flow.agrees_with_truth(result["version_id"], TABLE, folder)
            check(f"{variant}: the stored table agrees with the truth file", not problems, "; ".join(problems[:2]))
            ok, words = flow.level(result["memory"])
            check(f"{variant}: the worker's memory stays level", ok, words)
            latency = result["latency"]
            check(f"{variant}: an ordinary call stayed fast", latency["calls"] > 20 and latency["p95"] < 1.5 and latency["worst"] < 10,
                  f"{latency['calls']} calls, median {latency['median']:.2f}s, 95th {latency['p95']:.2f}s, worst {latency['worst']:.2f}s")
        finally:
            flow.remove(folder)
    return summary(ID)


if __name__ == "__main__":
    sys.exit(main())
