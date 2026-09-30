"""Carrying an approved run past its approval gate, as a background job.

Separate from `agent_run_workflow.py` for the reason those two are separate
things: the first invocation and the resume are started by different people at
different times, hours or days apart, and a workflow that did both would have
to be told which half it was doing. The graph's checkpoint is what joins them,
not a long-lived workflow holding a process open while a human decides.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .agent_run_activities import fail_run, resume_run

# A step is retried only when its hand-over or its "done" was lost, which is
# noticed by its heartbeats stopping. A real failure of the work is raised as
# non-retryable, and a retry never repeats work already begun: it returns the
# recorded outcome, or fails the run (worker/agent_attempt.py).
AGENT_WORK_RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5))
HEARTBEAT_TIMEOUT = timedelta(seconds=60)


@workflow.defn
class AgentRunResumeWorkflow:
    """Resume the checkpointed run, and record where it ends up."""

    @workflow.run
    async def run(self, params: dict) -> dict:
        run_id = params["run_id"]
        try:
            return await workflow.execute_activity(
                resume_run,
                params,
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=AGENT_WORK_RETRY,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
            )
        except Exception as exc:
            await workflow.execute_activity(
                fail_run,
                {"run_id": run_id, "error": str(exc)},
                start_to_close_timeout=timedelta(seconds=15),
            )
            raise
