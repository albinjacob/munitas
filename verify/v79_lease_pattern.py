"""U82: a custodian may choose whether a grant covers one purpose or any.

Every lease this platform ever granted covers exactly the purpose it was
approved under -- a second, differently-worded reason to touch the same data
gets its own decision. That is right for data nobody has vouched for yet, and
wrong as the *only* option for a workload a custodian already trusts: asking
again on day two for the identical job, just because the requester reworded
its own justification, is the approval-fatigue failure mode by another name.

"pattern" lets the custodian decide which shape fits, per lease, at the
moment they approve it -- not the platform, and not the requester. "strict"
(the default, and every lease before this column existed) behaves exactly as
before. "simple" covers any purpose while the lease lasts, and is refused
outright against RAW data regardless of how much the custodian trusts the
principal, enforced twice: once here in the API's own check, and again by
`access_lease`'s own trigger, so a bug in this file cannot be the only thing
standing in the way.

    docker compose exec -T munitas-api python /verify/v79_lease_pattern.py
"""

from __future__ import annotations

import sys

from common import (CUSTODIAN, RESEARCHER, api, bearer_for, check,
                    fixture_contract, fixture_department, fixture_tenant,
                    fixture_version, heading, require_api, summary)


def _request_and_approve(tenant: str, version: dict, purpose: str, pattern: str | None) -> dict:
    req = api("POST", "/leases/requests", json={
        "tenant_id": tenant,
        "principal": RESEARCHER,
        "dataset_version_id": version["id"],
        "purpose": purpose,
        "justification": "verifying the pattern feature",
        "ttl_hours": 24,
    }, headers=bearer_for(RESEARCHER))
    req.raise_for_status()
    request_id = req.json()["id"]

    body = {"pattern": pattern} if pattern else {}
    approval = api("POST", f"/leases/requests/{request_id}/approve", json=body,
                   headers=bearer_for(CUSTODIAN))
    return approval


def main() -> int:
    require_api()

    tenant = fixture_tenant()
    contract = fixture_contract(tenant)

    heading("U82: approving with no pattern behaves exactly as before")

    version = fixture_version(tenant, contract, "UNDER_REVIEW")
    fixture_department(tenant, version["dataset_id"])
    approval = _request_and_approve(tenant, version, "first look", None)
    check("an approval naming no pattern is accepted", approval.status_code == 201,
          f"HTTP {approval.status_code}: {approval.text}")

    cred = api("POST", "/credentials", json={
        "principal": RESEARCHER, "principal_kind": "human", "roles": ["notebook_explore"],
        "tenant_id": tenant, "dataset_version_id": version["id"], "purpose": "first look",
    }, headers=bearer_for(RESEARCHER))
    check("the same purpose it was granted under still reads",
          cred.status_code == 200, f"HTTP {cred.status_code}")

    cred_other = api("POST", "/credentials", json={
        "principal": RESEARCHER, "principal_kind": "human", "roles": ["notebook_explore"],
        "tenant_id": tenant, "dataset_version_id": version["id"], "purpose": "a different reason",
    }, headers=bearer_for(RESEARCHER))
    check("a strict (default) lease refuses a different purpose",
          cred_other.status_code == 403, f"HTTP {cred_other.status_code}")

    heading("U82: a custodian may approve 'simple', covering any purpose")

    simple_version = fixture_version(tenant, contract, "UNDER_REVIEW")
    fixture_department(tenant, simple_version["dataset_id"])
    approval = _request_and_approve(tenant, simple_version, "weekly report", "simple")
    check("an approval naming pattern=simple is accepted", approval.status_code == 201,
          f"HTTP {approval.status_code}: {approval.text}")

    cred_same = api("POST", "/credentials", json={
        "principal": RESEARCHER, "principal_kind": "human", "roles": ["notebook_explore"],
        "tenant_id": tenant, "dataset_version_id": simple_version["id"], "purpose": "weekly report",
    }, headers=bearer_for(RESEARCHER))
    check("a simple lease still reads for the purpose it was granted under",
          cred_same.status_code == 200, f"HTTP {cred_same.status_code}")

    cred_new = api("POST", "/credentials", json={
        "principal": RESEARCHER, "principal_kind": "human", "roles": ["notebook_explore"],
        "tenant_id": tenant, "dataset_version_id": simple_version["id"],
        "purpose": "an ad hoc spot-check, never mentioned at approval time",
    }, headers=bearer_for(RESEARCHER))
    check("a simple lease also reads for a purpose never stated at approval time",
          cred_new.status_code == 200, f"HTTP {cred_new.status_code}")

    heading("U82: 'simple' is refused against RAW data, regardless of trust")

    raw_version = fixture_version(tenant, contract, "RAW")
    fixture_department(tenant, raw_version["dataset_id"])
    approval = _request_and_approve(tenant, raw_version, "raw peek", "simple")
    check("approving pattern=simple against a RAW version is refused",
          approval.status_code == 400, f"HTTP {approval.status_code}: {approval.text}")

    strict_approval = _request_and_approve(tenant, raw_version, "raw peek, take two", "strict")
    check("the same version accepts an explicit pattern=strict approval",
          strict_approval.status_code == 201,
          f"HTTP {strict_approval.status_code}: {strict_approval.text}")

    return summary("U82: lease pattern")


if __name__ == "__main__":
    sys.exit(main())
