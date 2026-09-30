"""The GPU-free worker: everything the governance story needs, nothing the
real de-identification pipeline needs.

    python -m worker.lite_worker

worker/main.py (the host worker) and worker/sandbox_worker.py (the WSL2
distro worker) exist as two halves of one machine's setup, split because
Windows cannot reach both a GPU and a Docker socket from the same process
without real friction (see both files' own docstrings). Neither constraint
holds for this worker: it never touches a GPU at all, and it runs as its
own Docker Compose service (docker-compose.yml's `worker-lite`), so the
Docker socket is one bind mount away rather than a WSL2 boundary away.
That is what lets this file merge the GPU-free half of main.py with the
Docker-touching half of sandbox_worker.py into one process, and why it
needs neither host.

What runs here: PipelineDagWorkflow and CountRecordsPipeline (both GPU-
free by construction -- a DAG's script steps are arbitrary operator code,
never this platform's own models, and count_records is the built-in
starter pipeline that reads a manifest and writes a number), the plain
Postgres/API-facing activities those two workflows need, and the one
Docker-touching activity a DAG's own script step uses.

What does NOT run here, on purpose: DeidentificationPipeline and its
activities (transcribe, detect, redact, verify -- all in worker/
activities.py, all needing faster-whisper, the detection ensemble, and a
real GPU to be worth running), HuggingFaceFetchWorkflow, and
AgentRunWorkflow. None of those are needed to register a dataset, request
access, have it granted, run a pipeline, and see a real gate decision --
the actual governance mechanism this platform exists to prove -- which is
the whole point of this file: seeing that mechanism work should never
have required a GPU, and until this file existed it did.

Dependency set: worker/requirements-lite.txt, the same shape as
worker/requirements-sandbox.txt (temporalio, httpx, psycopg, docker) plus
boto3, because this worker (unlike the WSL2 sandbox worker) reads and
writes dataset versions through platform_client.py.

Running a DAG's sandboxed script step from inside this container is
Docker-outside-of-Docker: this process calls docker.from_env() over a
bind-mounted host socket (/var/run/docker.sock), which reaches the SAME
daemon this container itself runs under, so the containers it creates are
its siblings, not its children. That daemon resolves every bind-mount path
sandbox_run.py passes it (see that file's build_deps(), for one) against
its own host filesystem, never against this container's filesystem. So
MUNITAS_WORK must be set to a real path on the Docker host, not a path
meaningful only inside this container -- and that same host directory
must also be bind-mounted into this container at the identical path, so
this process's own file writes land where the daemon will later look for
them. docker-compose.yml's `worker-lite` service and this repository's
.env.example both document the env var that has to carry that host path;
get it wrong and dependency installation or code staging for a DAG's
script step will fail with a file-not-found from inside the sandboxed
container, not from here.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from . import config
from .activities import (close_pipeline_run, count_records, open_pipeline_run,
                         record_simple_gate_decision, resolve_actions)
from .dag_activities import (close_step_run, open_step_run,
                             run_dag_step_sandboxed)
from .dag_workflow import PipelineDagWorkflow
from .sandbox_run import reap_orphaned_containers
from .workflows import CountRecordsPipeline

config.configure_logging("lite-worker")
log = logging.getLogger("munitas.worker.lite")


async def main() -> None:
    config.require_work_dir()

    # Same reasoning as worker/sandbox_worker.py's own startup reap: Docker
    # is reachable here, so this is a real check rather than a best-effort
    # one. A container carrying the munitas.sandbox label at this point
    # was left by a previous worker process that died before its own
    # cleanup ran.
    reaped = reap_orphaned_containers()
    if reaped:
        log.warning("reaped %d orphaned sandbox container(s) from a previous run", reaped)
    else:
        log.info("no orphaned sandbox containers found")

    log.info("connecting to Temporal at %s", config.TEMPORAL)
    client = await Client.connect(config.TEMPORAL, runtime=config.temporal_runtime())

    # Two Workers, one process -- the same shape worker/sandbox_worker.py
    # already uses for its own two task queues. PipelineDagWorkflow lives
    # on the pipeline queue; its one Docker-touching activity is routed to
    # the DAG-script queue by the workflow itself (see dag_workflow.py),
    # exactly as it already is when the host worker and the WSL2 worker
    # split this same work across two machines. Here it is one machine
    # talking to itself over two queues instead of two.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool, \
         concurrent.futures.ThreadPoolExecutor(max_workers=8) as dag_pool:
        pipeline_worker = Worker(
            client,
            task_queue=config.TASK_QUEUE,
            workflows=[CountRecordsPipeline, PipelineDagWorkflow],
            activities=[open_pipeline_run, close_pipeline_run, resolve_actions, count_records,
                        record_simple_gate_decision, open_step_run,
                        close_step_run],
            activity_executor=pool,
            # No GPU to serialise access to, unlike worker/main.py's own
            # pipeline_worker (max_concurrent_activities=1 there, because
            # 8 GB of VRAM will not hold two models). Concurrency here is
            # bounded only by the thread pool above.
            max_concurrent_activities=8,
        )
        dag_script_worker = Worker(
            client,
            task_queue=config.DAG_SCRIPT_TASK_QUEUE,
            activities=[run_dag_step_sandboxed],
            activity_executor=dag_pool,
            max_concurrent_activities=8,
        )
        log.info("lite worker ready on task queues %r and %r",
                 config.TASK_QUEUE, config.DAG_SCRIPT_TASK_QUEUE)
        await asyncio.gather(pipeline_worker.run(), dag_script_worker.run())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except config.RefusedPath as exc:
        raise SystemExit(str(exc))
    except KeyboardInterrupt:
        log.info("lite worker stopped")
