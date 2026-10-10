r"""L1: tables of five million rows, as CSV and as zstd-compressed JSON lines, are written with the worker's memory level, and agree with their truth files.

LARGE TIER. Never part of the ordinary suite. Run it with verify\run_large.py, which says what it will do, how long it takes and how much disk it needs
before it does anything.

For each layout: the generated file is sent in parts through the person's door, the worker writes the table, and while that goes on the worker's memory is
read every few seconds and an ordinary call is timed every second. Checked:

  * the table is written, with every row;
  * the stored table agrees with the generator's truth file (count, nulls, smallest, largest, sums, the fingerprint of every row);
  * the worker's memory while the last 40 percent of the rows went by is no higher than while the first third did (give or take a little), and never
    near the container's limit: a table held in memory grows with its rows, a table written in batches does not;
  * an ordinary call, timed all through, stayed fast.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import flow  # noqa: E402
from common import ENGINEER, api, bearer_for, check, heading, require_api, summary  # noqa: E402

ID = "L1"
TITLE = "tables of five million rows, as CSV and as compressed JSON lines, are written with the worker's memory level"
TABLE = "fx_ticks"
ROWS = flow.scaled(5_000_000)
VARIANTS = ["csv", "jsonl-zstd"]


def need() -> dict:
    spec = flow.tables.SPECS[TABLE]
    return {"disk_bytes": flow.generate.estimate(spec, ROWS, VARIANTS), "minutes": "20 to 60 (not measured yet)",
            "does": [f"makes {TABLE} with {ROWS:,} rows as {', '.join(VARIANTS)} under {flow.DATA}",
                     "sends each file in parts, has the worker write it, and reads the worker's memory every few seconds",
                     "compares each stored table with the truth file, and removes the made files at the end"]}


def main() -> int:
    require_api()
    headers = bearer_for(ENGINEER)
    department = api("GET", "/organisation", headers=headers).json()["departments"][0]["id"]
    folder = flow.make_files(TABLE, ROWS, VARIANTS)
    try:
        for variant in VARIANTS:
            heading(f"{TABLE}, {ROWS:,} rows, {variant}")
            variant_path = folder / variant / f"{TABLE}.{flow.generate.formats.VARIANTS[variant].extension}"
            result = flow.bring_in(headers, department, TABLE, variant_path, ROWS)
            check(f"{variant}: written, with every row", result.get("status") == "sealed" and (result.get("progress") or {}).get("rows") == ROWS,
                  f"{result.get('status')}: {result.get('reason')} {str(result.get('problems'))[:200]}")
            if result.get("status") != "sealed":
                continue
            flow.out(f"    sent in {result['sent_seconds']:.0f}s, written in {result['written_seconds']:.0f}s")
            problems = flow.agrees_with_truth(result["version_id"], TABLE, folder)
            check(f"{variant}: the stored table agrees with the truth file", not problems, "; ".join(problems[:2]))
            ok, words = flow.level(result["memory"])
            check(f"{variant}: the worker's memory stays level", ok, words)
            latency = result["latency"]
            check(f"{variant}: an ordinary call stayed fast while the table was sent and written", latency["calls"] > 20 and latency["p95"] < 1.5 and latency["worst"] < 10,
                  f"{latency['calls']} calls, median {latency['median']:.2f}s, 95th {latency['p95']:.2f}s, worst {latency['worst']:.2f}s")
    finally:
        flow.remove(folder)
    return summary(ID)


if __name__ == "__main__":
    sys.exit(main())
