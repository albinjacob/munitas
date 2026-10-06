"""U142: a real custom pipeline step is given locked recordings, as sound, in its sandbox, after a second person approved its version.

U141 proves the platform's half: the approval and the two routes. This proves the thing they exist for. A registered pipeline with one custom script
step that says `recordings: true` is run over two patients' locked recordings. The script hashes each file it was given and tries to write into the
folder, and the check compares the hashes with the original sound, byte for byte. Then one patient is erased and the same pipeline is run again.

It runs on the host, because the script runs in the sandbox worker's Docker.

    $env:PG_DSN = "postgresql://munitas:munitas@localhost:5432/platform"
    $env:MUNITAS_VERIFY_KRATOS = "http://localhost:4433"
    $env:S3_ENDPOINT = "http://localhost:8333"
    .venv\\Scripts\\python.exe verify\\v141_custom_step_given_locked_recordings.py

It needs `python -m worker.main` and `python -m worker.sandbox_worker` running. About two minutes.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import time
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from common import API, CANARY, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402
from v128_person_keys_erasure import CUSTODIAN, ENGINEER, register_dataset, reset_keys  # noqa: E402
from v137_locked_recordings import make_wav, upload_whole  # noqa: E402

YAML = """
name: hash-the-sound
steps:
  - name: inspect
    kind: script
    script: steps/inspect.py
    recordings: true
  - name: decide
    kind: gate
    depends_on: [inspect]
    to_class: OPEN_FOR_ANNOTATION
    inputs:
      recommendation: "${steps.inspect.recommendation}"
      recommendation_reason: "${steps.inspect.reason}"
"""

SCRIPT = """
import hashlib
import json

data = json.load(open("/data/inputs.json"))
seen = []
for item in data["recordings"]:
    with open(item["path"], "rb") as handle:
        seen.append({"record_id": item["record_id"], "sha256": hashlib.sha256(handle.read()).hexdigest(), "bytes": item["bytes"]})
try:
    open("/data/audio/written-by-the-step", "wb").write(b"x")
    can_write = True
except OSError:
    can_write = False
