"""Put some data into Harbour Clinic, the organisation the closing walkthrough shuts down.

`infra/postgres/seed-harbour.sql` registers who exists: three people and one department. This
adds what a closing has something to delete: two datasets of real uploaded files, sealed through
the ordinary ingest path, so there are sealed versions and files in the organisation's own storage
to be removed when it is purged.

Every patient named in the files is invented. Everything goes through the control plane API, never
straight into the database, for the reason scripts/seed/seed-health-example.py gives.

Idempotent: it does nothing when the organisation already holds datasets.

    .venv\\Scripts\\python.exe scripts/seed/seed-harbour-example.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

import httpx

from seed_common import bearer_for, expect
from ports_config import PORTS  # noqa: E402

API = f"http://localhost:{PORTS['munitas_api_http']}"
WORKER_HEADERS = {"x-worker-token": os.environ.get("MUNITAS_WORKER_TOKEN", "dev-worker-token-not-for-production")}
TENANT = "harbour"
DEPARTMENT = "Patient Records"
CUSTODIAN = "cust-dunmore"
ANALYST = "ana-quinn"

LETTERS = {
    "appointment-reminders": {
        "reminder-001.txt": "Dear Ms Alder, this is a reminder of your appointment on 14 November at 10:30.\n",
        "reminder-002.txt": "Dear Mr Bellamy, this is a reminder of your appointment on 15 November at 09:15.\n",
        "reminder-003.txt": "Dear Ms Carrow, this is a reminder of your appointment on 18 November at 14:00.\n",
    },
    "discharge-letters": {
        "discharge-001.txt": "Mr Dalton was discharged on 2 October with advice to rest for one week.\n",
        "discharge-002.txt": "Ms Everett was discharged on 3 October and will return for a check in two weeks.\n",
    },
}


# A table, not a folder of files: one row per visit, so a legal demand about one patient can be answered with that
# patient's rows alone. The people are invented.
APPOINTMENTS = [
    {"appointment_id": "A-0001", "patient_id": "P-4471", "patient_name": "Ms Alder", "appointment_on": "2026-03-04", "reason": "Follow-up consultation"},
    {"appointment_id": "A-0002", "patient_id": "P-4472", "patient_name": "Mr Bellamy", "appointment_on": "2026-03-04", "reason": "Annual check"},
    {"appointment_id": "A-0003", "patient_id": "P-4473", "patient_name": "Ms Carrow", "appointment_on": "2026-03-11", "reason": "Blood test"},
    {"appointment_id": "A-0004", "patient_id": "P-4471", "patient_name": "Ms Alder", "appointment_on": "2026-04-09", "reason": "Procedure review"},
    {"appointment_id": "A-0005", "patient_id": "P-4474", "patient_name": "Mr Dalton", "appointment_on": "2026-04-09", "reason": "Annual check"},
    {"appointment_id": "A-0006", "patient_id": "P-4475", "patient_name": "Ms Everett", "appointment_on": "2026-05-20", "reason": "Follow-up consultation"},
    {"appointment_id": "A-0007", "patient_id": "P-4472", "patient_name": "Mr Bellamy", "appointment_on": "2026-06-02", "reason": "Blood test"},
    {"appointment_id": "A-0008", "patient_id": "P-4473", "patient_name": "Ms Carrow", "appointment_on": "2026-06-18", "reason": "Annual check"},
    {"appointment_id": "A-0009", "patient_id": "P-4474", "patient_name": "Mr Dalton", "appointment_on": "2026-07-07", "reason": "Follow-up consultation"},
    {"appointment_id": "A-0010", "patient_id": "P-4475", "patient_name": "Ms Everett", "appointment_on": "2026-08-25", "reason": "Annual check"},
]


def seed_table(session: dict, department: str) -> None:
    """Register the appointments table and seal it as rows, so the platform also holds it as a table that can be
    filtered. The rows are written to the organisation's own bucket at the prefix the platform reserved, as a producer
    would, and the seal names them."""
    import boto3
    from botocore.config import Config

    contract = httpx.post(f"{API}/schema-contracts", timeout=30.0, json={
        "tenant_id": TENANT, "name": "appointment",
        "fields": [
            {"name": "appointment_id", "type": "string", "sensitivity": "none", "added_by": "seed"},
            {"name": "patient_id", "type": "string", "sensitivity": "direct", "added_by": "seed"},
            {"name": "patient_name", "type": "string", "sensitivity": "direct", "added_by": "seed"},
            {"name": "appointment_on", "type": "string", "sensitivity": "quasi", "added_by": "seed"},
            {"name": "reason", "type": "string", "sensitivity": "phi", "added_by": "seed"},
        ],
        "primary_key": ["appointment_id"]})
    expect(contract, 201, doing="registering the appointment contract")
    registered = httpx.post(f"{API}/datasets/register", timeout=30.0, json={
        "tenant_id": TENANT, "name": "appointments", "department_id": department, "registered_by": CUSTODIAN,
        "provenance": "internal_regulated", "declared_class": "RAW", "source_kind": "upload", "modality": ["tabular"]})
    expect(registered, 201, doing="registering appointments")
    dataset_id = registered.json()["id"]
    where = httpx.get(f"{API}/datasets/{dataset_id}/next-version", params={"tenant_id": TENANT}, headers=session, timeout=30.0)
    where.raise_for_status()
    key = f"{where.json()['storage_prefix']}/records.json"
    body = json.dumps(APPOINTMENTS).encode("utf-8")
    store = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT", f"http://localhost:{PORTS['seaweedfs_s3']}"),
                         aws_access_key_id=os.environ.get("S3_ADMIN_KEY") or "munitas-admin",
                         aws_secret_access_key=os.environ.get("S3_ADMIN_SECRET") or "munitas-admin-secret",
                         config=Config(signature_version="s3v4"), region_name="us-east-1")
    store.put_object(Bucket=f"munitas-{TENANT}", Key=key, Body=body)
    sealed = httpx.post(f"{API}/dataset-versions", timeout=60.0, headers=WORKER_HEADERS, json={
        "tenant_id": TENANT, "dataset_id": dataset_id, "schema_id": contract.json()["id"], "visibility_class": "RAW",
        "object_manifest": [{"key": key, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}],
        "record_count": len(APPOINTMENTS), "records_key": key})
    expect(sealed, 201, doing="sealing appointments")
    print(f"  appointments: {len(APPOINTMENTS)} rows, sealed as a table")


def department_id(session: dict) -> str:
    orgs = httpx.get(f"{API}/organisation", params={"tenant_id": TENANT}, headers=session, timeout=30.0).json()
    for d in orgs["departments"]:
        if d["name"] == DEPARTMENT:
            return d["id"]
    raise SystemExit(f"department {DEPARTMENT!r} is missing from {TENANT!r}. Apply infra/postgres/seed-harbour.sql first.")


def history(session: dict) -> None:
    """A little history in the audit trail, which is what a deletion keeps for seven years: Quinn asked
    Dunmore for access to the reminders and was granted it. Skipped
    when Quinn has already asked, so running this twice adds nothing."""
    versions = httpx.get(f"{API}/dataset-versions", params={"tenant_id": TENANT}, headers=session, timeout=30.0).json()
    first_version = sorted(versions, key=lambda v: v["storage_prefix"])[0]["dataset_version_id"]
    existing = httpx.get(f"{API}/lease-requests", params={"tenant_id": TENANT}, headers=session, timeout=30.0)
    if existing.status_code == 200 and any(r["principal"] == ANALYST for r in existing.json().get("lease_requests", [])):
        print("  Quinn has already asked for access. Nothing to add.")
        return
    quinn = bearer_for(ANALYST)
    asked = httpx.post(f"{API}/leases/requests", timeout=30.0, headers=quinn, json={
        "tenant_id": TENANT, "principal": ANALYST, "dataset_version_id": first_version,
        "purpose": "checking that reminder letters were sent", "ttl_hours": 72,
        "justification": "Matching the reminders against the appointment book for the final audit.",
    })
    expect(asked, 201, doing="Quinn asking for access to the reminders")
    approved = httpx.post(f"{API}/leases/requests/{asked.json()['id']}/approve", timeout=30.0,
                          headers=session, json={"approver": CUSTODIAN})
    expect(approved, 201, doing="Dunmore approving the request")
    # The platform records every decision it makes, refusals and the like included. Quinn is a person, and people are
    # not handed a storage credential directly, so the platform answers that it permits the access but issues no
    # credential, and records that. The answer is the demonstration, so it is not treated as a failure.
    attempt = httpx.post(f"{API}/credentials", timeout=30.0, json={
        "principal": ANALYST, "principal_kind": "human", "roles": ["analyst"], "tenant_id": TENANT,
        "dataset_version_id": first_version, "purpose": "checking that reminder letters were sent"})
    if attempt.status_code not in (200, 503):
        raise SystemExit(f"Quinn asking for a credential: expected the platform to answer, got HTTP {attempt.status_code}")
    print("  Quinn asked for access to the reminders, was approved, and the platform recorded the decision")


def main() -> int:
    try:
        httpx.get(f"{API}/health", timeout=10.0).raise_for_status()
    except Exception as exc:
        print(f"control plane unreachable at {API}: {exc}")
        return 2
    session = bearer_for(CUSTODIAN)
    existing = httpx.get(f"{API}/datasets", params={"tenant_id": TENANT}, headers=session, timeout=30.0).json()
    if existing.get("total"):
        print(f"{TENANT} already holds {existing['total']} datasets.")
        history(session)
        return 0

    department = department_id(session)
    print(f"Seeding {TENANT}")
    for name, files in LETTERS.items():
        registered = httpx.post(f"{API}/datasets/register", timeout=30.0, json={
            "tenant_id": TENANT, "name": name, "department_id": department, "registered_by": CUSTODIAN,
            "provenance": "internal_regulated", "declared_class": "RAW", "source_kind": "upload",
            "modality": ["text"],
        })
        expect(registered, 201, doing=f"registering {name}")
        dataset_id = registered.json()["id"]
        for filename, text in files.items():
            uploaded = httpx.post(f"{API}/datasets/{dataset_id}/files", timeout=60.0, headers=session,
                                  files={"file": (filename, text.encode("utf-8"), "text/plain")})
            expect(uploaded, 201, doing=f"uploading {filename}")
        sealed = httpx.post(f"{API}/datasets/{dataset_id}/seal", timeout=60.0, headers=session)
        expect(sealed, 201, doing=f"sealing {name}")
        print(f"  {name}: {len(files)} files, sealed")

    seed_table(session, department)
    history(session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
