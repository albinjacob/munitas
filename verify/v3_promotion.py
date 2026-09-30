"""V3: promotion moves no bytes.

The claim that most distinguishes this design from a pipeline: a visibility
class is a visibility mask, so widening it changes a row and a grant and never
copies data. The test records every object key and etag under the version's
prefix before and after a promotion and asserts the sets are identical.

If this fails, the class model is a pipeline wearing different words.
"""

from __future__ import annotations

import sys

from common import (ADMIN, api, bucket_for, check, db, fixture_contract, fixture_tenant,
                    fixture_version, heading, require_api, s3_client, summary)


def snapshot(client, prefix: str, bucket: str) -> dict[str, str]:
    """Every key under the prefix, mapped to its etag."""
    out: dict[str, str] = {}
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        for obj in page.get("Contents", []):
            out[obj["Key"]] = obj["ETag"].strip('"')
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    return out


def main() -> int:
    require_api()
    tenant = fixture_tenant()
    contract = fixture_contract(tenant)
    version = fixture_version(tenant, contract, "UNDER_REVIEW")
    prefix = version["storage_prefix"]

    # The tenant's own bucket, created by the platform if this is its first write.
    # Writing to the shared bucket instead would pass, because this script
    # both writes and reads the objects itself, and would quietly leave them
    # somewhere nothing can reach and recreate a bucket nothing uses.
    bucket = bucket_for(tenant)

    admin = s3_client(*ADMIN)
    for i in range(3):
        admin.put_object(
            Bucket=bucket,
            Key=f"{prefix}/part-{i}.json",
            Body=f'{{"record": {i}, "transcript": "synthetic"}}'.encode(),
        )

    heading("V3: promotion moves no bytes")

    before = snapshot(admin, prefix, bucket)
    check("objects exist under the version prefix before promotion",
          len(before) == 3, f"{len(before)} objects at {prefix}")

    r = api("POST", f"/dataset-versions/{version['id']}/promote", json={
        "to_class": "OPEN_FOR_TRAINING",
        "decided_by": "verify-suite",
        "decided_by_kind": "workload",
        "gate_evidence": {"mlflow_run": "run-abc123", "recall": 0.99},
        "grant_roles": ["training_job"],
    })
    check("promotion accepted", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
    body = r.json() if r.status_code == 200 else {}

    after = snapshot(admin, prefix, bucket)

    check("object key set is identical after promotion",
          set(before) == set(after),
          f"before={len(before)} after={len(after)}")
    check("every etag is identical after promotion",
          before == after,
          "no object was rewritten" if before == after else
          f"changed: {[k for k in before if before[k] != after.get(k)]}")
    check("storage prefix is unchanged",
          body.get("storage_prefix") == prefix, str(body.get("storage_prefix")))

    # The sealed row must not have been touched either. The class moved in the
    # transition log, not on the version.
    with db() as conn:
        row = conn.execute(
            "select * from version_class where dataset_version_id = %s", (version["id"],)
        ).fetchone()
        transitions = conn.execute(
            "select * from class_transition where dataset_version_id = %s", (version["id"],)
        ).fetchall()

    check("sealed_class still records what it was sealed at",
          row["sealed_class"] == "UNDER_REVIEW", row["sealed_class"])
    check("current_class reflects the promotion", row["current_class"] == "OPEN_FOR_TRAINING",
          row["current_class"])
    check("a transition row records the change", len(transitions) == 1,
          f"{len(transitions)} transitions")
    check("the transition carries gate evidence",
          bool(transitions and transitions[0]["gate_evidence"]),
          str(transitions[0]["gate_evidence"]) if transitions else "none")

    # Demotion must be refused, because the people who could see it already have.
    r = api("POST", f"/dataset-versions/{version['id']}/promote", json={
        "to_class": "RAW",
        "decided_by": "verify-suite",
        "decided_by_kind": "workload",
        "gate_evidence": {"note": "attempted demotion"},
    })
    check("demotion is refused", r.status_code == 409, f"HTTP {r.status_code}")

    # A promotion without evidence is an assertion, and must not be accepted.
    r = api("POST", f"/dataset-versions/{version['id']}/promote", json={
        "to_class": "PUBLISHED",
        "decided_by": "verify-suite",
        "decided_by_kind": "workload",
        "gate_evidence": {},
    })
    check("promotion without gate evidence is rejected",
          r.status_code == 422, f"HTTP {r.status_code}")

    return summary("V3")


if __name__ == "__main__":
    sys.exit(main())
