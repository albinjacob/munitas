"""Running one DAG script step, sandboxed.

A simpler cousin of sandbox_run.run_sandboxed, deliberately: an agent run
stages a whole dataset version through a minted credential because it does
not yet know which parts of it it needs. A DAG step already knows exactly:
its inputs are resolved by the workflow before this ever runs, from prior
steps' own outputs, so there is nothing to stage and no credential to mint.
The step's inputs are written to one file inside the container; nothing
else of the tenant's data is reachable from it.
"""

from __future__ import annotations

import json
from pathlib import Path

import docker

from . import config
from .sandbox_run import (DEFAULT_RUN_TIMEOUT_SECONDS, RUN_IMAGE,
                          _last_json_object, _run_and_wait)


def run_dag_step(step_inputs: dict, code_dir: Path, deps_dir: Path | None,
                 script: str, run_id: str,
                 timeout: int = DEFAULT_RUN_TIMEOUT_SECONDS) -> dict:
    """Run one step's script once, with no network beyond agentnet.

    step_inputs is written to /data/inputs.json, read-only, the whole
    contract this step's code has for reading anything: no environment
    variable holds a value, only a path to a file.
    """
    inputs_dir = code_dir.parent / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    (inputs_dir / "inputs.json").write_text(json.dumps(step_inputs))

    client = docker.from_env()
    volumes = {
        str(code_dir): {"bind": "/code", "mode": "ro"},
        str(inputs_dir): {"bind": "/data", "mode": "ro"},
    }
    python_path = "/code"
    if deps_dir is not None:
        volumes[str(deps_dir)] = {"bind": "/deps", "mode": "ro"}
        python_path = "/deps:/code"

    exit_code, logs, timed_out = _run_and_wait(
        client,
        image=RUN_IMAGE,
        command=["python", f"/code/{script}"],
        environment={"PYTHONPATH": python_path, "MUNITAS_RUN_ID": run_id},
        volumes=volumes,
        network=config.AGENTNET,
        timeout=timeout,
        label=f"dag step {run_id}",
        run_id=run_id,
    )

    if timed_out:
        return {
            "status": "failed",
            "reason": f"the step exceeded its {timeout}s wall-clock ceiling and was killed",
            "output": None, "exit_code": None, "timed_out": True,
        }

    text = logs.decode("utf-8", errors="replace")
    output = _last_json_object(text)

    if exit_code != 0:
        return {
            "status": "failed", "reason": f"the step's process exited {exit_code}",
            "output": output, "exit_code": exit_code, "timed_out": False,
        }
    if output is None:
        return {
            "status": "failed",
            "reason": "the step's process exited 0 but printed no parseable JSON result",
            "output": None, "exit_code": exit_code, "timed_out": False,
        }
    return {
        "status": "succeeded", "reason": None, "output": output,
        "exit_code": exit_code, "timed_out": False,
    }
