"""Register the recurring probe-tenant sweep.

    python -m worker.schedule_tidy_probes --schedule-id nightly-tidy-probes

Runs scripts/admin/tidy-probes.py's own `run_sweep`, with `--apply` always on
and a real age floor (default 24h), so a probe tenant a verify run is still
using cannot be caught mid-flight. See worker/housekeeping_activities.py and
scripts/admin/tidy-probes.py for what "probe tenant" means and the guards
around deleting one.

Kept to one script, one concern, same as worker/schedule_pipeline.py: this
registers a schedule and nothing else. Pausing, deleting or inspecting one
afterwards is the `temporal schedule` CLI's job, not this file's.
"""

from __future__ import annotations

import argparse
import asyncio

from temporalio.client import (Client, Schedule, ScheduleActionStartWorkflow,
                                ScheduleSpec)

from . import config
from .housekeeping_workflow import TidyProbesWorkflow


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule-id", required=True,
                        help="a name for this schedule, e.g. nightly-tidy-probes")
    parser.add_argument("--cron", default="30 3 * * *",
                        help="standard 5-field cron, default: 03:30 daily")
    parser.add_argument("--min-age-hours", type=float, default=24,
                        help="skip anything younger than this (default: 24)")
    args = parser.parse_args()

    client = await Client.connect(config.TEMPORAL)

    await client.create_schedule(
        args.schedule_id,
        Schedule(
            action=ScheduleActionStartWorkflow(
                TidyProbesWorkflow.run,
                args.min_age_hours,
                # {ScheduledStartTime} makes each firing's workflow id unique;
                # a fixed id would collide with the previous run the moment
                # two firings overlap in Temporal's retained history.
                id=f"{args.schedule_id}-{{ScheduledStartTime}}",
                task_queue=config.HOUSEKEEPING_TASK_QUEUE,
            ),
            spec=ScheduleSpec(cron_expressions=[args.cron]),
        ),
    )
    print(f"scheduled {args.schedule_id!r}: {args.cron}, min age {args.min_age_hours}h")
    print(f"  temporal schedule describe {args.schedule_id}")
    print(f"  temporal schedule delete --schedule-id {args.schedule_id}")


if __name__ == "__main__":
    asyncio.run(main())
