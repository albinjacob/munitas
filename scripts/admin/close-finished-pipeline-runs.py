"""End, with how they ended, pipeline runs that Temporal says have stopped but the register still
shows as running.

A pipeline workflow ends its own run record as it finishes, succeeded or
failed. Two kinds of ending cannot do that: a workflow terminated from outside,
and one whose worker died. Their records stay open, and an open record stops
the console starting that version again. The console already asks Temporal
before refusing, one version at a time; this does the same for every open
record at once, so the register is right before anyone asks.

A record is open when it has no end time, and only then is it looked at. A run that has an end time is
finished, even when how it ended could not be found out (status unknown, because Temporal had already
forgotten it), and it is never looked at again, so running this twice in a row changes nothing the second time.

Only Temporal's answer closes a record. A run Temporal reports as still
running is left alone, and so is every run when Temporal cannot be reached:
not knowing is not evidence that a run stopped. A workflow Temporal has no
record of (history past retention, or never started) is not running, so it is
closed, as `unknown`, with the source `job_runner_no_record`. Every ending is
written with where it came from (`ended_source`).

Temporal keeps a finished workflow's history for the namespace's retention period (24 hours here). A run
closed after that has lost its real outcome, so this is meant to run often, on a schedule well inside
that window: `worker/schedule_close_finished_runs.py` registers it, and `worker/housekeeping_activities.py`
calls `close_finished` below, so the command line and the schedule do exactly the same thing.

    .venv\\Scripts\\python.exe scripts/admin/close-finished-pipeline-runs.py           # dry run
    .venv\\Scripts\\python.exe scripts/admin/close-finished-pipeline-runs.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ports_config import PORTS  # noqa: E402

PG_DSN = os.environ.get("PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform")
TEMPORAL = os.environ.get("TEMPORAL_ADDRESS", f"localhost:{PORTS['temporal']}")

# Temporal's words for how a workflow ended, in the database's words. The
# same table as platform/api/app/pipeline.py's _ENDED_AS.
ENDED_AS = {
    "COMPLETED": "succeeded",
    "FAILED": "failed",
    "CANCELED": "cancelled",
    "TERMINATED": "terminated",
    "TIMED_OUT": "timed_out",
}


async def ending(client: Client, workflow_id: str):
    """(ended_at, status, error, source), with ended_at None when it is running."""
    try:
        d = await client.get_workflow_handle(workflow_id).describe(
            rpc_timeout=timedelta(seconds=10))
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return (datetime.now(timezone.utc), "unknown",
                    "the job runner has no record of this run", "job_runner_no_record")
        raise
    if d.status in (None, WorkflowExecutionStatus.RUNNING):
        return None, "running", None, None
    name = d.status.name
    status = ENDED_AS.get(name, "unknown")
    error = None if status == "succeeded" else f"the job runner reports it {name.lower()}"
    return d.close_time or datetime.now(timezone.utc), status, error, "job_runner"


def open_runs(conn) -> list[dict]:
    return conn.execute(
        "select id, tenant_id, workflow_id, started_at from pipeline_run "
        "where ended_at is null order by started_at"
    ).fetchall()


def close_runs(conn, closing: list[tuple]) -> tuple[int, list[str]]:
    """Write each ending in a transaction of its own, so a row the database refuses (a retired organisation's, say)
    is reported and the rest still close. Returns how many closed and the workflow ids that were refused."""
    closed, refused = 0, []
    for when, status, error, source, run_id, workflow_id in closing:
        try:
            with conn.transaction():
                cur = conn.execute(
                    """update pipeline_run
                          set status = %s, error = %s, ended_at = coalesce(ended_at, %s), ended_source = coalesce(ended_source, %s)
                        where id = %s and ended_at is null""",
                    (status, error, when, source, run_id),
                )
                closed += cur.rowcount
        except psycopg.Error as exc:
            refused.append(f"{workflow_id}: {str(exc).splitlines()[0]}")
    return closed, refused


async def close_finished(client: Client, conn, apply: bool, report=None) -> dict:
    """Look at every open run, ask Temporal about each, and close the ones that have stopped when `apply`.

    The database calls are few and small, so they are made directly; the scheduled activity is the one caller
    that runs this in an event loop of its own thread, where that is harmless."""
    runs = open_runs(conn)
    closing, still_running = [], 0
    for r in runs:
        when, status, error, source = await ending(client, r["workflow_id"])
        if when:
            closing.append((when, status, error, source, r["id"], r["workflow_id"]))
        else:
            still_running += 1
        if report:
            report(r, status, source)
    result = {"open": len(runs), "stopped": len(closing), "running": still_running, "closed": 0, "refused": [],
              "by_source": {}}
    for _, _, _, source, _, _ in closing:
        result["by_source"][source] = result["by_source"].get(source, 0) + 1
    if apply and closing:
        result["closed"], result["refused"] = close_runs(conn, closing)
    conn.commit()
    return result


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="close them; without this, only report")
    args = parser.parse_args()

    try:
        client = await asyncio.wait_for(Client.connect(TEMPORAL), timeout=10)
    except Exception as exc:
        print(f"Temporal is not reachable at {TEMPORAL} ({exc}). Nothing was changed: "
              "without Temporal's answer no run can be shown to have stopped.")
        return 1

    def line(r, status, source):
        mark = "close" if source else "leave"
        print(f"  {mark:5}  {r['tenant_id']:<22} {r['workflow_id']:<32} {status}" + (f" ({source})" if source else ""))

    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn:
        result = await close_finished(client, conn, args.apply, report=line)

    print(f"\n{result['open']} open, {result['stopped']} stopped according to Temporal, {result['running']} still running.")
    if not args.apply:
        print("Dry run. Nothing changed. Pass --apply to close the stopped ones.")
        return 0
    print(f"Closed {result['closed']}. Open now: {result['open'] - result['closed']}.")
    for refused in result["refused"]:
        print(f"  refused by the database, left open: {refused}")
    return 1 if result["refused"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
