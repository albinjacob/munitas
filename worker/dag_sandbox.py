"""Running one DAG script step, sandboxed.

A simpler cousin of sandbox_run.run_sandboxed, deliberately: an agent run stages a whole dataset version through a minted credential because it does
not yet know which parts of it it needs. A DAG step already knows exactly: its inputs are resolved by the workflow before this ever runs, from prior
steps' own outputs, so there is nothing to stage and no credential to mint. The step's inputs are written to one file inside the container.

A step is the customer's own code, so its container is as closed as the platform can make it:

  * it runs as an unprivileged user, with every Linux capability dropped and no way to gain one, on a read-only root filesystem. Its only place to
    write is /tmp, which is memory-backed and size-limited, and goes with the container;
  * it has a memory limit, a process limit and a time limit;
  * it has no network at all, unless its pipeline file says `recordings: true`. Then it joins one internal network whose only member is a small
    gateway to the platform's step routes. It holds a pass for this one step, and no storage address, no storage key and no patient key. It reaches
    no database and no other service, and the internet is not reachable from there.
"""

from __future__ import annotations

import json
from pathlib import Path

import docker

from . import config
from .sandbox_run import (DEFAULT_RUN_TIMEOUT_SECONDS, RUN_IMAGE, SANDBOX_UID,
                          _last_json_object, _run_and_wait)

SDK_DIR = Path(__file__).parent / "step_sdk"
DEFAULT_MEMORY_MB, MAX_MEMORY_MB = 1024, 4096
DEFAULT_SCRATCH_MB, MAX_SCRATCH_MB = 256, 2048
PIDS = 256


def limits(memory_mb: int | None, scratch_mb: int | None) -> tuple[int, int]:
    """The memory and scratch space a step gets: what its pipeline file asked for, kept inside the ceilings. Scratch is memory-backed, so it counts
    against the memory limit and can never exceed it."""
    memory = min(max(int(memory_mb or DEFAULT_MEMORY_MB), 64), MAX_MEMORY_MB)
    scratch = min(max(int(scratch_mb or DEFAULT_SCRATCH_MB), 16), MAX_SCRATCH_MB, memory)
    return memory, scratch


def run_dag_step(step_inputs: dict, code_dir: Path, deps_dir: Path | None,
                 script: str, run_id: str,
                 timeout: int = DEFAULT_RUN_TIMEOUT_SECONDS,
                 step_pass: str | None = None,
                 memory_mb: int | None = None, scratch_mb: int | None = None) -> dict:
    """Run one step's script once.

    step_inputs is written to /data/inputs.json, read-only, the whole contract this step's code has for reading what earlier steps produced. A step
    given a `step_pass` also has the library (`import munitas_step`) and the two settings it needs to reach the gateway; no environment variable
    holds anything else.
    """
    inputs_dir = code_dir.parent / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    (inputs_dir / "inputs.json").write_text(json.dumps(step_inputs))

    client = docker.from_env()
    volumes = {
        str(code_dir): {"bind": "/code", "mode": "ro"},
        str(inputs_dir): {"bind": "/data", "mode": "ro"},
    }
    path = ["/code"]
    if deps_dir is not None:
        volumes[str(deps_dir)] = {"bind": "/deps", "mode": "ro"}
        path.insert(0, "/deps")
    environment = {"MUNITAS_RUN_ID": run_id, "HOME": "/tmp"}
    network = "none"
    if step_pass is not None:
        volumes[str(SDK_DIR)] = {"bind": "/sdk", "mode": "ro"}
        path.insert(0, "/sdk")
        environment.update({"MUNITAS_STEP_API": config.STEP_API, "MUNITAS_STEP_PASS": step_pass})
        network = config.STEPNET
    environment["PYTHONPATH"] = ":".join(path)

    memory, scratch = limits(memory_mb, scratch_mb)
    exit_code, logs, timed_out = _run_and_wait(
        client,
        image=RUN_IMAGE,
        command=["python", f"/code/{script}"],
        environment=environment,
        volumes=volumes,
        network=network,
        timeout=timeout,
        label=f"dag step {run_id}",
        run_id=run_id,
        user=SANDBOX_UID,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        read_only=True,
        tmpfs={"/tmp": f"size={scratch}m,mode=1777"},
        memory_limit=f"{memory}m",
        pids_limit=PIDS,
        working_dir="/tmp",
    )

    if timed_out:
        return {
            "status": "failed",
            "reason": f"the step exceeded its {timeout}s wall-clock ceiling and was killed",
            "output": None, "exit_code": None, "timed_out": True,
        }

    text = logs.decode("utf-8", errors="replace")
    output = _last_json_object(text)

    if exit_code == 137:
        return {
            "status": "failed", "reason": f"the step ran out of its {memory} MB of memory (its pipeline file can ask for more, up to {MAX_MEMORY_MB})",
            "output": output, "exit_code": exit_code, "timed_out": False,
        }
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
