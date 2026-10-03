"""The de-identification workflow.

Temporal's contribution here is specific and worth naming, because "we use a
workflow engine" is otherwise decoration. Three things it provides that the
polling lifecycle manager in the current system does not:

  * **Durable state.** If the worker dies mid-pipeline the workflow resumes at
    the activity that failed, not at the beginning. A twenty minute
    transcription is not repeated because a detector crashed after it.
    Resuming *within* an activity is not something Temporal provides: a retried
    activity restarts at its first line, so the expensive ones checkpoint per
    record themselves. That distinction cost a run that transcribed 112 records
    for a corpus of 60.
  * **Retry as policy rather than as code.** Each activity declares its own
    retry and timeout behaviour. Model work gets few retries and a timeout
    proportional to the record count; a control-plane call gets many retries
    and a short deadline.
  * **History.** What ran, in what order, with what result, is queryable
    afterwards without anyone having instrumented it.

The workflow holds no data, only keys and version ids. Activity return values
land in Temporal's history, which is durable and replicated, so a transcript
returned from an activity would be a copy of RAW data living outside the class
system entirely.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .activities import (adopt_version, check_score_store, close_pipeline_run,
                             describe_failure,
                             count_records,
                             detect, handoff,
                             ingest, open_pipeline_run, record_gate_decision,
                             record_simple_gate_decision, redact,
                             resolve_actions, transcribe, verify)

# Control-plane calls are quick and idempotent, so retry hard and fail fast.
CONTROL = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)

# Model work is slow and expensive. Retrying a transcription that failed for a
# deterministic reason just burns the same time again, so attempts are few. The
# activities checkpoint per record, so a retry resumes rather than restarting.
MODEL = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=3,
)

# Timeouts are derived from measured throughput rather than guessed.
#
# Measured on an RTX 2070 SUPER, 8 GB, faster-whisper large-v3 at int8_float16:
# 60 records of roughly 40 seconds each transcribed in about 6 minutes, so
# roughly 6 seconds per record. Detection over the same 60 took under 2 minutes
# on CPU.
#
# The first version of this file used a 3 hour ceiling for transcription. That
# is not a timeout, it is a formality: a job that normally takes 6 minutes can
# hang for an afternoon inside it, and a hang is worse than an error because it
# is indistinguishable from ordinary slowness. These allow roughly four times
# the measured rate, which absorbs GPU contention without hiding a stall.
SECONDS_PER_RECORD_TRANSCRIBE = 25
SECONDS_PER_RECORD_DETECT = 10
SECONDS_PER_RECORD_REDACT = 10


def _budget(records: int, per_record: int, floor_minutes: int) -> timedelta:
    """A timeout proportional to the work, with a floor for fixed costs.

    The floor covers model loading, which does not scale with record count and
    can be minutes on a cold cache.
    """
    return timedelta(seconds=max(records * per_record, floor_minutes * 60))


def _idem(*parts: str) -> str:
    """Derive an idempotency key from the workflow identity and the step.

    Derived rather than random, so a replayed workflow produces the same key and
    the control plane returns the original run instead of starting a second one.
    A random key here would silently defeat the guarantee V6 tests.
    """
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


@workflow.defn
class DeidentificationPipeline:
    """RAW audio to a promoted, de-identified dataset version."""

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
        dataset = params["dataset"]
        results: dict = {}
        # Known before ingest runs, because the caller says how many records to
        # take. Used to size every timeout below.
        n = params.get("limit") or 60
        # How this run started, forwarded into every activity that starts an
        # action_run. Built once here rather than re-read from `params` at
        # each site below, because it is the same fact every time: one
        # workflow run has one trigger, not five.
        trigger = {
            "trigger_kind": params.get("trigger_kind", "manual"),
            "triggered_by": params.get("triggered_by"),
            "schedule_id": params.get("schedule_id"),
        }

        # Which tenant's data this is, carried into every activity rather than
        # read from the worker's own environment at each seal. A worker
        # configured for one tenant used to seal every run's output into that
        # tenant whatever data it was actually processing, with nothing
        # enforcing the two being the same. The activities now refuse to seal
        # without this rather than falling back.
        tenant = params["tenant"]

        # One id for this run, stamped on every action_run below and passed to
        # anything the promotion later starts, so a run stays one thing to look
        # at even when it spans more than one workflow.
        opened = await workflow.execute_activity(
            open_pipeline_run,
            {"dataset": dataset, "workflow_id": run_key, "tenant": tenant,
             "source_version_id": params.get("source_version_id"),
             **trigger},
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=CONTROL,
        )
        pipeline_run_id = opened["pipeline_run_id"]
        self._pipeline_run_id = pipeline_run_id

        # A corpus run registers its dataset actions in run_pipeline.py and
        # passes their ids in. A console run does not, and the console has no
        # business knowing action ids, so the workflow resolves them itself.
        actions = params.get("actions") or await workflow.execute_activity(
            resolve_actions,
            {"names": ["transcribe", "detect", "handoff", "redact"],
             "tenant": tenant},
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=CONTROL,
        )

        # Before any GPU work: verify needs the score store, and finding it
        # missing after transcription wastes minutes of GPU time.
        await workflow.execute_activity(
            check_score_store,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=CONTROL,
        )

        # Two beginnings, one shape. A corpus run reads files off the worker's
        # own disk and seals a version from them; a console run is handed a
        # version somebody already sealed. Both return the same keys, so
        # nothing downstream knows or cares which one ran.
        if params.get("source_version_id"):
            ingested = await workflow.execute_activity(
                adopt_version,
                {
                    "source_version_id": params["source_version_id"],
                    "source_prefix": params["source_prefix"],
                    "tenant": tenant,
                    "bucket": params["bucket"],
                    "task_credential": opened["task_credential"],
                },
                # One small JSON read, measured at half a second. A minute is
                # a hundred times that, which absorbs any real contention and
                # still refuses to hide a stall. The first attempt of the
                # first console run on a freshly started worker was observed
                # hanging until its timeout and then succeeding instantly on
                # retry, cause not established; a five minute ceiling turned
                # that into five minutes of a screen that looked broken, and
                # this turns it into one.
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=CONTROL,
            )
        else:
            ingested = await workflow.execute_activity(
                ingest,
                {
                    "corpus": params["corpus"],
                    "limit": params.get("limit", 0),
                    "dataset": f"{dataset}-raw",
                    "action_id": actions["ingest"],
                    "idempotency_key": _idem(run_key, "ingest"),
                    "tenant": tenant,
                    **trigger,
                    "pipeline_run_id": pipeline_run_id,
                },
                start_to_close_timeout=_budget(n, 5, 5),
                retry_policy=CONTROL,
            )
        results["ingest"] = ingested

        transcribed = await workflow.execute_activity(
            transcribe,
            {
                "records_key": ingested["records_key"],
                "input_version": ingested["version_id"],
                "dataset": f"{dataset}-transcribed",
                "action_id": actions["transcribe"],
                "idempotency_key": _idem(run_key, "transcribe"),
                "tenant": tenant,
                "bucket": ingested["bucket"],
                **trigger,
                "pipeline_run_id": pipeline_run_id,
            },
            start_to_close_timeout=_budget(n, SECONDS_PER_RECORD_TRANSCRIBE, 10),
            # A record that takes longer than this has stalled, not slowed.
            heartbeat_timeout=timedelta(minutes=4),
            retry_policy=MODEL,
        )
        results["transcribe"] = transcribed

        detected = await workflow.execute_activity(
            detect,
            {
                "records_key": transcribed["records_key"],
                "input_version": transcribed["version_id"],
                "dataset": f"{dataset}-detected",
                "action_id": actions["detect"],
                "idempotency_key": _idem(run_key, "detect"),
                "tenant": tenant,
                "bucket": ingested["bucket"],
                **trigger,
                "pipeline_run_id": pipeline_run_id,
            },
            start_to_close_timeout=_budget(n, SECONDS_PER_RECORD_DETECT, 10),
            heartbeat_timeout=timedelta(minutes=4),
            retry_policy=MODEL,
        )
        results["detect"] = detected

        results["handoff"] = await workflow.execute_activity(
            handoff,
            {
                "records_key": detected["records_key"],
                "input_version": detected["version_id"],
                "dataset": f"{dataset}-handoff",
                "action_id": actions["handoff"],
                "idempotency_key": _idem(run_key, "handoff"),
                "tenant": tenant,
                "bucket": ingested["bucket"],
                **trigger,
                "pipeline_run_id": pipeline_run_id,
            },
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=CONTROL,
        )

        redacted = await workflow.execute_activity(
            redact,
            {
                "records_key": detected["records_key"],
                "audio_index_key": ingested["records_key"],
                "input_version": detected["version_id"],
                "dataset": f"{dataset}-redacted",
                "action_id": actions["redact"],
                "idempotency_key": _idem(run_key, "redact"),
                "tenant": tenant,
                "bucket": ingested["bucket"],
                "confidence_threshold": params.get("confidence_threshold", 0.4),
                **trigger,
                "pipeline_run_id": pipeline_run_id,
            },
            start_to_close_timeout=_budget(n, SECONDS_PER_RECORD_REDACT, 10),
            heartbeat_timeout=timedelta(minutes=4),
            retry_policy=MODEL,
        )
        results["redact"] = redacted

        score = await workflow.execute_activity(
            verify,
            {
                "detected_key": detected["records_key"],
                # Taken from what ingest actually wrote, not reconstructed.
                # Rebuilding the path here would break silently the moment the
                # control plane changed its prefix layout.
                "truth_prefix": ingested["prefix"],
                "bucket": ingested["bucket"],
                "tenant": tenant,
                "idempotency_key": _idem(run_key, "verify"),
            },
            start_to_close_timeout=_budget(n, 5, 5),
            retry_policy=CONTROL,
        )
        results["verify"] = score

        # Not "promote": this records what the gate found and stops. Promotion
        # is a person's act now, taken in the console against this row. A key
        # here still saying promote would be the same lie in a new place.
        results["gate"] = await workflow.execute_activity(
            record_gate_decision,
            {
                "version_id": redacted["version_id"],
                "to_class": params.get("to_class", "OPEN_FOR_ANNOTATION"),
                "score_card": score,
                "leak_detail": score["leak_detail"],
                "recall_threshold": params.get("recall_threshold", 0.95),
                "triggered_by": params.get("triggered_by"),
                "pipeline_run_id": pipeline_run_id,
                # What a workflow started after the promotion would need, so it
                # can begin from this row rather than from state that ended
                # with this workflow.
                "handoff": {
                    "records_key": redacted["records_key"],
                    "detected_key": detected["records_key"],
                    "truth_prefix": ingested["prefix"],
                },
            },
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=CONTROL,
        )

        return results


@workflow.defn(name="CountRecordsPipeline")
class CountRecordsPipeline:
    """The barebones starter kind: count a version's records, recommend it.

    Downloadable and runnable as web/public/starter-pipeline/starter_pipeline.py.
    One activity, one gate decision, no model and no GPU, so following along
    means running this rather than only reading it. Everything a second real
    kind needs is here in miniature: its own entry in pipelines.WORKFLOW_TYPE,
    its own row in worker/main.py's workflows=[...], and a gate decision at
    the end so review and promotion need no changes to support it.
    """

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
        opened = await workflow.execute_activity(
            open_pipeline_run,
            {"dataset": params["dataset"], "workflow_id": workflow.info().workflow_id,
             "tenant": params["tenant"],
             "trigger_kind": params.get("trigger_kind", "manual"),
             "triggered_by": params.get("triggered_by"),
             "schedule_id": params.get("schedule_id")},
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=CONTROL,
        )
        pipeline_run_id = opened["pipeline_run_id"]
        self._pipeline_run_id = pipeline_run_id

        counted = await workflow.execute_activity(
            count_records, params,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=CONTROL,
        )

        gate = await workflow.execute_activity(
            record_simple_gate_decision,
            {
                "version_id": counted["version_id"],
                "to_class": params.get("to_class", "OPEN_FOR_ANNOTATION"),
                "recommendation": "pass",
                "recommendation_reason":
                    f"{counted['record_count']} record"
                    f"{'' if counted['record_count'] == 1 else 's'} counted; "
                    f"this starter kind does no redaction, so there is "
                    f"nothing else to check",
                "triggered_by": params.get("triggered_by"),
                "pipeline_run_id": pipeline_run_id,
            },
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=CONTROL,
        )

        return {"count": counted, "gate": gate}
