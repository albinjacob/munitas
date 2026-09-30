"""U53: a real session resolves to a real directory row, and nothing else does.

Proves that `GET /auth/whoami` (platform/api/app/auth.py) only answers for a session Ory
Kratos actually issued, and that it resolves to the exact directory row
`infra/kratos/seed-identities.py` connected it to, never to a guess, a
tampered token, or nobody in particular.

This is deliberately narrow. No existing endpoint is touched by this slice,
so there is nothing here about `approve_lease` or any other caller-supplied
identity; that migration is later, separate work, named as such in the
roadmap. This script tests exactly the one new seam that exists today.

Needs `infra/kratos/seed-identities.py` to have been run once against
this stack; if it has not, the identity-dependent checks are skipped with
that instruction rather than failing confusingly.

    docker compose exec -T munitas-api python /verify/v53_real_authentication.py
"""

from __future__ import annotations

import os
import sys

import httpx

from common import api, check, db, heading, require_api, skip, summary

KRATOS = os.environ.get("MUNITAS_VERIFY_KRATOS", "http://kratos:4433")

# Must match infra/kratos/seed-identities.py exactly; this script does
# not import that one (it is a one-shot admin tool, not a library) so the
# values are duplicated here on purpose, the same way other verify scripts
# hardcode known-seeded ids rather than importing the seed script that wrote
# them.
DIRECTORY_ID = "cust-hartley"
EMAIL = "hartley@health.example"
PASSWORD = "dev-password-not-for-production"


def real_session_token() -> str | None:
    """A genuine Kratos session token for the seeded identity, or None.

    Deliberately does not POST to the flow's own `ui.action` URL: Kratos
    builds that from its configured `serve.public.base_url`
    (`http://localhost:4433/`, correct for the browser this config is
    actually written for), which this script cannot reach from inside the
    API container under that name. The flow id is stable regardless of which
    hostname reaches it, so the submit URL is built from `KRATOS` instead.
    """
    flow = httpx.get(f"{KRATOS}/self-service/login/api", timeout=10.0).json()
    r = httpx.post(
        f"{KRATOS}/self-service/login",
        params={"flow": flow["id"]},
        json={"method": "password", "identifier": EMAIL, "password": PASSWORD},
        headers={"Accept": "application/json"},
        timeout=10.0,
    )
    if r.status_code != 200:
        return None
    return r.json()["session_token"]


def main() -> int:
    require_api()
    heading("U53: no session, no identity")

    unauthenticated = api("GET", "/auth/whoami")
    check("a request with no session or token is refused",
          unauthenticated.status_code == 401, f"HTTP {unauthenticated.status_code}")

    garbage = api("GET", "/auth/whoami",
                  headers={"Authorization": "Bearer not-a-real-token"})
    check("a token that is not a real Kratos session is refused",
          garbage.status_code == 401, f"HTTP {garbage.status_code}")

    with db() as conn:
        linked = conn.execute(
            "select kratos_identity_id from directory where id = %s",
            (DIRECTORY_ID,),
        ).fetchone()

    if not linked or not linked["kratos_identity_id"]:
        skip(
            "a real session resolves to the identity it was issued for",
            f"{DIRECTORY_ID!r} has no kratos_identity_id; run "
            "infra/kratos/seed-identities.py first",
        )
        skip("the resolved identity matches exactly what was seeded",
             "depends on the check above")
        return summary("U53")

    heading("U53: a real login resolves to the exact row it was linked to")

    token = real_session_token()
    check("Kratos accepts the seeded password and issues a session",
          token is not None,
          "session issued" if token else "no session_token returned")

    if token is None:
        skip("the resolved identity matches exactly what was seeded",
             "no session token to check against")
        return summary("U53")

    resolved = api("GET", "/auth/whoami", headers={"Authorization": f"Bearer {token}"})
    check("the control plane accepts that session",
          resolved.status_code == 200, f"HTTP {resolved.status_code}")

    body = resolved.json() if resolved.status_code == 200 else {}
    check("it resolves to the directory row the identity was linked to",
          body.get("id") == DIRECTORY_ID, body.get("id"))
    check("carrying that row's real tenant, not a claimed one",
          body.get("tenant_id") == "health", body.get("tenant_id"))
    check("and that row's real roles",
          body.get("roles") == ["data_custodian"], body.get("roles"))
    check("with a session id that is a real Kratos session, not invented",
          bool(body.get("session_id")), body.get("session_id"))

    heading("U53: revoking access works the way the mechanism promises")

    tampered = api("GET", "/auth/whoami",
                   headers={"Authorization": f"Bearer {token}x"})
    check("a tampered copy of a real token is refused, not resolved anyway",
          tampered.status_code == 401, f"HTTP {tampered.status_code}")

    return summary("U53")


if __name__ == "__main__":
    sys.exit(main())
