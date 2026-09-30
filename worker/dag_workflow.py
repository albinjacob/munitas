"""The generic workflow behind every operator-registered pipeline.

One class for every DAG ever registered, not one generated class per
pipeline: the DAG's shape lives entirely in params['dag_config'], read at
run time. One interpreter keeps every pipeline's behaviour in one place,
where generating or hand-writing a class per pipeline would let them drift.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from . import config as worker_config
    from .activities import (adopt_version, check_score_store, close_pipeline_run,
                             describe_failure,
                             detect, handoff,
                             open_pipeline_run, redact,
                             record_simple_gate_decision, resolve_actions,
                             transcribe, verify)
    from .dag_activities import (close_step_run, open_step_run,
                                 run_dag_step_sandboxed)

BUILTIN_ACTIVITY = {
    "adopt_version": adopt_version, "transcribe": transcribe,
    "detect": detect, "handoff": handoff, "redact": redact, "verify": verify,
}

# adopt_version does not open an action_run -- reading an already-sealed
# version transforms nothing and seals nothing (see its own docstring in
# activities.py), so unlike every other builtin block it needs no
# dataset_action registered for it. Kept as its own set rather than
# inferred, so a future block that also skips action_run stays a one-line,
# explicit choice instead of a special case buried in the dispatch code.
BLOCKS_WITHOUT_ACTION = {"adopt_version"}

REF_PATTERN = re.compile(r"^\$\{steps\.([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\}$")

CONTROL = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)


def _idem(*parts: str) -> str:
    """Same derivation DeidentificationPipeline already uses, so a replayed
    DAG workflow finds its own earlier work instead of starting it twice."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def _resolve(value, results: dict):
    if isinstance(value, str):
        m = REF_PATTERN.match(value)
        if m:
            return results.get(m.group(1), {}).get(m.group(2))
    return value


def _resolve_inputs(inputs: dict | None, results: dict) -> dict:
    return {k: _resolve(v, results) for k, v in (inputs or {}).items()}


