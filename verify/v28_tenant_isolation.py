"""U28: tenant isolation holds at run time, not only in the policy.

The platform has always claimed that one organisation cannot reach another's
data, and there has always been a policy test asserting the rule. What there was
not, until there were two tenants carrying real traffic, was anything that tried
it against the running system. A rule that has only ever been tested against
itself is an assertion.

The check that matters is the second one. Refusing a request that admits to
being cross-tenant is easy. The question is what happens when the request claims
to belong to the organisation it is reaching into, because that is what an
attacker would send and, until this was written, it worked: the policy compared
the tenant named in the request rather than the one the principal is registered
in.

    docker compose exec -T munitas-api python /verify/v28_tenant_isolation.py
"""

from __future__ import annotations

import sys

from common import (RESEARCHER, api, bearer_for, check, db,
                    fixture_contract, fixture_tenant, fixture_version,
                    heading, require_api, summary)

# The demonstration organisation. Its people are seeded by
# infra/postgres/seed-organisation.sql and none of them belong to the canary
# tenant, which is the whole point of asking them for canary data.
OTHER = "health"
OTHER_PERSON = "sam-researcher"


def decisions_for(principal: str, version_id: str) -> list[dict]:
    with db() as conn:
        return conn.execute(
            """select allowed, reasons, phase from access_decision
               where principal = %s and dataset_version_id = %s
               order by at desc""",
            (principal, version_id),
        ).fetchall()


