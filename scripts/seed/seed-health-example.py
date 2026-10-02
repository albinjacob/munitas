"""Put a small worked example into the demonstration organisation.

`infra/postgres/seed-organisation.sql` registers who exists: two departments,
their custodians, a project, the workloads. It deliberately creates no data,
because who is accountable is one thing and what they are accountable for is
another.

The result was a console that opened onto an empty platform. Every screen was
correct and every screen was blank, which is the least useful way to be right.
So this script adds enough for each screen to have something true to show:
datasets in both departments, a promotion, a refusal, and a request already
decided. A request left waiting on a decision is added only when asked for
(--sample-queue): every real flow files its own request, so one left waiting by
default is noise that each of them has to work around.

Everything here goes through the control plane, never straight into the
database. A fixture that inserts rows behind the API can create states the API
would refuse, and a demonstration built out of impossible states teaches people
the wrong thing about the platform.

Idempotent: it looks for its own datasets by name first and does nothing if they
are already there.

    .venv\\Scripts\\python.exe scripts/seed/seed-health-example.py
    .venv\\Scripts\\python.exe scripts/seed/seed-health-example.py --sample-queue

The second form also leaves one request waiting for Hartley, so the custodian's
page has something to approve when it is opened by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

from seed_common import bearer_for, expect
# seed_common's own import above already inserted the repo root onto sys.path.
from ports_config import PORTS  # noqa: E402

API = f"http://localhost:{PORTS['munitas_api_http']}"
TENANT = "health"
# Only when asked for: see the module docstring.
SAMPLE_QUEUE = "--sample-queue" in sys.argv

CARDIOLOGY = "Cardiology"
RADIOLOGY = "Radiology"
ONCOLOGY = "Oncology"
ORTHOPAEDICS = "Orthopaedics"

SAMPLES = Path(__file__).resolve().parents[2] / "docs" / "audio-samples"

# The three synthetic consultation recordings, each into the department it is
# about. Oncology goes in without its answer key on purpose: its README says it
# exists to exercise a recording that arrives with no ground truth, which is
# what real recordings are like. The other two carry theirs, so a
# de-identification run on them can be scored.
# (dataset name, department, folder under docs/audio-samples, send answer key)
RECORDINGS = [
    ("cardiology-consultation-recording", CARDIOLOGY, "cardiology-consult", True),
    ("oncology-consultation-recording", ONCOLOGY, "oncology-consult", False),
    ("orthopaedics-consultation-recording", ORTHOPAEDICS, "orthopaedics-consult", True),
]

# The people from seed-organisation.sql. Named rather than looked up, because
# this file is a worked example and the example is about these particular roles.
CUSTODIAN_CARDIOLOGY = "cust-hartley"
CUSTODIAN_RADIOLOGY = "cust-okonjo"
RESEARCHER = "sam-researcher"
ENGINEER = "eng-devi"
PIPELINE = "health-pipeline"
TRAINER = "svc-trainer"


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
        "infra/postgres/seed-organisation.sql first."
    )


def contract() -> str:
    r = post("/schema-contracts", json={
        "tenant_id": TENANT,
        "name": "consultation",
        "fields": [
            {"name": "record_id", "type": "string", "sensitivity": "none",
             "added_by": "demo-data-seeder"},
            {"name": "transcript", "type": "string", "sensitivity": "phi",
             "added_by": "demo-data-seeder"},
        ],
        "primary_key": ["record_id"],
    })
    r.raise_for_status()
    return r.json()["id"]


def dataset(name: str, department_id: str, modality: list[str]) -> str:
    """Register through the ingest path, so the dataset arrives owned.

    `POST /datasets` creates one with no department, which is the state the
    ingest work exists to stop. A fixture that used it would demonstrate the
    problem rather than the platform.

    Declared at the most restrictive class on purpose. Declaring anything wider
    is a claim, and a claim needs a custodian to confirm it; the example shows
    data becoming readable through promotion with gate evidence, which is the
    path worth showing.
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
    r = post("/dataset-versions", json={
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
    """Register the triage agent the ordinary way, so its runtime identity
    comes into being through registration, the same way a real customer's
    would, rather than sitting pre-wired in a seed file with nothing to run
    it. Idempotent by name, the same as `dataset()`'s callers guard theirs.
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


def recording(name: str, department_id: str, folder: str, with_answer_key: bool) -> str:
    """Upload one real recording through the path a person would use.

    Unlike version() above, which only declares a manifest, this puts the
    bytes into health's own bucket, so the de-identification pipeline has
    something to run on. Registered, uploaded and sealed as Devi, because
    sealing needs a signed-in person, and the register records who that was.
    """
    session = bearer_for(ENGINEER)
    r = post("/datasets/register", json={
        "tenant_id": TENANT,
        "name": name,
        "department_id": department_id,
        "registered_by": ENGINEER,
        "provenance": "internal_regulated",
        "declared_class": "RAW",
        "source_kind": "upload",
        "modality": ["audio"],
    }, headers=session)
    expect(r, 201, doing=f"registering {name}")
    dataset_id = r.json()["id"]

    source = SAMPLES / folder
    uploads = [(f"{folder}.wav", source / f"{folder}-synthetic.wav", "audio/wav")]
    if with_answer_key:
        uploads.append((f"{folder}.truth.json", source / "truth.json", "application/json"))
    for filename, path, mime in uploads:
        r = httpx.post(f"{API}/datasets/{dataset_id}/files", timeout=120.0, headers=session,
                       files={"file": (filename, path.read_bytes(), mime)})
        expect(r, 201, doing=f"uploading {filename} into {name}")

    r = post(f"/datasets/{dataset_id}/seal-audio", headers=session)
    expect(r, 201, doing=f"sealing {name}")
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
    radiology = department(RADIOLOGY)
    triage_id = agent(
        "radiology-intake-triage", radiology,
        "Ranks radiology intake documents for human review",
    )
    print(f"  radiology-intake-triage agent ready ({triage_id})")

    # The pipeline's steps are registered per organisation, and nothing else
    # registers them for a freshly built one, so without this the console's
    # de-identify button fails at its second step. The list and the insert are
    # worker/run_pipeline.py's own, not a copy. Idempotent.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from worker.run_pipeline import ensure_actions
    print(f"  pipeline steps registered: {', '.join(sorted(ensure_actions(TENANT)))}")

    # Also ahead of the short-circuit, and idempotent by name for the same
    # reason: a health seeded before the recordings existed should gain them.
    missing = [f for f in SAMPLES.glob("*-consult") if not (f / f"{f.name}-synthetic.wav").is_file()]
    if missing or not SAMPLES.is_dir():
        raise SystemExit(f"recordings missing under {SAMPLES}; see its README files")
    have = {d["name"] for d in get("/datasets", params={"tenant_id": TENANT, "q": "recording"}, headers=bearer_for(ENGINEER)).json()["datasets"]}
    for name, dept, folder, with_key in RECORDINGS:
        if name in have:
            print(f"  {name} already there")
            continue
        version_id = recording(name, department(dept), folder, with_key)
        key = "with its answer key" if with_key else "with no answer key"
        print(f"  {name}: real audio sealed into {dept}, {key} ({version_id})")

    # By exact name, not by search: the recordings above are also named
    # "...-consultation-...", and a search for "consultation" would take them
    # for this worked example and skip it.
    already = get("/datasets", params={"tenant_id": TENANT, "q": "consultation-audio"}, headers=bearer_for(ENGINEER)).json()
    if any(d["name"] == "consultation-audio" for d in already["datasets"]):
        print(f"{TENANT} already holds the worked example. Nothing else to do.")
        return 0

    cardiology = department(CARDIOLOGY)
    schema = contract()

    print(f"Seeding a worked example into {TENANT}")

    # Cardiology: raw recordings, de-identified, then released. The promotion is
    # what makes the version detail screen worth opening, because it is the one
    # place the platform shows a class changing without the bytes moving.
    raw = dataset("consultation-audio", cardiology, ["audio"])
    raw_v = version(raw, schema, "RAW", 240)
    print("  consultation-audio v1, raw, 240 records")

    deid = dataset("consultation-deidentified", cardiology, ["audio", "text"])
    deid_v = version(deid, schema, "UNDER_REVIEW", 240)
    promoted = post(f"/dataset-versions/{deid_v}/promote", json={
        "to_class": "PUBLISHED",
        "decided_by": PIPELINE,
        "decided_by_kind": "workload",
        "gate_evidence": {
            "recall": 0.991,
            "direct_identifier_leaks": 0,
            "note": "gate passed on the held-out set",
        },
        "grant_roles": ["notebook_explore", "training_job"],
    })
    expect(promoted, 200, doing="promoting consultation-deidentified")
    print("  consultation-deidentified v1, under review, promoted to published")

    # Radiology, so the console has a second department and custodian scoping is
    # something somebody can watch rather than only read about.
    reports = dataset("radiology-reports", radiology, ["text"])
    version(reports, schema, "RAW", 1120)
    print("  radiology-reports v1, raw, 1120 records")

    # A refusal. The audit screen leads with denials, and a platform that has
    # never refused anything cannot demonstrate the thing it is for.
    refused = post("/credentials", json={
        "principal": TRAINER,
        "principal_kind": "workload",
        "roles": ["training_job"],
        "tenant_id": TENANT,
        "dataset_version_id": raw_v,
        "purpose": "train a model",
    })
    # The refusal is the demonstration, so 403 is the success case here and
    # a 200 would mean the platform let something through it should not have.
    expect(refused, 403, doing="the training job's refused credential request")
    print("  a training job asked for raw audio and was refused")

    # And a grant, so the log is not all refusals, which would be its own kind of
    # misleading.
    post("/credentials", json={
        "principal": PIPELINE,
        "principal_kind": "workload",
        "roles": ["pipeline_action"],
        "tenant_id": TENANT,
        "dataset_version_id": raw_v,
        "purpose": "de-identify",
    })
    print("  the de-identification pipeline asked for the same version and was granted it")

    # A request waiting on a decision, so the custodian's queue is not empty when
    # somebody logs in as Hartley by hand. Only when asked for (--sample-queue):
    # nothing else uses it, and every real flow files its own request, so by default
    # it would only be one more row each of them has to work around.
    if SAMPLE_QUEUE:
        request = post("/leases/requests", json={
            "tenant_id": TENANT,
            "principal": RESEARCHER,
            "dataset_version_id": raw_v,
            "purpose": "arrhythmia detection study",
            "justification":
                "Measuring how much recall the de-identification costs needs the "
                "original audio for a sample of 40 encounters.",
            "ttl_hours": 72,
        }, headers=bearer_for(RESEARCHER))
        expect(request, 201,
               doing=f"the researcher's access request to {CUSTODIAN_CARDIOLOGY}")
        print(f"  a researcher asked {CUSTODIAN_CARDIOLOGY} for access, still pending")

    # One already decided, so the queue shows a history rather than a single row
    # with no precedent.
    older = post("/leases/requests", json={
        "tenant_id": TENANT,
        "principal": RESEARCHER,
        "dataset_version_id": deid_v,
        "purpose": "arrhythmia detection study",
        "justification": "Model training on the de-identified set.",
        "ttl_hours": 168,
    }, headers=bearer_for(RESEARCHER))
    expect(older, 201, doing="the earlier request that gets approved")
    approved = post(
        f"/leases/requests/{older.json()['id']}/approve",
        json={"approver": CUSTODIAN_CARDIOLOGY},
        headers=bearer_for(CUSTODIAN_CARDIOLOGY),
    )
    # 201, not 200: approving creates the lease, and the response is it.
    expect(approved, 201, doing=f"{CUSTODIAN_CARDIOLOGY} approving the earlier request")
    print(f"  an earlier request was approved by {CUSTODIAN_CARDIOLOGY}")

    print(f"\nDone. Open the console and act as one of {TENANT}'s people.")
    waiting = (f"{CUSTODIAN_CARDIOLOGY} has a request waiting; " if SAMPLE_QUEUE
               else "Nothing is left waiting (use --sample-queue to add one); ")
    print(f"{waiting}{ENGINEER} and {CUSTODIAN_RADIOLOGY} see their own departments.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
