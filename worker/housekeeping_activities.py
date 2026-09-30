"""The one activity a scheduled housekeeping sweep runs.

Imports `run_sweep` from `scripts/admin/tidy-probes.py` by path, the same
trick that script already uses to load `nuke-tenant.py`, rather than
reimplementing the selection and deletion logic here. A hand run through the
CLI and this scheduled run must agree on what counts as a probe tenant, and
the only way to guarantee that is for both to call the same function.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

from temporalio import activity

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
