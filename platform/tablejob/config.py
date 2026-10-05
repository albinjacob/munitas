"""What the table worker is told by its environment. Deliberately small: it holds no database address, no master key and no
storage key. The one storage key it ever uses is made for one job and handed over by the control plane for that job."""

from __future__ import annotations

import os
from dataclasses import dataclass

# Must be the same as table_jobs.TABLE_SHARED_QUEUE and table_jobs.dedicated_queue_name in the control plane.
SHARED_QUEUE = "munitas-table-write"


@dataclass(frozen=True)
class Config:
    api: str
    temporal: str
    # When set, this worker serves one organisation only, on that organisation's own line of work, and refuses a job of
    # any other. Left out, it serves the shared pool.
    tenant: str | None
    queue: str


def load() -> Config:
    tenant = (os.environ.get("MUNITAS_TABLE_TENANT") or "").strip() or None
    if os.environ.get("MUNITAS_TABLE_DEDICATED") and not tenant:
        raise RuntimeError("a dedicated table worker must be told which organisation it serves (MUNITAS_TABLE_TENANT)")
    queue = f"{SHARED_QUEUE}-{tenant}" if tenant else SHARED_QUEUE
    return Config(
        api=os.environ.get("MUNITAS_API", "http://munitas-api:8000").rstrip("/"),
        temporal=os.environ.get("TEMPORAL_ADDRESS", "temporal:7233"),
        tenant=tenant, queue=queue)
