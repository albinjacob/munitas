"""U142: a real custom pipeline step reads locked recordings through the platform, from inside a closed container, after a second person approved it.

U141 proves the platform's half: the approval, the pass and the routes. This proves what they exist for. A registered pipeline with one script step
that says `recordings: true` is run over two patients' locked recordings, and the script, using the library the platform gives it:

  * reads each recording three ways (as a file it can seek in, as bytes, and saved to a path inside its own container) and hashes what it got, which
    this check compares with the original sound, byte for byte;
  * then looks at its own surroundings and reports them: who it runs as, where it can and cannot write, what it can reach by name and by address,
    what the gateway answers, and what its environment holds.

Then one patient is erased and the same pipeline is run again: the step is told that recording was erased, and never opens it.

It runs on the host, because the script runs in the sandbox worker's Docker.

    $env:PG_DSN = "postgresql://munitas:munitas@localhost:5432/platform"
    $env:MUNITAS_VERIFY_KRATOS = "http://localhost:4433"
    $env:S3_ENDPOINT = "http://localhost:8333"
    .venv\\Scripts\\python.exe verify\\v141_custom_step_given_locked_recordings.py

It needs `python -m worker.main` and `python -m worker.sandbox_worker` running, and the step gateway container up. About two minutes.
"""

from __future__ import annotations

import hashlib
import io
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

