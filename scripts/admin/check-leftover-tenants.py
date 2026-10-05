"""Fail if test tenants are still there after a verification run and its cleanup.

Every run of the verification suite makes tenants of its own, and a check that forgets to remove one leaves it for good: U52, U63 and U65 each did,
and the cost was found by hand, a long time later. This is the last step of `run-verification.ps1`, after the sweep that removes disposable probe
tenants, so what it sees is what the cleanup could not remove, and a leak shows up on the run that caused it.

A tenant is a leftover when it is not one of the standing set below and it is one of:

  * declared disposable (`scratch`), or
  * a `canary` tenant (the suite's own purpose for fixtures), or
  * named like a test fixture, whatever its purpose. (The throwaway organisations the closing and export checks build start as `production`,
    because that is what closing needs, so their name is what gives them away.)

Any other `production` tenant is never flagged, so onboarding a real organisation does not fail the suite. A tenant the suite is meant to keep
belongs in STANDING, on purpose: adding one there is the decision that it is permanent.

    .venv\\Scripts\\python.exe scripts/admin/check-leftover-tenants.py

Exit codes: 0 nothing left over, 1 leftovers (named, with what each holds), 2 it could not look (so nothing is known).
"""

from __future__ import annotations

import os
import sys

import psycopg
from psycopg.rows import dict_row

# The tenants that are meant to be there: the three organisations the platform is demonstrated with, the suite's own `canary` tenant, and the two
# that prove storage on real Cloudflare R2 (U57, which keeps them between runs).
STANDING = frozenset({"health", "finance", "harbour", "canary", "r2-probe-a", "r2-probe-b"})

# The names the suite gives tenants it makes. A tenant with one of these is a fixture, not somebody's organisation.
TEST_PREFIXES = (
    "storage-probe-", "ingest-probe-", "legacy-probe-", "scratch-probe-", "scratch-empty-", "verify-retired-", "pipeline-probe-",
    "verify-closing-", "scratch-", "u123-",
)


def why_left_over(tenant: dict) -> str | None:
    """The reason this tenant counts as a leftover, or None when it does not. Pure, so a check can prove each rule."""
    name, purpose = tenant["id"], tenant["purpose"]
    if name in STANDING:
        return None
    if purpose == "scratch":
        return "declared disposable (scratch) and not removed by the sweep"
    if purpose == "canary":
        return "a canary fixture tenant that is not one of the standing ones"
    if name.startswith(TEST_PREFIXES):
        return f"named like a test fixture and still there (purpose {purpose!r})"
    return None


def leftovers(tenants: list[dict]) -> list[tuple[dict, str]]:
    return [(t, reason) for t in tenants if (reason := why_left_over(t))]


def dsn() -> str:
    if os.environ.get("PG_DSN"):
        return os.environ["PG_DSN"]
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from ports_config import PORTS  # noqa: E402

    return f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform"


def read_tenants(conn) -> list[dict]:
    return conn.execute(
        """select t.id, t.purpose, t.created_at,
                  (select count(*) from dataset d where d.tenant_id = t.id) as datasets,
                  (select count(*) from dataset_version v where v.tenant_id = t.id and v.sealed) as sealed
             from tenant t order by t.created_at, t.id"""
    ).fetchall()


def main() -> int:
    try:
        with psycopg.connect(dsn(), row_factory=dict_row, connect_timeout=10) as conn:
            tenants = read_tenants(conn)
    except Exception as exc:  # noqa: BLE001 - reported with the exit code; not knowing is not a pass
        print(f"Could not look for leftover tenants ({type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}). Nothing is known.")
        return 2

    left = leftovers(tenants)
    standing = [t["id"] for t in tenants if t["id"] in STANDING]
    if not left:
        print(f"No leftover tenants. {len(tenants)} tenant(s), all expected ({', '.join(standing)} and any customer organisation).")
        return 0

    print(f"{len(left)} leftover tenant(s) after the run:")
    for t, reason in left:
        print(f"  {t['id']}  purpose={t['purpose']}  created {t['created_at']:%Y-%m-%d %H:%M}  "
              f"{t['datasets']} dataset(s), {t['sealed']} sealed version(s)")
        print(f"      {reason}")
    print("\nThe check that made each of these should remove it itself. A disposable one is also removed by `scripts/admin/tidy-probes.py --apply`;")
    print("one that holds sealed versions and is not disposable needs `scripts/admin/nuke-tenant.py`, which asks for the name typed back.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
