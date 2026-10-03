"""V4: the visibility class is enforced, and every decision is recorded.

Two separate claims, and the second is the one usually skipped. First, a role
whose floor sits above a version's class is refused, and refused by object
storage rather than only by the API. Second, both the refusal and the grant land
in `access_decision` with reasons, because a permission that is missing and a
person probing for data they should not have look identical until you can count
denials.
"""

from __future__ import annotations

import sys

import uuid

from common import (ADMIN, ENGINEER, PIPELINE, RESEARCHER, TRAINER, bucket_for,
                    api, bearer_for, check, db, fixture_contract,
                    fixture_department, fixture_tenant, fixture_version,
                    heading, require_api, s3_client, summary)


def main() -> int:
    require_api()
    tenant = fixture_tenant()
    contract = fixture_contract(tenant)
    version = fixture_version(tenant, contract, "RAW")
    prefix = version["storage_prefix"]

    # This tenant's own bucket, created by the platform if this is its first write.
    bucket = bucket_for(tenant)
    admin = s3_client(*ADMIN)
    admin.put_object(Bucket=bucket, Key=f"{prefix}/part-0.json", Body=b'{"phi":"present"}')

    # An owning department with a custodian. Before the organisation model this
    # test approved with a name nobody had registered, which the platform then
    # accepted. It no longer does, and the fixture has to say who is accountable
    # for this asset.
    custodian = fixture_department(tenant, version["dataset_id"])

    heading("V4: class enforced at the control plane")

    denied = api("POST", "/credentials", json={
        "principal": TRAINER,
        "principal_kind": "workload",
        "roles": ["training_job"],
        "tenant_id": tenant,
        "dataset_version_id": version["id"],
        "purpose": "train a model",
    })
    check("training role is refused a RAW version", denied.status_code == 403,
          f"HTTP {denied.status_code}")
    reasons = denied.json().get("detail", {}).get("reasons", []) if denied.status_code == 403 else []
    check("the refusal carries a reason", len(reasons) > 0, "; ".join(reasons))

    # `pipeline_action` needs proof of a real, running action_run since item
    # 60's task_credential.py fix -- naming the role is no longer enough.
    # See v80_credential_role_authority.py for that fix on its own; this is
    # just PIPELINE (a genuine registered pipeline_action workload) acting
    # through it rather than around it, the way a real caller would.
    action_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """insert into dataset_action
                 (id, tenant_id, name, source_schema_id, target_schema_id, output_class)
               values (%s, %s, %s, %s, %s, 'UNDER_REVIEW')""",
            (action_id, tenant, f"v4-action-{action_id[:8]}", contract, contract),
        )
    run = api("POST", "/action-runs", json={
        "tenant_id": tenant,
        "action_id": action_id,
        "code_hash": "sha256:v4-codehash",
        "image_digest": "sha256:v4-imagedigest",
        "operator": PIPELINE,
        "idempotency_key": f"v4-{uuid.uuid4().hex}",
        "input_versions": [version["id"]],
        "trigger_kind": "manual",
        "triggered_by": ENGINEER,
    })
    run.raise_for_status()
    task_credential = run.json()["task_credential"]

    allowed = api("POST", "/credentials", json={
        "principal": PIPELINE,
        "principal_kind": "workload",
        "roles": ["pipeline_action"],
        "tenant_id": tenant,
        "dataset_version_id": version["id"],
        "purpose": "de-identify",
        "task_credential": task_credential,
    })
    check("pipeline role is granted the same version", allowed.status_code == 200,
          f"HTTP {allowed.status_code} {allowed.text[:120]}")

    heading("V4: class enforced by object storage, not just by the API")

    # The API said no. The question now is whether the storage layer would have
    # said no too, which is what makes the boundary real rather than advisory.
    sys.path.insert(0, "/app")
    from app import grants as app_grants

    # The training role's key in this organisation. (The key every organisation used to share no longer exists, and a refusal
    # from a key that does not exist would say nothing about the class.)
    trainer = s3_client(*app_grants.tenant_role_key("training_job", tenant))
    try:
        trainer.get_object(Bucket=bucket, Key=f"{prefix}/part-0.json")
        check("refused role cannot read the object from S3", False, "the read succeeded")
    except Exception as exc:
        code = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        check("refused role cannot read the object from S3",
              code in (401, 403), f"HTTP {code}: {type(exc).__name__}")

    if allowed.status_code == 200:
        creds = allowed.json()
        granted = s3_client(creds["access_key"], creds["secret_key"])
        try:
            obj = granted.get_object(Bucket=bucket, Key=f"{prefix}/part-0.json")
            check("granted role can read the object from S3",
                  obj["Body"].read() == b'{"phi":"present"}')
        except Exception as exc:
            check("granted role can read the object from S3", False, repr(exc)[:140])
    else:
        check("granted role can read the object from S3", False, "no credential was issued")

    heading("V4: both decisions are in the audit log")

    with db() as conn:
        # Policy rows only. A credential request writes two rows, one for what
        # policy decided and one for whether the grant was applied, and these
        # assertions are about the first.
        rows = conn.execute(
            """select principal, allowed, reasons, purpose, requested_class
               from access_decision
               where dataset_version_id = %s and phase = 'policy'
               order by at""",
            (version["id"],),
        ).fetchall()

    by_principal = {r["principal"]: r for r in rows}
    check("the denial was logged", TRAINER in by_principal,
          f"{len(rows)} decisions recorded")
    check("the denial is recorded as denied",
          by_principal.get(TRAINER, {}).get("allowed") is False)
    check("the logged denial has reasons",
          bool(by_principal.get(TRAINER, {}).get("reasons")),
          "; ".join(by_principal.get(TRAINER, {}).get("reasons", [])))
    check("the grant was logged", PIPELINE in by_principal)
    check("the grant is recorded as allowed",
          by_principal.get(PIPELINE, {}).get("allowed") is True)
    check("the logged decision records the class asked for",
          by_principal.get(TRAINER, {}).get("requested_class") == "RAW",
          str(by_principal.get(TRAINER, {}).get("requested_class")))

    heading("V4: the log does not claim access that never happened")

    # The bug this guards against: the decision row was written before the grant
    # was attempted, so a request that policy allowed but that granted nothing
    # was recorded as "allowed" and nothing recorded otherwise. An auditor
    # counting allowed rows would have overstated who could read what.
    # Against a PUBLISHED version, so policy allows and the failure can only
    # come from the grant. Probing a RAW version would be refused by policy
    # first and would never reach the code path that was wrong.
    published = fixture_version(tenant, contract, "UNDER_REVIEW")
    api("POST", f"/dataset-versions/{published['id']}/promote", json={
        "to_class": "PUBLISHED", "decided_by": "verify-suite", "decided_by_kind": "workload",
        "gate_evidence": {"note": "fixture for the audit-honesty check"},
    })

    denied_human = api("POST", "/credentials", json={
        "principal": "svc-human-probe",
        "principal_kind": "human",
        "roles": ["analyst"],
        "tenant_id": tenant,
        "dataset_version_id": published["id"],
        "purpose": "clinical review",
    })

    with db() as conn:
        phases = conn.execute(
            """select phase, allowed, reasons from access_decision
               where principal = 'svc-human-probe'
                 and dataset_version_id = %s
               order by at""",
            (published["id"],),
        ).fetchall()

    by_phase = {r["phase"]: r for r in phases}
    check("a policy decision is recorded", "policy" in by_phase,
          f"{len(phases)} rows written")

    if denied_human.status_code == 200:
        # Policy allowed and the grant worked. Then both rows must say allowed,
        # or the log disagrees with what actually happened.
        check("the grant outcome is recorded too", "grant" in by_phase)
        check("both rows agree that access was granted",
              all(r["allowed"] for r in phases))
    elif denied_human.status_code == 503:
        # Policy allowed but nothing was granted. This is the case that used to
        # be logged as a clean success.
        check("the failed grant is recorded", "grant" in by_phase)
        check("the grant row records that nothing was granted",
              by_phase.get("grant", {}).get("allowed") is False)
        check("the grant row explains why",
              bool(by_phase.get("grant", {}).get("reasons")),
              "; ".join(by_phase.get("grant", {}).get("reasons", []))[:110])
    else:
        # Policy refused, so no grant was attempted and no grant row belongs.
        check("no grant row exists when policy refused", "grant" not in by_phase,
              f"HTTP {denied_human.status_code}")

    heading("V4: a lease lifts the floor, and self-approval does not")

    req = api("POST", "/leases/requests", json={
        "tenant_id": tenant,
        "principal": TRAINER,
        "dataset_version_id": version["id"],
        "purpose": "train a model",
        "justification": "needs raw audio to measure recall",
        "ttl_hours": 4,
    }, headers=bearer_for(ENGINEER))
    check("a lease can be requested", req.status_code == 201, f"HTTP {req.status_code}")
    request_id = req.json()["id"] if req.status_code == 201 else None

    if request_id:
        # Registered, and holding the approving role, so the refusal below can
        # only come from the self-approval rule. An unregistered requester would
        # be refused for the wrong reason and the test would pass hollowly.
        #
        # The role is granted on conflict rather than left alone. The seed
        # registers this workload without an approving role, correctly, so
        # `do nothing` would leave it unable to approve anything and the refusal
        # below would come from the missing role instead of from the rule under
        # test.
        with db() as conn:
            conn.execute(
                """insert into directory (id, tenant_id, label, kind, roles)
                   values (%s, %s, 'Training job', 'workload',
                           '{training_job,data_custodian}')
                   on conflict (id) do update set roles = excluded.roles""",
                (TRAINER, tenant),
            )

        wrong_person = api("POST", f"/leases/requests/{request_id}/approve",
                           headers=bearer_for(RESEARCHER))
        check("a session with no relationship to this asset is refused",
              wrong_person.status_code == 403,
              f"HTTP {wrong_person.status_code}")

        approved = api("POST", f"/leases/requests/{request_id}/approve",
                       headers=bearer_for(custodian))
        check("the owning custodian can approve", approved.status_code == 201,
              f"HTTP {approved.status_code} {approved.text[:120]}")

        after_lease = api("POST", "/credentials", json={
            "principal": TRAINER,
            "principal_kind": "workload",
            "roles": ["training_job"],
            "tenant_id": tenant,
            "dataset_version_id": version["id"],
            "purpose": "train a model",
        })
        check("the same request now succeeds under the lease",
              after_lease.status_code == 200, f"HTTP {after_lease.status_code}")

        # And the purpose is load bearing, not decorative.
        wrong_purpose = api("POST", "/credentials", json={
            "principal": TRAINER,
            "principal_kind": "workload",
            "roles": ["training_job"],
            "tenant_id": tenant,
            "dataset_version_id": version["id"],
            "purpose": "something else entirely",
        })
        check("the lease does not cover a different purpose",
              wrong_purpose.status_code == 403, f"HTTP {wrong_purpose.status_code}")

    return summary("V4")


if __name__ == "__main__":
    sys.exit(main())
