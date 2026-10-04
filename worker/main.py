"""Run the worker.

    python -m worker.main

Activities are synchronous and CPU or GPU bound, so they run in a thread pool
rather than on the event loop.

That sentence was in this file from the start and the code did not do it. The
activities were declared `async def`, and the Temporal SDK runs async activities
on the event loop regardless of `activity_executor`, which only ever applies to
synchronous ones. So blocking GPU work sat on the loop, and `activity.heartbeat`
recorded a heartbeat that the loop was never free to send.

The result was a heartbeat timeout on an activity that was working perfectly
well, then a retry, then two copies of the same transcription running at once.
The symptom was a pipeline that looked merely slow, and the diagnosis needed the
gaps between log lines: the longest pause between records was 17 seconds against
a 4 minute heartbeat timeout, so time was not the cause and the heartbeats
themselves had to be.

The activities are plain `def` now, which is what puts them in the pool.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from . import config
from .activities import (adopt_version, check_score_store, close_pipeline_run,
                         count_records,
                         detect, handoff,
                         ingest, open_pipeline_run, record_gate_decision,
                         record_simple_gate_decision, redact,
                         resolve_actions, transcribe, verify)
from .agent_run_activities import fail_run, resume_run, run_agent
from .agent_run_resume_workflow import AgentRunResumeWorkflow
from .agent_run_workflow import AgentRunWorkflow
from .dag_activities import close_step_run, open_step_run
from .dag_workflow import PipelineDagWorkflow
from .derivation_workflow import DerivationWorkflow, fail_derivation, seal_derivation
from .hf_ingest_activities import (cancel_job, fail_job, fetch_one_file,
                                   finalize_job, prepare_fetch)
from .hf_ingest_workflow import HuggingFaceFetchWorkflow
from .housekeeping_activities import close_stopped_pipeline_runs, sweep_stale_probes
from .housekeeping_workflow import CloseStoppedRunsWorkflow, TidyProbesWorkflow
from .workflows import CountRecordsPipeline, DeidentificationPipeline

config.configure_logging("host-worker")
log = logging.getLogger("munitas.worker")

# Which pipeline kinds this worker actually registers a workflow for.
#
# Mirrors platform/api/app/pipelines.py's WORKFLOW_TYPE keys, which is the
# authority a caller reads to pick a kind; this is the worker's own record of
# what it can run. Two constants in two processes can drift, so a verify
# script (v63's "the two processes agree on which pipeline kinds exist"
# section) compares them rather than trusting this comment.
PIPELINE_KINDS = ("deidentify", "count_records")


async def main() -> None:
    config.require_work_dir()
    config.require_secure_api()

    # No Docker here. Sandboxed agent runs, and the container-reaping that goes
    # with them, moved to worker/sandbox_worker.py inside the WSL2 distro,
    # where the Docker socket actually is. This host worker runs the pipeline,
    # HuggingFace ingestion and native agent runs, none of which touch Docker.

    log.info("connecting to Temporal at %s", config.TEMPORAL)
    client = await Client.connect(config.TEMPORAL, runtime=config.temporal_runtime())

    # One activity at a time. 8 GB of VRAM will not hold two models, and a
    # worker that accepts concurrent GPU work fails with an out-of-memory error
    # halfway through rather than queueing politely.
    #
    # HuggingFace ingestion is a second worker on its own task queue and its
    # own thread pool, not sharing this one: it is network I/O, not GPU
    # work, and a large or slow fetch should not have to wait behind a
    # transcription, or the other way round.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool, \
         concurrent.futures.ThreadPoolExecutor(max_workers=8) as hf_pool, \
         concurrent.futures.ThreadPoolExecutor(max_workers=4) as agent_pool, \
         concurrent.futures.ThreadPoolExecutor(max_workers=1) as housekeeping_pool:
        pipeline_worker = Worker(
            client,
            task_queue=config.TASK_QUEUE,
            # Every registered pipeline kind lives on this one task queue.
            # pipelines.WORKFLOW_TYPE, in the API, names the same classes by
            # the same strings; U6x asserts the two lists agree.
            #
            # PipelineDagWorkflow lives here too, though a DAG's script
            # steps do not: their one activity, run_dag_step_sandboxed,
            # needs Docker, which this host cannot reach (see this file's
            # own "No Docker here" note above), so it is registered on
            # worker/sandbox_worker.py instead, on DAG_SCRIPT_TASK_QUEUE.
            # open_step_run/close_step_run are plain Postgres writes and
            # stay here, next to the workflow that calls them.
            workflows=[DeidentificationPipeline, CountRecordsPipeline,
                       PipelineDagWorkflow],
            activities=[open_pipeline_run, close_pipeline_run, check_score_store,
                        resolve_actions, ingest,
                        adopt_version, transcribe, detect, handoff, redact,
                        verify, record_gate_decision, count_records,
                        record_simple_gate_decision, open_step_run,
                        close_step_run],
            activity_executor=pool,
            max_concurrent_activities=1,
        )
        hf_worker = Worker(
            client,
            task_queue=config.HF_INGEST_TASK_QUEUE,
            workflows=[HuggingFaceFetchWorkflow],
            activities=[prepare_fetch, fetch_one_file, finalize_job, fail_job,
                        cancel_job],
            activity_executor=hf_pool,
            max_concurrent_activities=8,
        )
        # AgentRunWorkflow lives here and routes its sandboxed branch to
        # config.SANDBOX_TASK_QUEUE, served by worker/sandbox_worker.py in the
        # distro. This worker runs the workflow and the native activities; it
        # never executes run_sandboxed_agent, so that activity is not
        # registered here.
        agent_worker = Worker(
            client,
            task_queue=config.AGENT_RUN_TASK_QUEUE,
            workflows=[AgentRunWorkflow, AgentRunResumeWorkflow],
            activities=[run_agent, resume_run, fail_run],
            activity_executor=agent_pool,
            max_concurrent_activities=4,
        )
        housekeeping_worker = Worker(
            client,
            task_queue=config.HOUSEKEEPING_TASK_QUEUE,
            workflows=[TidyProbesWorkflow, CloseStoppedRunsWorkflow],
            activities=[sweep_stale_probes, close_stopped_pipeline_runs],
            activity_executor=housekeeping_pool,
            max_concurrent_activities=1,
        )
        derivation_worker = Worker(
            client,
            task_queue=config.DERIVATION_TASK_QUEUE,
            workflows=[DerivationWorkflow],
            activities=[seal_derivation, fail_derivation],
            activity_executor=housekeeping_pool,
            max_concurrent_activities=2,
        )
        log.info("worker ready on task queues %r, %r, %r, %r and %r",
                 config.TASK_QUEUE, config.HF_INGEST_TASK_QUEUE,
                 config.AGENT_RUN_TASK_QUEUE, config.HOUSEKEEPING_TASK_QUEUE,
                 config.DERIVATION_TASK_QUEUE)
        log.info("DAG script steps route to %r, served by worker.sandbox_worker "
                 "in the distro", config.DAG_SCRIPT_TASK_QUEUE)
        await asyncio.gather(pipeline_worker.run(), hf_worker.run(),
                              agent_worker.run(), housekeeping_worker.run(),
                              derivation_worker.run())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except config.RefusedPath as exc:
        raise SystemExit(str(exc))
    except KeyboardInterrupt:
        log.info("worker stopped")
