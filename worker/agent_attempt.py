"""One piece of agent work, done at most once however often Temporal hands it
over.

Messages between Temporal and a worker can be lost when Temporal's database
answers slowly: Temporal can record a step as handed to a worker that never
received it, or a worker can finish a step and its "done" never arrive. An
agent run used to be tried exactly once with nothing checking in, so either
loss surfaced only when the step's time limit ran out, and the run was then
recorded as failed even when it had succeeded.

Retrying is how Temporal expects a lost message to be survived, but running an
agent twice would repeat its tool calls and the access decisions they record.
So every piece of agent work (a first run, a resume, a sandboxed run) records
in the database, before doing anything, that it has begun, and every time it
is handed over it reads that record first:

  * finished: its "done" was lost. The recorded outcome is returned, and
    nothing runs again.
  * begun, never finished: the worker stopped partway. The run is recorded as
    failed with that reason, and is not run again, because its tool calls may
    already have been made.
  * nothing: the hand-over was lost before any work. It runs.

One case it cannot tell apart: a worker still running but whose check-ins
all fail to arrive for the whole heartbeat timeout. The retry then fails the
run as stopped partway, and the first attempt's own result is not recorded
over it. A run recorded failed that in fact finished is the safe direction to
be wrong in; repeating its tool calls is not.

While it works it checks in with Temporal every few seconds, so a lost
hand-over is noticed within the heartbeat timeout rather than at the end of the
run's time limit. Imports only Temporal and the database, because the slim
sandbox worker (worker/sandbox_worker.py) uses it too.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
from contextlib import contextmanager
from typing import Callable

from temporalio import activity
from temporalio.exceptions import ApplicationError

from .db import _db

log = logging.getLogger("munitas.worker")

HEARTBEAT_SECONDS = 10

STOPPED_PARTWAY = (
    "the worker stopped partway through this run, so it was not started again: "
    "its tool calls may already have been made"
)


@contextmanager
def _heartbeating():
    """Check in with Temporal from a side thread while the work blocks.

    The work itself blocks for minutes (a graph invocation, a container), so
    it cannot call heartbeat() itself. The activity's context is copied so the
    side thread's heartbeats are this activity's.
    """
    context = contextvars.copy_context()
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                context.run(activity.heartbeat)
            except Exception:  # noqa: BLE001 - a missed beat is Temporal's to notice
                pass

    thread = threading.Thread(target=beat, name="agent-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=HEARTBEAT_SECONDS)


def _finish(segment: str, outcome: dict) -> None:
    with _db() as conn:
        conn.execute(
            """update agent_run_attempt set finished_at = now(), outcome = %s
                where segment = %s and finished_at is null""",
            (json.dumps(outcome, default=str), segment),
        )


def run_once(run_id: str, work: Callable[[], dict], *, segment: str | None = None,
             heartbeat: bool = True) -> dict:
    """Do `work` for this run at most once, however often it is handed over.

    `segment` names this piece of work; inside an activity it is derived from
    the Temporal execution and activity, which stay the same across retries of
    one step and differ between a run's first attempt and its resumes. It is
    passed explicitly only by tests.
    """
    if segment is None:
        info = activity.info()
        segment = f"{info.workflow_run_id}/{info.activity_id}"

    with _db() as conn:
        row = conn.execute(
            "select finished_at, outcome from agent_run_attempt where segment = %s",
            (segment,),
        ).fetchone()
        if row and row[0] is not None:
            return row[1]
        if row:
            conn.execute(
                """update agent_run set status = 'failed', error = %s, ended_at = now()
                    where id = %s and status in ('running', 'awaiting_approval')""",
                (STOPPED_PARTWAY, run_id),
            )
            outcome = {"status": "failed", "reason": STOPPED_PARTWAY}
            conn.execute(
                """update agent_run_attempt set finished_at = now(), outcome = %s
                    where segment = %s and finished_at is null""",
                (json.dumps(outcome), segment),
            )
            return outcome
        claimed = conn.execute(
            """insert into agent_run_attempt (segment, run_id) values (%s, %s)
               on conflict (segment) do nothing returning segment""",
            (segment, run_id),
        ).fetchone()
    if claimed is None:
        # Another attempt claimed it in the same instant; it is doing the work.
        raise ApplicationError("this piece of work is already being done",
                               non_retryable=True)

    try:
        if heartbeat:
            with _heartbeating():
                outcome = work()
        else:
            outcome = work()
    except BaseException as exc:
        # A real failure of the work, not a lost message: recorded, and not
        # retried, so the run is not repeated. The workflow records the error
        # on the run itself (fail_run). If recording it fails too, the
        # original error is the one that goes on, not the second.
        try:
            _finish(segment, {"status": "failed", "reason": str(exc)[:2000]})
        except Exception:  # noqa: BLE001 - the work's own error matters more
            log.exception("could not record the failed attempt %s", segment)
        raise ApplicationError(str(exc)[:2000], type=type(exc).__name__,
                               non_retryable=True) from exc
    _finish(segment, outcome)
    return outcome
