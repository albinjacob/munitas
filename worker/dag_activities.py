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

from . import config, recording_open
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


def _fetch_recordings(spec: dict, audio_dir: Path) -> dict:
    """The recordings a custom step declared it wants, opened by the platform into a folder the step reads from (/data/audio, read-only).

    The platform refuses unless this step's version was approved to be given them. A recording of an erased patient is left out and named
    under `recordings_skipped`. The step is told what it has in `recordings`: each one's id, where it is, and its size.
    """
    principal = f"{spec['tenant']}-pipeline"  # the same naming the pipeline's own steps act as
    audio_dir.mkdir(parents=True, exist_ok=True)
    given, skipped = [], []
    for item in recording_open.list_for_step(principal=principal, tenant_id=spec["tenant"], task_credential=spec["task_credential"],
                                             version_id=spec["version_id"], step=spec["step"]):
        record_id = item["record_id"]
        if item["erased"]:
            skipped.append(record_id)
            continue
        try:
            recording_open.stream_to_file(audio_dir / f"{record_id}.wav", principal=principal, tenant_id=spec["tenant"],
                                          task_credential=spec["task_credential"], version_id=spec["version_id"], record_id=record_id,
                                          purpose="given to the step", step=spec["step"])
        except recording_open.RecordingErased:
            skipped.append(record_id)
            continue
        given.append({"record_id": record_id, "path": f"/data/audio/{record_id}.wav", "bytes": item["bytes"]})
    return {"recordings": given, "recordings_skipped": skipped}


@activity.defn
def run_dag_step_sandboxed(params: dict) -> dict:
    """Run one script step.

    params: pipeline_id, pipeline_version_id, script, inputs (already
    resolved by the workflow), run_id, and, for a step that declared
    `recordings: true`, `recordings`: what the platform needs to open them.
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

        inputs = params["inputs"]
        if params.get("recordings"):
            try:
                inputs = {**inputs, **_fetch_recordings(params["recordings"], work_dir / "inputs" / "audio")}
            except (recording_open.ControlPlaneError, httpx.HTTPError) as exc:
                return {"status": "failed", "reason": str(exc), "output": None, "exit_code": None, "timed_out": False}

        return run_dag_step(
            inputs, code_dir, deps_dir, params["script"], params["run_id"],
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
