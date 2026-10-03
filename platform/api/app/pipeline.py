"""Starting a de-identification run, and watching one.

Separate from `ingest.py` on purpose. That module's whole argument is that it
is the one place dataset bytes enter the platform, and starting a pipeline
moves no bytes: it names a version that already exists and asks a worker to go
and read it. Folding this in would weaken a claim that file makes about itself.

Nothing slow happens inside a request here. Every check is a local database
read or a question to a service on the same network, and the actual work is
handed to Temporal, which is the shape
`POST /datasets/{id}/fetch-huggingface` already established: validate what is
cheap, record the row the console will poll, start the workflow, return.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from temporalio.service import RPCError, RPCStatusCode

from . import auth, config, db, opa, pipelines, storage, temporal_client

router = APIRouter(tags=["pipeline"])


class StartPipeline(BaseModel):
    # Which workflow this run is. Defaults to the one pipeline that existed
    # before this field did, so every caller from before pipeline_kind
    # existed keeps working with no change on their side. Checked against
    # pipelines.WORKFLOW_TYPE below rather than a pydantic pattern, so the
    # one place a new kind gets registered is that dict, not this file too.
    pipeline_kind: str = "deidentify"
    # Required only when pipeline_kind == 'dag': names which sealed
    # pipeline_version runs. Every other kind ignores this field entirely.
    pipeline_version_id: str | None = None

AUDIO_SUFFIX = ".wav"
ANSWER_KEY_SUFFIX = ".truth.json"

# The contract a version must carry for the pipeline to be able to read it.
# Sealed by ingest.py's seal-audio path, which earns the name by reading the
# audio headers on the way in. The ordinary seal produces `uploaded_files`,
# two fields that say only that a file arrived and how big it was.
PIPELINE_CONTRACT = "encounter_raw"

# The object-storage backends the pipeline worker can read and write.
#
# Mirrors `worker/platform_client.py`'s SERVED_BACKENDS, which is the authority:
# that module builds the client, and this one only refuses early so a person
# gets an explanation now rather than a failed run in several minutes. Two
# constants in two processes can drift, so U61 asserts they are equal rather
# than trusting this comment.
PIPELINE_BACKENDS = ("seaweedfs",)

# How many missing answer keys to name before summarising. Enough to fix a
# small mistake from the message alone, few enough that a wholly unprepared
# dataset does not answer with a wall of text.
MAX_NAMED = 10


def _stems(manifest: list[dict], suffix: str) -> set[str]:
    """Record ids in a version's manifest whose object carries this suffix.

    Read from the version's own manifest rather than from `dataset_source`, so
    the answer does not depend on upload rows that a later change might tidy
    away. The manifest is part of what was sealed and cannot move.
    """
    found = set()
    for entry in manifest:
        name = str(entry.get("key", "")).rsplit("/", 1)[-1]
        if name.endswith(suffix):
            found.add(name[: -len(suffix)])
    return found


async def _pipeline_worker_pollers() -> int:
    """How many workers have polled the pipeline's task queue recently.

    Asked rather than assumed. Starting a workflow that nothing will pick up
    does not fail, it hangs, and a hang is worse than an error because it is
    indistinguishable from ordinary slowness: the run simply stays at its first
    step, looking like a slow model rather than an absent worker.

    "Recently" rather than "right now", and the difference is worth stating
    because it bounds what the refusal can promise. Temporal keeps a poller's
    record for a short while after that poller stops, so a worker killed a
    minute ago is still counted here. Measured on this stack: a worker stopped
    90 seconds earlier was still listed. So zero means nothing has polled for
    some minutes, which is a solid signal that no worker exists; a non-zero
    count is weaker, and a run started into a queue whose only worker died
    seconds ago will still hang. This closes the common case, where nobody
    started the worker at all, and does not claim to close the race.
    """
    from temporalio.api.enums.v1 import TaskQueueType
    from temporalio.api.taskqueue.v1 import TaskQueue
    from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

    client = temporal_client.get()
    response = await client.workflow_service.describe_task_queue(
        DescribeTaskQueueRequest(
            namespace="default",
            task_queue=TaskQueue(name=config.PIPELINE_TASK_QUEUE),
            task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
        )
    )
    return len(response.pollers)


@router.get("/pipeline/served-backends", dependencies=[Depends(auth.person_or_worker)])
def served_backends() -> dict:
    """Which object-storage backends the pipeline can read and write.

    Exists so the worker's own answer can be compared with this one from
    outside both processes. The constant below mirrors the worker's, and a
    mirror nobody checks is a copy waiting to disagree.
    """
    return {"backends": list(PIPELINE_BACKENDS)}


async def _score_store_problem() -> str | None:
    """Why a scoring run could not record its score, or None if it could."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{config.MLFLOW_URL}/health")
    except httpx.HTTPError:
        return (
            "the score store (MLflow) is not running, so this run could not "
            "record its score and would stop before finishing. "
            "Start it with `docker compose --profile full up -d mlflow`, then start the run again"
        )
    if r.status_code != 200:
        return f"the score store (MLflow) answered HTTP {r.status_code} to its health check"
    return None


