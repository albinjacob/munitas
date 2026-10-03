"""Put a second worked example into a different kind of organisation.

`scripts/seed/seed-health-example.py` puts a hospital into tenant `health`: two departments,
consultation audio, a de-identification pipeline. Every screenshot, every
walkthrough, and every example anyone reading this repository has seen so
far is that hospital, which reads as "a healthcare tool" rather than "a
governance platform that happens to have a healthcare example." This script
exists to make that untrue: a card-payments company, a fraud operations
team, transaction records instead of consultation audio, in its own tenant
(`finance`), with nothing platform-specific changed to make it fit.

Same shape as scripts/seed/seed-health-example.py, deliberately: a dataset, a promotion, a
refusal, a grant, and a request left pending, so the console has something
true to show on every screen. Read that file first if a choice here doesn't
make sense on its own -- the reasoning is identical, only the industry
changed.

Everything here goes through the control plane API, never straight into the
database, for the same reason scripts/seed/seed-health-example.py gives: a fixture that
inserts rows behind the API can create states the API would refuse, and a
demonstration built out of impossible states teaches people the wrong thing
about the platform.

Idempotent: it looks for its own datasets by name first and does nothing if
they are already there.

    .venv\\Scripts\\python.exe scripts/seed/seed-finance-example.py
"""

from __future__ import annotations

import os
import sys

import httpx

from seed_common import bearer_for, expect
# seed_common's own import above already inserted the repo root onto sys.path.
from ports_config import PORTS  # noqa: E402

API = f"http://localhost:{PORTS['munitas_api_http']}"
WORKER_HEADERS = {"x-worker-token": os.environ.get("MUNITAS_WORKER_TOKEN", "dev-worker-token-not-for-production")}
TENANT = "finance"

FRAUD_OPS = "Fraud Operations"
RISK_COMPLIANCE = "Risk & Compliance"

# The people from infra/postgres/seed-finance.sql.
CUSTODIAN_FRAUD = "cust-marcus"
CUSTODIAN_RISK = "cust-naomi"
ENGINEER = "eng-lena"
ANALYST = "ana-omar"
PIPELINE = "finance-pipeline"
SCORER = "svc-fraud-scorer"


def post(path: str, **kwargs) -> httpx.Response:
    return httpx.post(f"{API}{path}", timeout=30.0, **kwargs)


def get(path: str, **kwargs) -> httpx.Response:
    return httpx.get(f"{API}{path}", timeout=30.0, **kwargs)


def department(name: str) -> str:
    orgs = get("/organisation", params={"tenant_id": TENANT}, headers=bearer_for(ENGINEER)).json()
    for d in orgs["departments"]:
        if d["name"] == name:
            return d["id"]
    raise SystemExit(
        f"department {name!r} is missing from {TENANT!r}. Apply "
        "infra/postgres/seed-finance.sql first."
    )


def contract_transactions() -> str:
    # "phi" (protected health information) is one value in a four-value
    # sensitivity enum ("none", "quasi", "direct", "phi"), not a healthcare-
    # only concept -- this contract uses "direct" and "quasi" instead, which
    # is the point: the same classification mechanism, a different industry.
    r = post("/schema-contracts", json={
        "tenant_id": TENANT,
        "name": "card-transaction",
        "fields": [
            {"name": "record_id", "type": "string", "sensitivity": "none",
             "added_by": "demo-data-seeder"},
            {"name": "cardholder_name", "type": "string", "sensitivity": "direct",
             "added_by": "demo-data-seeder"},
            {"name": "card_number_last4", "type": "string", "sensitivity": "quasi",
             "added_by": "demo-data-seeder"},
            {"name": "amount_cents", "type": "int", "sensitivity": "none",
             "added_by": "demo-data-seeder"},
            {"name": "merchant_category", "type": "string", "sensitivity": "none",
             "added_by": "demo-data-seeder"},
        ],
        "primary_key": ["record_id"],
    })
    r.raise_for_status()
    return r.json()["id"]


