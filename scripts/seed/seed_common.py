"""A real Kratos session for the demo seed scripts.

`request_lease` and the other human-decision endpoints
(platform/api/app/auth.py's `current_session`) require a real session now,
regardless of caller. scripts/seed/seed-health-example.py and scripts/seed/seed-finance-example.py both
file lease requests as a human principal, which means both need one of
these, not just verify/ and the console's Playwright suite.

Identities are read from infra/kratos/identities.json rather than
duplicated as a literal map here -- the same file infra/kratos/
seed-identities.py itself reads from, and the same choice
web/tests/auth-helpers.ts made for the same reason: a tenant added there
needs no matching edit here.

    from seed_common import bearer_for
    bearer_for("sam-researcher")
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ports_config import PORTS  # noqa: E402

KRATOS = os.environ.get("MUNITAS_SEED_KRATOS", f"http://localhost:{PORTS['kratos_public']}")
PASSWORD = "dev-password-not-for-production"

_IDENTITIES_PATH = Path(__file__).resolve().parents[2] / "infra" / "kratos" / "identities.json"


def _load_emails() -> dict[str, str]:
    data = json.loads(_IDENTITIES_PATH.read_text(encoding="utf-8"))
    return {
        identity["directory_id"]: identity["email"]
        for group in data["tenants"]
        for identity in group["identities"]
    }


_EMAIL_BY_DIRECTORY_ID = _load_emails()


def bearer_for(directory_id: str) -> dict[str, str]:
    """A real Kratos session's bearer header for a seeded identity."""
    email = _EMAIL_BY_DIRECTORY_ID.get(directory_id)
    if not email:
        raise RuntimeError(
            f"{directory_id!r} has no seeded email in "
            f"infra/kratos/identities.json"
        )
    flow = httpx.get(f"{KRATOS}/self-service/login/api", timeout=10.0).json()
    r = httpx.post(
        f"{KRATOS}/self-service/login",
        params={"flow": flow["id"]},
        json={"method": "password", "identifier": email, "password": PASSWORD},
        headers={"Accept": "application/json"},
        timeout=10.0,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"Kratos login failed for {directory_id!r} ({email}): "
            f"HTTP {r.status_code} {r.text[:200]}"
        )
    token = r.json()["session_token"]
    return {"Authorization": f"Bearer {token}"}


def expect(response, *allowed: int, doing: str):
    """Fail here, loudly, rather than print a sentence that is not true.

    Both seed scripts used to print what they intended ("still pending",
    "promoted to published") beside the status code they actually got, which
    reads as success at a glance whatever happened. A rebuild of `finance`
    printed two pending access requests that were never created: both calls
    returned 401, because the tenant's directory rows had been recreated
    without the `kratos_identity_id` that connects a login to a person, and
    nothing checked. A seed that reports states it failed to create teaches
    the reader the wrong thing about the platform, which is the same reason
    these scripts go through the API instead of writing rows directly.
    """
    if response.status_code not in allowed:
        wanted = " or ".join(str(code) for code in allowed)
        raise SystemExit(
            f"\nSeeding stopped: {doing} returned HTTP "
            f"{response.status_code}, expected {wanted}.\n"
            f"  {response.text[:300]}\n"
            "Nothing further was seeded. The tenant is now half-built; "
            "rebuild it with scripts/seed/reseed-tenant.ps1 rather than re-running this "
            "script, which does nothing once its datasets exist."
        )
    return response
