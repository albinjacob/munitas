"""The one activity a DAG's script steps ever call.

Fetches this pipeline version's whole scripts.zip once per step (caching it
per-run would save a repeated download across a multi-script DAG; left as a
follow-up, not required for correctness, since each fetch is one small HTTP
call against the platform's own API, not the sandbox itself).
"""

from __future__ import annotations

import contextlib
import contextvars
import shutil
import tempfile
import threading
from pathlib import Path

import httpx
from temporalio import activity

from . import config, locked_file_open
from .dag_sandbox import run_dag_step
from .sandbox_run import SandboxBuildError, build_deps, extract_code


HEARTBEAT_SECONDS = 15


@contextlib.contextmanager
def heartbeating(every: float = HEARTBEAT_SECONDS):
    """Tell Temporal every few seconds that this activity is alive, from a thread of its own, for as long as the block runs.

    A step runs for minutes inside one blocking call, so without this Temporal knows nothing until the activity returns. If the result is then lost
    on its way back, or this worker dies, the workflow would wait out the whole start-to-close time (30 minutes for a step given recordings) and
    would then try the step again. With it, the workflow sets a heartbeat timeout of a minute and a half, and notices within that.

    The beats stop before the activity returns, which is why a lost result is noticed at all: nothing more is heard from this activity.
    """
    context = contextvars.copy_context()
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(every):
            try:
                activity.heartbeat()
            except Exception:  # noqa: BLE001 - the activity is over, or cancelled: nothing left to say
                return

    thread = threading.Thread(target=context.run, args=(beat,), name="step-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def _fetch_scripts(pipeline_id: str, version_id: str) -> bytes:
    response = httpx.get(
        f"{config.API}/pipelines/{pipeline_id}/versions/{version_id}/code",
        headers={"x-worker-token": config.WORKER_TOKEN},
        timeout=30.0, verify=config.api_verify(),
    )
    response.raise_for_status()
    return response.content


@activity.defn
def run_dag_step_sandboxed(params: dict) -> dict:
    """Run one script step.

    params: pipeline_id, pipeline_version_id, script, inputs (already
    resolved by the workflow), run_id, and, for a step that declared
    `recordings: true` or `writes_files: true`, `step_access`: what the platform
    needs to make the step's pass and, for a writing step, to finish it; and
    `memory_mb` and `scratch_mb` from its pipeline file.
    """
    with heartbeating():
        return _run_script_step(params)


def _run_script_step(params: dict) -> dict:
    work_dir = Path(tempfile.mkdtemp(prefix="munitas-dag-"))
    try:
        payload = _fetch_scripts(params["pipeline_id"], params["pipeline_version_id"])
        code_dir = extract_code(payload, work_dir / "code")

        deps_dir = None
        try:
            deps_dir = build_deps(code_dir, work_dir / "deps", params["run_id"])
        except SandboxBuildError as exc:
            return {"status": "failed", "reason": str(exc), "output": None,
                    "exit_code": None, "timed_out": False}

        step_pass = None
        spec = params.get("step_access")
        if spec:
            # The platform makes the pass only for a step its version declares, whose version is approved, in a run that is still going. The
            # step then reads its recordings from the platform itself, through the gateway: nothing is fetched or written on this machine.
            try:
                step_pass = locked_file_open.step_pass(principal=f"{spec['tenant']}-pipeline", tenant_id=spec["tenant"],
                                                     task_credential=spec["task_credential"], step=spec["step"])
            except (locked_file_open.ControlPlaneError, httpx.HTTPError) as exc:
                return {"status": "failed", "reason": str(exc), "output": None, "exit_code": None, "timed_out": False}

        result = run_dag_step(
            params["inputs"], code_dir, deps_dir, params["script"], params["run_id"],
            step_pass=step_pass, memory_mb=params.get("memory_mb"), scratch_mb=params.get("scratch_mb"),
        )
        if not (spec and spec.get("writes_files")):
            return result
        # The container has exited. The platform seals what the step wrote as one version under review if it succeeded, and throws it all away if
        # it did not. Asked in both cases, so a step that failed leaves nothing behind.
        try:
            finished = locked_file_open.finish_step(principal=f"{spec['tenant']}-pipeline", tenant_id=spec["tenant"], task_credential=spec["task_credential"],
                                                  step=spec["step"], succeeded=result.get("status") == "succeeded")
        except (locked_file_open.ControlPlaneError, httpx.HTTPError) as exc:
            return {**result, "status": "failed", "reason": f"what the step wrote could not be sealed: {exc}", "output": None}
        if finished["status"] == "refused":
            return {**result, "status": "failed", "reason": finished["reason"], "output": None}
        if result.get("status") != "succeeded":
            return result
        # The platform's own keys come last, so a step cannot claim to have made a version it did not.
        sealed = {k: finished[k] for k in ("files_written", "output_version_id", "output_dataset") if k in finished}
        return {**result, "output": {**(result["output"] or {}), **sealed}}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@activity.defn
def open_step_run(params: dict) -> str:
    """Open one pipeline_step_run row, so the console's run page has
    something to show the moment a step starts rather than only once it
    finishes. Mirrors open_pipeline_run's own reasoning, one level down.
    """
    import uuid

    from .db import _db

    step_run_id = str(uuid.uuid4())
    with _db() as conn:
        conn.execute(
            """insert into pipeline_step_run (id, pipeline_run_id, step_name, status)
               values (%s, %s, %s, 'running')""",
            (step_run_id, params["pipeline_run_id"], params["step_name"]),
        )
    return step_run_id


@activity.defn
def close_step_run(params: dict) -> None:
    import json

    from .db import _db

    with _db() as conn:
        conn.execute(
            """update pipeline_step_run set status = %s, output = %s, ended_at = now()
               where id = %s""",
            (params["status"], json.dumps(params.get("output")), params["step_run_id"]),
        )
