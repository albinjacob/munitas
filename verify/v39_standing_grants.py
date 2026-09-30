"""U39: a workload's normal work does not require re-approval every 30 days.

`ttl_hours` on a lease caps at 720 (30 days), and nothing renews. For a
workload whose ordinary, recurring job genuinely needs something below its
role floor, that is approval fatigue by another name, not a safeguard: nobody
is deciding anything new on day 31, they are just clicking approve again.

A standing lease lasts until revoked instead. Restricted to workloads only --
a human's access must always stay bounded and re-justified, and that is the
one invariant this feature must not weaken, so it is checked here two ways:
through the API, and by writing to the database directly, because the
guarantee that matters is the one the API cannot be routed around.

    docker compose exec -T munitas-api python /verify/v39_standing_grants.py
"""

from __future__ import annotations

import sys
import uuid

import psycopg

from common import (CUSTODIAN, ENGINEER, PG_DSN, RESEARCHER, TRAINER, api,
                    bearer_for, check, db, fixture_contract,
                    fixture_department, fixture_tenant, fixture_version,
                    heading, require_api, summary)


def db_one(sql: str, params: tuple) -> dict | None:
    with db() as conn:
        return conn.execute(sql, params).fetchone()


def main() -> int:
    require_api()

    tenant = fixture_tenant()
    contract = fixture_contract(tenant)
    version = fixture_version(tenant, contract, "RAW")
    fixture_department(tenant, version["dataset_id"])

    heading("U39: a workload may request a standing lease")

    req = api("POST", "/leases/requests", json={
        "tenant_id": tenant,
        "principal": TRAINER,
        "dataset_version_id": version["id"],
        "purpose": "nightly training run",
        "justification": "recurring job, cleared once rather than every 30 days",
        "standing": True,
    }, headers=bearer_for(ENGINEER))
    check("a standing request is accepted with no ttl_hours",
          req.status_code == 201, f"HTTP {req.status_code}")
    request_id = req.json()["id"] if req.status_code == 201 else None

    if request_id:
        row = db_one(
            "select standing, requested_ttl_hours from lease_request where id = %s",
            (request_id,),
        )
        check("the request row records standing with no ttl",
              row is not None and row["standing"] is True
              and row["requested_ttl_hours"] is None,
              str(row))

    heading("U39: a human cannot be granted standing access")

    human_req = api("POST", "/leases/requests", json={
        "tenant_id": tenant,
        "principal": RESEARCHER,
        "dataset_version_id": version["id"],
        "purpose": "should never be standing",
        "justification": "a human's access must always stay bounded",
        "standing": True,
    }, headers=bearer_for(RESEARCHER))
    check("a standing request for a human principal is refused",
          human_req.status_code == 400, f"HTTP {human_req.status_code}")

    heading("U39: approving it grants a lease with no expiry")

    if request_id:
        approved = api(
            "POST", f"/leases/requests/{request_id}/approve",
            headers=bearer_for(CUSTODIAN),
        )
        check("the standing request is approved",
              approved.status_code == 201, f"HTTP {approved.status_code}")
        body = approved.json() if approved.status_code == 201 else {}
        check("the response says standing, with no expires_at",
              body.get("standing") is True and body.get("expires_at") is None,
              str(body))

        row = db_one(
            "select expires_at from access_lease where id = %s",
            (body.get("lease_id"),),
        )
        check("the lease row itself has no expiry",
              row is not None and row["expires_at"] is None,
              str(row))

        heading("U39: it grants access, with a reason that says standing")

        cred = api("POST", "/credentials", json={
            "principal": TRAINER,
            "principal_kind": "workload",
            "roles": ["training_job"],
            "tenant_id": tenant,
            "dataset_version_id": version["id"],
            "purpose": "nightly training run",
        })
        check("the standing lease grants access",
              cred.status_code == 200, f"HTTP {cred.status_code}: {cred.text[:200]}")
        reasons = cred.json().get("reasons", []) if cred.status_code == 200 else []
        check("the reason names it as a standing lease, not a bounded one",
              any("standing lease" in r for r in reasons), str(reasons))

        heading("U39: /leases reports it as active and standing")

        leases = api("GET", "/leases", params={
            "tenant_id": tenant, "principal": TRAINER, "active_only": True,
        }, headers=bearer_for(ENGINEER))
        matches = [l for l in leases.json()["leases"] if l["id"] == body.get("lease_id")]
        check("the active-only listing includes it",
              bool(matches), f"{len(matches)} matches")
        check("and marks it standing",
              bool(matches) and matches[0]["standing"] is True,
              str(matches[0]) if matches else "not found")

        summary_counts = api("GET", "/summary", params={"tenant_id": tenant},
                             headers=bearer_for(ENGINEER)).json()
        check("the platform summary counts it as an active lease",
              summary_counts.get("active_leases", 0) >= 1,
              str(summary_counts.get("active_leases")))

        heading("U39: revoking requires the asset's own authority")

        unauthorized = api("POST", f"/leases/{body['lease_id']}/revoke",
                           headers=bearer_for(RESEARCHER))
        check("a session with no relationship to this asset cannot revoke it",
              unauthorized.status_code == 403, f"HTTP {unauthorized.status_code}")

        no_session = api("POST", f"/leases/{body['lease_id']}/revoke")
        check("no session at all is refused", no_session.status_code == 401,
              f"HTTP {no_session.status_code}")

        heading("U39: it is still revocable")

        revoked = api("POST", f"/leases/{body['lease_id']}/revoke",
                      headers=bearer_for(CUSTODIAN))
        check("the owning custodian can revoke it", revoked.status_code == 200,
              f"HTTP {revoked.status_code}")

        row = db_one(
            "select revoked_by, revoked_at from access_lease where id = %s",
            (body["lease_id"],),
        )
        check("revoked_by/revoked_at record who actually revoked it",
              row is not None and row["revoked_by"] == CUSTODIAN
              and row["revoked_at"] is not None,
              str(row))

        refused = api("POST", "/credentials", json={
            "principal": TRAINER,
            "principal_kind": "workload",
            "roles": ["training_job"],
            "tenant_id": tenant,
            "dataset_version_id": version["id"],
            "purpose": "nightly training run",
        })
        check("access is refused after revocation",
              refused.status_code == 403, f"HTTP {refused.status_code}")
        refused_reasons = (
            refused.json().get("detail", {}).get("reasons", [])
            if refused.status_code == 403 else []
        )
        check("the refusal names revocation, not expiry",
              any("revoked" in r for r in refused_reasons)
              and not any("expired" in r for r in refused_reasons),
              str(refused_reasons))

    heading("U39: the database itself refuses a standing lease for a human")

    # Bypassing the API entirely. The guarantee that matters is the one a
    # future endpoint, or a hand-written script, cannot route around.
    raised = False
    detail = ""
    try:
        with psycopg.connect(PG_DSN, autocommit=True) as conn:
            conn.execute(
                """insert into access_lease
                     (id, tenant_id, principal, dataset_version_id, purpose,
                      approved_by, expires_at)
                   values (%s, %s, %s, %s, %s, %s, null)""",
                (str(uuid.uuid4()), tenant, RESEARCHER, version["id"],
                 "direct insert probe", CUSTODIAN),
            )
    except Exception as exc:  # psycopg raises its own exception hierarchy
        raised = True
        detail = str(exc)[:200]
    check("a direct SQL insert of a standing lease for a human raises",
          raised, detail)

    return summary("U39")


if __name__ == "__main__":
    sys.exit(main())