def main() -> int:
    require_api()

    canary = fixture_tenant()
    contract = fixture_contract(canary)
    # Published, so nothing but the tenant can be the reason for a
    # refusal. Against a raw version the class would refuse first and this whole
    # script would pass without testing anything.
    canary_version = fixture_version(canary, contract, "UNDER_REVIEW")
    api("POST", f"/dataset-versions/{canary_version['id']}/promote", json={
        "to_class": "PUBLISHED", "decided_by": "verify-suite",
        "decided_by_kind": "workload",
        "gate_evidence": {"note": "fixture for the isolation check"},
        "grant_roles": ["notebook_explore"],
    })

    other_version = db_one(
        """select dataset_version_id from version_class
           where tenant_id = %s and current_class = 'PUBLISHED' limit 1""",
        (OTHER,),
    )

    heading("U28: somebody from another organisation is refused")

    # Honest about where they are from. The easy case, and the one a policy test
    # already covers; here to prove the wiring reaches the policy at all.
    honest = api("POST", "/credentials", json={
        "principal": OTHER_PERSON,
        "principal_kind": "human",
        "roles": ["notebook_explore"],
        "tenant_id": OTHER,
        "dataset_version_id": canary_version["id"],
        "purpose": "curiosity",
    })
    check("a request that admits to being cross-tenant is refused",
          honest.status_code == 403, f"HTTP {honest.status_code}")
    honest_reasons = (honest.json().get("detail", {}).get("reasons", [])
                      if honest.status_code == 403 else [])
    check("and the refusal says it is about the organisation, not the class",
          any("tenant" in r for r in honest_reasons), "; ".join(honest_reasons)[:120])

    heading("U28: and claiming otherwise does not help")

    # The same person, now naming the tenant they are reaching into. Before the
    # principal's tenant was read from the directory this was granted.
    lying = api("POST", "/credentials", json={
        "principal": OTHER_PERSON,
        "principal_kind": "human",
        "roles": ["notebook_explore"],
        "tenant_id": canary,
        "dataset_version_id": canary_version["id"],
        "purpose": "curiosity",
    })
    check("a request claiming the tenant it is reaching into is still refused",
          lying.status_code == 403, f"HTTP {lying.status_code}")
    lying_reasons = (lying.json().get("detail", {}).get("reasons", [])
                     if lying.status_code == 403 else [])
    check("and refused for the same reason, so the claim was ignored",
          any("tenant" in r for r in lying_reasons), "; ".join(lying_reasons)[:120])

    heading("U28: the same holds in the other direction")

    if other_version:
        reverse = api("POST", "/credentials", json={
            "principal": RESEARCHER,
            "principal_kind": "human",
            "roles": ["notebook_explore"],
            "tenant_id": OTHER,
            "dataset_version_id": str(other_version["dataset_version_id"]),
            "purpose": "curiosity",
        })
        check("a canary principal is refused the other organisation's data",
              reverse.status_code == 403, f"HTTP {reverse.status_code}")
        reverse_reasons = (reverse.json().get("detail", {}).get("reasons", [])
                           if reverse.status_code == 403 else [])
        check("and for the tenant, not for anything else",
              any("tenant" in r for r in reverse_reasons),
              "; ".join(reverse_reasons)[:120])
    else:
        check("a canary principal is refused the other organisation's data", False,
              f"{OTHER} holds no published version to ask for. "
              "Run scripts/seed/seed-health-example.py.")
        check("and for the tenant, not for anything else", False, "not attempted")

    heading("U28: somebody from the right organisation is not refused")

    # Without this the checks above would pass on a platform that refuses
    # everybody, which proves nothing about isolation.
    inside = api("POST", "/credentials", json={
        "principal": RESEARCHER,
        "principal_kind": "human",
        "roles": ["notebook_explore"],
        "tenant_id": canary,
        "dataset_version_id": canary_version["id"],
        "purpose": "curiosity",
    })
    check("a principal in the owning organisation is granted the same version",
          inside.status_code == 200,
          f"HTTP {inside.status_code} {inside.text[:110]}")

    heading("U28: and cannot be described to them either")

    # Refusing access is not the same as refusing to say a thing exists.
    # These four endpoints issue no credential and serve no bytes, so
    # scoping them was never about the access boundary above, but about
    # anybody holding a version id being able to read its dataset name, its
    # class, its record count and its release history regardless.
    #
    # They are not all scoped the same way any more. `/dataset-versions/{id}`
    # and `/lineage/{id}` are deliberately left open with no session at all,
    # for internal callers (the pipeline, this verify suite) that carry no
    # Kratos login, and take an honest `tenant_id` query parameter as their
    # only scoping. `/dataset-versions/{id}/transitions` and
    # `/datasets/{id}/versions` were closed by item 24's read-endpoint audit:
    # they now require a real session and derive tenant from it, and a
    # `tenant_id` query parameter on them is silently ignored, not honoured
    # and not rejected. Proving isolation on those two means asking as a
    # real person from each organisation, not naming a tenant by hand.
    version_id = canary_version["id"]
    dataset_id = canary_version["dataset_id"]

    param_scoped = [
        ("the version itself", f"/dataset-versions/{version_id}"),
        ("its lineage", f"/lineage/{version_id}"),
    ]
    session_scoped = [
        ("its release history", f"/dataset-versions/{version_id}/transitions"),
        ("the versions in its dataset", f"/datasets/{dataset_id}/versions"),
    ]

    for label, path in param_scoped:
        r = api("GET", path, params={"tenant_id": OTHER})
        # A list answers with an empty list; a single record answers 404. Both
        # say the same thing: there is nothing here for you.
        empty = r.status_code == 404 or (r.status_code == 200 and r.json() == [])
        check(f"{label} is not visible to another organisation", empty,
              f"HTTP {r.status_code} {r.text[:60]}")

    # 404 and not 403, which matters more than it looks. A refusal confirms the
    # version exists, and existence is half of what somebody trying ids at
    # random was trying to learn.
    forbidden = api("GET", f"/dataset-versions/{version_id}",
                    params={"tenant_id": OTHER})
    check("and is reported as absent rather than as refused",
          forbidden.status_code == 404, f"HTTP {forbidden.status_code}")

    for label, path in param_scoped:
        r = api("GET", path, params={"tenant_id": canary})
        answered = r.status_code == 200 and r.json() not in ([], None)
        check(f"{label} still answers its own organisation", answered,
              f"HTTP {r.status_code}")

    # Omitting the tenant still answers, which is what keeps the pipeline, the
    # verification scripts and anything else internal working. It is also the
    # limit of this claim: there is no authentication, so nothing stops a caller
    # leaving it out. What this closes is the console showing one organisation
    # another's metadata, not a request somebody wrote by hand.
    unscoped = api("GET", f"/dataset-versions/{version_id}")
    check("omitting the organisation still answers, which is the limit of this",
          unscoped.status_code == 200, f"HTTP {unscoped.status_code}")

    # The session-scoped pair: a real health session asking about canary's
    # own version and dataset gets an empty list, not a 403 or a 404 -- the
    # same "nothing here for you" answer the pair above gives, just reached
    # by logging in as somebody rather than by naming a tenant.
    for label, path in session_scoped:
        r = api("GET", path, headers=bearer_for(OTHER_PERSON))
        check(f"{label} is not visible to another organisation's session",
              r.status_code == 200 and r.json() == [],
              f"HTTP {r.status_code} {r.text[:60]}")

    # A tenant_id in the query string is not merely useless here, it is
    # ignored outright: the same cross-tenant request with canary's own id
    # forged onto it still answers empty, proving the session decides this,
    # not anything the caller can type.
    for label, path in session_scoped:
        r = api("GET", path, params={"tenant_id": canary},
                headers=bearer_for(OTHER_PERSON))
        check(f"{label} ignores a forged tenant_id from another organisation's session",
              r.status_code == 200 and r.json() == [],
              f"HTTP {r.status_code} {r.text[:60]}")

    for label, path in session_scoped:
        r = api("GET", path, headers=bearer_for(RESEARCHER))
        answered = r.status_code == 200 and r.json() not in ([], None)
        check(f"{label} still answers for a session in its own organisation",
              answered, f"HTTP {r.status_code}")

    # No session at all is refused outright on this pair, unlike the
    # deliberately-open pair above: these two carry no internal-caller
    # exception, since item 24 closed them precisely because nothing about
    # them needed to stay reachable without a login.
    for label, path in session_scoped:
        r = api("GET", path)
        check(f"{label} refuses a caller with no session at all",
              r.status_code == 401, f"HTTP {r.status_code}")

    heading("U28: and cannot be asked for either")

    # Filing the request is refused, not just granting it. The tenant on a
    # lease_request row decides whose queue the request appears in, so a caller
    # who could set it would put work in front of a custodian in another
    # organisation, naming a version that custodian cannot even see. The policy
    # would refuse the approval in the end, which is too late to be useful.
    #
    # 404 rather than 403, for the reason the detail endpoints use it: a 403
    # confirms the version exists, which is half of what the caller wanted.
    asked = api("POST", "/leases/requests", json={
        "tenant_id": canary,
        "principal": OTHER_PERSON,
        "dataset_version_id": canary_version["id"],
        "purpose": "read another organisation's data",
        "justification": "should never be filed at all",
        "ttl_hours": 1,
    }, headers=bearer_for(OTHER_PERSON))
    check("a request for another organisation's version is refused",
          asked.status_code == 404, f"HTTP {asked.status_code}")
    check("and refused as not found, so its existence is not confirmed",
          asked.status_code != 403, f"HTTP {asked.status_code}")

    filed = db_one(
        """select count(*) as n from lease_request
           where principal = %s and dataset_version_id = %s""",
        (OTHER_PERSON, canary_version["id"]),
    )
    check("and nothing was filed in any queue", (filed or {}).get("n") == 0,
          f"{(filed or {}).get('n')} rows")

    heading("U28: every attempt is in the audit log")

    outsider = decisions_for(OTHER_PERSON, canary_version["id"])
    check("both cross-tenant attempts were recorded", len(outsider) >= 2,
          f"{len(outsider)} decisions")
    check("and recorded as refusals",
          bool(outsider) and all(d["allowed"] is False for d in outsider),
          ", ".join(str(d["allowed"]) for d in outsider))
    check("with the reason kept, not just the outcome",
          bool(outsider) and all(d["reasons"] for d in outsider))

    insider = decisions_for(RESEARCHER, canary_version["id"])
    check("and the permitted request is recorded too, so the log is not all denials",
          any(d["allowed"] for d in insider), f"{len(insider)} decisions")

    return summary("U28")


def db_one(sql: str, params: tuple) -> dict | None:
    with db() as conn:
        return conn.execute(sql, params).fetchone()


if __name__ == "__main__":
    sys.exit(main())
