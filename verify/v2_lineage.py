"""V2: lineage resolves.

The claim is that from a dataset version you can reach, in one query, the run
that produced it, the code hash and image digest behind that run, and the input
versions it consumed. If any of those is null the lineage is decorative.
"""

from __future__ import annotations

import sys
import uuid

from common import (ENGINEER, api, check, db, fixture_contract, fixture_tenant,
                    fixture_version, heading, require_api, summary)


def main() -> int:
    require_api()
    tenant = fixture_tenant()
    contract = fixture_contract(tenant)

    # An input version, then an action that consumes it and produces an output.
    source = fixture_version(tenant, contract, "RAW")

    action_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """insert into dataset_action
                 (id, tenant_id, name, source_schema_id, target_schema_id, output_class)
               values (%s, %s, %s, %s, %s, 'UNDER_REVIEW')""",
            (action_id, tenant, f"redact-{action_id[:8]}", contract, contract),
        )

    r = api("POST", "/action-runs", json={
        "tenant_id": tenant,
        "action_id": action_id,
        "code_hash": "sha256:codehash-abc",
        "image_digest": "sha256:imagedigest-def",
        "operator": "verify-suite",
        "idempotency_key": f"v2-{uuid.uuid4().hex}",
        "input_versions": [source["id"]],
        "trigger_kind": "manual",
        "triggered_by": ENGINEER,
    })
    r.raise_for_status()
    run_id = r.json()["id"]

    r = api("POST", "/datasets", json={"tenant_id": tenant, "name": f"v2-out-{uuid.uuid4().hex[:8]}"})
    r.raise_for_status()
    out_dataset = r.json()["id"]

    r = api("POST", "/dataset-versions", json={
        "tenant_id": tenant,
        "dataset_id": out_dataset,
        "schema_id": contract,
        "visibility_class": "UNDER_REVIEW",
        "produced_by_run": run_id,
        "record_count": 1,
    })
    r.raise_for_status()
    out_version = r.json()["id"]

    heading("V2: lineage resolves in one query")

    r = api("GET", f"/lineage/{out_version}")
    check("lineage endpoint answers", r.status_code == 200, f"HTTP {r.status_code}")
    row = r.json() if r.status_code == 200 else {}

    check("resolves the producing run", row.get("run_id") == run_id, str(row.get("run_id")))
    check("resolves the action name", bool(row.get("action_name")), str(row.get("action_name")))
    check("resolves the code hash", row.get("code_hash") == "sha256:codehash-abc",
          str(row.get("code_hash")))
    check("resolves the image digest", row.get("image_digest") == "sha256:imagedigest-def",
          str(row.get("image_digest")))
    check("resolves the operator", row.get("operator") == "verify-suite", str(row.get("operator")))
    check("resolves the input version it consumed",
          source["id"] in str(row.get("input_versions")), str(row.get("input_versions")))

    # A run that produced a version must be marked finished, otherwise the
    # lineage record says a completed dataset came from a job still in flight.
    with db() as conn:
        run = conn.execute("select status, output_version from action_run where id = %s",
                           (run_id,)).fetchone()
    check("producing run is marked succeeded", run["status"] == "succeeded", run["status"])
    check("run points back at its output version", str(run["output_version"]) == out_version)

    return summary("V2")


if __name__ == "__main__":
    sys.exit(main())
