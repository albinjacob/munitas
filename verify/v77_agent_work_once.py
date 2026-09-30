"""U77: a piece of agent work is done at most once, however often Temporal
hands it over.

Temporal can lose a hand-over or a "done" when its database answers slowly,
and retries the step when that happens. worker/agent_attempt.py records each
piece of work in agent_run_attempt so the retry is safe. This drives it
directly against the real database, with the work replaced by a counter so
"did it run again" is a number rather than an inference:

  * fresh: the work runs once and its outcome is recorded
  * handed over again after finishing (its "done" was lost): the recorded
    outcome comes back and the work does not run
  * handed over again after starting but never finishing (the worker
    stopped partway): the run is failed with that reason and the work does
    not run
  * the work itself fails: recorded, and raised as not to be retried

Runs on the host, where the worker package is importable.

    .venv\\Scripts\\python.exe verify\\v77_agent_work_once.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "verify"))

from common import CANARY, check, db, heading, summary  # noqa: E402
from temporalio.exceptions import ApplicationError  # noqa: E402
from worker.agent_attempt import STOPPED_PARTWAY, run_once  # noqa: E402


def make_run() -> str:
    """A real agent_run row, copied from an existing canary agent, left
    'running' so the stopped-partway case has something to fail."""
    run_id = str(uuid.uuid4())
    with db() as conn:
        agent = conn.execute(
            """select a.id as agent_id, v.id as version_id from agent a
                 join agent_version v on v.agent_id = a.id
                where a.tenant_id = %s limit 1""", (CANARY,)).fetchone()
        if not agent:
            raise RuntimeError("no canary agent to attach a run to; run the suite first")
        conn.execute(
            """insert into agent_run (id, tenant_id, agent_id, agent_version_id, status,
                                      purpose, requested_by, execution_mode)
               values (%s, %s, %s, %s, 'running', 'U77 check', 'canary-engineer', 'native')""",
            (run_id, CANARY, agent["agent_id"], agent["version_id"]))
    return run_id


def main() -> int:
    run_id = make_run()
    calls = {"n": 0}

    def work() -> dict:
        calls["n"] += 1
        return {"status": "succeeded", "tool_calls": 3}

    segment = f"u77-{uuid.uuid4().hex[:8]}/1"
    try:
        heading("U77a: fresh work runs once, and its outcome is recorded")
        first = run_once(run_id, work, segment=segment, heartbeat=False)
        with db() as conn:
            row = conn.execute("select started_at, finished_at, outcome from agent_run_attempt "
                               "where segment = %s", (segment,)).fetchone()
        check("the work ran once", calls["n"] == 1, f"{calls['n']} run(s)")
        check("and returned its outcome", first == {"status": "succeeded", "tool_calls": 3},
              f"{first}")
        check("the attempt is recorded, finished, with that outcome",
              row and row["finished_at"] is not None and row["outcome"] == first, f"{row}")

        heading("U77b: handed over again after finishing, it does not run again")
        again = run_once(run_id, work, segment=segment, heartbeat=False)
        check("the work did not run a second time", calls["n"] == 1, f"{calls['n']} run(s)")
        check("the recorded outcome came back", again == first, f"{again}")

        heading("U77c: begun but never finished, the run fails instead of repeating")
        stuck = f"u77-{uuid.uuid4().hex[:8]}/1"
        with db() as conn:
            conn.execute("insert into agent_run_attempt (segment, run_id) values (%s, %s)",
                         (stuck, run_id))
        before = calls["n"]
        result = run_once(run_id, work, segment=stuck, heartbeat=False)
        with db() as conn:
            run = conn.execute("select status, error, ended_at from agent_run where id = %s",
                               (run_id,)).fetchone()
            att = conn.execute("select finished_at from agent_run_attempt where segment = %s",
                               (stuck,)).fetchone()
        check("the work did not run", calls["n"] == before, f"{calls['n'] - before} extra run(s)")
        check("the run is recorded as failed, saying the worker stopped partway",
              run["status"] == "failed" and run["error"] == STOPPED_PARTWAY
              and run["ended_at"] is not None, f"{dict(run)}")
        check("and the attempt is closed", att["finished_at"] is not None)
        check("the step returns a failed outcome rather than raising",
              result.get("status") == "failed", f"{result}")

        heading("U77d: the work itself failing is recorded and not retried")
        broken = f"u77-{uuid.uuid4().hex[:8]}/1"

        def fails() -> dict:
            raise RuntimeError("u77: the container exited 137")

        try:
            run_once(run_id, fails, segment=broken, heartbeat=False)
            raised = None
        except ApplicationError as exc:
            raised = exc
        check("it raises as not to be retried",
              raised is not None and raised.non_retryable is True, f"{raised!r}")
        with db() as conn:
            att = conn.execute("select finished_at, outcome from agent_run_attempt "
                               "where segment = %s", (broken,)).fetchone()
        check("with the failure recorded against the attempt",
              att["finished_at"] is not None and "exited 137" in att["outcome"]["reason"],
              f"{att}")
    finally:
        with db() as conn:
            conn.execute("delete from agent_run_attempt where run_id = %s", (run_id,))
            conn.execute("delete from agent_run where id = %s", (run_id,))
    return summary("U77")


if __name__ == "__main__":
    sys.exit(main())
