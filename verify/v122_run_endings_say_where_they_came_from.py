"""U122: every pipeline run's ending says where it came from, and the sweep that closes stopped runs is scheduled inside Temporal's memory.

A run's status says how it ended; `ended_source` says how that was found out, which is the difference between "failed" and "we do not know
how this ended". The database sets the rules, and the scheduled sweep that fills in the endings nobody recorded has to run often enough
that Temporal still remembers them. This checks, one item at a time:

  * a run cannot have an end time without a source, nor a source without an end time, nor a source outside the list of five;
  * the scheduled job's own function closes a stopped run, as unknown with the source "job runner had no record", and calling it again
    changes nothing;
  * a run that already has an end time is left exactly as it was by that function;
  * the schedule exists, is not paused, and fires at least twice inside the retention period Temporal reports for its namespace.

Not covered: the three other sources are asserted on live runs in U61 (the workflow's own ending, the job runner's account of a killed
run, and a run it never heard of). The "start failed" source has no automatic check, because nothing here can make the platform's attempt to start a
workflow fail. It runs on the host, where the worker package is importable and Temporal is reachable:

    .venv\\Scripts\\python.exe verify\\v122_run_endings_say_where_they_came_from.py
"""

from __future__ import annotations

import asyncio
import os
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

import psycopg  # noqa: E402
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest  # noqa: E402
from temporalio.client import Client  # noqa: E402

from common import CANARY, check, db, fixture_tenant, heading, summary  # noqa: E402
from worker import config  # noqa: E402
from worker.housekeeping_activities import close_stopped_pipeline_runs  # noqa: E402

SCHEDULE_ID = "close-stopped-pipeline-runs"


def make(tag: str, *, ended_days_ago: int | None = None, status: str = "running", source: str | None = None) -> str:
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    ended = now - timedelta(days=ended_days_ago) if ended_days_ago else None
    with db() as conn:
        conn.execute(
            "insert into pipeline_run (id, tenant_id, dataset, workflow_id, status, started_at, ended_at, ended_source) "
            "values (%s, %s, 'u122', %s, %s, %s, %s, %s)",
            (run_id, CANARY, f"u122-{tag}-{uuid.uuid4().hex[:10]}", status, now - timedelta(days=3), ended, source),
        )
    return run_id


def row(run_id: str) -> tuple:
    with db() as conn:
        r = conn.execute("select status, error, ended_at, ended_source from pipeline_run where id = %s", (run_id,)).fetchone()
    return (r["status"], r["error"], r["ended_at"], r["ended_source"])


def refused(sql: str, params: tuple) -> str | None:
    """The name of the constraint the database refused the statement with, or None if it was accepted (and undone)."""
    try:
        with db() as conn, conn.transaction():
            conn.execute(sql, params)
            raise _Undo()
    except _Undo:
        return None
    except psycopg.errors.CheckViolation as exc:
        return exc.diag.constraint_name or str(exc)


class _Undo(Exception):
    pass


async def schedule_facts() -> dict:
    client = await Client.connect(config.TEMPORAL)
    handle = client.get_schedule_handle(SCHEDULE_ID)
    desc = await handle.describe()
    times = [t for t in desc.info.next_action_times][:3]
    namespace = await client.workflow_service.describe_namespace(DescribeNamespaceRequest(namespace="default"))
    retention = namespace.config.workflow_execution_retention_ttl.ToTimedelta()
    return {"paused": desc.schedule.state.paused, "times": times, "retention": retention}


def main() -> int:
    fixture_tenant(CANARY)
    made: list[str] = []
    try:
        heading("The database refuses an ending that does not say where it came from")
        open_run = make("open")
        made.append(open_run)
        closed_run = make("closed", ended_days_ago=1, status="succeeded", source="workflow")
        made.append(closed_run)
        no_source = refused("update pipeline_run set status = 'failed', ended_at = now() where id = %s", (open_run,))
        check("an end time with no source is refused", no_source == "pipeline_run_ended_source_matches_end", str(no_source))
        check("a source with no end time is refused",
              refused("update pipeline_run set ended_source = 'workflow' where id = %s", (open_run,)) == "pipeline_run_ended_source_matches_end")
        check("a source outside the list is refused",
              refused("update pipeline_run set ended_source = 'a person said so' where id = %s", (closed_run,)) == "pipeline_run_ended_source_valid")
        for good in ("workflow", "job_runner", "job_runner_no_record", "start_failed", "not_recorded"):
            check(f"the source {good} is accepted", refused("update pipeline_run set ended_source = %s where id = %s", (good, closed_run)) is None)

        heading("The scheduled job's own function closes a stopped run once")
        stopped = make("stopped")
        already = make("already", ended_days_ago=2, status="unknown", source="job_runner_no_record")
        made += [stopped, already]
        before_already = row(already)
        first = close_stopped_pipeline_runs()
        check("it ran and counted what it closed", first["closed"] >= 1, str(first))
        after = row(stopped)
        check("the stopped run has an end time", after[2] is not None)
        check("it ended as unknown, because Temporal has no record of the made-up workflow", after[0] == "unknown", str(after[0]))
        check("and says the job runner had no record", after[3] == "job_runner_no_record", str(after[3]))
        check("the run that already had an end time is exactly as it was", row(already) == before_already, f"{row(already)} vs {before_already}")
        second = close_stopped_pipeline_runs()
        check("calling it again closes nothing", second["closed"] == 0 and second["stopped"] == 0, str(second))
        check("and the run it closed is unchanged", row(stopped) == after)
        check("a run that succeeded is left alone as well", row(closed_run)[2] is not None)

        heading("The schedule runs inside what Temporal remembers")
        try:
            facts = asyncio.run(schedule_facts())
        except Exception as exc:  # noqa: BLE001 - reported as a failed check, not a crash
            check("the schedule can be read from Temporal", False, f"{type(exc).__name__}: {exc}")
        else:
            check("the schedule exists and is not paused", facts["paused"] is False, str(facts["paused"]))
            gap = (facts["times"][1] - facts["times"][0]) if len(facts["times"]) > 1 else None
            check("it has upcoming firings", gap is not None, str(facts["times"]))
            if gap is not None:
                check("it fires at least twice inside the retention period, so a run is seen before Temporal forgets it",
                      gap * 2 <= facts["retention"], f"every {gap}, retention {facts['retention']}")
    finally:
        with db() as conn:
            conn.execute("delete from pipeline_run where id = any(%s)", (made,))
    return summary("U122")


if __name__ == "__main__":
    sys.exit(main())
