"""The one Docker-touching activity of a derivation, for the sandbox worker.

Kept apart from derivation_workflow.py, which the host worker imports, because
this file needs the Docker client and the host worker has none. See
sandbox_worker.py for why the split exists.
"""

from __future__ import annotations

import contextvars
import threading

from temporalio import activity
from temporalio.exceptions import ApplicationError

from . import derivation_sandbox

HEARTBEAT_EVERY_SECONDS = 20


def _heartbeat_until(done: threading.Event) -> None:
    """Tell Temporal every few seconds that this activity is still alive.

    The activity blocks for minutes inside container waits, so it cannot
    heartbeat from its own thread. Without heartbeats a finished run whose
    completion never reached Temporal (a network error at the wrong moment)
    looks identical to a run still going, and is waited on until the whole
    activity timeout. With them, the workflow's heartbeat timeout notices in
    seconds and the activity runs again, which is safe: the run is recorded
    under a fixed key, the upload overwrites the same object and sealing reuses
    a version that already exists.
    """
    while not done.wait(HEARTBEAT_EVERY_SECONDS):
        try:
            activity.heartbeat()
        except Exception:  # noqa: BLE001 - a missed heartbeat is retried at the next one
            pass


@activity.defn
def run_derivation_sandboxed(params: dict) -> dict:
    """Run one confirmed derivation. A failure that retrying cannot fix is
    raised as DerivationFailed, which the workflow reports to the person."""
    done = threading.Event()
    beat = threading.Thread(
        target=contextvars.copy_context().run, args=(_heartbeat_until, done), daemon=True)
    beat.start()
    try:
        return derivation_sandbox.run(params)
    except derivation_sandbox.DerivationFailed as exc:
        raise ApplicationError(str(exc), type="DerivationFailed", non_retryable=True) from exc
    finally:
        done.set()
