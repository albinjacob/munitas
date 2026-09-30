"""U38: a workload's lease can name the human whose request justified it.

`svc-trainer` is a workload, not a person, and it goes through the same
`lease_request -> custodian approves -> access_lease` path a human does. Until
this, there was no way to say "svc-trainer read this because Devi's request was
approved by Hartley": the reading principal and the requester were the same
column, so a human's request either lost the human or made the workload read as
them.

The check that matters is the last one. Recording who asked is worthless if it
lets the workload's own identity drift: policy and lease-matching must still see
only the workload, never the human, or this closes one gap by reopening the one
agent/identity.py's own docstring already warns against -- identity read from
whatever the caller supplied.

    docker compose exec -T munitas-api python /verify/v38_grant_provenance.py
"""

from __future__ import annotations

import sys

from common import (CUSTODIAN, ENGINEER, TRAINER, api, bearer_for, check, db,
                    fixture_contract, fixture_department, fixture_tenant,
                    fixture_version, heading, require_api, summary)


def db_one(sql: str, params: tuple) -> dict | None:
    with db() as conn:
        return conn.execute(sql, params).fetchone()


def main() -> int:
    require_api()

    tenant = fixture_tenant()
    contract = fixture_contract(tenant)
    version = fixture_version(tenant, contract, "RAW")
    fixture_department(tenant, version["dataset_id"])

    heading("U38: a human may request a lease for a workload")

    req = api("POST", "/leases/requests", json={
        "tenant_id": tenant,
        "principal": TRAINER,
        "dataset_version_id": version["id"],
        "purpose": "train a model on raw audio",
        "justification": "measuring recall against unredacted transcripts",
        "ttl_hours": 4,
    }, headers=bearer_for(ENGINEER))
    check("the request is accepted", req.status_code == 201, f"HTTP {req.status_code}")
    request_id = req.json()["id"] if req.status_code == 201 else None

    if request_id:
        row = db_one(
            "select principal, requested_by from lease_request where id = %s",
            (request_id,),
        )
        check("the reading principal is the workload, not the human",
              row is not None and row["principal"] == TRAINER,
              str(row))
        check("requested_by names the human who actually asked",
              row is not None and row["requested_by"] == ENGINEER,
              str(row))

    heading("U38: a human cannot be named as principal by somebody else")

    # A human may only request for themselves now; naming a different human
    # as the reader is refused the same non-disclosure way a cross-tenant
    # request is (404, not 403 or 400).
    human_req = api("POST", "/leases/requests", json={
        "tenant_id": tenant,
        "principal": ENGINEER,
        "dataset_version_id": version["id"],
        "purpose": "should never be filed this way",
        "justification": "a human requests for themselves, not via someone else",
        "ttl_hours": 4,
    }, headers=bearer_for(CUSTODIAN))
    check("a human naming a different human as principal is refused",
          human_req.status_code == 404, f"HTTP {human_req.status_code}")

    heading("U38: a custodian cannot approve a request made on their behalf")

    self_requested = api("POST", "/leases/requests", json={
        "tenant_id": tenant,
        "principal": TRAINER,
        "dataset_version_id": version["id"],
        "purpose": "the custodian asking for themselves, via a workload",
        "justification": "should not be approvable by the same custodian",
        "ttl_hours": 4,
    }, headers=bearer_for(CUSTODIAN))
    check("the on-behalf-of request is filed",
          self_requested.status_code == 201, f"HTTP {self_requested.status_code}")
    self_requested_id = self_requested.json()["id"] if self_requested.status_code == 201 else None

    if self_requested_id:
        blocked = api(
            "POST", f"/leases/requests/{self_requested_id}/approve",
            headers=bearer_for(CUSTODIAN),
        )
        check("approving your own on-behalf-of request is refused",
              blocked.status_code == 403, f"HTTP {blocked.status_code}")

        row = db_one(
            "select lease_id, state from lease_request where id = %s",
            (self_requested_id,),
        )
        check("nothing was granted",
              row is not None and row["lease_id"] is None and row["state"] == "pending",
              str(row))

    heading("U38: a normal approval carries the provenance through")

    if request_id:
        approved = api(
            "POST", f"/leases/requests/{request_id}/approve",
            headers=bearer_for(CUSTODIAN),
        )
        check("approved by somebody who is neither the workload nor the requester",
              approved.status_code == 201, f"HTTP {approved.status_code}")

        listed = api("GET", "/lease-requests", params={
            "tenant_id": tenant, "principal": TRAINER,
        }, headers=bearer_for(ENGINEER))
        rows = [r for r in listed.json()["lease_requests"] if r["id"] == request_id]
        check("the lease request lists requested_by and its label",
              bool(rows) and rows[0]["requested_by"] == ENGINEER
              and rows[0]["requested_by_label"] is not None,
              str(rows[0]) if rows else "not found")

        leases = api("GET", "/leases", params={"tenant_id": tenant, "principal": TRAINER},
                     headers=bearer_for(ENGINEER))
        lease_rows = [l for l in leases.json()["leases"] if l.get("requested_by") == ENGINEER]
        check("the granted lease carries requested_by too",
              bool(lease_rows), f"{len(lease_rows)} matching leases")

        heading("U38: the audit log still names the workload, never the human")

        # The point of this whole feature: provenance is recorded, not
        # substituted. A credential request as the workload should still show
        # the workload as the acting principal in access_decision, with the
        # human visible only through the lease's own requested_by column.
        cred = api("POST", "/credentials", json={
            "principal": TRAINER,
            "principal_kind": "workload",
            "roles": ["training_job"],
            "tenant_id": tenant,
            "dataset_version_id": version["id"],
            "purpose": "train a model on raw audio",
        })
        check("the workload can now read using the on-behalf-of lease",
              cred.status_code == 200, f"HTTP {cred.status_code}: {cred.text[:200]}")

        decision = db_one(
            """select principal, principal_kind from access_decision
               where dataset_version_id = %s and phase = 'policy'
               order by at desc limit 1""",
            (version["id"],),
        )
        check("access_decision names the workload as the acting principal",
              decision is not None and decision["principal"] == TRAINER
              and decision["principal_kind"] == "workload",
              str(decision))

    return summary("U38")


if __name__ == "__main__":
    sys.exit(main())
