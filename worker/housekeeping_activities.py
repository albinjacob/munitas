"""The activities the scheduled housekeeping sweeps run.

Imports `run_sweep` from `scripts/admin/tidy-probes.py` by path, the same
trick that script already uses to load `nuke-tenant.py`, rather than
reimplementing the selection and deletion logic here. A hand run through the
CLI and this scheduled run must agree on what counts as a probe tenant, and
the only way to guarantee that is for both to call the same function.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from temporalio import activity
from temporalio.client import Client

from . import config

log = logging.getLogger("munitas.worker")

_SCRIPT = Path(__file__).parent.parent / "scripts" / "admin" / "tidy-probes.py"
_spec = importlib.util.spec_from_file_location("tidy_probes", _SCRIPT)
tidy_probes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tidy_probes)


@activity.defn
def sweep_stale_probes(min_age_hours: float) -> dict:
    """Delete probe tenants older than `min_age_hours`, and log what happened.

    Blocking (Postgres, S3), so this is a plain `def`, which is what puts it
    in the worker's thread pool rather than the event loop -- the exact bug
    worker/main.py's own module docstring describes for the pipeline
    activities, avoided here from the start rather than found the same way.
    """
    result = tidy_probes.run_sweep(min_age_hours=min_age_hours, apply=True)
    log.info(
        "housekeeping sweep: %d probe tenant(s) found, %d removed, "
        "%d object(s) freed (min age %sh)",
        len(result["found"]), len(result["removed"]),
        result["freed_objects"], min_age_hours,
    )
    for c in result["found"]:
        removed = c["id"] in result["removed"]
        log.info("  %s purpose=%s created=%s versions=%d -> %s",
                  c["id"], c["purpose"], c["created_at"], c["versions"],
                  "removed" if removed else "left")
    return result


_CLOSE_SCRIPT = Path(__file__).parent.parent / "scripts" / "admin" / "close-finished-pipeline-runs.py"
_close_spec = importlib.util.spec_from_file_location("close_finished_pipeline_runs", _CLOSE_SCRIPT)
close_finished_pipeline_runs = importlib.util.module_from_spec(_close_spec)
_close_spec.loader.exec_module(close_finished_pipeline_runs)


@activity.defn
def close_stopped_pipeline_runs() -> dict:
    """End every pipeline run the register shows as running that Temporal says has stopped.

    The same `close_finished` function the command line runs, so a hand run and this scheduled run cannot
    disagree about what counts as stopped. A plain `def`, for the same reason as above, and it runs its own
    event loop in the pool thread so the worker's main loop is never blocked.

    Raises when Temporal cannot be reached, so the failure shows in Temporal's own list of workflows rather than
    passing as a run that found nothing: not knowing is not evidence that a run stopped, and a quiet success
    would hide that the register was not checked.
    """
    async def go() -> dict:
        client = await asyncio.wait_for(Client.connect(config.TEMPORAL), timeout=10)
        dsn = os.environ.get("PG_DSN", close_finished_pipeline_runs.PG_DSN)
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            return await close_finished_pipeline_runs.close_finished(client, conn, apply=True)

    result = asyncio.run(go())
    log.info("pipeline run sweep: %d open, %d stopped, %d still running, %d closed %s",
             result["open"], result["stopped"], result["running"], result["closed"], result["by_source"])
    for refused in result["refused"]:
        log.warning("pipeline run sweep: the database refused to close %s", refused)
    return result
