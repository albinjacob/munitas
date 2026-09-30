"""U84: every tenant's pipeline_action workload is actually named `<tenant>-pipeline`.

Item 63 renamed `svc-pipeline` / `svc-fraud-pipeline` to `health-pipeline` /
`finance-pipeline` for exactly this reason: `worker/activities.py`'s
`_pipeline_principal(tenant)` *derives* the name a running pipeline asserts
to `/credentials` as `f"{tenant}-pipeline"` rather than reading it from a
static config value (item 64 fixed the single-worker-serves-every-tenant bug
that a static value caused). Derivation only works if the real seeded
identity actually follows the convention it assumes -- nothing before this
script checked that a future tenant's seed file gets the name right, so a
typo or an old-pattern copy-paste would sit undetected until a real pipeline
run failed with a confusing 403, the same shape of failure item 65's live
run diagnosed by hand.

This does not check that every tenant *has* a pipeline_action workload --
plenty legitimately do not yet -- only that whichever ones exist are named
the one way `_pipeline_principal()` will ever look for them, and that no
tenant has drifted into having more than one (item 63's own conclusion: the
identity represents the tenant's worker process, not any one pipeline
definition, so more than one is itself a naming-adjacent mistake).

    docker compose exec -T munitas-api python /verify/v81_pipeline_principal_naming.py
"""

from __future__ import annotations

import sys

from common import check, db, heading, require_api, summary


def main() -> int:
    require_api()

    heading("U84: every registered pipeline_action workload follows <tenant>-pipeline")

    with db() as conn:
        rows = conn.execute(
            """select id, tenant_id
                 from directory
                where kind = 'workload'
                  and roles @> array['pipeline_action']
                order by tenant_id, id"""
        ).fetchall()

    if not rows:
        check("at least one pipeline_action workload exists to check", False,
              "none found at all -- nothing to verify, which is itself worth noticing")
        return summary("U84: pipeline_action naming convention")

    by_tenant: dict[str, list[str]] = {}
    for row in rows:
        by_tenant.setdefault(row["tenant_id"], []).append(row["id"])

    for tenant_id, ids in by_tenant.items():
        expected = f"{tenant_id}-pipeline"
        check(f"{tenant_id}: exactly one pipeline_action workload, not {len(ids)}",
              len(ids) == 1, f"found {ids}")
        check(f"{tenant_id}: its pipeline_action workload is named {expected!r}",
              expected in ids, f"found {ids}, expected {expected!r} among them")

    return summary("U84: pipeline_action naming convention")


if __name__ == "__main__":
    sys.exit(main())
