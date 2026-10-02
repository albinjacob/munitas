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

import sys

import httpx

from seed_common import bearer_for, expect
from ports_config import PORTS  # noqa: E402

API = f"http://localhost:{PORTS['munitas_api_http']}"
TENANT = "harbour"
DEPARTMENT = "Patient Records"
CUSTODIAN = "cust-dunmore"

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


def department_id(session: dict) -> str:
    orgs = httpx.get(f"{API}/organisation", params={"tenant_id": TENANT}, headers=session, timeout=30.0).json()
    for d in orgs["departments"]:
        if d["name"] == DEPARTMENT:
            return d["id"]
    raise SystemExit(f"department {DEPARTMENT!r} is missing from {TENANT!r}. Apply infra/postgres/seed-harbour.sql first.")


def main() -> int:
    try:
        httpx.get(f"{API}/health", timeout=10.0).raise_for_status()
    except Exception as exc:
        print(f"control plane unreachable at {API}: {exc}")
        return 2
    session = bearer_for(CUSTODIAN)
    existing = httpx.get(f"{API}/datasets", params={"tenant_id": TENANT}, headers=session, timeout=30.0).json()
    if existing.get("total"):
        print(f"{TENANT} already holds {existing['total']} datasets. Nothing to do.")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