# The job runner's words for how a workflow ended, in the database's words.
_ENDED_AS = {
    "COMPLETED": "succeeded",
    "FAILED": "failed",
    "CANCELED": "cancelled",
    "TERMINATED": "terminated",
    "TIMED_OUT": "timed_out",
}


async def _workflow_ending(workflow_id: str):
    """(ended_at, status, error) if Temporal says this workflow stopped, or
    None if it is still running or Temporal cannot say.

    Used only for a run whose record is still open: a run that ended through
    its own workflow already recorded how. One still open after its workflow
    stopped was killed from outside (terminated, timed out, or its worker
    died), and this is where that gets recorded.

    A workflow Temporal has no record of is not running either: its history
    is gone past retention, or it never started. It ends now, as 'unknown'.
    """
    from datetime import datetime, timezone

    from temporalio.client import WorkflowExecutionStatus

    try:
        described = await temporal_client.get().get_workflow_handle(workflow_id).describe(
            rpc_timeout=timedelta(seconds=10))
    except temporal_client.TemporalUnavailable:
        return None
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return (datetime.now(timezone.utc), "unknown",
                    "the job runner has no record of this run")
        return None
    if described.status in (None, WorkflowExecutionStatus.RUNNING):
        return None
    name = described.status.name
    status = _ENDED_AS.get(name, "unknown")
    error = None if status == "succeeded" else f"the job runner reports it {name.lower()}"
    return described.close_time or datetime.now(timezone.utc), status, error


def _record_ending(run_id, ending) -> None:
    """Close a still-open run record with the ending Temporal reported."""
    ended_at, status, error = ending
    db.execute(
        """update pipeline_run
              set status = %s, error = %s, ended_at = %s
            where id = %s and ended_at is null""",
        (status, error, ended_at, run_id),
    )


@router.get("/pipeline/kinds", dependencies=[Depends(auth.person_or_worker)])
def pipeline_kinds() -> dict:
    """Which pipeline kinds this process knows how to start.

    Mirrors worker/main.py's own registered-workflows list, the same
    reasoning as served_backends above: this process names the kind, that
    process names the workflow class backing it, and a verify script checks
    the two agree rather than trusting a comment to keep them in step.
    """
    return {"kinds": sorted(pipelines.WORKFLOW_TYPE)}


@router.post("/datasets/{dataset_id}/versions/{version_id}/deidentify",
             status_code=202)
