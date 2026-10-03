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
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from markers import COUNTS_MARKER

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
    ("U90 a sealed tabular version is also an Iceberg table that agrees with the register", "v90_iceberg_projection.py"),
    ("U91 the Iceberg catalog shows and opens only what a person may read, and ends with their lease", "v91_iceberg_catalog.py"),
    ("U94 a query over existing datasets is drafted and confirmed on terms, never run unchecked", "v94_derivation_draft.py"),
    ("U98 closing an organisation: who may start it and stop it, and what its people can do meanwhile", "v98_organisation_closing.py"),
    ("U99 a legal hold needs two different administrators and stops the deletion while it stands", "v99_legal_hold.py"),
    ("U100 a due and unheld organisation is deleted completely, and nothing else can be", "v100_purge.py"),
    ("U102 records are produced for a legal matter only with three different people, and nothing is erased meanwhile", "v102_legal_export_rules.py"),
    ("U103 a legal export is built, signed, delivered and opened, and cannot be altered or opened wrongly", "v103_legal_export_package.py"),
    ("U104 a table is handed over as the rows for people the custodian names, never whole", "v104_legal_export_filter.py"),
    ("U105 whether a version is also stored as a table, and why not, is on record and readable", "v105_table_copy_visible.py"),
    ("U106 an unexpected error while writing a table never stops a seal and never quotes a value", "v106_projection_unexpected_failure.py"),
    ("U107 a version that must be a table is refused when its table cannot be written, and nothing is left behind", "v107_table_required.py"),
    ("U108 a large table is read in batches and written a file at a time, from lines of JSON or from Parquet", "v108_large_tables.py"),
    ("U109 a large table is written by a worker in a job, and the platform seals the version when the worker reports", "v109_table_jobs.py"),
    ("U111 sealing a version is the platform's own workers' act, and nobody else can do it", "v111_seal_requires_worker.py"),
    ("U112 no storage key opens more than one organisation's data, and the pipeline's is one key per organisation", "v112_pipeline_key_per_organisation.py"),
    ("U113 every route knows who is calling, and the ones that act for a person act as that person", "v113_every_route_has_a_caller.py"),
]

here = Path(__file__).parent
results = []


def run_script(script: str) -> tuple[int, dict | None]:
    """Run one script with its output streamed as it arrives, and return its exit
    code and the counts it reported.

    The counts come from the exact line common.summary() prints, which is kept
    off the console: it is for this program, not for a reader. None means the
    script printed no such line (or an unreadable one), which is reported as
    such rather than guessed at.

    A script that hangs must fail, not stall the whole suite: it is killed at
    the limit and recorded as failed, and the rest still run.
    """
    proc = subprocess.Popen([sys.executable, "-u", str(here / script)], cwd=here,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    timed_out = threading.Event()

    def stop() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(SCRIPT_TIMEOUT_SECONDS, stop)
    timer.start()
    counts = None
    try:
        for line in proc.stdout:
            if line.startswith(COUNTS_MARKER):
                try:
                    parsed = json.loads(line[len(COUNTS_MARKER):])
                    if all(isinstance(parsed.get(k), int) for k in ("passed", "failed", "skipped")):
                        counts = {k: parsed[k] for k in ("passed", "failed", "skipped")}
                except (json.JSONDecodeError, AttributeError):
                    pass
                continue
            sys.stdout.write(line)
            sys.stdout.flush()
        proc.wait()
    finally:
        timer.cancel()
    if timed_out.is_set():
        print(f"\n  [FAIL] stopped after {SCRIPT_TIMEOUT_SECONDS} s: longer than any "
              "script should take, so it was treated as hung")
        return 124, counts
    return proc.returncode, counts


def classify(code: int, counts: dict | None) -> str:
    """fail, skip or pass. A script that exited cleanly but passed nothing has
    proved nothing, whether every check was skipped or none ran at all, so it is
    a skip. One that passed something and skipped the rest is a pass, with the
    skips shown beside it rather than hidden in it.

    A clean exit with no counts is also a skip. Every script ends by calling
    summary(), which reports them, so one that did not went out by another
    route (U57's skip path once returned a bare 0) and nothing is known to have
    passed. Calling that a pass is the overclaim this classification exists to
    prevent."""
    if code != 0:
        return "fail"
    if counts is None or counts["passed"] == 0:
        return "skip"
    return "pass"


run_started = datetime.now(timezone.utc)
run_clock = time.monotonic()

for label, script in SCRIPTS:
    print("\n" + "=" * 66)
    print(label)
    print("=" * 66)
    started = time.monotonic()
    code, counts = run_script(script)
    # monotonic, not wall clock: a clock adjustment mid-run must not be able to
    # produce a negative duration in the history.
    results.append((label, script, code, round(time.monotonic() - started, 2), counts))

run_seconds = round(time.monotonic() - run_clock, 2)

print("\n" + "=" * 66)
print("Per script")
print("=" * 66)
statuses = [classify(code, counts) for _, _, code, _, counts in results]
for (label, script, code, seconds, counts), status in zip(results, statuses):
    note = ""
    if status == "skip" and counts is None:
        note = "   (no counts reported, so nothing is known to have passed)"
    elif status == "skip":
        note = (f"   (nothing passed: {counts['skipped']} skipped)" if counts["skipped"]
                else "   (no checks ran)")
    elif status == "pass" and counts["skipped"]:
        # Only a pass can reach here with counts in hand: a failure that never
        # got as far as reporting them (a crash, a timeout) has none.
        note = f"   ({counts['skipped']} skipped, not counted as passes)"
    print(f"  {status.upper()}  {seconds:7.2f}s  {label}{note}")
tally = Counter(statuses)
print(f"\n  Total {run_seconds:.2f}s across {len(results)} scripts: "
      f"{tally['pass']} passed, {tally['skip']} skipped entirely, {tally['fail']} failed")
skipped_checks = sum(counts["skipped"] for _, _, _, _, counts in results if counts)
if skipped_checks:
    print(f"  {skipped_checks} individual checks were skipped across the suite; none is counted as a pass")

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
print("  U95 runs real queries through the host worker, the sandbox worker and the query")
print("  container, so it needs both workers and the image built (RUNBOOK, \"Running a query")
print("  to make a new dataset\"). U96 runs on the host with no Docker:")
print("    .venv\\Scripts\\python.exe verify\\v96_reap_leftover_containers.py")
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
            # pass, fail or skip. A skip is not a failure, so it does not change
            # this program's exit code below.
            "status": status,
            # What the script reported, or null where it reported nothing.
            "passed": counts["passed"] if counts else None,
            "failed": counts["failed"] if counts else None,
            "skipped": counts["skipped"] if counts else None,
            "seconds": seconds,
        }
        for (label, script, code, seconds, counts), status in zip(results, statuses)
    ],
}
print(f"{RESULTS_MARKER} {json.dumps(run, separators=(',', ':'))}")

sys.exit(1 if any(code for _, _, code, _, _ in results) else 0)