def contract_kyc() -> str:
    # Risk & Compliance's own schema, distinct from Fraud Operations's --
    # different department, different data, different reason it's sensitive
    # (identity verification, not transaction behaviour).
    r = post("/schema-contracts", json={
        "tenant_id": TENANT,
        "name": "kyc-identity-document",
        "fields": [
            {"name": "record_id", "type": "string", "sensitivity": "none",
             "added_by": "demo-data-seeder"},
            {"name": "applicant_name", "type": "string", "sensitivity": "direct",
             "added_by": "demo-data-seeder"},
            {"name": "document_type", "type": "string", "sensitivity": "none",
             "added_by": "demo-data-seeder"},
            {"name": "document_image_ref", "type": "string", "sensitivity": "direct",
             "added_by": "demo-data-seeder"},
        ],
        "primary_key": ["record_id"],
    })
    r.raise_for_status()
    return r.json()["id"]


def dataset(name: str, department_id: str, modality: list[str]) -> str:
    """Register through the ingest path, so the dataset arrives owned --
    same reasoning as scripts/seed/seed-health-example.py's own dataset(): `POST /datasets`
    creates one with no department, which is the state the ingest work
    exists to stop.
    """
    r = post("/datasets/register", json={
        "tenant_id": TENANT,
        "name": name,
        "department_id": department_id,
        "registered_by": ENGINEER,
        "provenance": "internal_regulated",
        "declared_class": "RAW",
        "source_kind": "upload",
        "modality": modality,
    })
    r.raise_for_status()
    return r.json()["id"]


def version(dataset_id: str, schema_id: str, klass: str, records: int) -> str:
    r = post("/dataset-versions", headers=WORKER_HEADERS, json={
        "tenant_id": TENANT,
        "dataset_id": dataset_id,
        "schema_id": schema_id,
        "visibility_class": klass,
        "object_manifest": [{"key": "part-0.json", "bytes": 4096}],
        "record_count": records,
    })
    r.raise_for_status()
    return r.json()["id"]


def agent(name: str, department_id: str, purpose: str) -> str:
    """Register the fraud-scoring agent the ordinary way, mirroring
    scripts/seed/seed-health-example.py's own agent(): idempotent by name, and registration
    only. A version, a deployment and a run are each their own real action
    with their own screen, so they happen live in the console rather than
    being pre-seeded here.
    """
    existing = get("/agents", params={"tenant_id": TENANT}, headers=bearer_for(ENGINEER)).json()["agents"]
    for a in existing:
        if a["name"] == name:
            return a["id"]
    r = post("/agents/register", json={
        "tenant_id": TENANT,
        "name": name,
        "department_id": department_id,
        "registered_by": ENGINEER,
        "purpose": purpose,
    })
    r.raise_for_status()
    return r.json()["id"]


