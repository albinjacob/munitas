"""Register a recurring pipeline run.

    python -m worker.schedule_pipeline --schedule-id nightly-deid --cron "0 2 * * *"

Every run this creates is tagged `trigger_kind='scheduled'`, so it shows up in
`action_run` and `lineage` as what it is, distinct from a data engineer's
manual invocation through `run_pipeline.py`. `health-pipeline`'s role floor is 0,
so the pipeline actions themselves (ingest, transcribe, detect, handoff,
redact) need no lease and no human to interrupt for approval, exactly as a
manual run does not either. Anything downstream of this schedule that reads
below its own floor -- a training step run by a different workload, say -- has
to hold a standing lease approved ahead of time, because a scheduled run has
nobody to ask.

Kept to one script, one concern, the same way `run_pipeline.py` starts a run
and `scripts/admin/reclaim-storage.py` reclaims: this registers a schedule and nothing else.
Pausing, deleting or inspecting one afterwards is the `temporal schedule` CLI's
job, not this file's.
"""

from __future__ import annotations

import argparse
import asyncio

from temporalio.client import (Client, Schedule, ScheduleActionStartWorkflow,
                                ScheduleSpec)

from . import config
from .run_pipeline import ensure_actions, ensure_tenant
from .workflows import DeidentificationPipeline


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule-id", required=True,
                        help="a name for this schedule, e.g. nightly-deid")
    parser.add_argument("--cron", default="0 2 * * *",
                        help="standard 5-field cron, default: 02:00 daily")
    parser.add_argument("--dataset", default="encounters")
    parser.add_argument("--limit", type=int, default=0,
                        help="records per firing; 0 means the full corpus")
    parser.add_argument("--recall-threshold", type=float, default=0.95)
    parser.add_argument("--confidence-threshold", type=float, default=0.4)
    parser.add_argument("--to-class", default="OPEN_FOR_ANNOTATION")
    args = parser.parse_args()
    corpus = config.require_corpus_dir()

    ensure_tenant()
    actions = ensure_actions()
    print(f"dataset actions: {actions}")

    client = await Client.connect(config.TEMPORAL)

    await client.create_schedule(
        args.schedule_id,
        Schedule(
            action=ScheduleActionStartWorkflow(
                DeidentificationPipeline.run,
                {
                    "corpus": str(corpus),
                    "limit": args.limit,
                    "dataset": args.dataset,
                    "actions": actions,
                    # Frozen into the schedule at the moment it is created, the
                    # same way the corpus path and the action ids are. A
                    # recurring run has one tenant and it is chosen here.
                    "tenant": config.TENANT,
                    "recall_threshold": args.recall_threshold,
                    "confidence_threshold": args.confidence_threshold,
                    "to_class": args.to_class,
                    "trigger_kind": "scheduled",
                    "triggered_by": None,
                    "schedule_id": args.schedule_id,
                },
                # {ScheduledStartTime} makes each firing's workflow id unique;
                # a fixed id would collide with the previous run the moment
                # two firings overlap in Temporal's retained history.
                id=f"{args.schedule_id}-{{ScheduledStartTime}}",
                task_queue=config.TASK_QUEUE,
            ),
            spec=ScheduleSpec(cron_expressions=[args.cron]),
        ),
    )
    print(f"scheduled {args.schedule_id!r}: {args.cron}")
    print(f"  temporal schedule describe {args.schedule_id}")
    print(f"  temporal schedule delete --schedule-id {args.schedule_id}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except config.RefusedPath as exc:
        raise SystemExit(str(exc))
