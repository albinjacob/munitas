"""U125: what is checked after a run's cleanup is kept on the history page as a second line tied to that run, and nothing already recorded is rewritten.

A run is recorded before its cleanup, because the cleanup deletes data. The check that no test tenants were left behind runs after that, so its
result cannot be in the run's own line, and the history file is append-only. `verify/report.py --add-after` appends a second line, `{"kind":
"after_run", "run_started_at": ...}`, and the loader attaches it to the run it names. This checks, one item at a time, against a folder of its own
(never this machine's real record):

  * adding it leaves the run's own line exactly as it was, and the file only grows;
  * the second line is not counted as a run, and the run's own results are unchanged;
  * the result appears as a row of its own, marked as coming after the cleanup, with its detail, and in the per-check summary with its failure;
  * a result for a run that is not recorded is refused and nothing is written, and so is a record with no results;
  * a stray line naming a missing run, and a corrupt line, are skipped aloud and do not cost the rest of the history;
  * a run recorded before this existed renders and summarises exactly as before;
  * the page carries the card that says how the last run's cleanup went.

    docker compose exec -T munitas-api python /verify/v125_history_keeps_after_run_results.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Set before report.py is read: it fixes where the history lives when it loads.
HISTORY = Path(tempfile.mkdtemp(prefix="u125-history-"))
os.environ["MUNITAS_VERIFY_HISTORY_DIR"] = str(HISTORY)

from common import check, heading, summary  # noqa: E402

spec = importlib.util.spec_from_file_location("verify_report", Path(__file__).resolve().parent / "report.py")
report = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = report
spec.loader.exec_module(report)

STARTED = "2026-10-04T13:29:39.348558+00:00"
OLDER = "2026-10-03T19:02:50.927665+00:00"


def a_run(started: str, seconds: float = 100.0) -> dict:
    return {"started_at": started, "seconds": seconds, "host": "u125",
            "results": [{"check": "U1", "label": "first", "script": "v1.py", "status": "pass", "passed": 3, "failed": 0, "skipped": 0, "seconds": 1.5},
                        {"check": "U2", "label": "second", "script": "v2.py", "status": "pass", "passed": 2, "failed": 0, "skipped": 0, "seconds": 2.5}]}


def after_record(started: str, status: str = "fail", detail: str = "1 leftover tenant(s)") -> dict:
    return {"run_started_at": started, "recorded_at": "2026-10-04T13:47:00Z",
            "results": [{"check": "LEAK", "label": "no test tenants left behind", "script": "scripts/admin/check-leftover-tenants.py", "status": status,
                         "passed": int(status == "pass"), "failed": int(status != "pass"), "skipped": 0, "seconds": 0.4, "detail": detail}]}


def feed(name: str, payload: dict, add: str) -> None:
    path = HISTORY / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    (report.append_run if add == "run" else report.append_after_run)(str(path))


def refused(add, payload: dict) -> bool:
    path = HISTORY / "refused.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        add(str(path))
    except SystemExit:
        return True
    return False


def quiet_load() -> tuple[list[dict], str]:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        runs = report.load_runs()
    return runs, err.getvalue()


def main() -> int:
    heading("A run recorded before this existed is unchanged")
    feed("old.json", a_run(OLDER), "run")
    runs, _ = quiet_load()
    rows = report.flatten(runs)
    check("it loads as one run with no after-run results", len(runs) == 1 and "after_results" not in runs[0])
    check("its rows are exactly its own results, all marked as the suite's", len(rows) == 2 and {r["phase"] for r in rows} == {"suite"})
    check("the page still renders", "verification history" in report.render(runs))

    heading("An after-run result is appended and tied to its run")
    feed("new.json", a_run(STARTED), "run")
    before = (HISTORY / "runs.jsonl").read_bytes()
    first_line_before = before.splitlines()[1]
    feed("after.json", after_record(STARTED), "after")
    after = (HISTORY / "runs.jsonl").read_bytes()
    check("the file only grew: everything that was there is byte for byte the same", after.startswith(before) and len(after) > len(before))
    check("the run's own line was not rewritten", after.splitlines()[1] == first_line_before)
    check("the new line says what it is", json.loads(after.splitlines()[-1]).get("kind") == "after_run")

    runs, warned = quiet_load()
    check("it is not counted as a run", len(runs) == 2, f"{len(runs)} runs")
    target = next(r for r in runs if r["started_at"] == STARTED)
    check("it is attached to the run it names", [r["check"] for r in target.get("after_results", [])] == ["LEAK"])
    check("and the run's own results are untouched", len(target["results"]) == 2)
    check("the other run has nothing attached", "after_results" not in next(r for r in runs if r["started_at"] == OLDER))
    check("nothing was warned about", warned == "", warned)

    heading("It shows as a row and in the per-check summary")
    rows = report.flatten(runs)
    leak_rows = [r for r in rows if r["check"] == "LEAK"]
    check("one row of its own, marked as after the cleanup", len(leak_rows) == 1 and leak_rows[0]["phase"] == "after")
    check("carrying the run's start time, so it sorts with that run", leak_rows[0]["at"] == STARTED)
    check("and its detail", leak_rows[0]["detail"] == "1 leftover tenant(s)")
    check("the suite's own rows are all marked as the suite's", all(r["phase"] == "suite" for r in rows if r["check"] != "LEAK"))
    leak = next(s for s in report.summarise(rows) if s["check"] == "LEAK")
    check("the summary has it, failing, with when it last failed", leak["latest"] == "fail" and leak["fails"] == 1 and leak["last_failed_at"] == STARTED, str(leak))
    page = report.render(runs)
    check("the page carries the card for how the last run's cleanup went", "After the last run" in page and "after_results" in page)

    heading("Records that do not belong are refused or skipped aloud")
    size = (HISTORY / "runs.jsonl").stat().st_size
    check("a result for a run that is not recorded is refused", refused(report.append_after_run, after_record("2020-01-01T00:00:00+00:00")))
    check("a record with no results is refused", refused(report.append_after_run, {"run_started_at": STARTED, "results": []}))
    check("a record with no run named is refused", refused(report.append_after_run, {"results": after_record(STARTED)["results"]}))
    check("and nothing was written by any of them", (HISTORY / "runs.jsonl").stat().st_size == size)

    with (HISTORY / "runs.jsonl").open("a", encoding="utf-8") as handle:
        stray = after_record("2021-01-01T00:00:00+00:00")
        stray["kind"] = "after_run"
        handle.write(json.dumps(stray) + "\n" + "{this is not json\n")
    runs, warned = quiet_load()
    check("a stray line naming a missing run is skipped, and said", "not in the history" in warned, warned)
    check("a corrupt line is skipped, and said", "unreadable history line" in warned, warned)
    check("and the rest of the history is intact", len(runs) == 2 and any(r.get("after_results") for r in runs))

    return summary("U125")


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(HISTORY, ignore_errors=True)
