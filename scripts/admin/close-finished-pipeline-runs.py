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
closed.

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
    """(ended_at, status, error), with ended_at None when it is running."""
    try:
        d = await client.get_workflow_handle(workflow_id).describe(
            rpc_timeout=timedelta(seconds=10))
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return (datetime.now(timezone.utc), "unknown",
                    "the job runner has no record of this run")
        raise
    if d.status in (None, WorkflowExecutionStatus.RUNNING):
        return None, "running", None
    name = d.status.name
    status = ENDED_AS.get(name, "unknown")
    error = None if status == "succeeded" else f"the job runner reports it {name.lower()}"
    return d.close_time or datetime.now(timezone.utc), status, error


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

    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn:
        runs = conn.execute(
            "select id, tenant_id, workflow_id, started_at from pipeline_run "
            "where ended_at is null order by started_at"
        ).fetchall()
        closing = []
        for r in runs:
            when, status, error = await ending(client, r["workflow_id"])
            mark = "close" if when else "leave"
            print(f"  {mark:5}  {r['tenant_id']:<22} {r['workflow_id']:<32} {status}")
            if when:
                closing.append((when, status, error, r["id"]))

        print(f"\n{len(runs)} open, {len(closing)} stopped according to Temporal, "
              f"{len(runs) - len(closing)} still running.")
        if not args.apply:
            print("Dry run. Nothing changed. Pass --apply to close the stopped ones.")
            return 0
        for when, status, error, run_id in closing:
            conn.execute(
                """update pipeline_run
                      set status = %s, error = %s, ended_at = coalesce(ended_at, %s)
                    where id = %s and ended_at is null""",
                (status, error, when, run_id),
            )
        conn.commit()
        left = conn.execute(
            "select count(*) as n from pipeline_run where ended_at is null"
        ).fetchone()["n"]
    print(f"Closed {len(closing)}. Open now: {left}.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
