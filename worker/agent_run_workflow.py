"""Invoking an agent, as a background job.

The API starts this and returns immediately; nothing polls a job table for
progress the way `huggingface_fetch_job` is polled, because a triage run is
short enough that the console can just poll `GET /agents/runs/{id}` on the
same interval pattern `useHuggingFaceFetchJobs` already uses.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from . import config
    from .agent_run_activities import fail_run, run_agent
    from .sandbox_run_activities import run_sandboxed_agent

# A step is retried only when its hand-over or its "done" was lost, which is
# noticed by its heartbeats stopping. A real failure of the work is raised as
# non-retryable, and a retry never repeats work already begun: it returns the
# recorded outcome, or fails the run (worker/agent_attempt.py).
AGENT_WORK_RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5))
HEARTBEAT_TIMEOUT = timedelta(seconds=60)

# The sandboxed path gets more headroom than the native one: it pulls a base
# image on first use, installs a project's own dependencies in a build
# container, then runs a second container: three steps the in-process
# native path never pays for.
NATIVE_TIMEOUT = timedelta(minutes=10)
SANDBOXED_TIMEOUT = timedelta(minutes=15)


@workflow.defn
class AgentRunWorkflow:
    """Build the identity, execute the agent once, record the outcome.

    Branches here, not inside a shared activity, between the platform's own
    built-in graph and an uploaded agent's sandboxed code. `execution_mode`
    is pinned once, at insert, by `start_run`/`_start_waiting_on_access`
    (`platform/api/app/agents.py`), the same discipline `dataset_version_id`
    already follows. Branching at this level, rather than inside one
    activity that decides internally, means each path gets its own
    Temporal timeout and its own row in Temporal's own history, so which
    path a run took is never a fact you have to go dig for.
    """

    @workflow.run
    async def run(self, params: dict) -> dict:
        run_id = params["run_id"]
        sandboxed = params.get("execution_mode") == "sandboxed"
        activity = run_sandboxed_agent if sandboxed else run_agent
        timeout = SANDBOXED_TIMEOUT if sandboxed else NATIVE_TIMEOUT
        # The sandboxed activity runs on its own task queue, served by the
        # worker inside the WSL2 distro that can actually reach Docker
        # (worker/sandbox_worker.py). The native path, and this workflow
        # itself, stay on whichever queue started the run. Building the kwarg
        # conditionally rather than passing task_queue=None keeps the native
        # call on its own queue, which is what an omitted task_queue means.
        activity_kwargs: dict = {
            "start_to_close_timeout": timeout,
            "retry_policy": AGENT_WORK_RETRY,
            "heartbeat_timeout": HEARTBEAT_TIMEOUT,
        }
        if sandboxed:
            activity_kwargs["task_queue"] = config.SANDBOX_TASK_QUEUE
        try:
            return await workflow.execute_activity(
                activity,
                params,
                **activity_kwargs,
            )
        except Exception as exc:
            await workflow.execute_activity(
                fail_run,
                {"run_id": run_id, "error": str(exc)},
                start_to_close_timeout=timedelta(seconds=15),
            )
            raise
