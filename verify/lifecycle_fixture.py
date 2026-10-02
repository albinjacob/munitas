"""A throwaway organisation with real people, for the checks on closing an organisation.

Closing needs the thing closed to be a customer organisation (purpose `production`),
and the checks that matter need real sign-ins for the people in it: a custodian who
may cancel, a member who may not, a person named as the custodian of a hold. So this
builds an organisation with its own people, each with a real Kratos login, and takes
them away again afterwards.

It never touches `health`, `finance`, `harbour` or `canary`. Two platform
administrators already exist (`ops-priya` and `ops-ravi`), and a hold needs both.

An organisation that never sealed anything is removed outright at the end. One that
did cannot be (a sealed version is immutable, which is the point), so the checks that
seal data end by purging it, which is the behaviour under test.
"""

from __future__ import annotations

import os
import uuid

import httpx

from common import (ADMIN, _EMAIL_BY_DIRECTORY_ID, _PASSWORD, api, bearer_for, bucket_for, db,
                    fixture_contract, s3_client)

KRATOS_ADMIN = os.environ.get("MUNITAS_VERIFY_KRATOS_ADMIN", "http://kratos:4434")

ADMIN_A = "ops-priya"
ADMIN_B = "ops-ravi"

# What an organisation here holds. Everything is looked up by these names, so a check
# never has to guess which row it made.
PEOPLE = {
    "custodian": ("data_custodian", "Custodian"),
    "member": ("analyst", "Member"),
    "dpo": ("dpo", "Records officer"),
}


class Org:
    def __init__(self, tenant_id: str):
        self.id = tenant_id
        self.people: dict[str, str] = {}
        self.identities: list[str] = []
        self.hold_ids: list[str] = []

    def bearer(self, who: str) -> dict[str, str]:
        return bearer_for(self.people[who])


def make_org(prefix: str = "verify-closing-") -> Org:
    org = Org(f"{prefix}{uuid.uuid4().hex[:8]}")
    with db() as conn:
        conn.execute(
            "insert into tenant (id, isolation_level, key_ref, purpose, note) "
            "values (%s, 'shared', %s, 'production', 'verification organisation for closing')",
            (org.id, f"key/{org.id}"),
        )
        for who, (role, label) in PEOPLE.items():
            person_id = f"{org.id}-{who}"
            email = f"{person_id}@verify.example"
            conn.execute(
                "insert into directory (id, tenant_id, label, kind, roles) values (%s, %s, %s, 'human', %s)",
                (person_id, org.id, label, [role]),
            )
            made = httpx.post(
                f"{KRATOS_ADMIN}/admin/identities",
                json={"schema_id": "default", "traits": {"email": email, "name": label},
                      "credentials": {"password": {"config": {"password": _PASSWORD}}}},
                timeout=10.0,
            )
            made.raise_for_status()
            identity_id = made.json()["id"]
            conn.execute("update directory set kratos_identity_id = %s where id = %s", (identity_id, person_id))
            _EMAIL_BY_DIRECTORY_ID[person_id] = email
            org.people[who] = person_id
            org.identities.append(identity_id)
    return org


def seal_data(org: Org) -> dict:
    """A contract, a dataset, a sealed version and a real file in the organisation's own bucket."""
    schema_id = fixture_contract(org.id)
    made = api("POST", "/datasets", json={"tenant_id": org.id, "name": "records"})
    made.raise_for_status()
    dataset_id = made.json()["id"]
    sealed = api("POST", "/dataset-versions", json={
        "tenant_id": org.id, "dataset_id": dataset_id, "schema_id": schema_id,
        "visibility_class": "RAW", "object_manifest": [{"key": "part-0.json", "bytes": 128}],
        "record_count": 1,
    })
    sealed.raise_for_status()
    bucket = bucket_for(org.id)
    client = s3_client(*ADMIN)
    client.put_object(Bucket=bucket, Key=f"{org.id}/records/part-0.json", Body=b'{"record_id": "1"}')
    client.put_object(Bucket=bucket, Key=f"{org.id}/records/part-1.json", Body=b'{"record_id": "2"}')
    version_id = sealed.json()["id"]
    # The other kinds of record a real organisation accumulates: a sealed agent version (the
    # second table with the immutability rule), a lease on the version, and an audit entry.
    agent_id, agent_version_id = str(uuid.uuid4()), str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            "insert into agent (id, tenant_id, name, registered_by, purpose, principal_id) "
            "values (%s, %s, 'summariser', %s, 'summarising records', %s)",
            (agent_id, org.id, org.people["custodian"], org.people["custodian"]))
        conn.execute(
            "insert into agent_version (id, tenant_id, agent_id, version, code_hash, source_path, model_id, "
            "registered_by, content_hash, sealed) values (%s, %s, %s, 1, 'h', 'agent.py', 'm', %s, 'c', true)",
            (agent_version_id, org.id, agent_id, org.people["custodian"]))
        conn.execute(
            "insert into access_lease (id, tenant_id, principal, dataset_version_id, purpose, approved_by, expires_at) "
            "values (%s, %s, %s, %s, 'shape exploration', %s, now() + interval '30 days')",
            (str(uuid.uuid4()), org.id, org.people["member"], version_id, org.people["custodian"]))
        conn.execute(
            "insert into access_decision (principal, principal_kind, principal_roles, tenant_id, "
            "dataset_version_id, allowed, reasons) values (%s, 'human', '{analyst}', %s, %s, true, '{}')",
            (org.people["member"], org.id, version_id))
    return {"dataset_id": dataset_id, "version_id": version_id, "bucket": bucket,
            "schema_id": schema_id, "agent_version_id": agent_version_id}