@workflow.defn(name="PipelineDagWorkflow")
class PipelineDagWorkflow:
    _pipeline_run_id: str | None = None

    @workflow.run
    async def run(self, params: dict) -> dict:
        # The run record is ended here whether the steps succeed or fail, and
        # with how it ended: the database keeps a run's outcome, not only its
        # end time. A run terminated from outside never reaches this; the API
        # records those from the job runner's answer instead.
        status, error = "failed", None
        try:
            result = await self._run(params)
            status = "succeeded"
            return result
        except asyncio.CancelledError:
            status, error = "cancelled", "the run was cancelled"
            raise
        except BaseException as exc:
            error = describe_failure(exc)
            raise
        finally:
            if self._pipeline_run_id:
                await workflow.execute_activity(
                    close_pipeline_run,
                    {"pipeline_run_id": self._pipeline_run_id,
                     "status": status, "error": error},
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=CONTROL,
                )

    async def _run(self, params: dict) -> dict:
        run_key = workflow.info().workflow_id
        steps = params["dag_config"]["steps"]
        results: dict = {}
        done: set[str] = set()
        started: set[str] = set()

        trigger = {
            "trigger_kind": params.get("trigger_kind", "manual"),
            "triggered_by": params.get("triggered_by"),
            "schedule_id": params.get("schedule_id"),
        }

        opened = await workflow.execute_activity(
            open_pipeline_run,
            {"dataset": params["dataset"], "workflow_id": run_key,
             "tenant": params["tenant"],
             "source_version_id": params.get("source_version_id"), **trigger},
            start_to_close_timeout=timedelta(minutes=1), retry_policy=CONTROL,
        )
        pipeline_run_id = opened["pipeline_run_id"]
        self._pipeline_run_id = pipeline_run_id
        # Stashed onto params rather than threaded as its own parameter
        # through _run_step/_execute_step: adopt_version is the only block
        # that ever reads it, and every activity already receives params
        # whole, so a new positional parameter down two call layers just to
        # reach the one block that needs it would be a wider diff for no
        # real gain.
        params = {**params, "pipeline_task_credential": opened["task_credential"]}

        # Only the builtin blocks this DAG actually uses need a registered
        # dataset_action row; a DAG that never uses transcribe should not
        # need transcribe registered for this tenant. adopt_version is
        # excluded outright -- see BLOCKS_WITHOUT_ACTION -- so registering
        # this DAG never requires an "adopt_version" action nobody else has
        # (or would ever need) a reason to create.
        blocks_used = sorted(
            {s["block"] for s in steps if s["kind"] == "builtin"} - BLOCKS_WITHOUT_ACTION
        )
        if "verify" in blocks_used:
            # Before any GPU work: verify needs the score store, and finding it
            # missing after transcription wastes minutes of GPU time.
            await workflow.execute_activity(
                check_score_store,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=CONTROL,
            )

        actions: dict = {}
        if blocks_used:
            actions = await workflow.execute_activity(
                resolve_actions,
                {"dataset": params["dataset"], "names": blocks_used,
                 "tenant": params["tenant"]},
                start_to_close_timeout=timedelta(minutes=1), retry_policy=CONTROL,
            )

        while len(done) < len(steps):
            ready = [
                s for s in steps
                if s["name"] not in started
                and all(dep in done for dep in s.get("depends_on", []))
            ]
            if not ready:
                remaining = [s["name"] for s in steps if s["name"] not in done]
                raise ApplicationError(
                    f"DAG stalled: {len(done)}/{len(steps)} steps done, none ready. "
                    f"Remaining: {remaining}"
                )
            for s in ready:
                started.add(s["name"])

            await asyncio.gather(*(self._run_step(s, params, results, actions,
                                                   pipeline_run_id, run_key)
                                   for s in ready))
            for s in ready:
                done.add(s["name"])

        return {"steps": results}

    async def _run_step(self, step: dict, params: dict, results: dict,
                        actions: dict, pipeline_run_id: str, run_key: str) -> None:
        step_run_id = await workflow.execute_activity(
            open_step_run,
            {"pipeline_run_id": pipeline_run_id, "step_name": step["name"]},
            start_to_close_timeout=timedelta(minutes=1), retry_policy=CONTROL,
        )

        try:
            out = await self._execute_step(step, params, results, actions,
                                            pipeline_run_id, run_key)
        except Exception:
            await workflow.execute_activity(
                close_step_run,
                {"step_run_id": step_run_id, "status": "failed", "output": None},
                start_to_close_timeout=timedelta(minutes=1), retry_policy=CONTROL,
            )
            raise

        await workflow.execute_activity(
            close_step_run,
            {"step_run_id": step_run_id, "status": "succeeded", "output": out},
            start_to_close_timeout=timedelta(minutes=1), retry_policy=CONTROL,
        )
        results[step["name"]] = out

    async def _execute_step(self, step: dict, params: dict, results: dict,
                            actions: dict, pipeline_run_id: str, run_key: str) -> dict:
        inputs = _resolve_inputs(step.get("inputs"), results)
        kind = step["kind"]

        if kind == "builtin":
            activity_fn = BUILTIN_ACTIVITY[step["block"]]
            # source_version_id/source_prefix are handed to every builtin
            # step, not only adopt_version: harmless extra keys for a block
            # that never reads them, the same reasoning dataset/tenant/
            # bucket below already follow. That is what lets an
            # adopt_version step run with no `inputs:` declared at all, the
            # same as it needs none in the real DeidentificationPipeline.
            extra = ({} if step["block"] in BLOCKS_WITHOUT_ACTION
                    else {"action_id": actions[step["block"]]})
            return await workflow.execute_activity(
                activity_fn,
                {**inputs, "dataset": params["dataset"], "tenant": params["tenant"],
                 "bucket": params["bucket"],
                 "source_version_id": params.get("source_version_id"),
                 "source_prefix": params.get("source_prefix"),
                 "task_credential": params.get("pipeline_task_credential"),
                 **extra,
                 "idempotency_key": _idem(run_key, step["name"]),
                 "trigger_kind": params.get("trigger_kind", "manual"),
                 "triggered_by": params.get("triggered_by"),
                 "schedule_id": params.get("schedule_id"),
                 "pipeline_run_id": pipeline_run_id},
                start_to_close_timeout=timedelta(minutes=30),
                task_queue=worker_config.TASK_QUEUE,
                retry_policy=CONTROL,
            )
        if kind == "script":
            raw = await workflow.execute_activity(
                run_dag_step_sandboxed,
                {"pipeline_id": params["pipeline_id"],
                 "pipeline_version_id": params["pipeline_version_id"],
                 "script": step["script"], "inputs": inputs,
                 "run_id": pipeline_run_id},
                start_to_close_timeout=timedelta(minutes=10),
                task_queue=worker_config.DAG_SCRIPT_TASK_QUEUE,
                retry_policy=CONTROL,
            )
            if raw.get("status") != "succeeded":
                raise ApplicationError(
                    f"step {step['name']!r} failed: {raw.get('reason')}"
                )
            return raw.get("output") or {}
        if kind == "gate":
            return await workflow.execute_activity(
                record_simple_gate_decision,
                {"version_id": params["source_version_id"], "to_class": step["to_class"],
                 "recommendation": inputs.get("recommendation"),
                 "recommendation_reason": inputs.get("recommendation_reason"),
                 "leak_detail": inputs.get("leak_detail"),
                 "triggered_by": params.get("triggered_by"),
                 "pipeline_run_id": pipeline_run_id},
                start_to_close_timeout=timedelta(minutes=2),
                task_queue=worker_config.TASK_QUEUE,
                retry_policy=CONTROL,
            )
        raise ApplicationError(
            f"step {step['name']!r} has kind {kind!r}, which is not runnable yet "
            f"(wait_for_human is Phase 2)"
        )
