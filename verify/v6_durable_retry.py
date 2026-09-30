"""V6, the half slice 1 could not test: Temporal resumes a killed worker.

Slice 1 proved that an idempotency key keeps a retried action single. That is
the cheaper half. This is the claim that actually distinguishes a durable
workflow engine from a script with a try block: kill the worker mid-pipeline,
start it again, and the workflow continues from the activity that was running
rather than from the beginning.

Run by hand, because it involves killing a process:

  1. Start the worker:
         .venv\\Scripts\\python.exe -m worker.main
  2. Start a pipeline:
         .venv\\Scripts\\python.exe -m worker.run_pipeline --limit 4
  3. While transcription is running, kill the worker. Do not stop Temporal.
  4. Start the worker again and let the workflow finish.
  5. Run this script with the workflow id that run_pipeline printed.

What must be true afterwards, and what this checks:

  * the workflow completed, despite the worker having died
  * the history records the interruption, so the test exercised what it claims
  * exactly one dataset_version exists per step, because a second version would
    mean the retry duplicated data rather than resuming
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from temporalio.client import Client  # noqa: E402

from worker import config  # noqa: E402
from ports_config import PORTS  # noqa: E402

PG_DSN = f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform"

_results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    _results.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


async def main() -> int:
    if len(sys.argv) < 2:
        print("usage: v6_durable_retry.py <workflow-id>")
        print("the workflow id is printed by run_pipeline.py when it starts")
        return 2

    workflow_id = sys.argv[1]
    client = await Client.connect(config.TEMPORAL)
    handle = client.get_workflow_handle(workflow_id)
    description = await handle.describe()

    print("\nV6: durable retry across a worker restart")
    print("-" * 41)
    print(f"  workflow {workflow_id}")
    print(f"  status   {description.status.name}")

    check("the workflow reached a terminal state",
          description.status.name in ("COMPLETED", "FAILED", "TIMED_OUT"),
          description.status.name)
    check("the workflow completed rather than failed",
          description.status.name == "COMPLETED", description.status.name)

    # A restart shows up as an activity that ran again: Temporal retries an
    # interrupted activity itself and records no failed or timed-out event for
    # one that later succeeds, only a higher attempt number on the started
    # event, carrying the reason the earlier attempt was lost. No such attempt
    # means the worker was never actually killed, so a pass would be hollow.
    #
    # Matched by which attributes an event carries, not by str(event_type):
    # in this SDK version that is the enum's integer, so no name ever matches.
    scheduled: dict[int, str] = {}
    retried: list[str] = []
    completions = 0
    async for event in handle.fetch_history_events():
        if event.HasField("activity_task_scheduled_event_attributes"):
            attrs = event.activity_task_scheduled_event_attributes
            scheduled[event.event_id] = attrs.activity_type.name
        if event.HasField("activity_task_started_event_attributes"):
            started = event.activity_task_started_event_attributes
            if started.attempt > 1:
                why = started.last_failure.message if started.HasField("last_failure") else "no reason recorded"
                retried.append(f"{scheduled.get(started.scheduled_event_id, '?')} "
                               f"attempt {started.attempt} ({why})")
        if event.HasField("activity_task_completed_event_attributes"):
            completions += 1

    check("the history shows an interruption, so a restart really happened",
          len(retried) > 0,
          "; ".join(retried) if retried else "every activity ran once")
    print(f"  activities completed: {completions}")

    with psycopg.connect(PG_DSN, row_factory=dict_row, autocommit=True) as conn:
        # Only this workflow's own steps. Without the join, every pipeline run
        # the database has ever held is counted, and "exactly one per step"
        # fails as soon as a second run exists.
        versions = conn.execute(
            """select da.name, count(distinct dv.id) as n
               from dataset_version dv
               join action_run ar on ar.id = dv.produced_by_run
               join pipeline_run pr on pr.id = ar.pipeline_run_id
               join dataset_action da on da.id = ar.action_id
               where pr.workflow_id = %s
               group by da.name order by da.name""",
            (workflow_id,),
        ).fetchall()

    # A filter that matches nothing would leave the loop below with no checks
    # and the script reporting a clean pass, so the absence is its own check.
    check("the workflow produced at least one dataset version to count",
          len(versions) > 0, f"{len(versions)} step(s) found for {workflow_id}")

    print("\n  dataset versions per step")
    for row in versions:
        print(f"    {row['name']:<14} {row['n']}")

    # Per step, never in aggregate: a total that happens to match would hide a
    # step that ran twice alongside one that never ran at all.
    for row in versions:
        check(f"exactly one dataset version for {row['name']}",
              row["n"] == 1, f"{row['n']} versions")

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"\nV6 durable retry: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