def main() -> int:
    try:
        get("/health").raise_for_status()
    except Exception as exc:
        print(f"control plane unreachable at {API}: {exc}")
        return 2

    # Ahead of the dataset short-circuit below, and unconditional: a demo
    # that already has its datasets from an earlier run should still end up
    # with the agent registered, not skip it because something else exists.
    # Same reasoning as scripts/seed/seed-health-example.py's own triage agent.
    fraud_ops = department(FRAUD_OPS)
    scoring_id = agent(
        "fraud-transaction-scoring", fraud_ops,
        "Scores card transactions for likely fraud before a human reviews them",
    )
    print(f"  fraud-transaction-scoring agent ready ({scoring_id})")

    already = get("/datasets", params={"tenant_id": TENANT, "q": "transaction"}, headers=bearer_for(ENGINEER)).json()
    if already["total"] and already["datasets"]:
        print(f"{TENANT} already holds {already['total']} datasets. Nothing else to do.")
        return 0

    risk_compliance = department(RISK_COMPLIANCE)
    txn_schema = contract_transactions()
    kyc_schema = contract_kyc()

    print(f"Seeding a worked example into {TENANT}")

    # Raw card transactions, aggregated into a merchant-day summary that
    # drops cardholder identity, then released. The promotion is what makes
    # the version detail screen worth opening: the one place the platform
    # shows a class changing without the bytes moving.
    raw = dataset("card-transaction-log", fraud_ops, ["tabular"])
    raw_v = version(raw, txn_schema, "RAW", 48000)
    print("  card-transaction-log v1, raw, 48000 records")

    summary_ds = dataset("card-transaction-summary", fraud_ops, ["tabular"])
    summary_v = version(summary_ds, txn_schema, "UNDER_REVIEW", 48000)
    promoted = post(f"/dataset-versions/{summary_v}/promote", json={
        "to_class": "PUBLISHED",
        "decided_by": PIPELINE,
        "decided_by_kind": "workload",
        "gate_evidence": {
            "cardholder_pii_fields_removed": True,
            "false_positive_rate": 0.021,
            "note": "gate passed against the labelled holdout set",
        },
        "grant_roles": ["analyst", "training_job"],
    })
    expect(promoted, 200, doing="promoting card-transaction-summary")
    print("  card-transaction-summary v1, under review, promoted to published")

    # A refusal. The audit screen leads with denials, and a platform that
    # has never refused anything cannot demonstrate the thing it is for.
    refused = post("/credentials", json={
        "principal": SCORER,
        "principal_kind": "workload",
        "roles": ["training_job"],
        "tenant_id": TENANT,
        "dataset_version_id": raw_v,
        "purpose": "train a fraud model",
    })
    # The refusal is the demonstration, so 403 is the success case here and
    # a 200 would mean the platform let something through it should not have.
    expect(refused, 403, doing="the scoring job's refused credential request")
    print("  a scoring job asked for raw transactions and was refused")

    # And a grant, so the log is not all refusals.
    post("/credentials", json={
        "principal": PIPELINE,
        "principal_kind": "workload",
        "roles": ["pipeline_action"],
        "tenant_id": TENANT,
        "dataset_version_id": raw_v,
        "purpose": "aggregate into a merchant-day summary",
    })
    print("  the fraud triage pipeline asked for the same version and was granted it")

    # A request waiting on a decision, so the custodian's queue is not empty
    # when somebody logs in as Marcus.
    request = post("/leases/requests", json={
        "tenant_id": TENANT,
        "principal": ANALYST,
        "dataset_version_id": raw_v,
        "purpose": "card fraud detection model",
        "justification":
            "Measuring the false-positive rate the aggregation costs needs "
            "the original transactions for a sample of 500 accounts.",
        "ttl_hours": 72,
    }, headers=bearer_for(ANALYST))
    expect(request, 201, doing=f"the analyst's access request to {CUSTODIAN_FRAUD}")
    print(f"  an analyst asked {CUSTODIAN_FRAUD} for access, still pending")

    # One already decided, so the queue shows a history rather than a single
    # row with no precedent.
    older = post("/leases/requests", json={
        "tenant_id": TENANT,
        "principal": ANALYST,
        "dataset_version_id": summary_v,
        "purpose": "card fraud detection model",
        "justification": "Model training on the merchant-day summary.",
        "ttl_hours": 168,
    }, headers=bearer_for(ANALYST))
    expect(older, 201, doing="the earlier request that gets approved")
    approved = post(
        f"/leases/requests/{older.json()['id']}/approve",
        json={"approver": CUSTODIAN_FRAUD},
        headers=bearer_for(CUSTODIAN_FRAUD),
    )
    # 201, not 200: approving creates the lease, and the response is it.
    expect(approved, 201, doing=f"{CUSTODIAN_FRAUD} approving the earlier request")
    print(f"  an earlier request was approved by {CUSTODIAN_FRAUD}")

    # Risk & Compliance: a completely different kind of data (identity
    # documents, not transaction behaviour) under a different custodian, so
    # custodian scoping is something to watch inside finance too, the same
    # way Hartley/Okonjo show it inside health. Marcus cannot see this
    # department's request; only Naomi can.
    kyc = dataset("kyc-identity-documents", risk_compliance, ["image"])
    kyc_v = version(kyc, kyc_schema, "RAW", 3200)
    print("  kyc-identity-documents v1, raw, 3200 records")

    kyc_request = post("/leases/requests", json={
        "tenant_id": TENANT,
        "principal": ENGINEER,
        "dataset_version_id": kyc_v,
        "purpose": "identity document deduplication pipeline",
        "justification":
            "Building a pipeline that flags duplicate applicants needs the "
            "original document images to compare against, not just the "
            "extracted fields.",
        "ttl_hours": 72,
    }, headers=bearer_for(ENGINEER))
    expect(kyc_request, 201, doing=f"the engineer's access request to {CUSTODIAN_RISK}")
    print(f"  an engineer asked {CUSTODIAN_RISK} for access, still pending")

    print(f"\nDone. Open the console and act as one of {TENANT}'s people.")
    print(f"{CUSTODIAN_FRAUD} has a Fraud Operations request waiting; "
          f"{CUSTODIAN_RISK} has a Risk & Compliance one; {ENGINEER} and "
          f"{ANALYST} see both departments.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
