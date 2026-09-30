"""The activator: making allowed storage access take effect, and resuming the
runs that were waiting for it.

An allowed decision is active once a successful print of the permissions
document started after it (grants.is_active). A request prints straight away,
so this loop only matters when that print failed: it retries, with growing
gaps, until a print succeeds, then resumes every run that parked waiting for
its access. Nobody has to activate or resume anything by hand. An
administrator is told when printing keeps failing, which is an outage to fix,
never a queue of requests to work through.

Everything it decides is read from storage_permission_print and agent_run on
every tick, so a restart loses nothing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from . import agents, grants, logs, temporal_client

log = logs.get_logger("activation")

TICK_SECONDS = 10


def _should_print(status: dict) -> bool:
    if status["failing"]:
        due = status["retry_due_at"]
        return bool(status["retryable"]) and due is not None and due <= datetime.now(timezone.utc)
    # No print has ever succeeded: a fresh install whose start-up print could
    # not even be recorded, because the database was still coming up. Nothing
    # else would ever ask for one.
    if status["last_success_at"] is None:
        return True
    return status["unserved"] > 0


async def tick(state: dict) -> dict:
    """One pass: print if something is waiting and a retry is due, resume
    the runs a successful print has served, and report a change of alert.

    Also connects to Temporal if the API could not at start-up: Temporal may
    still have been starting, and without a client no run can be resumed or
    started at all.
    """
    if not temporal_client.connected():
        await temporal_client.connect()
        if temporal_client.connected():
            log.info("connected to Temporal after start-up")
    status = await asyncio.to_thread(grants.activation_status)
    if _should_print(status):
        try:
            await asyncio.to_thread(grants.reconcile, trigger="activator")
        except Exception:
            # Recorded in storage_permission_print by reconcile itself; the
            # alert below is how anyone hears about it.
            pass
        status = await asyncio.to_thread(grants.activation_status)

    resumed = await agents.resume_activated_runs()
    if resumed:
        log.info("resumed runs whose storage access is now active",
                 extra={"count": len(resumed)})

    if status["alert"] and not state.get("alerting"):
        log.error(
            "new storage access is not taking effect",
            extra={"reason": status["reason"],
                   "state": "retrying" if status["retryable"] else "needs a person",
                   "count": status["parked_runs"]})
    elif state.get("alerting") and not status["failing"]:
        log.info("storage access is taking effect again")
    return {"alerting": status["alert"]}


async def run_forever() -> None:
    state: dict = {}
    while True:
        try:
            state = await tick(state)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A tick that breaks (the database restarting, say) must not end
            # the loop: the next tick reads everything afresh.
            log.exception("activator tick failed; trying again next tick")
        await asyncio.sleep(TICK_SECONDS)
