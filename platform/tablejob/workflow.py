"""The workflow of one table job. It has one step, which the worker's activity carries out.

A failure that retrying cannot fix is not an error here: the activity reports it to the control plane, which refuses the
seal or seals without a table as the caller asked. What the workflow retries is what might work the second time: storage
or the control plane not answering, the worker restarted halfway through. Whatever a failed attempt wrote is removed by the
attempt itself, so the next one starts clean.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

MAX_ATTEMPTS = 3
RETRY = RetryPolicy(initial_interval=timedelta(seconds=10), maximum_interval=timedelta(minutes=1), maximum_attempts=MAX_ATTEMPTS,
                    non_retryable_error_types=["WrongOrganisation", "JobEnded"])


REPORT = RetryPolicy(initial_interval=timedelta(seconds=2), maximum_interval=timedelta(seconds=30), maximum_attempts=5)


@workflow.defn(name="TableWriteWorkflow")
class TableWriteWorkflow:
    @workflow.run
    async def run(self, params: dict) -> dict:
        try:
            return await workflow.execute_activity(
                "write_table_job", params, start_to_close_timeout=timedelta(hours=6),
                heartbeat_timeout=timedelta(seconds=90), retry_policy=RETRY)
        except ActivityError as exc:
            cause = getattr(exc, "cause", None)
            # A worker that was given a job of an organisation it does not serve must not end that job: the job is the
            # other organisation's, and it waits for its own worker. Anything else that stopped the work ends the job, so
            # its version number and key are given back.
            if isinstance(cause, ApplicationError) and cause.type in ("WrongOrganisation", "JobEnded"):
                raise
            await workflow.execute_activity(
                "report_table_job_failure",
                {**params, "reason": "Writing the table did not work, and trying again did not help. Nothing was sealed."},
                start_to_close_timeout=timedelta(minutes=2), retry_policy=REPORT)
            raise