async def start_deidentification(
    dataset_id: str, version_id: str,
    body: StartPipeline = StartPipeline(),
    identity: dict = Depends(auth.current_session),
) -> dict:
    """Start a pipeline run against a version already sealed.

    Every refusal below is decided before Temporal is touched, so a request
    that cannot succeed leaves nothing behind: no workflow, no run row, and
    nothing for anyone to find and clean up later.

    The identity comes from the session and never from a body. A run is
    attributed to whoever started it, and that attribution is exactly what
    stops the same person clearing its gate afterwards, so it cannot be a value
    the caller asserts about themselves.

    body.pipeline_kind picks which workflow runs. The checks below that are
    specific to the de-identification pipeline (the recordings contract, the
    answer-key check) only apply when that is the kind requested; a kind that
    is not 'deidentify' still needs a sealed version, a supported backend, no
    run already in flight, and a live worker, the same as every kind does.
    """
    try:
        workflow_type = pipelines.workflow_type_for(body.pipeline_kind)
    except ValueError as exc:
        raise HTTPException(400, {"started": False, "reasons": [str(exc)]}) from exc

    pipeline_version = None
    if body.pipeline_kind == "dag":
        if not body.pipeline_version_id:
            raise HTTPException(400, {"started": False, "reasons": [
                "pipeline_kind 'dag' needs a pipeline_version_id"
            ]})
        pipeline_version = db.one(
            """select pv.id, pv.dag_config, pv.pipeline_id, p.tenant_id
                 from pipeline_version pv join pipeline p on p.id = pv.pipeline_id
                where pv.id = %s""",
            (body.pipeline_version_id,),
        )
        if not pipeline_version:
            raise HTTPException(404, {"started": False, "reasons": [
                "no such pipeline version"
            ]})
        # wait_for_human is Phase 2: the config format already reserves the
        # step kind so a version naming one can still register, but nothing
        # can run it yet. Refused here, synchronously, rather than starting
        # a workflow that would only fail once it actually reached that
        # step, minutes later, with no explanation at the point somebody
        # was looking.
        waiting_steps = [
            s["name"] for s in pipeline_version["dag_config"]["steps"]
            if s.get("kind") == "wait_for_human"
        ]
        if waiting_steps:
            raise HTTPException(400, {"started": False, "reasons": [
                f"this pipeline has a 'wait_for_human' step ({', '.join(waiting_steps)}), "
                f"which is not runnable yet"
            ]})

    permitted, reasons = opa.may_start_pipeline(
        {"operator": {"id": identity["id"], "roles": identity["roles"]}}
    )
    if not permitted:
        raise HTTPException(403, {"started": False, "reasons": reasons})

    version = db.one(
        """select v.id, v.tenant_id, v.storage_prefix, v.record_count,
                  v.object_manifest, v.sealed, v.storage_backend,
                  c.name as contract_name, d.name as dataset_name
             from dataset_version v
             join schema_contract c on c.id = v.schema_id
             join dataset d on d.id = v.dataset_id
            where v.id = %s and v.dataset_id = %s""",
        (version_id, dataset_id),
    )
    # One query rather than three, so a caller cannot learn from the error
    # which of the three facts was the wrong one.
    if not version or not version["sealed"]:
        raise HTTPException(404, {"started": False, "reasons": [
            "no sealed version with that id belongs to this dataset"
        ]})

    if pipeline_version and pipeline_version["tenant_id"] != version["tenant_id"]:
        raise HTTPException(404, {"started": False, "reasons": [
            "no such pipeline version"
        ]})

    # Collected rather than raised one at a time. Reporting only the first
    # would turn three problems into three attempts, which is the same
    # argument seal-audio already makes about bad uploads.
    problems: list[str] = []

    if body.pipeline_kind == "deidentify":
        if version["contract_name"] != PIPELINE_CONTRACT:
            problems.append(
                f"this version was sealed with the contract "
                f"{version['contract_name']!r}, which records only that files "
                f"arrived and how large they were. The pipeline needs recordings "
                f"it can read a duration and a sample rate from. Upload the audio "
                f"again and use Seal as recordings"
            )
        else:
            manifest = version["object_manifest"] or []
            missing = sorted(_stems(manifest, AUDIO_SUFFIX)
                             - _stems(manifest, ANSWER_KEY_SUFFIX))
            if missing:
                named = ", ".join(missing[:MAX_NAMED])
                more = (f", and {len(missing) - MAX_NAMED} more"
                        if len(missing) > MAX_NAMED else "")
                problems.append(
                    f"{named}{more} has no answer key, so this run could redact "
                    f"the recording but could not tell you whether the redaction "
                    f"worked. Scoring a run that has no answer key is not built yet"
                )

    backend = version["storage_backend"] or "seaweedfs"
    if backend not in PIPELINE_BACKENDS:
        problems.append(
            f"this version's objects are stored on {backend!r}, and the "
            f"pipeline worker can only read "
            f"{' or '.join(PIPELINE_BACKENDS)}. A run would write its output "
            f"to the wrong storage and seal a version nothing can read"
        )

    if problems:
        raise HTTPException(400, {"started": False, "reasons": problems})

    existing = db.one(
        """select id, started_at, workflow_id from pipeline_run
            where source_version_id = %s and ended_at is null
            order by started_at desc limit 1""",
        (version_id,),
    )
    # A run killed from outside, or whose worker died, never reaches the
    # `finally` that ends it, so its row stays open although nothing is
    # running. Temporal knows, so it is asked rather than the row believed.
    # When Temporal cannot answer the refusal stands: not knowing is not
    # evidence that the run stopped.
    if existing:
        ending = await _workflow_ending(existing["workflow_id"])
        if ending:
            _record_ending(existing["id"], ending)
            existing = None
    if existing:
        raise HTTPException(409, {
            "started": False,
            "pipeline_run_id": str(existing["id"]),
            "reasons": [
                f"a pipeline run of this version started at "
                f"{existing['started_at']:%H:%M on %d %b} and has not finished"
            ],
        })

    try:
        pollers = await _pipeline_worker_pollers()
    except temporal_client.TemporalUnavailable as exc:
        raise HTTPException(503, {"started": False, "reasons": [
            f"the background job runner is unavailable: {exc}"
        ]}) from exc
    if pollers == 0:
        raise HTTPException(503, {"started": False, "reasons": [
            "no pipeline worker is running, so this run would sit in a queue "
            "looking slow rather than failing. Start `python -m worker.main` "
            "on the machine with the GPU"
        ]})

    # A run that scores its output needs the score store, and without it
    # would spend minutes of GPU time before failing at verify. Refused here
    # instead, with nothing started. The workflow checks again itself,
    # because run_pipeline.py and schedules start workflows without this.
    scores = body.pipeline_kind == "deidentify" or (
        pipeline_version is not None and any(
            s.get("block") == "verify" for s in pipeline_version["dag_config"]["steps"]))
    if scores:
        problem = await _score_store_problem()
        if problem:
            raise HTTPException(503, {"started": False, "reasons": [problem]})

    run_id = str(uuid.uuid4())
    workflow_id = f"deid-{run_id[:10]}"

    # Written before the workflow starts, so the console has something to poll
    # from the moment this returns rather than racing a worker that may not
    # have picked the workflow up yet. The workflow's own open_pipeline_run
    # finds this row by workflow_id and reuses it, which POST /pipeline-runs
    # already does, because Temporal replays a workflow after a crash and a
    # replay is the same run rather than a second one.
    db.execute(
        """insert into pipeline_run
             (id, tenant_id, dataset, workflow_id, trigger_kind, triggered_by,
              source_version_id, started_from, pipeline_kind, pipeline_version_id)
           values (%s, %s, %s, %s, 'manual', %s, %s, 'console', %s, %s)""",
        (run_id, version["tenant_id"], version["dataset_name"], workflow_id,
         identity["id"], version_id, body.pipeline_kind,
         pipeline_version["id"] if pipeline_version else None),
    )

    try:
        client = temporal_client.get()
        await client.start_workflow(
            workflow_type,
            {
                "dataset": version["dataset_name"],
                "source_version_id": version_id,
                "source_prefix": version["storage_prefix"],
                # The run's tenant and its bucket, sent rather than left for
                # the worker to read from its own environment. That is what
                # lets a run belong to whichever tenant owns the data, instead
                # of to whichever tenant the worker process was started for.
                "tenant": version["tenant_id"],
                "bucket": storage.bucket_for(
                    version["storage_backend"] or "seaweedfs", version["tenant_id"]
                ),
                "limit": version["record_count"],
                "trigger_kind": "manual",
                "triggered_by": identity["id"],
                "schedule_id": None,
                "pipeline_run_id": run_id,
                **({"dag_config": pipeline_version["dag_config"],
                    "pipeline_id": pipeline_version["pipeline_id"],
                    "pipeline_version_id": pipeline_version["id"]}
                   if pipeline_version else {}),
            },
            id=workflow_id,
            task_queue=config.PIPELINE_TASK_QUEUE,
        )
    except Exception as exc:
        # Closed rather than deleted. The row records that somebody tried and
        # that it did not start, which is the question asked afterwards; a
        # deleted row answers nothing.
        db.execute(
            "update pipeline_run set ended_at = now() where id = %s", (run_id,)
        )
        raise HTTPException(502, {"started": False, "reasons": [
            f"could not start the background job: {exc}"
        ]}) from exc

    return {
        "pipeline_run_id": run_id,
        "workflow_id": workflow_id,
        "status": "running",
        "records": version["record_count"],
    }


