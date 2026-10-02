"""The sandboxed-agent worker, run inside the WSL2 distro.

    python -m worker.sandbox_worker

The host worker (worker/main.py) runs the pipeline, HuggingFace ingestion and
native agent runs on Windows, next to the GPU. It cannot start a sandboxed
agent run: that needs Docker, and the Docker Engine on this machine lives
inside the Ubuntu-20.04 WSL2 distro, reachable only over the distro's own unix
socket. There is no TCP listener the Windows side could point DOCKER_HOST at,
and opening one would expose root-equivalent control of the machine to anything
local. So the Docker-dependent half runs here instead, next to the socket.

Two Temporal workers now, both here, both next to the socket: one on
config.SANDBOX_TASK_QUEUE, hosting run_sandboxed_agent; one on
config.DAG_SCRIPT_TASK_QUEUE, hosting run_dag_step_sandboxed, for the same
reason and the same constraint, a DAG's own script steps need Docker too.
AgentRunWorkflow and PipelineDagWorkflow (both still hosted by the host
worker) route only those two activities here; everything else, both
workflows themselves, native agent runs, the pipeline, ingestion, stays on
Windows. The Docker socket never leaves the distro.

Its dependency set is deliberately slim (worker/requirements-sandbox.txt):
temporalio, docker, httpx, psycopg, and nothing else. It never imports the
pipeline's ML stack or agent.graph, which is the whole reason
sandbox_run_activities.py takes _db from worker/db.py rather than from
agent_run_activities.py. The distro's system Python is 3.8; this runs in a
provisioned modern-Python venv (see RUNBOOK.md / start-dev.ps1).

Reaching the platform: Compose runs inside this same distro, so its published
ports answer on localhost here exactly as they do on the Windows host. The one
address that differs is MUNITAS_WORK, which must be a distro path (the run
directory is bind-mounted into the sandbox containers by the distro's own
daemon, so a Windows drive path would be meaningless to it).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from . import config
from .dag_activities import run_dag_step_sandboxed
from .derivation_activities import run_derivation_sandboxed
from .sandbox_run import reap_orphaned_containers
from .sandbox_run_activities import run_sandboxed_agent

config.configure_logging("sandbox-worker")
log = logging.getLogger("munitas.worker.sandbox")


async def main() -> None:
    config.require_work_dir()

    # Docker is reachable here, so the reap is a real startup step rather than
    # the best-effort attempt it had to be on the host. A container carrying
    # the munitas.sandbox label at this point was left by a previous worker
    # process that died before its own cleanup ran.
    reaped = reap_orphaned_containers()
    if reaped:
        log.warning("reaped %d orphaned sandbox container(s) from a previous run", reaped)
    else:
        log.info("no orphaned sandbox containers found")

    log.info("connecting to Temporal at %s", config.TEMPORAL)
    client = await Client.connect(config.TEMPORAL, runtime=config.temporal_runtime())

    # Both activities are synchronous (they block on container.wait()), so
    # both run in a thread pool, the same as every other activity in this
    # platform. This worker hosts no workflows: AgentRunWorkflow and
    # PipelineDagWorkflow both run on the host worker and only dispatch
    # their one Docker-touching activity here.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool, \
         concurrent.futures.ThreadPoolExecutor(max_workers=8) as dag_pool:
        agent_worker = Worker(
            client,
            task_queue=config.SANDBOX_TASK_QUEUE,
            activities=[run_sandboxed_agent, run_derivation_sandboxed],
            activity_executor=pool,
            max_concurrent_activities=4,
        )
        dag_script_worker = Worker(
            client,
            task_queue=config.DAG_SCRIPT_TASK_QUEUE,
            activities=[run_dag_step_sandboxed],
            activity_executor=dag_pool,
            max_concurrent_activities=8,
        )
        log.info("sandbox worker ready on task queues %r and %r",
                 config.SANDBOX_TASK_QUEUE, config.DAG_SCRIPT_TASK_QUEUE)
        await asyncio.gather(agent_worker.run(), dag_script_worker.run())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except config.RefusedPath as exc:
        raise SystemExit(str(exc))
    except KeyboardInterrupt:
        log.info("sandbox worker stopped")
