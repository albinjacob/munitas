"""The worker's one direct database connection, shared.

Both the native agent-run path (agent_run_activities.py) and the sandboxed one
(sandbox_run_activities.py) record a run's outcome straight to Postgres via
psycopg, the same accepted exception the pipeline already uses where the control
plane has no endpoint for a write yet.

This helper lived in agent_run_activities.py until the worker was split. The
sandboxed path now runs in a second, deliberately slim worker inside the WSL2
distro (worker/sandbox_worker.py), next to the Docker socket, and that worker
has no reason to carry agent_run_activities.py's LangGraph and agent.graph
imports. Keeping the connection here lets sandbox_run_activities.py import just
the connection, not the whole native-run module behind it.
"""

from __future__ import annotations

from contextlib import contextmanager

import psycopg

from . import config


@contextmanager
def _db():
    with psycopg.connect(config.PG_DSN, autocommit=True) as conn:
        yield conn