@router.get("/pipeline-runs/{pipeline_run_id}")
async def read_pipeline_run(
    pipeline_run_id: str, identity: dict = Depends(auth.current_session)
) -> dict:
    """One run: what it started from, what it has done, and where it stands.

    The workflow's status is asked of Temporal on every read rather than stored
    in a column here. A stored status is written by the very process whose
    death it would need to report: kill the worker mid-run and the column says
    `running` for as long as the database exists. This platform has that defect
    live in one place already, on `/health`, where the Temporal error is set
    once at startup and never cleared, so the API keeps reporting Temporal down
    after Temporal has recovered. Repeating the shape one table over would be
    hard to defend.

    The steps and the gate decision are database facts, so an unreachable
    Temporal costs the status line and nothing else. That is why it produces a
    null status and a stated reason rather than an error.

    Previously took no session at all: any run's detail, including who
    triggered it, readable by anyone who knew or guessed its id, cross-
    tenant. Answers "no such pipeline run" rather than "not yours" for one
    in another tenant, the same non-disclosure posture `get_dataset`
    (`ingest.py`) already uses.
    """
    row = db.one(
        """select r.*, d.label as triggered_by_label, p.name as pipeline_name
             from pipeline_run r
             left join directory d on d.id = r.triggered_by
             left join pipeline_version pv on pv.id = r.pipeline_version_id
             left join pipeline p on p.id = pv.pipeline_id
            where r.id = %s""",
        (pipeline_run_id,),
    )
    if not row or row["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, {"reasons": ["no such pipeline run"]})

    if row["pipeline_kind"] == "dag":
        # Generic, unlike action_run: a DAG's step names are whatever the
        # operator called them, not one of the fixed pipeline's own.
        steps = db.all_rows(
            """select step_name as action, status, started_at, ended_at,
                      null as output_version
                 from pipeline_step_run
                where pipeline_run_id = %s
                order by started_at""",
            (pipeline_run_id,),
        )
    else:
        steps = db.all_rows(
            """select a.status, a.started_at, a.ended_at, a.output_version,
                      act.name as action
                 from action_run a
                 join dataset_action act on act.id = a.action_id
                where a.pipeline_run_id = %s
                order by a.started_at""",
            (pipeline_run_id,),
        )

    gate = db.one(
        """select id, state, recommendation, recommendation_reason, metrics,
                  to_class
             from gate_decision where pipeline_run_id = %s
            order by created_at desc limit 1""",
        (pipeline_run_id,),
    )

    # The outcome is read from the record. Only a run the record still shows
    # as running is checked with the job runner, because one killed from
    # outside never recorded its own ending; when that is what happened, it
    # is recorded now and read back. When the job runner cannot be reached
    # the record stands, with the reason alongside: not knowing is not
    # evidence that the run stopped.
    status_error = None
    if row["status"] == "running":
        try:
            ending = await _workflow_ending(row["workflow_id"])
        except Exception as exc:  # noqa: BLE001 - reported, not raised, on purpose
            ending, status_error = None, f"could not reach the job runner: {exc}"
        if ending:
            _record_ending(row["id"], ending)
            row = {**row, **db.one(
                "select status, error, ended_at from pipeline_run where id = %s",
                (row["id"],))}
        elif not temporal_client.connected():
            status_error = "could not reach the job runner, so this may have finished"

    return {
        "id": str(row["id"]),
        "dataset": row["dataset"],
        "pipeline_kind": row["pipeline_kind"],
        "pipeline_name": row["pipeline_name"],
        "workflow_id": row["workflow_id"],
        "status": row["status"],
        "error": row["error"],
        "status_error": status_error,
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "triggered_by": row["triggered_by"],
        "triggered_by_label": row["triggered_by_label"],
        "started_from": row["started_from"],
        "source_version_id": (str(row["source_version_id"])
                              if row["source_version_id"] else None),
        "steps": [
            {
                "action": s["action"],
                "status": s["status"],
                "started_at": s["started_at"],
                "ended_at": s["ended_at"],
                "output_version": (str(s["output_version"])
                                   if s["output_version"] else None),
            }
            for s in steps
        ],
        "gate_decision": (
            {
                "id": str(gate["id"]),
                "state": gate["state"],
                "recommendation": gate["recommendation"],
                "recommendation_reason": gate["recommendation_reason"],
                "to_class": gate["to_class"],
                "metrics": gate["metrics"],
            }
            if gate else None
        ),
    }
