"""U121: closing the pipeline runs that stopped closes each once, and running it again changes nothing.

`scripts/admin/close-finished-pipeline-runs.py` used to pick up every run with no end time and also every run marked `unknown`. When
Temporal had forgotten a run, the only thing it could write was `unknown`, so that run was picked up again every time and the report
said it was open when it was not. A run is open when it has no end time, and only then. This checks, one item at a time, with runs made
up for the purpose that Temporal has no record of:

  * a preview lists the open run and changes nothing;
  * applying closes the open run, with an end time, how it ended (unknown, because Temporal has no record) and where that came from;
  * a run that was already closed as unknown is not listed and is not changed;
  * a run that already succeeded is not listed and is not changed;
  * applying again lists nothing, closes nothing, and changes none of the three rows.

Not covered here: a run Temporal reports as still running is left alone (that needs a live workflow).
It runs on the host, because it drives a script that lives beside the Compose file:

    .venv\\Scripts\\python.exe verify\\v121_closing_runs_twice_changes_nothing.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verify"))
sys.path.insert(0, str(ROOT))

if "PG_DSN" not in os.environ:
    from ports_config import PORTS
    os.environ["PG_DSN"] = f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform"

from common import CANARY, check, db, fixture_tenant, heading, summary  # noqa: E402

SCRIPT = ROOT / "scripts" / "admin" / "close-finished-pipeline-runs.py"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(PYTHON), str(SCRIPT), *args], capture_output=True, text=True, cwd=str(ROOT), timeout=180)


def make(tag: str, status: str, ended_days_ago: int | None, error: str | None, source: str | None = None) -> tuple[str, str]:
    run_id, workflow = str(uuid.uuid4()), f"u121-{tag}-{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)
    ended = now - timedelta(days=ended_days_ago) if ended_days_ago else None
    with db() as conn:
        conn.execute(
            "insert into pipeline_run (id, tenant_id, dataset, workflow_id, status, error, started_at, ended_at, ended_source) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (run_id, CANARY, "u121", workflow, status, error, now - timedelta(days=3), ended, source),
        )
    return run_id, workflow


def row(run_id: str) -> tuple:
    with db() as conn:
        r = conn.execute("select status, error, ended_at, ended_source from pipeline_run where id = %s", (run_id,)).fetchone()
    return (r["status"], r["error"], r["ended_at"], r["ended_source"])


def main() -> int:
    fixture_tenant(CANARY)
    open_id, open_wf = make("open", "running", None, None)
    unknown_id, unknown_wf = make("unknown", "unknown", 2, "the job runner has no record of this run", "job_runner_no_record")
    done_id, done_wf = make("done", "succeeded", 2, None, "workflow")
    mine = [open_id, unknown_id, done_id]
    try:
        before_unknown, before_done = row(unknown_id), row(done_id)
        check("the closed runs have the end times they were made with", before_unknown[2] is not None and before_done[2] is not None)

        heading("A preview lists the open run and changes nothing")
        preview = run_script()
        check("the preview ran", preview.returncode == 0, preview.stderr[-200:])
        check("it lists the open run", open_wf in preview.stdout)
        check("it does not list the run already closed as unknown", unknown_wf not in preview.stdout)
        check("it does not list the run that succeeded", done_wf not in preview.stdout)
        check("the open run is still open", row(open_id)[2] is None)

        heading("Applying closes the open run once")
        first = run_script("--apply")
        check("the apply ran", first.returncode == 0, first.stderr[-200:])
        closed = row(open_id)
        check("the open run now has an end time", closed[2] is not None)
        check("and says it ended as unknown, because Temporal has no record", closed[0] == "unknown", str(closed[0]))
        check("and says why", bool(closed[1]) and "no record" in closed[1], str(closed[1]))
        check("and records where that came from: the job runner had no record", closed[3] == "job_runner_no_record", str(closed[3]))
        check("the run already closed as unknown is exactly as it was", row(unknown_id) == before_unknown, f"{row(unknown_id)} vs {before_unknown}")
        check("the run that succeeded is exactly as it was", row(done_id) == before_done, f"{row(done_id)} vs {before_done}")

        heading("Applying again changes nothing")
        second = run_script("--apply")
        check("the second apply ran", second.returncode == 0, second.stderr[-200:])
        check("it lists none of the three runs", not any(w in second.stdout for w in (open_wf, unknown_wf, done_wf)))
        check("it says nothing was left to close", "Closed 0" in second.stdout, second.stdout.strip().splitlines()[-1] if second.stdout.strip() else "")
        check("the run closed the first time is unchanged", row(open_id) == closed)
        check("the other two are still unchanged", row(unknown_id) == before_unknown and row(done_id) == before_done)
    finally:
        with db() as conn:
            conn.execute("delete from pipeline_run where id = any(%s)", (mine,))
    return summary("U121")


if __name__ == "__main__":
    sys.exit(main())
