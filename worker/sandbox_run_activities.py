"""Running an uploaded agent's own code, as a Temporal activity.

Mirrors `agent_run_activities.py`'s shape (build context, do the work,
record the outcome), but "the work" here is a throwaway container built
and supervised by `sandbox_run.py`, not `graph.invoke()`. Kept in its own
file rather than folded into `agent_run_activities.py` for the same reason
the two execution paths get their own Temporal activity: a reviewer asking
"what actually runs a native agent" versus "what actually runs an uploaded
one" should get a one-file answer to either question.
"""

from __future__ import annotations

import json
import logging
import shutil

from temporalio import activity

from . import config, sandbox_run
from .agent_attempt import run_once
from .db import _db

log = logging.getLogger("munitas.worker")


def _record_outcome(run_id: str, outcome: dict) -> dict:
    """Write a sandboxed run's outcome back, with the same honesty
    `agent_run_activities._record_outcome` already applies: a run is only
    ever `succeeded` when the sandboxed process said so, never by default.
    """
    status = outcome["status"]
    findings = [outcome["output"]] if outcome.get("output") else []
    error = outcome["reason"] if status == "failed" else None

    with _db() as conn:
        conn.execute(
            """update agent_run
                 set status = %s, error = %s, findings = %s, ended_at = now()
               where id = %s and status = 'running'""",
            (status, error, json.dumps(findings), run_id),
        )
    return outcome


@activity.defn
def run_sandboxed_agent(params: dict) -> dict:
    """Run an uploaded version's own code once, at most once however often
    Temporal hands it over (agent_attempt)."""
    return run_once(params["run_id"], lambda: _run_sandboxed_agent(params))


def _run_sandboxed_agent(params: dict) -> dict:
    """Fetch, extract, and execute an uploaded version's own code once.

    `principal_id`/`tenant_id`/`purpose`/`dataset_version_id` come straight
    from `params`, the same trust level `agent_run_activities._context_for`
    already gives those fields for the native path, since they were pinned
    into the `agent_run` row and the workflow's own start params together,
    at the same moment, by `start_run`. Only `code_object_key`/`entrypoint`
    need a fresh read here, because those live on `agent_version`, not on
    the workflow's own params.
    """
    run_id = params["run_id"]
    with _db() as conn:
        row = conn.execute(
            "select code_object_key, entrypoint, data_access "
            "from agent_version where id = %s",
            (params["agent_version_id"],),
        ).fetchone()
    if not row or not row[0]:
        raise RuntimeError(
            f"agent_version {params['agent_version_id']} has no uploaded code "
            "to execute"
        )
    entrypoint, data_access = row[1], row[2]

    run_dir = config.WORK / "agent-runs" / run_id
    try:
        payload = sandbox_run.fetch_code(params["agent_id"], params["agent_version_id"])
        code_dir = sandbox_run.extract_code(payload, run_dir / "code")
        deps_dir = sandbox_run.build_deps(code_dir, run_dir / "deps", run_id)
        data_dir = (
            sandbox_run.stage_data(params, run_dir)
            if data_access == "copy" else None
        )
        outcome = sandbox_run.run_sandboxed(
            params, code_dir, deps_dir, entrypoint, data_dir=data_dir
        )
    except sandbox_run.AwaitingActivation:
        # Parked, not failed. The platform starts the run again from staging
        # once its storage access is in effect (platform/api/app/agents.py,
        # resume_activated_runs).
        with _db() as conn:
            conn.execute(
                """update agent_run
                     set status = 'awaiting_activation',
                         awaiting_activation_since = now()
                   where id = %s and status = 'running'""",
                (run_id,),
            )
        return {"status": "awaiting_activation"}
    except (sandbox_run.SandboxBuildError, sandbox_run.SandboxDataStagingError) as exc:
        outcome = {
            "status": "failed", "reason": str(exc),
            "output": None, "exit_code": None, "timed_out": False,
        }
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    return _record_outcome(run_id, outcome)
