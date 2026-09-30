"""U76: allowed access that cannot take effect yet parks the run, and the
platform resumes it by itself once it can.

The in-container half, U75, proves the record and the activator's timing with
a print made to fail inside its own process. This half makes the running API's
own prints fail, the two ways they fail in life, and watches what a caller
and a run actually see:

  * Storage is down (the SeaweedFS container is stopped). A credential request
    that policy allows answers 202, never 403, and an ordinary agent run
    parks at a checkpoint instead of recording a refusal. When storage comes
    back, nobody does anything: the run continues and reaches its sign-off.
  * The permissions document is busy (its lock is held by this script). A
    sandboxed run, whose code has to be fetched from storage and so cannot be
    tested with storage down, parks while staging its data, and is started
    again from staging once the lock is released.

Runs inside the WSL distro, because it stops and starts a container, beside
the API, both workers and a stack that is otherwise idle. It restores storage
and releases the lock however it ends.

    PG_DSN=postgresql://munitas:munitas@localhost:5432/platform \\
    VERIFY_KRATOS=http://localhost:4433 S3_ENDPOINT=http://localhost:8333 \\
    $HOME/.munitas/verify-venv/bin/python verify/v76_activation_live.py
"""

from __future__ import annotations

import subprocess
import sys
import time
import uuid

import psycopg

from common import (ENGINEER, PG_DSN, PIPELINE, api, bearer_for, check, db,
                    fixture_contract, fixture_version, heading, require_api,
                    summary)
# common's own import above already inserted the repo root onto sys.path.
from ports_config import PORTS  # noqa: E402
from v55_sandboxed_agent_run import (TENANT, department, register_agent,
                                     seed_content_version, start_run,
                                     upload_and_deploy)
from v50_agent_deploy_and_runs import register_version

STORAGE = "munitas-seaweedfs-1"
ADVISORY_LOCK_ID = 846_152_907_331  # platform/api/app/grants.py, _ADVISORY_LOCK_ID
# While storage is down the activator's gaps grow (10, 20, 40 s ...), so the
# first retry after it comes back can be over a minute away.
RESUME_WAIT_SECONDS = 240


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          check=True, timeout=120).stdout.strip()


def storage_up() -> bool:
    try:
        return docker("inspect", "-f", "{{.State.Running}}", STORAGE) == "true"
    except subprocess.CalledProcessError:
        return False


def wait_for_storage(timeout: float = 90.0) -> bool:
    import httpx
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://localhost:{PORTS['seaweedfs_filer']}/", timeout=3.0).status_code < 500:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return False


def run_row(run_id: str) -> dict:
    return api("GET", f"/agents/runs/{run_id}", headers=bearer_for("canary-engineer")).json()


