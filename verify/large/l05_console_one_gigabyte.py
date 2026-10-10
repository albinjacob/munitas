r"""L5: the console sends a file of about a gigabyte in parts, and the page itself stays small.

LARGE TIER. Never part of the ordinary suite. Run it with verify\run_large.py, which says what it will do, how long it takes and how much disk it needs
before it does anything.

Makes the file and its contract, then runs the one Playwright test that is allowed to exist only for this (web/tests/large, MUNITAS_LARGE=1) against the
live console. That test reads the page's own JavaScript memory while the file goes and asks the console to show the table sealed. Checked here: the test
passes, and the table the platform stored agrees with the truth file.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import flow  # noqa: E402
from common import ENGINEER, check, db, require_api, summary  # noqa: E402

ID = "L5"
TITLE = "the console sends a file of about a gigabyte in parts and the page stays small"
TABLE = "vehicle_readings"
WANTED_BYTES = flow.scaled(1024 ** 3)
WEB = flow.ROOT / "web"


def rows_for_bytes() -> int:
    per_row = flow.generate.estimate(flow.tables.SPECS[TABLE], 1000, ["csv"]) / 1000
    return int(WANTED_BYTES / per_row)


def need() -> dict:
    rows = rows_for_bytes()
    return {"disk_bytes": flow.generate.estimate(flow.tables.SPECS[TABLE], rows, ["csv"]), "minutes": "30 to 90 (not measured yet)",
            "does": [f"makes {TABLE} with about {rows:,} rows as one CSV file of about 1 GB under {flow.DATA}, and its contract",
                     "opens the console in a browser, signs in as the canary engineer, chooses the file and sends it, reading the page's memory every 5 seconds",
                     "checks the console shows the table sealed, and that the stored table agrees with the truth file; removes the made file at the end"]}


def main() -> int:
    require_api()
    rows = rows_for_bytes()
    folder = flow.make_files(TABLE, rows, ["csv"])
    try:
        contract = folder.parent / "contract.yaml"
        env = {**os.environ, "MUNITAS_LARGE": "1", "MUNITAS_LARGE_FILE": str(folder / "csv" / f"{TABLE}.csv"), "MUNITAS_LARGE_CONTRACT": str(contract),
               **({"MUNITAS_LARGE_WAIT_MINUTES": "4", "MUNITAS_SHOTS": str(flow.DATA)} if flow.DIVISOR > 1 else {})}
        with db() as conn:
            before = {r["id"] for r in conn.execute("select id from dataset_version").fetchall()}
        done = subprocess.run("npx playwright test --project=large", cwd=str(WEB), env=env, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(done.stdout[-3000:])
        measured = re.search(r"LARGE-MEASURE (\{.*\})", done.stdout)
        check("the console sent the file, the page's memory stayed level, and the table was sealed", done.returncode == 0, (measured.group(1) if measured else done.stderr[-300:]))
        with db() as conn:
            fresh = [r["id"] for r in conn.execute("select id from dataset_version").fetchall() if r["id"] not in before]
        check("one new version was made", len(fresh) == 1, f"{len(fresh)} new")
        if len(fresh) == 1:
            problems = flow.agrees_with_truth(str(fresh[0]), TABLE, folder)
            check("the stored table agrees with the truth file", not problems, "; ".join(problems[:2]))
    finally:
        flow.remove(folder)
    return summary(ID)


if __name__ == "__main__":
    sys.exit(main())
