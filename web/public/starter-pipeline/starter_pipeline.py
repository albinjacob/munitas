# starter_pipeline.py: the smallest complete pipeline kind.
#
# This is Munitas's real CountRecordsPipeline (worker/workflows.py) and its
# two activities (worker/activities.py), copied here so you can read the
# whole thing in one file before it is spread across the real ones. Do not
# import this file directly; copy its shape into worker/workflows.py and
# worker/activities.py instead, following the numbered comments below.
#
# It runs end to end on a laptop: no model, no GPU, no audio. It reads the
# record count the console already knows about a sealed version, writes it
# down as a recommendation, and lets review and promotion happen exactly the
# way they do for the real de-identification pipeline. That is the whole
# point: proving the plumbing works before you write a step that does
# anything interesting.

from datetime import timedelta

from temporalio import activity, workflow

# --------------------------------------------------------------------
# 1. Give your kind a name.
#
# This becomes:
#   - a new value in platform/schema.sql's pipeline_run_kind_valid CHECK
#     constraint (alongside 'deidentify' and 'count_records')
#   - a new key in platform/api/app/pipelines.py's WORKFLOW_TYPE dict,
#     pointing at the workflow class name from step 3 below
# --------------------------------------------------------------------
PIPELINE_KIND = "count_records"


# --------------------------------------------------------------------
# 2. One activity: plain input in, plain output out.
#
# Real steps (transcribe, detect, redact, in worker/activities.py) read a
# version's actual data from storage and write real output back. This one
# skips storage entirely and reads the record count the API already looked
# up and forwarded as params['limit']. The point of this activity is
# showing where a step's real work goes, not adding a storage round trip a
# teaching example gains nothing from.
#
# Register the function itself in worker/main.py's activities=[...] list.
# --------------------------------------------------------------------
@activity.defn
def count_records(params: dict) -> dict:
    return {"version_id": params["source_version_id"],
            "record_count": params.get("limit") or 0}


# --------------------------------------------------------------------
# 3. One activity to record the decision.
#
# A real pipeline that runs detection ends with record_gate_decision, which
# rebuilds a leak-scoring ScoreCard and derives pass/fail from a recall
# threshold. This kind never scored anything, so it writes the gate_decision
# row directly with a recommendation it already knows, through
# record_simple_gate_decision (worker/activities.py) rather than inventing
# leak metrics to reuse the scoring-specific activity.
# --------------------------------------------------------------------
# (record_simple_gate_decision already exists in worker/activities.py;
# nothing to copy here, just call it from your workflow as step 4 does.)


# --------------------------------------------------------------------
# 4. One workflow: calls the activity, then records a gate decision, so
#    review and promotion work exactly like they do for every other kind.
#
# Register the class in worker/main.py's workflows=[...] list, and its
# `name=` string as the value for your PIPELINE_KIND in
# platform/api/app/pipelines.py's WORKFLOW_TYPE.
# --------------------------------------------------------------------
@workflow.defn(name="CountRecordsPipeline")
class CountRecordsPipeline:
    @workflow.run
    async def run(self, params: dict) -> dict:
        # open_pipeline_run (worker/activities.py) is the same for every
        # kind: it opens the pipeline_run row every step below stamps its
        # work onto, so a run stays one thing to look at.
        pipeline_run_id = await workflow.execute_activity(
            "open_pipeline_run",
            {"dataset": params["dataset"], "workflow_id": workflow.info().workflow_id,
             "tenant": params["tenant"],
             "trigger_kind": params.get("trigger_kind", "manual"),
             "triggered_by": params.get("triggered_by"),
             "schedule_id": params.get("schedule_id")},
            start_to_close_timeout=timedelta(minutes=1),
        )

        counted = await workflow.execute_activity(
            count_records, params,
            start_to_close_timeout=timedelta(minutes=1),
        )

        gate = await workflow.execute_activity(
            "record_simple_gate_decision",
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
        )

        return {"count": counted, "gate": gate}