print(json.dumps({"recommendation": "not_measured", "reason": "fixture", "seen": seen, "skipped": data["recordings_skipped"], "can_write": can_write}))
"""

CEILING_SECONDS = 8 * 60


def zipped() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("steps/inspect.py", SCRIPT)
    return buffer.getvalue()


def run_and_wait(dataset: str, version: str, pipeline_version: str, headers: dict) -> tuple[dict, dict]:
    started = api("POST", f"/datasets/{dataset}/versions/{version}/deidentify", json={"pipeline_kind": "dag", "pipeline_version_id": pipeline_version}, headers=headers)
    check("the run starts", started.status_code == 202, f"{started.status_code} {started.text[:240]}")
    if started.status_code != 202:
        return {}, {}
    run_id = started.json()["pipeline_run_id"]
    began = time.monotonic()
    run: dict = {}
    while time.monotonic() - began < CEILING_SECONDS:
        run = api("GET", f"/pipeline-runs/{run_id}", headers=headers).json()
        if run["status"] != "running":
            break
        time.sleep(5)
    with db() as conn:
        row = conn.execute("select output, status from pipeline_step_run where pipeline_run_id = %s and step_name = 'inspect'", (run_id,)).fetchone()
    check("it completes", run.get("status") == "succeeded", f"{run.get('status')} {run.get('error') or ''}")
    return {"id": run_id, **run}, (row["output"] if row and row["output"] else {})


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    engineer = bearer_for(ENGINEER)
    tag = uuid.uuid4().hex[:8]
    a, b = f"A-{tag}-chart", f"B-{tag}-chart"

    heading("Two patients' recordings, locked")
    wav_a, wav_b = make_wav(400_000), make_wav(300_000)
    dataset = register_dataset(ENGINEER)
    upload_whole(dataset, "s-0001.wav", wav_a, a)
    upload_whole(dataset, "s-0002.wav", wav_b, b)
    sealed = api("POST", f"/datasets/{dataset}/seal-recordings", headers=engineer)
    check("they seal", sealed.status_code == 201, sealed.text[:160])
    version = sealed.json()["id"]

    heading("A custom pipeline whose one step asks for the recordings")
    with db() as conn:
        dept = str(conn.execute("select id from department where tenant_id = %s and name = 'Verification'", (CANARY,)).fetchone()["id"])
    pipeline = api("POST", "/pipelines/register", json={"tenant_id": CANARY, "name": f"hash-{tag}", "department_id": dept, "registered_by": ENGINEER}, headers=engineer)
    pid = pipeline.json()["id"]
    uploaded = httpx.post(f"{API}/pipelines/{pid}/versions/upload", data={"registered_by": ENGINEER}, headers=engineer, timeout=30.0,
                          files={"config_file": ("pipeline.yaml", YAML.encode(), "application/x-yaml"), "scripts": ("scripts.zip", zipped(), "application/zip")})
    check("the version uploads", uploaded.status_code == 201, uploaded.text[:200])
    pv = uploaded.json()["id"]
    refused = api("POST", f"/datasets/{dataset}/versions/{version}/deidentify", json={"pipeline_kind": "dag", "pipeline_version_id": pv}, headers=engineer)
    check("before approval the run is refused", refused.status_code == 400 and "has not been approved" in refused.text, f"{refused.status_code} {refused.text[:200]}")
    approved = api("POST", f"/pipelines/{pid}/versions/{pv}/approve", json={"note": "reviewed on synthetic data"}, headers=bearer_for(CUSTODIAN))
    check("the department's approver approves it", approved.status_code == 200, approved.text[:160])

    heading("The step is given the sound")
    run, out = run_and_wait(dataset, version, pv, engineer)
    seen = {x["record_id"]: x for x in out.get("seen", [])}
    check("it saw both recordings", sorted(seen) == ["s-0001", "s-0002"], str(sorted(seen)))
    check("each is the original sound, byte for byte (the hash matches)", seen.get("s-0001", {}).get("sha256") == hashlib.sha256(wav_a).hexdigest()
          and seen.get("s-0002", {}).get("sha256") == hashlib.sha256(wav_b).hexdigest())
    check("its sizes are the plain sizes", seen.get("s-0001", {}).get("bytes") == len(wav_a) and seen.get("s-0002", {}).get("bytes") == len(wav_b))
    check("nobody was skipped", out.get("skipped") == [], str(out.get("skipped")))
    check("the step could not write into the folder it was given", out.get("can_write") is False, str(out.get("can_write")))
    with db() as conn:
        first = [r["purpose"] for r in conn.execute("select purpose from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s", (CANARY, f"pipeline_run {run.get('id')}")).fetchall()]
    check("two openings are in the key log, each naming the step, the pipeline and its version",
          len(first) == 2 and all(f"custom step inspect of pipeline hash-{tag} version 1" in p for p in first), str(first))

    heading("One patient is erased, and the same pipeline is run again")
    with db() as conn:
        code = conn.execute("select locked_person_code from dataset_source where dataset_id = %s and locator = 's-0002.wav'", (dataset,)).fetchone()["locked_person_code"]
        conn.execute("update subject_key set wrapped_key = null, destroyed_at = now() where tenant_id = %s and person_code = %s", (CANARY, code))
    run2, out2 = run_and_wait(dataset, version, pv, engineer)
    check("the step saw only the other patient's recording", [x["record_id"] for x in out2.get("seen", [])] == ["s-0001"], str(out2.get("seen")))
    check("and was told the erased patient's was left out", out2.get("skipped") == ["s-0002"], str(out2.get("skipped")))
    with db() as conn:
        second = conn.execute("select person_code from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s", (CANARY, f"pipeline_run {run2.get('id')}")).fetchall()
    check("the erased patient's key was never asked for in the second run", len(second) == 1 and second[0]["person_code"] != code, str(second))

    reset_keys()
    return summary("v141")


if __name__ == "__main__":
    sys.exit(main())
