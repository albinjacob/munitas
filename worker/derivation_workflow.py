"""The workflow and activities behind a confirmed derivation.

The query itself runs in worker/derivation_sandbox.py, on the worker that has
the Docker socket (config.SANDBOX_TASK_QUEUE). Sealing the result and recording
how the run ended happen here, on the host worker, with the same client every
other pipeline step uses to seal a version.

A failure that retrying cannot fix (the query is wrong, the result does not fit
what was confirmed) is reported to the person with its reason. One that it can
(Docker or storage not answering yet) is retried a few times first.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from . import config
    from . import platform_client as cp

CONTROL = RetryPolicy(initial_interval=timedelta(seconds=1),
                      maximum_interval=timedelta(seconds=30), maximum_attempts=5)
RUN = RetryPolicy(initial_interval=timedelta(seconds=10), maximum_interval=timedelta(minutes=1),
                  maximum_attempts=4, non_retryable_error_types=["DerivationFailed"])


def _headers() -> dict:
    return {"x-worker-token": config.WORKER_TOKEN}


@activity.defn
def seal_derivation(params: dict) -> dict:
    """Seal the result as a version and tell the platform, which checks it.

    Safe to run twice: if the run already produced a version (the activity
    sealed it and then lost its connection before reporting), that version is
    reported again and a second one is not made.
    """
    import httpx

    from .db import _db

    with _db() as conn:
        row = conn.execute("select output_version from action_run where id = %s",
                           (params["action_run_id"],)).fetchone()
    version_id = str(row[0]) if row and row[0] else None
    if not version_id:
        manifest = [{"key": params["records_key"], "bytes": params["bytes"], "sha256": params["sha256"]}]
        sealed = cp.seal_version(
            params["dataset_id"], params["schema_id"], params["output_class"], manifest,
            params["rows"], params["action_run_id"], tenant_id=params["tenant_id"],
            records_key=params["records_key"])
        version_id = sealed["id"]
    done = httpx.post(f"{config.API}/derivations/{params['derivation_id']}/complete",
                      json={"output_version_id": version_id}, headers=_headers(),
                      timeout=30.0, verify=config.api_verify())
    done.raise_for_status()
    return {"output_version_id": version_id}


@activity.defn
def fail_derivation(params: dict) -> None:
    import httpx

    httpx.post(f"{config.API}/derivations/{params['derivation_id']}/fail",
               json={"reason": params["reason"]}, headers=_headers(),
               timeout=30.0, verify=config.api_verify()).raise_for_status()


def _reason(exc: BaseException) -> str:
    """What to tell the person. Only a failure the sandbox declared safe carries
    its own words; anything else is reported by kind, because an unexpected
    error's message could quote data."""
    cause = getattr(exc, "cause", None)
    if isinstance(cause, ApplicationError) and cause.type == "DerivationFailed":
        return cause.message
    return "the run stopped because of a platform error. Nothing was sealed"


@workflow.defn(name="DerivationWorkflow")
class DerivationWorkflow:
    @workflow.run
    async def run(self, params: dict) -> dict:
        try:
            ran = await workflow.execute_activity(
                "run_derivation_sandboxed", params, task_queue=config.SANDBOX_TASK_QUEUE,
                start_to_close_timeout=timedelta(minutes=30),
                heartbeat_timeout=timedelta(seconds=90), retry_policy=RUN)
            sealed = await workflow.execute_activity(
                seal_derivation, {**params, **ran}, start_to_close_timeout=timedelta(minutes=5),
                retry_policy=CONTROL)
            return {**ran, **sealed}
        except ActivityError as exc:
            await workflow.execute_activity(
                fail_derivation, {**params, "reason": _reason(exc)},
                start_to_close_timeout=timedelta(minutes=1), retry_policy=CONTROL)
            raise
