"""Ending an action run that did not succeed.

An action run is `running` from the moment a task opens it, and the only thing that ever moved one out of `running` was sealing the
version it produced, so a run that failed stayed `running` for ever: nine days old, with no end time, on a live database. Nothing
read that as a problem until a storage key came to depend on the run ending.

A run's own read key (grants.identity_for_task) lasts while the run is running, inside the lifetime of its task credential. This module
makes "running" mean what it says: a failed task marks its run failed at once, and anything still running past the credential's
lifetime is marked failed too, because past that moment the credential is refused and nothing can finish the run.
"""

from __future__ import annotations

from . import db, logs, task_credential

log = logs.get_logger("task_runs")

LIMIT_SECONDS = task_credential.DEFAULT_TTL_SECONDS

# A closed organisation takes no writes (refuse_write_to_retired_tenant), so its runs are left exactly as they were.
_NOT_RETIRED = "tenant_id not in (select id from tenant where purpose = 'retired')"


def fail_run(action_run_id: str, reason: str) -> bool:
    """Mark a run that is still running as failed, with the reason. Returns whether anything changed."""
    row = db.one(
        f"""update action_run set status = 'failed', failure_reason = %s, ended_at = now()
             where id = %s and status = 'running' and {_NOT_RETIRED}
         returning id""",
        (reason[:500], action_run_id),
    )
    return bool(row)


def expire_stale() -> int:
    """Mark every run still running past the task credential's lifetime as failed. Returns how many."""
    rows = db.all_rows(
        f"""update action_run
               set status = 'failed', ended_at = now(),
                   failure_reason = 'no result within ' || (%s / 3600) || ' hours, so the task credential had expired'
             where status = 'running' and started_at <= now() - make_interval(secs => %s) and {_NOT_RETIRED}
         returning id""",
        (LIMIT_SECONDS, LIMIT_SECONDS),
    )
    if rows:
        log.info("ended runs that ran past their limit", extra={"count": len(rows)})
    return len(rows)
