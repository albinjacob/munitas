"""Give the Health organisation a note drafter that really runs, and the data to run it on.

Until now `consultation-note-drafter` was only a record: a name, a purpose, declared tools. This replaces its code with a project that runs in the platform's sandbox (examples/note-drafter): it
reads consultation transcripts and drafts a visit note from each, for a clinician to read, correct and sign. It adds, through the control plane and as the organisation's own people:

  1. a dataset of three made-up consultation transcripts, owned by the Cardiology department, sealed as a version;
  2. a second version of the agent made of that code. It asks for a copy of the data it is run on, and it names no model, so the platform sees what it widens: it is held for the
     Cardiology department's approval and the AI risk owner's, because the agent is held at High;
  3. both approvals, as Hartley and Okafor, and the deployment, as Devi;
  4. a third version, the corrected drafter, left for a person to deploy. Version 2 is the drafter as it was first written (scripts/seed/fixtures/note-drafter-first-version), which files a
     greeting and a question under Plan, so that there is a mistake for a reviewer to find.

Starting a run is left to you: it is the part to try in the console. Idempotent: it looks for the dataset and for an uploaded version first.

    .venv\\Scripts\\python.exe scripts/seed/seed-health-note-drafter.py

Health is walkthrough data. Tests never use it.
"""

from __future__ import annotations

import io
import sys
import time
import zipfile
from pathlib import Path

import httpx

from seed_common import bearer_for, expect
from ports_config import PORTS  # noqa: E402

API = f"http://localhost:{PORTS['munitas_api_http']}"
TENANT = "health"
ENGINEER = "eng-devi"
OFFICER = "risk-okafor"
APPROVER = "cust-hartley"
DEPARTMENT = "Cardiology"
DATASET = "synthetic-consultation-transcripts"
AGENT = "consultation-note-drafter"

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "examples" / "note-drafter"                                   # the corrected drafter: version 3
FIRST_VERSION = ROOT / "scripts" / "seed" / "fixtures" / "note-drafter-first-version"   # the drafter as it was first written: version 2


def call(method: str, path: str, who: str, *allowed: int, doing: str, **kwargs) -> httpx.Response:
    r = httpx.request(method, f"{API}{path}", headers=bearer_for(who), timeout=120.0, **kwargs)
    expect(r, *allowed, doing=doing)
    return r


def department_id() -> str:
    org = call("GET", "/organisation", ENGINEER, 200, doing="reading the organisation", params={"tenant_id": TENANT}).json()
    return next(d["id"] for d in org["departments"] if d["name"] == DEPARTMENT)


def project_zip(project: Path) -> bytes:
    """The note drafter as the platform takes it: a zip with its entry point named in munitas.json."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(project / "main.py", "main.py")
        archive.write(project / "munitas.json", "munitas.json")
    return buffer.getvalue()


def upload(agent_id: str, project: Path, filename: str) -> dict:
    return call("POST", f"/agents/{agent_id}/versions/upload", ENGINEER, 201, doing="uploading the code", files={"zip": (filename, project_zip(project), "application/zip")}, data={
        "model_id": "none", "model_hosting": "platform", "tool_scope": "", "declares_no_tools": "true", "data_access": "copy", "requested_hosts": "", "registered_by": ENGINEER}).json()


def main() -> int:
    call("GET", "/health", ENGINEER, 200, doing="reaching the platform")

    # 1. the data
    found = call("GET", "/datasets", ENGINEER, 200, doing="looking for the dataset", params={"q": DATASET}).json()["datasets"]
    existing = next((d for d in found if d["name"] == DATASET), None)
    if existing:
        print(f"{DATASET} is already here")
    else:
        made = call("POST", "/datasets/register", ENGINEER, 201, doing="registering the dataset", json={
            "tenant_id": TENANT, "name": DATASET, "department_id": department_id(), "registered_by": ENGINEER, "provenance": "internal_regulated", "declared_class": "RAW",
            "source_kind": "upload", "modality": ["text"]}).json()
        for sample in sorted((PROJECT / "sample-transcripts").glob("*.txt")):
            call("POST", f"/datasets/{made['id']}/files", ENGINEER, 201, doing=f"uploading {sample.name}", files={"file": (sample.name, sample.read_bytes(), "text/plain")})
        sealed = call("POST", f"/datasets/{made['id']}/seal", ENGINEER, 201, doing="sealing the dataset").json()
        print(f"registered and sealed {DATASET}: version {sealed['id']} ({sealed.get('files')} files)")

    # 2. the agent's code, as a new version
    agents = call("GET", "/agents", ENGINEER, 200, doing="listing agents", params={"limit": 200}).json()["agents"]
    agent = next(a for a in agents if a["name"] == AGENT)
    detail = call("GET", f"/agents/{agent['id']}", ENGINEER, 200, doing="reading the agent").json()
    made_of_code = [v for v in detail["versions"] if v["sandboxed"]]
    if made_of_code:
        print(f"{AGENT} already has a version made of code")
        if len(made_of_code) < 2:
            corrected = upload(agent["id"], PROJECT, "note-drafter-corrected.zip")
            print(f"uploaded version {corrected['version']}, the corrected drafter, and left it for a person to deploy")
        return 0
    up = upload(agent["id"], FIRST_VERSION, "note-drafter.zip")
    print(f"uploaded version {up['version']}: widening {up['widening']}")
    for change in up["changes"]:
        print(f"   {change['label']}. {change['what']}")

    # 3. the approvals, and the deployment
    approvals = call("GET", f"/agents/{agent['id']}/change-approvals", OFFICER, 200, doing="reading the changes").json()["approvals"]
    approval = next(a for a in approvals if a["agent_version_id"] == up["id"])
    if approval["state"] == "waiting":
        call("POST", f"/agent-change-approvals/{approval['id']}/decisions", APPROVER, 201, doing="the department's approval", json={
            "part": "department", "decision": "approved", "reason": "The code only reads the transcripts it is given and writes a draft. It reaches nothing outside the platform."})
        call("POST", f"/agent-change-approvals/{approval['id']}/decisions", OFFICER, 201, doing="the officer's approval", json={
            "part": "officer", "decision": "approved", "reason": "The agent stays at High. A clinician reads and signs every draft, and the draft quotes the transcript line by line so it can be checked."})
    call("POST", f"/agents/{agent['id']}/deploy", ENGINEER, 201, doing="deploying", json={"agent_version_id": up["id"]})
    print(f"{AGENT} version {up['version']} approved and deployed")
    # The corrected drafter is uploaded now and left for a person to deploy: it widens nothing against version 2, so it needs no approval, and the review walkthrough deploys it.
    corrected = upload(agent["id"], PROJECT, "note-drafter-corrected.zip")
    print(f"uploaded version {corrected['version']}, the corrected drafter, and left it for a person to deploy")
    time.sleep(0.2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
