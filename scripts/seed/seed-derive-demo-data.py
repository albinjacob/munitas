"""Tabular demo data for the "make a new dataset from a query" demo.

    python scripts/seed/seed-derive-demo-data.py   (from the repo root, with the .venv active)

Four datasets, two in each example organisation, every row synthetic:

  health    admissions         Cardiology    raw         400 rows, with patient names
            diagnosis_codes    Cardiology    published   a small public lookup
  finance   transactions       Fraud Ops     raw         600 rows, with account holders
            merchants          Risk & Comp.  published   a small public lookup

The raw tables are the sensitive ones: they carry direct identifiers and cannot
be read without a lease the owning department's custodian approves. The lookups
are readable by everybody who is signed in, so a query can join the two.

Each is registered through the ordinary route, so it arrives owned by a
department. The rows are uploaded as a file and sealed with a records key, which
is how the pipeline's own tabular outputs become Iceberg tables.

Safe to run again: a dataset that already has a sealed version is left alone.
Needs the stack up and the example organisations seeded (infra/postgres).
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import date, timedelta

import httpx

from seed_common import bearer_for, expect
from ports_config import PORTS  # noqa: E402

API = f"http://localhost:{PORTS['munitas_api_http']}"


def post(path: str, **kwargs) -> httpx.Response:
    return httpx.post(f"{API}{path}", timeout=60.0, **kwargs)


def get(path: str, **kwargs) -> httpx.Response:
    return httpx.get(f"{API}{path}", timeout=30.0, **kwargs)


# ------------------------------------------------------------------ health --

FIRST = ["Alma", "Bruno", "Chidi", "Dara", "Elif", "Farid", "Grace", "Hugo", "Ines", "Jamal",
         "Keiko", "Lars", "Mina", "Noor", "Oscar", "Priya", "Quinn", "Rosa", "Sven", "Tariq"]
LAST = ["Abara", "Berg", "Castillo", "Dubois", "Eze", "Fontaine", "Gupta", "Haddad", "Ivanov",
        "Jensen", "Kovacs", "Lindgren", "Mwangi", "Novak", "Okafor", "Petrov", "Quist", "Rossi"]

# code, description, whether it is a chronic condition
DIAGNOSES = [
    ("I10", "Essential hypertension", True), ("I20.9", "Angina pectoris, unspecified", True),
    ("I21.4", "Acute myocardial infarction", False), ("I25.1", "Coronary artery disease", True),
    ("I48.91", "Atrial fibrillation, unspecified", True), ("I50.9", "Heart failure, unspecified", True),
    ("I63.9", "Cerebral infarction, unspecified", False), ("I42.0", "Dilated cardiomyopathy", True),
    ("I47.1", "Supraventricular tachycardia", False), ("R07.9", "Chest pain, unspecified", False),
]

ADMISSION_FIELDS = [
    ("admission_id", "string", "none"), ("patient_name", "string", "direct"),
    ("age", "int", "quasi"), ("sex", "string", "quasi"), ("postcode", "string", "quasi"),
    ("diagnosis_code", "string", "phi"), ("admitted_on", "string", "quasi"),
    ("length_of_stay_days", "int", "quasi"), ("readmitted_30d", "bool", "phi"),
]
DIAGNOSIS_FIELDS = [("code", "string", "none"), ("description", "string", "none"), ("chronic", "bool", "none")]


def admissions() -> list[dict]:
    rng = random.Random(20261002)
    rows = []
    for i in range(400):
        age = max(18, min(96, int(rng.gauss(64, 15))))
        code = rng.choice(DIAGNOSES)[0]
        stay = max(1, int(rng.gauss(4 + (age - 60) / 15, 2)))
        rows.append({
            "admission_id": f"ADM-{i + 1:04d}",
            "patient_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
            "age": age, "sex": rng.choice(["F", "M"]),
            "postcode": f"{rng.choice(['NW', 'SE', 'EC', 'N', 'W'])}{rng.randint(1, 20)}",
            "diagnosis_code": code,
            "admitted_on": (date(2026, 1, 1) + timedelta(days=rng.randint(0, 270))).isoformat(),
            "length_of_stay_days": stay,
            "readmitted_30d": rng.random() < (0.08 + max(0, age - 60) / 250),
        })
    return rows


def diagnosis_codes() -> list[dict]:
    return [{"code": c, "description": d, "chronic": chronic} for c, d, chronic in DIAGNOSES]


# ----------------------------------------------------------------- finance --

HOLDERS = [f"{f} {l}" for f in FIRST for l in LAST[:6]]
MERCHANTS = [
    ("M001", "Harbour Groceries", "groceries", False), ("M002", "Northwind Electronics", "electronics", False),
    ("M003", "Skyline Air", "travel", False), ("M004", "QuickCash Transfers", "money transfer", True),
    ("M005", "Lumen Fuel", "fuel", False), ("M006", "Oro Jewellers", "jewellery", True),
    ("M007", "Pinecone Cafe", "dining", False), ("M008", "BetStar Online", "gambling", True),
    ("M009", "Meridian Hotels", "travel", False), ("M010", "Fable Books", "retail", False),
]
TRANSACTION_FIELDS = [
    ("txn_id", "string", "none"), ("account_holder", "string", "direct"), ("card_last4", "string", "direct"),
    ("amount", "float", "none"), ("merchant_id", "string", "none"), ("country", "string", "quasi"),
    ("occurred_at", "string", "quasi"), ("flagged", "bool", "quasi"),
]
MERCHANT_FIELDS = [("merchant_id", "string", "none"), ("merchant_name", "string", "none"),
                   ("category", "string", "none"), ("high_risk", "bool", "none")]


def transactions() -> list[dict]:
    rng = random.Random(20261003)
    rows = []
    for i in range(600):
        merchant = rng.choice(MERCHANTS)
        country = rng.choices(["US", "GB", "DE", "NG", "BR", "SG"], [60, 12, 8, 6, 8, 6])[0]
        amount = round(rng.lognormvariate(4.3, 1.1), 2)
        risky = merchant[3] and amount > 800 and country != "US"
        rows.append({
            "txn_id": f"T-{i + 1:05d}", "account_holder": rng.choice(HOLDERS),
            "card_last4": f"{rng.randint(0, 9999):04d}", "amount": amount, "merchant_id": merchant[0],
            "country": country,
            "occurred_at": (date(2026, 6, 1) + timedelta(days=rng.randint(0, 120))).isoformat(),
            "flagged": risky or rng.random() < 0.02,
        })
    return rows


def merchants() -> list[dict]:
    return [{"merchant_id": m, "merchant_name": n, "category": c, "high_risk": r} for m, n, c, r in MERCHANTS]


# -------------------------------------------------------------------- seeding --


def department(tenant: str, name: str, asking: str) -> str:
    orgs = get("/organisation", params={"tenant_id": tenant}, headers=bearer_for(asking)).json()
    for d in orgs["departments"]:
        if d["name"] == name:
            return d["id"]
    raise SystemExit(f"department {name!r} is missing from {tenant!r}. Apply infra/postgres first.")


def seed(*, tenant: str, engineer: str, custodian: str, dept: str, name: str, contract_name: str,
         fields: list[tuple], pk: str, rows: list[dict], klass: str) -> str:
    found = get("/datasets", params={"q": name, "has_versions": "true"}, headers=bearer_for(engineer))
    if found.status_code == 200 and any(d.get("name") == name for d in found.json().get("datasets", [])):
        print(f"  {tenant}/{name}: already sealed, left alone")
        return name

    session = bearer_for(engineer)
    r = post("/datasets/register", json={
        "tenant_id": tenant, "name": name, "department_id": dept, "registered_by": engineer,
        "provenance": "internal_regulated", "declared_class": klass, "source_kind": "upload",
        "modality": ["tabular"]}, headers=session)
    if r.status_code == 409 or (r.status_code >= 400 and "already" in r.text):
        raise SystemExit(f"{tenant}/{name} is registered but has no sealed version. Remove it and run again.")
    expect(r, 201, doing=f"registering {name}")
    dataset_id = r.json()["id"]

    if klass != "RAW":
        # Declaring data less restricted than raw is a claim, and a claim needs
        # the owning department's custodian to agree. Not the same person.
        c = post(f"/datasets/{dataset_id}/confirm-classification", json={"confirmed_by": custodian},
                 headers=bearer_for(custodian))
        expect(c, 200, doing=f"the custodian confirming {name} as {klass}")

    contract = post("/schema-contracts", json={
        "tenant_id": tenant, "name": contract_name, "primary_key": [pk],
        "fields": [{"name": n, "type": t, "sensitivity": s, "added_by": engineer} for n, t, s in fields]})
    expect(contract, 201, doing=f"registering the {contract_name} contract")

    body = json.dumps(rows).encode("utf-8")
    up = httpx.post(f"{API}/datasets/{dataset_id}/files", timeout=60.0, headers=session,
                    files={"file": ("records.json", body, "application/json")})
    expect(up, 201, doing=f"uploading the rows of {name}")
    # The upload does not hand back where the object went, by design. It went to
    # the version's reserved prefix, which is what next-version answers.
    where = get(f"/datasets/{dataset_id}/next-version", params={"tenant_id": tenant}).json()
    key = f"{where['storage_prefix']}/records.json"

    sealed = post("/dataset-versions", json={
        "tenant_id": tenant, "dataset_id": dataset_id, "schema_id": contract.json()["id"],
        "visibility_class": klass, "record_count": len(rows), "records_key": key,
        "object_manifest": [{"key": key, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}]})
    expect(sealed, 201, doing=f"sealing {name}")
    print(f"  {tenant}/{name}: sealed, {len(rows)} rows, {klass}")
    return name


def main() -> int:
    print("Seeding tabular demo data (every row is synthetic)")
    cardiology = department("health", "Cardiology", "eng-devi")
    seed(tenant="health", engineer="eng-devi", custodian="cust-hartley", dept=cardiology,
         name="admissions", contract_name="admission", fields=ADMISSION_FIELDS, pk="admission_id",
         rows=admissions(), klass="RAW")
    seed(tenant="health", engineer="eng-devi", custodian="cust-hartley", dept=cardiology,
         name="diagnosis_codes", contract_name="diagnosis_code", fields=DIAGNOSIS_FIELDS, pk="code",
         rows=diagnosis_codes(), klass="PUBLISHED")

    fraud = department("finance", "Fraud Operations", "eng-lena")
    risk = department("finance", "Risk & Compliance", "eng-lena")
    seed(tenant="finance", engineer="eng-lena", custodian="cust-marcus", dept=fraud,
         name="transactions", contract_name="transaction", fields=TRANSACTION_FIELDS, pk="txn_id",
         rows=transactions(), klass="RAW")
    seed(tenant="finance", engineer="eng-lena", custodian="cust-naomi", dept=risk,
         name="merchants", contract_name="merchant", fields=MERCHANT_FIELDS, pk="merchant_id",
         rows=merchants(), klass="PUBLISHED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
