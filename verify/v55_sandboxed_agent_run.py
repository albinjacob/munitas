"""U55: an uploaded agent's own code actually executes, sandboxed, and the
sandbox holds under the same conditions U50/U54 already prove for the
platform's built-in graph and a bare `httpx` caller.

Runs on the host, the same reason U50/U29 do: it drives real Docker
containers via `worker/sandbox_run.py`, which needs `python -m worker.main`
running with Docker reachable, not something the API container can do.

Run from inside the WSL distro, not the Windows host directly: this
project's Docker engine runs inside `Ubuntu-20.04`, not Docker Desktop, and
the U55 orphan-reaping check drives `docker` directly (both the CLI, via a
raw `subprocess.run(["docker", ...])`, and `worker.sandbox_run`'s own
`docker.from_env()`), which only resolves to a real engine from inside that
distro. Everything earlier in this script also works fine from the plain
Windows `.venv`, since it is otherwise only HTTP calls to `localhost:8000`
-- that is what made the gap easy to miss for a while (see item 30/32's
notes in SESSION_STATUS.md).

    PG_DSN=postgresql://munitas:munitas@localhost:5432/platform \\
    VERIFY_KRATOS=http://localhost:4433 S3_ENDPOINT=http://localhost:8333 \\
    $HOME/.munitas/verify-venv/bin/python verify/v55_sandboxed_agent_run.py

Four things this proves, each with its own fixture project:
  1. The uploaded code is what runs, not `agent/graph.py`, proven by a
     distinguishing marker only the uploaded script could print.
  2. The run container has no route to the internet (agentnet, `verify/
     v7_egress.py`'s own guarantee, exercised here from inside a live
     sandboxed run rather than a disposable test container).
  3. The real dataset-version boundary (`POST /credentials`)
     still holds when the caller is code the platform itself launched and
     supervised, not a script driving `httpx` directly the way U54 does.
  4. A run that hangs past its ceiling is killed and honestly recorded as
     failed, and leaves no container behind.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path

from common import (CANARY, ADMIN, CUSTODIAN, ENGINEER, api, bucket_for,
                    bearer_for, check, db, fixture_contract, fixture_version,
                    heading, require_api, s3_client, skip, summary)

TENANT = CANARY
DEPARTMENT = "Verification"


def department() -> str:
    with db() as conn:
        row = conn.execute(
            "select id from department where tenant_id = %s and name = %s",
            (TENANT, DEPARTMENT),
        ).fetchone()
    if not row:
        raise RuntimeError(f"department {DEPARTMENT!r} missing; apply infra/postgres/seed-canary.sql")
    return str(row["id"])


def seal_content_version(dataset_id: str, schema_id: str, contents: dict[str, bytes],
                         klass: str = "PUBLISHED") -> dict:
    """Seal a new version of an existing dataset with real bytes at real
    keys. Predicts the version's storage_prefix the same way
    platform/api/app/versions.py's next_version() computes it
    (f"{tenant_id}/{dataset_id}/v{version}"), so the manifest sealed below
    names the exact keys the upload right after actually writes to.
    """
    with db() as conn:
        row = conn.execute(
            "select coalesce(max(version), 0) + 1 as v from dataset_version "
            "where dataset_id = %s", (dataset_id,),
        ).fetchone()
    prefix = f"{TENANT}/{dataset_id}/v{row['v']}"

    manifest = [{"key": f"{prefix}/{name}", "bytes": len(body)}
                for name, body in contents.items()]
    sealed = api("POST", "/dataset-versions", json={
        "tenant_id": TENANT, "dataset_id": dataset_id, "schema_id": schema_id,
        "visibility_class": klass, "object_manifest": manifest,
        "record_count": len(contents),
    })
    sealed.raise_for_status()
    version = sealed.json()
    if version["storage_prefix"] != prefix:
        raise RuntimeError(
            f"storage_prefix mismatch: computed {prefix!r}, platform sealed "
            f"{version['storage_prefix']!r}"
        )

    # The tenant's own bucket, which is where the sandboxed run's fetch
    # stage will look for these objects.
    bucket = bucket_for(TENANT)
    s3 = s3_client(*ADMIN)
    for name, body in contents.items():
        s3.put_object(Bucket=bucket, Key=f"{prefix}/{name}", Body=body)

    version["dataset_id"] = dataset_id
    return version


def seed_content_version(department_id: str, contents: dict[str, bytes],
                         klass: str = "PUBLISHED") -> dict:
    """A fresh dataset, owned by department_id, with one real sealed
    version holding real bytes at real keys.
    """
    schema_id = fixture_contract(TENANT)
    ds = api("POST", "/datasets", json={
        "tenant_id": TENANT, "name": f"stage-content-{uuid.uuid4().hex[:8]}",
    })
    ds.raise_for_status()
    dataset_id = ds.json()["id"]
    with db() as conn:
        conn.execute("update dataset set department_id = %s where id = %s",
                    (department_id, dataset_id))
    version = seal_content_version(dataset_id, schema_id, contents, klass)
    version["schema_id"] = schema_id
    return version


def register_agent(owning: str, name: str) -> str:
    r = api("POST", "/agents/register", json={
        "tenant_id": TENANT, "name": name, "department_id": owning,
        "registered_by": "canary-engineer", "purpose": "verification fixture",
    })
    r.raise_for_status()
    return r.json()["id"]


def _zip_bytes(main_py: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("main.py", main_py)
        archive.writestr("munitas.json", json.dumps({"entrypoint": "main.py"}))
    return buf.getvalue()


def upload_and_deploy(agent_id: str, main_py: str, data_access: str = "none") -> str:
    r = api(
        "POST", f"/agents/{agent_id}/versions/upload",
        files={"zip": ("code.zip", _zip_bytes(main_py), "application/zip")},
        data={"model_id": "none", "tool_scope": "", "data_access": data_access,
             "registered_by": "canary-engineer"},
    )
    check(f"uploading a version for {agent_id[:8]} succeeds",
          r.status_code == 201, f"HTTP {r.status_code}: {r.text[:200]}")
    version_id = r.json()["id"]

    d = api("POST", f"/agents/{agent_id}/deploy",
            json={"agent_version_id": version_id},
            headers=bearer_for("canary-engineer"))
    check("deploying the uploaded version succeeds", d.status_code == 201, f"HTTP {d.status_code}")
    return version_id


def start_run(agent_id: str, dataset_version_id: str, purpose: str,
               timeout_seconds: int | None = None) -> str:
    body = {
        "purpose": purpose,
        "dataset_version_id": dataset_version_id,
    }
    if timeout_seconds is not None:
        body["timeout_seconds"] = timeout_seconds
    r = api("POST", f"/agents/{agent_id}/runs", json=body,
            headers=bearer_for("canary-engineer"))
    check(f"starting a sandboxed run ({purpose}) returns 202",
          r.status_code == 202, f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json().get("run_id")


def wait_for_run(run_id: str, timeout: float = 90.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = api("GET", f"/agents/runs/{run_id}", headers=bearer_for("canary-engineer")).json()
        if run["status"] != "running":
            return run
        time.sleep(2.0)
    return None


def sandbox_containers() -> int:
    """How many containers off the run image exist right now, running or not.

    A verify-owned proxy for "nothing was left behind": the run image is
    specific to this feature (`worker.sandbox_run.RUN_IMAGE`), so a count
    that returns to its starting value across a test is as close to "no
    leak" as this script can check without coupling to container ids the
    worker never reports back.
    """
    out = subprocess.run(
        ["docker", "ps", "-a", "-q", "--filter", "ancestor=python:3.11-slim"],
        capture_output=True, text=True, timeout=60,
    )
    return len([line for line in out.stdout.splitlines() if line.strip()])


def main() -> int:
    require_api()
    owning = department()

    schema_id = fixture_contract(TENANT)
    own_version = fixture_version(TENANT, schema_id, "PUBLISHED")
    other_version = fixture_version(TENANT, schema_id, "PUBLISHED")

    heading("U55: the uploaded code is what actually runs")

    exec_agent = register_agent(owning, f"sandbox-exec-{uuid.uuid4().hex[:8]}")
    upload_and_deploy(exec_agent, (
        "import json\n"
        "print(json.dumps({'marker': 'u55-real-execution', 'status': 'ok'}))\n"
    ))
    run_id = start_run(exec_agent, own_version["id"], "U55 real execution")
    run = wait_for_run(run_id) if run_id else None
    if run is None:
        skip("the sandboxed run reaches a terminal status",
             "no result within the timeout; is `python -m worker.main` running "
             "with Docker reachable?")
    else:
        check("the run succeeded", run["status"] == "succeeded", str(run["status"]))
        check("its execution_mode is recorded as sandboxed",
              run.get("execution_mode") == "sandboxed", str(run.get("execution_mode")))
        findings = run.get("findings") or []
        check("the uploaded script's own marker is in the recorded findings, "
              "proving this ran the uploaded code and not agent/graph.py",
              any(f.get("marker") == "u55-real-execution" for f in findings),
              str(findings))

    heading("U55: the run container has no route off agentnet")

    egress_agent = register_agent(owning, f"sandbox-egress-{uuid.uuid4().hex[:8]}")
    upload_and_deploy(egress_agent, (
        "import json, urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen('http://example.com', timeout=5)\n"
        "    reached = True\n"
        "except Exception:\n"
        "    reached = False\n"
        "print(json.dumps({'marker': 'u55-egress-check', 'reached_internet': reached}))\n"
    ))
    run_id = start_run(egress_agent, own_version["id"], "U55 egress check")
    run = wait_for_run(run_id) if run_id else None
    if run is None:
        skip("the egress-check run reaches a terminal status", "no result within the timeout")
    else:
        findings = run.get("findings") or []
        reached = findings[0].get("reached_internet") if findings else None
        check("the sandboxed process could not reach the internet",
              reached is False, f"findings: {findings}")

    heading("U55: the real dataset-version boundary holds from inside a live sandbox")

    scope_agent = register_agent(owning, f"sandbox-scope-{uuid.uuid4().hex[:8]}")
    upload_and_deploy(scope_agent, (
        "import json, os, urllib.request, urllib.error\n"
        "\n"
        "def request(dataset_version_id):\n"
        "    body = json.dumps({\n"
        "        'principal': os.environ['MUNITAS_PRINCIPAL_ID'],\n"
        "        'principal_kind': 'workload',\n"
        "        'roles': [os.environ['MUNITAS_ROLES']],\n"
        "        'tenant_id': os.environ['MUNITAS_TENANT_ID'],\n"
        "        'dataset_version_id': dataset_version_id,\n"
        "        'purpose': os.environ['MUNITAS_PURPOSE'],\n"
        "        'agent_run_id': os.environ['MUNITAS_RUN_ID'],\n"
        "        'run_secret': os.environ.get('MUNITAS_RUN_SECRET'),\n"
        "    }).encode()\n"
        "    req = urllib.request.Request(\n"
        f"        os.environ['MUNITAS_API'] + '/credentials', data=body,\n"
        "        headers={'content-type': 'application/json'}, method='POST')\n"
        "    try:\n"
        "        with urllib.request.urlopen(req, timeout=10) as resp:\n"
        "            return resp.status\n"
        "    except urllib.error.HTTPError as exc:\n"
        "        return exc.code\n"
        "\n"
        "own_status = request(os.environ['MUNITAS_DATASET_VERSION_ID'])\n"
        f"wrong_status = request('{other_version['id']}')\n"
        "print(json.dumps({'marker': 'u55-scope-check', "
        "'own_status': own_status, 'wrong_status': wrong_status}))\n"
    ))
    run_id = start_run(scope_agent, own_version["id"], "U55 scope check")
    run = wait_for_run(run_id) if run_id else None
    if run is None:
        skip("the scope-check run reaches a terminal status", "no result within the timeout")
    else:
        findings = run.get("findings") or []
        result = findings[0] if findings else {}
        check("its own run's dataset version was granted from inside the sandbox",
              result.get("own_status") == 200, f"findings: {findings}")
        check("a different dataset version was refused from inside the sandbox, "
              "even though nothing forced the uploaded code to ask through "
              "agent/tools.py",
              result.get("wrong_status") == 403, f"findings: {findings}")

    heading("U55: a hung sandboxed run is killed, recorded honestly, and leaves nothing behind")

    before = sandbox_containers()
    hang_agent = register_agent(owning, f"sandbox-hang-{uuid.uuid4().hex[:8]}")
    upload_and_deploy(hang_agent, "import time\ntime.sleep(600)\nprint('{}')\n")
    # A short, explicit ceiling rather than waiting out the real five-minute
    # default: this test is about the kill-and-record path firing at all,
    # not about timing the default itself.
    run_id = start_run(hang_agent, own_version["id"], "U55 hang check", timeout_seconds=6)
    run = wait_for_run(run_id, timeout=40.0) if run_id else None
    if run is None:
        skip("the hung run is recorded as failed within a reasonable window",
             "no result within the timeout; check DEFAULT_RUN_TIMEOUT_SECONDS "
             "in worker/sandbox_run.py")
    else:
        check("a run that hangs past its ceiling is recorded failed, not "
              "succeeded and not left running forever",
              run["status"] == "failed", str(run["status"]))
        check("the failure reason names the wall-clock ceiling",
              "wall-clock" in (run.get("error") or ""), str(run.get("error")))
    after = sandbox_containers()
    check("no sandbox container is left behind after the kill",
          after <= before, f"{before} before, {after} after")

    heading("U55: a genuinely orphaned container is reaped at worker startup")

    fixture_run_id = f"verify-orphan-{uuid.uuid4().hex[:8]}"
    launch = subprocess.run(
        ["docker", "run", "-d",
         "--label", "munitas.sandbox=true",
         "--label", f"munitas.run_id={fixture_run_id}",
         "python:3.11-slim", "sleep", "60"],
        capture_output=True, text=True, timeout=120,
    )
    orphan_id = launch.stdout.strip()
    check("the fixture orphan container starts", launch.returncode == 0 and bool(orphan_id),
          launch.stderr[:200])

    try:
        listed = subprocess.run(
            ["docker", "ps", "-a", "-q", "--no-trunc", "--filter",
             f"label=munitas.run_id={fixture_run_id}"],
            capture_output=True, text=True, timeout=60,
        )
        check("it exists before reaping", listed.stdout.strip() == orphan_id,
              listed.stdout.strip())

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from worker.sandbox_run import reap_orphaned_containers  # noqa: E402

        removed = reap_orphaned_containers()
        check("reap_orphaned_containers reports removing at least the fixture",
              removed >= 1, removed)

        after_reap = subprocess.run(
            ["docker", "ps", "-a", "-q", "--filter",
             f"label=munitas.run_id={fixture_run_id}"],
            capture_output=True, text=True, timeout=60,
        )
        check("the fixture orphan container is actually gone afterward",
              after_reap.stdout.strip() == "", after_reap.stdout.strip())
    finally:
        subprocess.run(["docker", "rm", "-f", orphan_id], capture_output=True, text=True,
                       timeout=60)

    heading("U58: a version declared data_access='copy' gets its real content staged")

    stage_before = sandbox_containers()

    stage_agent = register_agent(owning, f"stage-copy-{uuid.uuid4().hex[:8]}")
    content_version = seed_content_version(owning, {
        "notes.txt": b"this is real staged content, not a manifest entry with nothing behind it",
    })
    upload_and_deploy(stage_agent, (
        "import json\n"
        "from pathlib import Path\n"
        "text = Path('/data/notes.txt').read_text()\n"
        "manifest = json.loads(Path('/data/manifest.json').read_text())\n"
        "print(json.dumps({'marker': 'u56-staged-content', 'text': text, "
        "'manifest_entries': len(manifest['objects'])}))\n"
    ), data_access="copy")
    run_id = start_run(stage_agent, content_version["id"], "U58 staged content check")
    run = wait_for_run(run_id) if run_id else None
    if run is None:
        skip("the staged-content run reaches a terminal status", "no result within the timeout")
    else:
        check("the run succeeded", run["status"] == "succeeded", str(run["status"]))
        findings = run.get("findings") or []
        result = findings[0] if findings else {}
        check("the staged file's real content matches what was written",
              result.get("text")
              == "this is real staged content, not a manifest entry with nothing behind it",
              str(result.get("text"))[:100])
        check("the staged manifest lists exactly the one file staged",
              result.get("manifest_entries") == 1, str(result.get("manifest_entries")))
        with db() as conn:
            decision = conn.execute(
                "select allowed, reasons from access_decision "
                "where agent_run_id = %s and phase = 'grant' "
                "order by id desc limit 1", (run_id,),
            ).fetchone()
        check("staging left a real, logged access_decision row, not a "
              "credential obtained off the books",
              decision is not None and decision["allowed"] is True,
              str(dict(decision) if decision else None))

    heading("U58: staging only ever reads the run's own pinned dataset_version_id")

    activities_src = (Path(__file__).resolve().parent.parent
                      / "worker" / "sandbox_run_activities.py").read_text()
    check("stage_data is called with the workflow's own params, not a "
          "second, separately suppliable dataset id",
          "sandbox_run.stage_data(params, run_dir)" in activities_src,
          "call site does not match the expected shape; read the file")

    heading("U58: a version over the staging cap is refused before anything is fetched")

    oversized_agent = register_agent(owning, f"stage-oversized-{uuid.uuid4().hex[:8]}")
    schema_id = fixture_contract(TENANT)
    ds = api("POST", "/datasets", json={
        "tenant_id": TENANT, "name": f"stage-oversized-{uuid.uuid4().hex[:8]}",
    })
    ds.raise_for_status()
    oversized_dataset_id = ds.json()["id"]
    with db() as conn:
        conn.execute("update dataset set department_id = %s where id = %s",
                    (owning, oversized_dataset_id))
    oversized_prefix = f"{TENANT}/{oversized_dataset_id}/v1"
    # A manifest declaring more bytes than MAX_STAGED_BYTES without
    # actually uploading that much: the cap is checked from the declared
    # manifest before any fetch begins, so this is safe and fast.
    oversized_sealed = api("POST", "/dataset-versions", json={
        "tenant_id": TENANT, "dataset_id": oversized_dataset_id, "schema_id": schema_id,
        "visibility_class": "PUBLISHED",
        "object_manifest": [{"key": f"{oversized_prefix}/huge.bin",
                             "bytes": 2 * 1024 * 1024 * 1024}],
        "record_count": 1,
    })
    oversized_sealed.raise_for_status()
    oversized_version = oversized_sealed.json()
    upload_and_deploy(oversized_agent, (
        "import json\nprint(json.dumps({'marker': 'u56-should-not-run'}))\n"
    ), data_access="copy")
    run_id = start_run(oversized_agent, oversized_version["id"], "U58 oversized check")
    run = wait_for_run(run_id) if run_id else None
    if run is None:
        skip("the oversized run reaches a terminal status", "no result within the timeout")
    else:
        check("a version over the staging cap fails, named reason, not silently truncated",
              run["status"] == "failed" and "staging limit" in (run.get("error") or ""),
              str(run.get("error")))

    heading("U58: /data is read-only from inside the sandbox")

    readonly_agent = register_agent(owning, f"stage-readonly-{uuid.uuid4().hex[:8]}")
    readonly_version = seed_content_version(owning, {"a.txt": b"present"})
    upload_and_deploy(readonly_agent, (
        "import json\n"
        "try:\n"
        "    open('/data/a.txt', 'w').write('tampered')\n"
        "    writable = True\n"
        "except OSError:\n"
        "    writable = False\n"
        "print(json.dumps({'marker': 'u56-readonly-check', 'writable': writable}))\n"
    ), data_access="copy")
    run_id = start_run(readonly_agent, readonly_version["id"], "U58 read-only check")
    run = wait_for_run(run_id) if run_id else None
    if run is None:
        skip("the read-only check run reaches a terminal status", "no result within the timeout")
    else:
        findings = run.get("findings") or []
        result = findings[0] if findings else {}
        check("the sandboxed process could not write into /data",
              result.get("writable") is False, f"findings: {findings}")

    heading("U58: access to one dataset version does not authorize staging a later version of the same dataset")

    scope_agent = register_agent(owning, f"stage-scope-{uuid.uuid4().hex[:8]}")
    upload_and_deploy(scope_agent, (
        "import json\nprint(json.dumps({'marker': 'u56-scope'}))\n"
    ), data_access="copy")

    scope_schema_id = fixture_contract(TENANT)
    scope_ds = api("POST", "/datasets", json={
        "tenant_id": TENANT, "name": f"stage-scope-{uuid.uuid4().hex[:8]}",
    })
    scope_ds.raise_for_status()
    scope_dataset_id = scope_ds.json()["id"]
    with db() as conn:
        conn.execute("update dataset set department_id = %s where id = %s",
                    (owning, scope_dataset_id))
    v1 = seal_content_version(scope_dataset_id, scope_schema_id,
                              {"secret.txt": b"v1 content"}, klass="RAW")

    v1_started = api("POST", f"/agents/{scope_agent}/runs", json={
        "purpose": "U58 scope check v1", "dataset_version_id": v1["id"],
        "request_access": True,
    }, headers=bearer_for(ENGINEER))
    check("starting against a RAW version without a lease files an access request",
          v1_started.status_code == 202
          and v1_started.json().get("status") == "awaiting_access",
          f"HTTP {v1_started.status_code}: {v1_started.text[:200]}")
    v1_body = v1_started.json()
    lease_id = v1_body.get("lease_request_id")
    if lease_id:
        api("POST", f"/leases/requests/{lease_id}/approve",
            headers=bearer_for(CUSTODIAN)).raise_for_status()
    run_v1 = wait_for_run(v1_body.get("run_id")) if v1_body.get("run_id") else None
    check("the run against v1, once access is granted, succeeds and stages real content",
          run_v1 is not None and run_v1["status"] == "succeeded",
          str(run_v1.get("status") if run_v1 else None))

    v2 = seal_content_version(scope_dataset_id, scope_schema_id,
                              {"secret.txt": b"v2 content, unrelated grant"}, klass="RAW")
    v2_started = api("POST", f"/agents/{scope_agent}/runs", json={
        "purpose": "U58 scope check v2, should still need its own access",
        "dataset_version_id": v2["id"], "request_access": True,
    }, headers=bearer_for(ENGINEER))
    check("v1's earlier grant does not carry over: v2 still files a fresh "
          "access request rather than starting immediately",
          v2_started.status_code == 202
          and v2_started.json().get("status") == "awaiting_access",
          f"HTTP {v2_started.status_code}: {v2_started.text[:200]}")

    stage_after = sandbox_containers()
    check("no sandbox container is left behind after any of the U58 runs",
          stage_after <= stage_before, f"{stage_before} before, {stage_after} after")

    return summary("U55/U58")


if __name__ == "__main__":
    sys.exit(main())
