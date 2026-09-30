"""Recurring housekeeping: currently just the stale-probe-tenant sweep.

One workflow, one activity, on its own task queue (worker/config.py's
HOUSEKEEPING_TASK_QUEUE). worker/schedule_tidy_probes.py registers the
Temporal schedule that fires this.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .housekeeping_activities import sweep_stale_probes

# Postgres and S3 calls against a handful of rows: quick, and safe to retry
# on a transient failure the way any other control-plane call is (see
# worker/workflows.py's own CONTROL policy, which this mirrors).
CONTROL = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)


@workflow.defn
class TidyProbesWorkflow:
    """Sweep probe tenants older than `min_age_hours`."""

    @workflow.run
    async def run(self, min_age_hours: float) -> dict:
        return await workflow.execute_activity(
            sweep_stale_probes,
            min_age_hours,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=CONTROL,
        )
