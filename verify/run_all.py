"""Run every verification script and report per script.

Deliberately does not aggregate into a single number. Each script prints its own
per-assertion lines, and this only collects the exit codes so a failure
somewhere is impossible to miss.

Each script is timed, and the whole run is also emitted as one JSON line at the
end, tagged with RESULTS_MARKER. That line is how run-verification.ps1 gets
structured results back: this file usually runs inside the munitas-api
container, where /verify is mounted read-only, so it cannot write a history
file itself. Standard output is the only channel out, and a marked line is
unambiguous to find in it.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RESULTS_MARKER = "##MUNITAS-VERIFY-RESULTS##"

# The slowest script takes under two minutes; this only ever stops a hang.
SCRIPT_TIMEOUT_SECONDS = 900

SCRIPTS = [
    ("V1  immutability", "v1_immutability.py"),
    ("V2  lineage", "v2_lineage.py"),
    ("V3  promotion moves no bytes", "v3_promotion.py"),
    ("V4  class enforced", "v4_class_enforced.py"),
    ("V6  idempotent retry", "v6_idempotency.py"),
    ("V10 deletion", "v10_deletion.py"),
    ("U22 bringing data in", "v22_ingest.py"),
    ("U28 tenant isolation at run time", "v28_tenant_isolation.py"),
    ("U33 a retired tenant is closed", "v33_retired_tenant.py"),
    ("U38 grant provenance", "v38_grant_provenance.py"),
    ("U39 standing grants for workloads", "v39_standing_grants.py"),
    ("U40 trigger provenance", "v40_trigger_provenance.py"),
    ("U43 huggingface fetch", "v43_huggingface_fetch.py"),
    ("U44 license-derived provenance", "v44_license_provenance.py"),
    ("U45 your own huggingface account", "v45_huggingface_credentials.py"),
    ("U46 huggingface ingestion is a background job", "v46_huggingface_async_ingest.py"),
    ("U48 agent registry and versioning", "v48_agent_registry.py"),
    ("U50 agent deployment and runs", "v50_agent_deploy_and_runs.py"),
    ("U51 an agent asks for access to its input", "v51_agent_access_request.py"),
    ("U53 real authentication resolves to the right identity", "v53_real_authentication.py"),
    ("U54 an agent cannot read outside its own run's scope", "v54_agent_run_scope.py"),
    ("U56 a tenant's storage is its own, not shared, from first write", "v56_per_tenant_storage.py"),
    ("U57 the same guarantee, proved against real Cloudflare R2", "v57_per_tenant_r2.py"),
    ("U60 audio arrives as audio", "v60_audio_prepare.py"),
    ("U61 a person starts the pipeline", "v61_console_pipeline.py"),
    ("U64 grants are a projection, not an edit", "v64_grant_projection.py"),
    ("U65 ingestion writes through a tenant's own identity", "v65_tenant_ingest_identity.py"),
    ("U67 the committed API reference matches the live schema", "v67_openapi_current.py"),
    ("U69 the log carries identifiers, never contents", "v69_logging.py"),
    ("U70 disposability is declared, never acquired", "v70_declared_disposable.py"),
    ("U72 a role is asked for by one person and granted by another", "v72_role_administration.py"),
    ("U74 storage agrees with the register", "v74_storage_agrees.py"),
    ("U75 approved access takes effect, and the platform retries until it does", "v75_activation.py"),
    ("U78 people see what they can read, and the preview agrees with the check", "v78_access_preview.py"),
    ("U85 writing as pipeline_action needs the same proof reading does", "v82_write_credential_compiler.py"),
]

here = Path(__file__).parent
results = []

run_started = datetime.now(timezone.utc)
run_clock = time.monotonic()

for label, script in SCRIPTS:
    print("\n" + "=" * 66)
    print(label)
    print("=" * 66)
    started = time.monotonic()
    # A script that hangs must fail, not stall the whole suite: it is stopped
    # at the limit and recorded as failed, and the rest still run.
    try:
        code = subprocess.call([sys.executable, str(here / script)], cwd=here,
                               timeout=SCRIPT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        print(f"\n  [FAIL] stopped after {SCRIPT_TIMEOUT_SECONDS} s: longer than any "
              "script should take, so it was treated as hung")
        code = 124
    # monotonic, not wall clock: a clock adjustment mid-run must not be able to
    # produce a negative duration in the history.
    results.append((label, script, code, round(time.monotonic() - started, 2)))

run_seconds = round(time.monotonic() - run_clock, 2)

print("\n" + "=" * 66)
print("Per script")
print("=" * 66)
for label, script, code, seconds in results:
    print(f"  {'PASS' if code == 0 else 'FAIL'}  {seconds:7.2f}s  {label}")
print(f"\n  Total {run_seconds:.2f}s across {len(results)} scripts")

print("\nV5 (policy) runs separately and needs no services:")
print("  docker compose run --rm --entrypoint /opa opa test /policy -v")
print("\nNot run here, and not passes:")
print("  V6b, V7 to V9, V11 to V14 run on the host, where the models and the")
print("  agent packages are installed.")
print("  U29 to U31 drive scripts/admin/reclaim-storage.py, which sits beside the Compose file")
print("  rather than inside this image:")
print("    .venv\\Scripts\\python.exe verify\\v29_reclamation.py")
print("  U47 drives scripts/admin/cleanup-dataset.py, the same reason:")
print("    .venv\\Scripts\\python.exe verify\\v47_cleanup_dataset.py")
print("  U49 drives scripts/admin/register-agent-version.py, the same reason:")
print("    .venv\\Scripts\\python.exe verify\\v49_external_agent_version.py")
print("  U52 drives scripts/admin/create-tenant.py, scripts/admin/retire-tenant.py and scripts/admin/nuke-tenant.py, the")
print("  same reason:")
print("    .venv\\Scripts\\python.exe verify\\v52_tenant_lifecycle.py")
print("  U55 drives real Docker containers via worker/sandbox_run.py and needs")
print("  python -m worker.main running on the host with Docker reachable:")
print("    .venv\\Scripts\\python.exe verify\\v55_sandboxed_agent_run.py")
print("  U59 calls worker.activities.record_gate_decision directly, which is the")
print("  only honest way to prove the pipeline stopped promoting, so it runs on")
print("  the host where the worker package is importable:")
print("    .venv\\Scripts\\python.exe verify\\v59_gate_decision.py")
print("  U62 runs a whole de-identification against three real recordings, so it")
print("  needs the synthetic corpus, which lives on the host and is not mounted")
print("  into this image, and the pipeline worker on the machine with the GPU.")
print("  Several minutes:")
print("    .venv\\Scripts\\python.exe verify\\v62_console_pipeline_run.py")
print("  U63 runs the pipeline for a tenant that has its own storage bucket,")
print("  which is the only arrangement where writing to the wrong one is")
print("  visible. Same host requirements as U62:")
print("    .venv\\\\Scripts\\\\python.exe verify\\\\v63_pipeline_tenant.py")
print("  U66 runs an operator-registered DAG's sandboxed script steps, which")
print("  needs a live worker on this machine's Docker daemon:")
print("    .venv\\Scripts\\python.exe verify\\v66_pipeline_dag.py")
print("  U68 runs a real recording with no answer key through the committed")
print("  redact-without-scoring template -- real transcribe/detect/redact, so")
print("  it needs python -m worker.main on the machine with the GPU. Several")
print("  minutes:")
print("    .venv\\Scripts\\python.exe verify\\v68_production_case_no_ground_truth.py")
print("  U73 starts the worker entry points on Windows and inside WSL to prove")
print("  they refuse a work directory they cannot use, so it runs on the host:")
print("    .venv\\Scripts\\python.exe verify\\v73_work_dir_refused.py")
print("  U46's checks past the first two need the HuggingFace ingestion worker")
print("  running on the host, or they report a skip rather than a failure:")
print("    .venv\\Scripts\\python.exe -m worker.main")


# The structured copy, for run-verification.ps1 to append to the history that
# verify/report.py renders. Printed last so a reader sees the human summary
# first, and on one line so it survives being mixed with anything else.
run = {
    "started_at": run_started.isoformat(),
    "seconds": run_seconds,
    "host": platform.node(),
    "results": [
        {
            # "U43 huggingface fetch" splits into the claim id and its words.
            # The id is what the docs call each check, so it is what the
            # report filters and groups on.
            "check": label.split(None, 1)[0],
            "label": label.split(None, 1)[1].strip(),
            "script": script,
            "status": "pass" if code == 0 else "fail",
            "seconds": seconds,
        }
        for label, script, code, seconds in results
    ],
}
print(f"{RESULTS_MARKER} {json.dumps(run, separators=(',', ':'))}")

sys.exit(1 if any(code for _, _, code, _ in results) else 0)
