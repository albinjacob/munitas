"""V6: durable retry produces exactly one output.

Two halves. The first is that an action run keyed by an idempotency key stays
single no matter how many times it is submitted, which is testable now. The
second is that Temporal resumes a killed worker and reaches that same state.
That half needs a worker process to kill, so it cannot run inside this
container. It is verify/v6_durable_retry.py, run by hand on the host, and it
is not part of run_all.py.

Reporting the first as V6 would overstate it, so the second is skipped by name.
"""

from __future__ import annotations

import sys
import uuid

from common import (ENGINEER, api, check, db, fixture_contract, fixture_tenant,
                    fixture_version, heading, require_api, skip, summary)


def main() -> int:
    require_api()
    tenant = fixture_tenant()
    contract = fixture_contract(tenant)
    source = fixture_version(tenant, contract, "RAW")

    action_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """insert into dataset_action (id, tenant_id, name, output_class)
               values (%s, %s, %s, 'UNDER_REVIEW')""",
            (action_id, tenant, f"idem-{action_id[:8]}"),
        )

    key = f"v6-{uuid.uuid4().hex}"
    payload = {
        "tenant_id": tenant,
        "action_id": action_id,
        "code_hash": "sha256:same",
        "image_digest": "sha256:same",
        "operator": "verify-suite",
        "idempotency_key": key,
        "input_versions": [source["id"]],
        "trigger_kind": "manual",
        "triggered_by": ENGINEER,
    }

    heading("V6: an idempotency key keeps a retried action single")

    first = api("POST", "/action-runs", json=payload)
    check("first submission creates a run", first.status_code == 201 and first.json()["created"],
          f"HTTP {first.status_code}")
    run_id = first.json()["id"]

    # Five retries, as a crashed and restarted worker would produce.
    repeats = [api("POST", "/action-runs", json=payload) for _ in range(5)]
    check("every retry returns the original run",
          all(r.json().get("id") == run_id for r in repeats),
          f"ids={ {r.json().get('id') for r in repeats} }")
    check("no retry reports having created anything",
          all(r.json().get("created") is False for r in repeats))

    with db() as conn:
        rows = conn.execute(
            "select id from action_run where idempotency_key = %s", (key,)
        ).fetchall()
    check("exactly one run row exists for the key", len(rows) == 1, f"{len(rows)} rows")

    with db() as conn:
        try:
            # Trigger fields set to a valid manual shape, so the only
            # constraint this insert can trip is the one under test: the
            # unique index on idempotency_key.
            conn.execute(
                """insert into action_run
                     (id, tenant_id, action_id, code_hash, image_digest, status,
                      operator, idempotency_key, trigger_kind, triggered_by)
                   values (gen_random_uuid(), %s, %s, 'x', 'y', 'running', 'z', %s,
                           'manual', %s)""",
                (tenant, action_id, key, ENGINEER),
            )
            check("the database itself refuses a duplicate key", False,
                  "the insert succeeded")
        except Exception as exc:
            check("the database itself refuses a duplicate key",
                  "unique" in str(exc).lower(), type(exc).__name__)

    skip("Temporal resumes a killed worker and converges on one output",
         "a real kill is by hand: verify/v6_durable_retry.py (the workflow's retry "
         "settings are checked by verify/v6c_retry_policy.py)")

    return summary("V6")


if __name__ == "__main__":
    sys.exit(main())
