"""The API's one connection to Temporal, used only to start workflows.

The API never does the slow work itself, only starts it and returns. The
worker (`worker/hf_ingest_workflow.py`), a separate process, is what
actually downloads anything; this module's whole job is handing it a task
and stepping out of the way, the same shape `worker/run_pipeline.py`
already uses from the other side.

A module-level client, set once at startup, matching how `db.pool` already
works: one connection, opened in `main.py`'s lifespan, read from here.
"""

from __future__ import annotations

import asyncio

from temporalio.client import Client

from . import config

CONNECT_TIMEOUT_SECONDS = 10

_client: Client | None = None
_error: str | None = None


async def connect() -> None:
    global _client, _error
    try:
        # Bounded: the activator calls this every tick while Temporal is
        # unreachable, and a connect that hangs would stall that loop too.
        _client = await asyncio.wait_for(Client.connect(config.TEMPORAL_ADDRESS),
                                         timeout=CONNECT_TIMEOUT_SECONDS)
        _error = None
    except Exception as exc:  # noqa: BLE001 - reported, not raised, on purpose
        # Not fatal to the whole API. Every other surface (registering,
        # uploading, sealing, leases) works without Temporal; only starting
        # a HuggingFace fetch needs it, and that endpoint reports the
        # problem itself rather than the API refusing to boot.
        _error = str(exc)


def get() -> Client:
    if _client is None:
        raise TemporalUnavailable(_error or "not connected")
    return _client


def connected() -> bool:
    return _client is not None


def error() -> str | None:
    return _error


class TemporalUnavailable(Exception):
    pass
