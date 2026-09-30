"""The one activity a DAG's script steps ever call.

Fetches this pipeline version's whole scripts.zip once per step (caching it
per-run would save a repeated download across a multi-script DAG; left as a
follow-up, not required for correctness, since each fetch is one small HTTP
call against the platform's own API, not the sandbox itself).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import httpx
from temporalio import activity

from . import config
from .dag_sandbox import run_dag_step
from .sandbox_run import SandboxBuildError, build_deps, extract_code


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
    resolved by the workflow), run_id.
    """
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

        return run_dag_step(
            params["inputs"], code_dir, deps_dir, params["script"], params["run_id"],
        )
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