def wait_for_status(run_id: str, wanted: set[str], timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    run = run_row(run_id)
    while time.monotonic() < deadline and run["status"] not in wanted:
        time.sleep(2)
        run = run_row(run_id)
    return run


def decisions_for(run_id: str) -> list[dict]:
    with db() as conn:
        return conn.execute(
            "select id, phase, allowed, reasons from access_decision "
            "where agent_run_id = %s order by id", (run_id,)).fetchall()


def grant_active(decision_id: int) -> bool | None:
    rows = api("GET", "/access-decisions", params={"limit": 500},
               headers=bearer_for("canary-engineer")).json()["decisions"]
    for row in rows:
        if row["id"] == decision_id:
            return row["active"]
    return None


def main() -> int:
    require_api()
    if not storage_up():
        print(f"{STORAGE} is not running; start the stack first")
        return 1
    health = api("GET", "/health").json()["storage_permissions"]
    if health["failing"] or health["parked_runs"]:
        print(f"storage permissions are not in a clean state to start from: {health}")
        return 1

    owning = department()
    schema_id = fixture_contract(TENANT)
    published = fixture_version(TENANT, schema_id, "PUBLISHED")

    # PIPELINE now needs a real action_run's own task credential to reach
    # /credentials at all (item 60's task_credential.py fix); this is the
    # storage-activation scenario, not the credential-authority one, so one
    # run's token covering `published` is enough for both direct calls below.
    action_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """insert into dataset_action
                 (id, tenant_id, name, source_schema_id, target_schema_id, output_class)
               values (%s, %s, %s, %s, %s, 'UNDER_REVIEW')""",
            (action_id, TENANT, f"u76-action-{action_id[:8]}", schema_id, schema_id),
        )
    pipeline_run = api("POST", "/action-runs", json={
        "tenant_id": TENANT, "action_id": action_id,
        "code_hash": "sha256:u76-codehash", "image_digest": "sha256:u76-imagedigest",
        "operator": PIPELINE, "idempotency_key": f"u76-{uuid.uuid4().hex}",
        "input_versions": [published["id"]],
        "trigger_kind": "manual", "triggered_by": ENGINEER,
    })
    pipeline_run.raise_for_status()
    pipeline_task_credential = pipeline_run.json()["task_credential"]

    native_agent = register_agent(owning, f"u76-native-{uuid.uuid4().hex[:8]}")
    native_version = register_version(native_agent)
    api("POST", f"/agents/{native_agent}/deploy",
        json={"agent_version_id": native_version},
        headers=bearer_for("canary-engineer")).raise_for_status()

    sandbox_agent = register_agent(owning, f"u76-sandboxed-{uuid.uuid4().hex[:8]}")
    staged = seed_content_version(owning, {"notes.txt": b"u76 staged after activation"})
    upload_and_deploy(sandbox_agent, (
        "import json\n"
        "from pathlib import Path\n"
        "print(json.dumps({'marker': 'u76', 'text': Path('/data/notes.txt').read_text()}))\n"
    ), data_access="copy")

    lock_conn = None
    try:
        # --- U76a: storage down ------------------------------------------
        heading("U76a: with storage down, allowed access answers 202, never a refusal")
        docker("stop", STORAGE)
        check("storage is stopped", not storage_up())

        direct = api("POST", "/credentials", json={
            "principal": PIPELINE, "principal_kind": "workload",
            "roles": ["pipeline_action"], "tenant_id": TENANT,
            "dataset_version_id": published["id"], "purpose": "u76 direct request",
            "task_credential": pipeline_task_credential,
        })
        body = direct.json() if direct.headers.get("content-type", "").startswith("application/json") else {}
        check("the request answers 202", direct.status_code == 202,
              f"HTTP {direct.status_code}: {direct.text[:200]}")
        check("saying allowed and not active, with no key",
              body.get("allowed") is True and body.get("active") is False
              and "access_key" not in body, f"{body}")
        check("with a Retry-After header matching the body",
              direct.headers.get("retry-after") == str(body.get("retry_after_seconds")),
              f"header {direct.headers.get('retry-after')}, body {body.get('retry_after_seconds')}")

        heading("U76b: an ordinary run parks instead of recording a refusal")
        started = api("POST", f"/agents/{native_agent}/runs",
                      json={"purpose": "u76 parks on activation",
                            "dataset_version_id": published["id"]},
                      headers=bearer_for("canary-engineer"))
        native_run = started.json().get("run_id")
        check("the run starts", started.status_code == 202, f"HTTP {started.status_code}")
        run = wait_for_status(native_run, {"awaiting_activation", "failed", "succeeded",
                                           "awaiting_approval", "halted"}, 60)
        check("the run parks as awaiting_activation", run["status"] == "awaiting_activation",
              f"{run['status']} {run.get('error') or ''}"[:200])
        check("with no end time", run.get("ended_at") is None, str(run.get("ended_at")))
        rows = decisions_for(native_run)
        grants_allowed = [r for r in rows if r["phase"] == "grant" and r["allowed"]]
        check("its tool call recorded an allowed grant", bool(grants_allowed), f"{rows}")
        check("and no refusal (negative)", not [r for r in rows if not r["allowed"]], f"{rows}")
        parked_grant = grants_allowed[-1]["id"] if grants_allowed else None
        check("the console's audit log shows that grant as not yet active",
              parked_grant is not None and grant_active(parked_grant) is False,
              str(parked_grant and grant_active(parked_grant)))
        status = api("GET", "/health").json()["storage_permissions"]
        check("/health reports printing failing with the run parked",
              status["failing"] and status["parked_runs"] >= 1, f"{status}")

        heading("U76c: storage comes back and the run continues by itself")
        docker("start", STORAGE)
        check("storage is back", wait_for_storage())
        run = wait_for_status(native_run, {"awaiting_approval", "failed", "succeeded", "halted"},
                              RESUME_WAIT_SECONDS)
        check(f"the run reaches its sign-off within {RESUME_WAIT_SECONDS} s, untouched",
              run["status"] == "awaiting_approval", f"{run['status']} {run.get('error') or ''}"[:200])
        check("the grant it waited on now shows as active",
              parked_grant is not None and grant_active(parked_grant) is True)
        rows = decisions_for(native_run)
        check("still no refusal anywhere in the run's record (negative)",
              not [r for r in rows if not r["allowed"]], f"{len(rows)} decisions")
        with db() as conn:
            prints = conn.execute(
                "select trigger, succeeded from storage_permission_print "
                "where started_at > now() - interval '10 minutes' order by id").fetchall()
        check("the print that ended the wait was the activator's",
              any(p["trigger"] == "activator" and p["succeeded"] for p in prints),
              f"{[(p['trigger'], p['succeeded']) for p in prints][-6:]}")
        again = api("POST", "/credentials", json={
            "principal": PIPELINE, "principal_kind": "workload",
            "roles": ["pipeline_action"], "tenant_id": TENANT,
            "dataset_version_id": published["id"], "purpose": "u76 direct request",
            "task_credential": pipeline_task_credential,
        })
        check("the same direct request now gets its key (200)",
              again.status_code == 200 and "access_key" in again.json(),
              f"HTTP {again.status_code}")

        # --- U76d: document busy, sandboxed run --------------------------
        heading("U76d: a sandboxed run parks while staging, and starts again from staging")
        lock_conn = psycopg.connect(PG_DSN, autocommit=True)
        lock_conn.execute("select pg_advisory_lock(%s)", (ADVISORY_LOCK_ID,))
        sandbox_run = start_run(sandbox_agent, staged["id"], "u76 sandboxed parks")
        run = wait_for_status(sandbox_run, {"awaiting_activation", "failed", "succeeded"}, 180)
        check("the sandboxed run parks as awaiting_activation",
              run["status"] == "awaiting_activation",
              f"{run['status']} {run.get('error') or ''}"[:300])
        rows = decisions_for(sandbox_run)
        check("its staging request was allowed, not refused (negative)",
              bool(rows) and all(r["allowed"] for r in rows), f"{rows}")
        lock_conn.execute("select pg_advisory_unlock(%s)", (ADVISORY_LOCK_ID,))
        lock_conn.close()
        lock_conn = None
        run = wait_for_status(sandbox_run, {"succeeded", "failed"}, RESUME_WAIT_SECONDS)
        check(f"once the document is free it finishes within {RESUME_WAIT_SECONDS} s",
              run["status"] == "succeeded", f"{run['status']} {run.get('error') or ''}"[:300])
        findings = run.get("findings") or []
        check("having staged and read the real content",
              bool(findings) and findings[0].get("text") == "u76 staged after activation",
              str(findings)[:200])
        status = api("GET", "/health").json()["storage_permissions"]
        check("and nothing is left parked or failing",
              not status["failing"] and status["parked_runs"] == 0, f"{status}")
    finally:
        if lock_conn is not None:
            lock_conn.close()
        if not storage_up():
            docker("start", STORAGE)
            wait_for_storage()

    return summary("U76")


if __name__ == "__main__":
    sys.exit(main())