def bucket_exists(bucket: str) -> bool:
    from botocore.exceptions import ClientError
    try:
        s3_client(*ADMIN).head_bucket(Bucket=bucket)
        return True
    except ClientError:
        return False


def move_dates(org_id: str, retiring_ended: bool = False, closing_ended: bool = False) -> None:
    """Bring an organisation's two dates forward, as if the days had passed. The
    tenant table is the one the retired-tenant guard leaves open, so this needs no
    special access, and it is the same thing a clock moving on would change."""
    with db() as conn:
        conn.execute(
            """update tenant set
                   retiring_until = case when %s then now() - interval '2 hours' else retiring_until end,
                   closing_until  = case when %s then now() - interval '1 hour'
                                         when %s then greatest(closing_until, now() + interval '10 days')
                                         else closing_until end
             where id = %s""",
            (retiring_ended, closing_ended, retiring_ended, org_id),
        )


def hold_body(org: Org, number: str = "HC-2026-0417", **changes) -> dict:
    body = {
        "tenant_id": org.id,
        "matter_name": "Doe v the clinic",
        "matter_number": number,
        "description": "A patient claim about a procedure carried out in 2024.",
        "triggering_event": "Letter before claim received on 2026-09-30",
        "issuing_authority": "Aldous and Brennan LLP, for the claimant",
        "authority_reference": "AB/2026/17",
        "attorney_name": "Ruth Aldous",
        "attorney_email": "ruth.aldous@example.test",
        "notice_received_on": "2026-10-01",
        "preserve": "Every record of the claimant and the audit trail of who read it.",
        "custodian_id": org.people["dpo"],
    }
    body.update(changes)
    return body


def forget_deletion_record(tenant_id: str) -> None:
    """Remove the record a purge leaves, for a throwaway organisation only.

    The record is protected from deletion by a rewrite rule, as it should be. A
    verification run would otherwise leave one behind every time, so this lifts
    the rule for the one statement inside a transaction, the same way
    scripts/admin/nuke-tenant.py does for the sealed-version rules, and the
    transaction puts it back whatever happens."""
    with db() as conn:
        with conn.transaction():
            conn.execute("alter table tenant_deletion_record disable rule tenant_deletion_record_no_delete")
            conn.execute("delete from tenant_deletion_record where tenant_id = %s "
                         "and tenant_id like 'verify-%%'", (tenant_id,))
            conn.execute("alter table tenant_deletion_record enable rule tenant_deletion_record_no_delete")


def drop_org(org: Org) -> None:
    """Take away what a check made. Whatever was sealed has been purged by the check
    itself; whatever was not is declared disposable and deleted."""
    with db() as conn:
        left = conn.execute("select purged_at, purpose from tenant where id = %s", (org.id,)).fetchone()
        if left and not left["purged_at"]:
            conn.execute("update tenant set purpose = 'scratch' where id = %s", (org.id,))
            for table in ("legal_hold", "lifecycle_event", "catalog_token", "dataset", "schema_contract",
                          "directory"):
                conn.execute(f'delete from "{table}" where tenant_id = %s', (org.id,))
            conn.execute("delete from tenant where id = %s", (org.id,))
        elif left:
            conn.execute("delete from tenant where id = %s", (org.id,))
    forget_deletion_record(org.id)
    for identity in org.identities:
        httpx.delete(f"{KRATOS_ADMIN}/admin/identities/{identity}", timeout=10.0)
