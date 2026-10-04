"""Register the recurring sweep that ends pipeline runs which stopped without recording it.

    python -m worker.schedule_close_finished_runs --schedule-id close-stopped-pipeline-runs

Runs scripts/admin/close-finished-pipeline-runs.py's own `close_finished`, with apply always on. See
worker/housekeeping_activities.py for the activity and the script for what counts as stopped.

How often. Temporal keeps a finished workflow's history for the namespace's retention period (24 hours here:
`temporal operator namespace describe default`). A run closed after that has lost its real outcome and can only be
recorded as unknown. Once a day would reach a run with barely a minute to spare, and any delay would lose it, so the
default is every six hours. Pick an interval well inside the retention period if the retention is ever changed.

Kept to one script, one concern, same as worker/schedule_tidy_probes.py: this registers a schedule and nothing else.
Pausing, deleting or inspecting one afterwards is the `temporal schedule` CLI's job.
"""

from __future__ import annotations

import argparse
import asyncio

from temporalio.client import (Client, Schedule, ScheduleActionStartWorkflow,
                                ScheduleOverlapPolicy, SchedulePolicy, ScheduleSpec)

from . import config
from .housekeeping_workflow import CloseStoppedRunsWorkflow


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule-id", required=True,
                        help="a name for this schedule, e.g. close-stopped-pipeline-runs")
    parser.add_argument("--cron", default="17 */6 * * *",
                        help="standard 5-field cron, default: minute 17 of every sixth hour")
    args = parser.parse_args()

    client = await Client.connect(config.TEMPORAL)

    await client.create_schedule(
        args.schedule_id,
        Schedule(
            action=ScheduleActionStartWorkflow(
                CloseStoppedRunsWorkflow.run,
                # Temporal appends the scheduled time to this id itself, so every firing's id is unique.
                id=args.schedule_id,
                task_queue=config.HOUSEKEEPING_TASK_QUEUE,
            ),
            spec=ScheduleSpec(cron_expressions=[args.cron]),
            # A firing that finds the last one still going is skipped, never run beside it.
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
        ),
    )
    print(f"scheduled {args.schedule_id!r}: {args.cron}")
    print(f"  temporal schedule describe --schedule-id {args.schedule_id}")
    print(f"  temporal schedule trigger --schedule-id {args.schedule_id}     (run it now)")
    print(f"  temporal schedule delete --schedule-id {args.schedule_id}")


if __name__ == "__main__":
    asyncio.run(main())
