"""Database access. One pool, opened at startup."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timezone

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

pool = ConnectionPool(config.PG_DSN, min_size=1, max_size=10, open=False)


@contextmanager
def cursor(commit: bool = False):
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            yield cur
        if commit:
            conn.commit()


def one(sql: str, params: tuple = ()) -> dict | None:
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def all_rows(sql: str, params: tuple = ()) -> list[dict]:
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def policy_lease(r: dict) -> dict:
    """One access_lease row, shaped as the policy expects a lease.

    Shared by every caller that hands leases to OPA. The timestamp format is
    the detail that drifts: policy compares these strings, so `+00:00` where
    `Z` was expected reads as a lease that does not apply.
    """
    return {
        "id": str(r["id"]),
        "dataset_version": str(r["dataset_version_id"]),
        "purpose": r["purpose"],
        "pattern": r["pattern"],
        "approved_by": r["approved_by"],
        "revoked": r["revoked"],
        # None for a standing lease, which policy's `lease_current` treats
        # as never expiring rather than as missing data.
        "expires_at": (
            r["expires_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            if r["expires_at"] else None
        ),
    }


def active_leases(principal: str, version_id: str) -> list[dict]:
    """Leases for one principal against one version, shaped as OPA expects them.

    Lives here rather than beside either caller because two of them ask the same
    question: the credential endpoint, when access is actually being attempted,
    and the agent preflight, when the console wants to know whether it would be.
    A second copy would drift, and the timestamp format is exactly the kind of
    detail that drifts silently: policy compares these strings, so `+00:00`
    where `Z` was expected reads as a lease that does not apply rather than as a
    formatting difference.

    Expired and revoked leases are returned rather than filtered out. OPA
    decides, and it can only answer "your lease expired" if it is shown the
    expired lease.
    """
    rows = all_rows(
        """select id, dataset_version_id, purpose, pattern, approved_by, revoked, expires_at
           from access_lease
           where principal = %s and dataset_version_id = %s""",
        (principal, version_id),
    )
    return [policy_lease(r) for r in rows]


def execute(sql: str, params: tuple = ()) -> dict | None:
    """Run a statement and return its RETURNING row, if any.

    Returns None where the statement matched nothing. That is a real outcome
    here rather than an anomaly: the immutability rules rewrite forbidden
    UPDATEs and DELETEs into no-ops, so callers must treat an empty result as
    "the database refused" instead of assuming success.
    """
    with cursor(commit=True) as cur:
        cur.execute(sql, params)
        if cur.description is None:
            return None
        return cur.fetchone()
