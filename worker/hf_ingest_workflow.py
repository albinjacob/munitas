"""Fetching a HuggingFace dataset repo, as a background job.

The API starts this and returns immediately; the console polls
`huggingface_fetch_job` for progress instead of holding a request open.
Everything that can fail (a repo that does not exist, one that is gated, a
file too large, a dropped connection mid-download) fails as an activity
here, in a process separate from the one serving every other request, so
none of it can take the API down with it the way the old inline version
did.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from .hf_ingest_activities import (cancel_job, fail_job, fetch_one_file,
                                       finalize_job, prepare_fetch)

# Metadata calls: quick, worth retrying hard. Same shape as CONTROL in
# workflows.py.
CONTROL = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)

# File transfers: slower, and a failure partway through a large file is
# worth another attempt, but not five of them run back to back.
DOWNLOAD = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=4,
)


@workflow.defn
class HuggingFaceFetchWorkflow:
    """List a repo, fetch what is missing, fold the licence into the dataset."""

    @workflow.run
    async def run(self, params: dict) -> dict:
        job_id = params["job_id"]

        try:
            prepared = await workflow.execute_activity(
                prepare_fetch,
                params,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=CONTROL,
            )

            # Bounded by the worker's own `max_concurrent_activities` on this
            # task queue, not by anything here: gathering every file at once
            # just means Temporal queues the excess rather than the workflow
            # having to reimplement a semaphore.
            results = await asyncio.gather(*[
                workflow.execute_activity(
                    fetch_one_file,
                    {
                        "repo_id": params["repo_id"],
                        "revision": params["revision"],
                        "entry_path": entry["path"],
                        "expected_size": entry["size"],
                        "dataset_id": params["dataset_id"],
                        "tenant_id": params["tenant_id"],
                        "fetched_by": params["fetched_by"],
                        "job_id": job_id,
                        "storage_prefix": prepared["storage_prefix"],
                        "bucket": prepared["bucket"],
                        "license_tag": prepared["license_tag"],
                        "task_credential": params["task_credential"],
                    },
                    start_to_close_timeout=timedelta(minutes=20),
                    heartbeat_timeout=timedelta(minutes=3),
                    retry_policy=DOWNLOAD,
                )
                for entry in prepared["entries"]
            ])

            await workflow.execute_activity(
                finalize_job,
                {
                    "job_id": job_id,
                    "dataset_id": params["dataset_id"],
                    "license_tag": prepared["license_tag"],
                    "license_export_unmodified": prepared["license_export_unmodified"],
                    "license_export_modified": prepared["license_export_modified"],
                    "files_total": prepared["files_total"],
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=CONTROL,
            )

            return {
                "job_id": job_id,
                "files_fetched": len(results),
                "already_had": prepared["already_had"],
            }
        except (Exception, asyncio.CancelledError) as exc:
            # A cancelled `fetch_one_file` reaches here as `ActivityError`
            # with an `is_cancelled_exception` cause, not as a bare
            # `asyncio.CancelledError`: Temporal resolves a cancelled
            # activity's own awaitable with that wrapper rather than
            # raising cancellation directly on the workflow's task, since
            # several were running at once under `asyncio.gather`.
            # `is_cancelled_exception` is what the SDK itself recommends for
            # telling the two apart, rather than matching exception types
            # by hand.
            #
            # A new activity call here still goes through: the cancellation
            # already delivered to the awaitables above does not carry
            # forward to ones started after it was caught. Files that had
            # already landed stay recorded in dataset_source (fetch_one_file
            # writes that row itself, as each file finishes), so a later
            # fetch of the same repo picks up from there rather than from
            # scratch.
            if is_cancelled_exception(exc):
                await workflow.execute_activity(
                    cancel_job,
                    {"job_id": job_id},
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=CONTROL,
                )
            else:
                await workflow.execute_activity(
                    fail_job,
                    {"job_id": job_id, "error": _reason(exc)},
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=CONTROL,
                )
            raise


def _reason(exc: BaseException) -> str:
    """The message worth showing, not Temporal's own wrapper around it.

    An activity failure reaches here as a chain of wrapper exceptions:
    `ActivityError` (message always the useless "Activity task failed")
    around an `ApplicationError` (whatever `_terminal()` or a bare `raise`
    actually said) around, sometimes, the original library exception that
    caused it. Stop at the first `ApplicationError` and use its own
    message, rather than walking past it into that root cause, which for a
    HuggingFace client error is a Request ID and a URL, not a sentence
    anyone reading a job's `error` column wants to see.
    """
    from temporalio.exceptions import ApplicationError

    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, ApplicationError):
            return current.message
        current = current.__cause__
    return str(exc) or "the fetch failed for an unrecorded reason"