SCRIPT = '''
import errno
import hashlib
import os
import socket
import urllib.error
import urllib.request

import munitas_step as step

seen, skipped = [], []
for item in step.list_recordings():
    record = item["record_id"]
    if item["erased"]:
        skipped.append(record)
        continue
    digest = hashlib.sha256()
    with step.open_recording(record) as source:
        source.seek(0, 2)
        size = source.tell()
        source.seek(0)
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    whole = step.read_recording(record)
    step.save_recording(record, "/tmp/" + record + ".wav")
    with open("/tmp/" + record + ".wav", "rb") as saved:
        saved_digest = hashlib.sha256(saved.read()).hexdigest()
    seen.append({"record_id": record, "bytes": size, "streamed": digest.hexdigest(), "whole": hashlib.sha256(whole).hexdigest(), "saved": saved_digest})


def attempt(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return "refused: " + type(exc).__name__


def write_to(path, size=1):
    with open(path, "wb") as handle:
        handle.write(b"x" * size)
    return True


def resolves(name):
    try:
        socket.gethostbyname(name)
        return True
    except OSError:
        return False


def connects(host, port):
    try:
        socket.create_connection((host, port), 3).close()
        return True
    except OSError:
        return False


def status_of(path):
    try:
        return urllib.request.urlopen(os.environ["MUNITAS_STEP_API"] + path, timeout=10).status
    except urllib.error.HTTPError as exc:
        return exc.code


def fill_tmp():
    # the scratch space is memory-backed and limited: a step that fills it is stopped by the limit, not by the machine
    try:
        write_to("/tmp/fill.bin", 300 * 1024 * 1024)
        return "wrote 300 MiB"
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))


probe = {
    "uid": os.getuid(),
    "write_root": attempt(lambda: write_to("/etc/x")),
    "write_code": attempt(lambda: write_to("/code/x")),
    "write_data": attempt(lambda: write_to("/data/x")),
    "write_tmp": attempt(lambda: write_to("/tmp/ok", 1024 * 1024)),
    "fill_tmp": fill_tmp(),
    "resolves": {name: resolves(name) for name in ("seaweedfs", "postgres", "munitas-api", "temporal", "opa")},
    "internet": connects("1.1.1.1", 443),
    "gateway_health": status_of("/healthz"),
    "gateway_credentials": status_of("/credentials"),
    "gateway_internal": status_of("/internal/recordings/begin"),
    "environment": sorted(os.environ),
}
step.result({"recommendation": "not_measured", "reason": "fixture", "seen": seen, "skipped": skipped, "probe": probe})
'''

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

    heading("The step reads the sound, three ways, from inside its container")
    run, out = run_and_wait(dataset, version, pv, engineer)
    seen = {x["record_id"]: x for x in out.get("seen", [])}
    check("it saw both recordings", sorted(seen) == ["s-0001", "s-0002"], str(sorted(seen)))
    for record, wav in (("s-0001", wav_a), ("s-0002", wav_b)):
        want = hashlib.sha256(wav).hexdigest()
        got = seen.get(record, {})
        check(f"{record}: read as a file, as bytes and saved to its own scratch space, it is the original sound, byte for byte, all three ways",
              got.get("streamed") == want and got.get("whole") == want and got.get("saved") == want and got.get("bytes") == len(wav), str(got)[:200])
    check("nobody was skipped", out.get("skipped") == [], str(out.get("skipped")))
    with db() as conn:
        first = [r["purpose"] for r in conn.execute("select purpose from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s", (CANARY, f"step_run {run.get('id')}")).fetchall()]
    check("every opening is in the key log, naming the step, the pipeline and its version: three ways of reading, two recordings, six openings",
          len(first) == 6 and all(f"custom step inspect of pipeline hash-{tag} version 1" in p for p in first), f"{len(first)}: {first[:1]}")

    heading("What the step could and could not do, as it reported it from inside")
    probe = out.get("probe", {})
    check("it ran as an unprivileged user, not as root", probe.get("uid") == 65534, str(probe.get("uid")))
    check("it could not write to the container's system folders, to its own code, or to its inputs",
          all(str(probe.get(k, "")).startswith("refused") for k in ("write_root", "write_code", "write_data")), str({k: probe.get(k) for k in ("write_root", "write_code", "write_data")}))
    check("it could write to its scratch space, which is the only place it can", probe.get("write_tmp") is True, str(probe.get("write_tmp")))
    check("and its scratch space is limited: writing more than it holds is stopped by the limit", probe.get("fill_tmp") == "ENOSPC", str(probe.get("fill_tmp")))
    check("it could not even find storage, the database, the platform, the workflow engine or the policy engine by name",
          probe.get("resolves") == {"seaweedfs": False, "postgres": False, "munitas-api": False, "temporal": False, "opa": False}, str(probe.get("resolves")))
    check("and it could not reach the internet", probe.get("internet") is False, str(probe.get("internet")))
    check("the gateway answered its health check, and refused everything that is not a step route",
          (probe.get("gateway_health"), probe.get("gateway_credentials"), probe.get("gateway_internal")) == (200, 404, 404),
          str({k: probe.get(k) for k in ("gateway_health", "gateway_credentials", "gateway_internal")}))
    names = probe.get("environment", [])
    # Exactly what the base image sets (GPG_KEY there is the public id of the key Python releases are signed with) and the platform's own four.
    allowed = {"GPG_KEY", "HOME", "HOSTNAME", "LANG", "PATH", "PYTHON_VERSION", "PYTHON_SHA256", "PYTHONPATH", "MUNITAS_RUN_ID", "MUNITAS_STEP_API", "MUNITAS_STEP_PASS"}
    check("its environment held the image's own settings, its pass and the gateway's address, and nothing else: no storage address, key, password or worker token",
          set(names) <= allowed and {"MUNITAS_STEP_API", "MUNITAS_STEP_PASS"} <= set(names), f"unexpected {sorted(set(names) - allowed)}")

    heading("One patient is erased, and the same pipeline is run again")
    with db() as conn:
        code = conn.execute("select locked_person_code from dataset_source where dataset_id = %s and locator = 's-0002.wav'", (dataset,)).fetchone()["locked_person_code"]
        conn.execute("update subject_key set wrapped_key = null, destroyed_at = now() where tenant_id = %s and person_code = %s", (CANARY, code))
    run2, out2 = run_and_wait(dataset, version, pv, engineer)
    check("the step was told that recording was erased, and saw only the other patient's", out2.get("skipped") == ["s-0002"] and [x["record_id"] for x in out2.get("seen", [])] == ["s-0001"],
          str({"skipped": out2.get("skipped"), "seen": [x["record_id"] for x in out2.get("seen", [])]}))
    with db() as conn:
        second = conn.execute("select person_code from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s", (CANARY, f"step_run {run2.get('id')}")).fetchall()
    check("the erased patient's key was never asked for in the second run", len(second) == 3 and all(r["person_code"] != code for r in second), str(second))

    reset_keys()
    return summary("v141")


if __name__ == "__main__":
    sys.exit(main())
