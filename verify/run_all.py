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
    ("U191 a policy engine that cannot answer is a 503 with one plain sentence on every route that asks it", "v180_policy_down_is_503.py"),
    ("U192 the bar a de-identified result is judged by is the customer's: set per purpose by the data protection officer, worked out again at release, and kept with the decision", "v181_release_bars.py"),
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
    ("U114 a derivation run reads with a key of its own, which opens only its inputs and ends with the run", "v114_task_read_keys.py"),
    ("U116 a pipeline run and an agent run read with a key of their own, which opens only what was allowed and ends with the task", "v116_pipeline_and_agent_read_keys.py"),
    ("U117 a writer is handed a key for its one output folder, and not the pipeline role's key", "v117_writer_keys.py"),
    ("U118 a dataset made from a version belongs to the department of the dataset that version is in", "v118_outputs_take_the_inputs_department.py"),
    ("U119 the canary tidy works on the canary tenant and refuses everything else, at every point", "v119_canary_tidy_is_canary_only.py"),
    ("U120 the custodian's queue says how many claims are waiting and can be read page by page", "v120_custodian_queue_total_and_paging.py"),
    ("U124 the check that fails a run for leaving test tenants behind flags the right ones", "v124_leftover_tenant_check.py"),
    ("U125 what is checked after a run's cleanup is kept on the history page as a second line tied to that run", "v125_history_keeps_after_run_results.py"),
    ("U126 who may bring data in, register code and confirm a claim, and the second approver when the only approver made the claim", "v126_roles_on_person_routes.py"),
    ("U127 a department has several approvers, any one of whom may act, and approvers change who they are, on the record", "v127_department_approvers.py"),
    ("U128 one person's data is locked under a key of their own, and erasing the person destroys that key and nothing else", "v128_person_keys_erasure.py"),
    ("U129 locked columns are read in full only by the owning department's approvers, and by anybody else only through a lease an auditor can see", "v129_locked_column_leases.py"),
    ("U130 a sealed table reports how many different people share each combination of its readable quasi columns, as advice and in counts", "v130_group_size_reports.py"),
    ("U131 a gate can say not measured, which is not a pass and not a fail", "v131_gate_not_measured.py"),
    ("U133 a table is sealed from a contract file and a CSV file, and a bad file is refused with every problem listed", "v133_seal_table_from_files.py"),
    ("U134 a screen can ask what a person may do with a locked table, and the lease lists name people", "v134_locking_summary.py"),
    ("U136 a whole file is locked under a person's key in chunks, and a chunk that is moved, repeated, cut off or added to is refused", "v136_locked_file_stream.py"),
    ("U137 a file is uploaded in parts that can each be sent again, locked under its patient's key, sealed, and unreadable once the patient is erased", "v137_locked_files.py"),
    ("U141 a custom pipeline step is given the files only when a second person approved its version, and the platform opens them for it", "v140_custom_steps_and_files.py"),
    ("U148 what a step makes from a patient's file is locked under that patient's key as it arrives, belongs to that patient, and sound without a source is refused", "v145_locked_outputs.py"),
    ("U149 a person opens the files of a version through the platform, only for a reason the platform holds, unlocked inside it, logged, and stopped when the reason ends", "v146_record_door.py"),
    ("U163 a custom pipeline's run says which step waits for which (names, kinds and dependencies only, nothing else of the definition) so it can be drawn as a graph, and a run with no steps, a built-in run and another organisation's run are handled", "v156_run_graph.py"),
    ("U164 a person opens a few of a version's real files only when an approver of the owning department, who is not them, has allowed it: the limits, who may ask and decide, files the platform draws and fixes, what the door lists, opens and refuses, a log that names the lease, a lease that ends or runs out stopping what was playing, and a lease never counting as development data", "v157_file_leases.py"),
    ("U167 a notebook signs in on behalf of a person and the pass it receives can do only what a notebook may: it shows a code, the person types it in the console, is signed in by the console session and approves, and the notebook receives a short-lived pass; a code the platform did not start is refused, wrong codes are limited, the pass reaches only the routes the client library calls and is refused with a reason everywhere else (including deciding a request, for a custodian), renews with reuse detection, is ended at once by revoke or deny, runs out with its sign-in, is counted when used from another address, and is listed for the person and for the auditor only; the client library signs in, renews, signs out and refuses plain http", "v160_notebook_sign_in.py"),
    ("U168 an organisation's limits on what people may ask for are settings with today's behaviour as their defaults: who may ask for locked-column rows, the most rows and days, the most files and hours in the page and in a notebook; the value in force is the one every screen and decision uses, a setting is never edited and says who set it and why, the most the platform allows holds even for a row put in by hand, and another organisation is not affected", "v161_access_rules.py"),
    ("U170 the directory of people is one organisation's: whoever holds an ordinary role sees their own organisation and no other whatever they ask for, the platform administrator is offered every live organisation, a custodian's row still says which department they answer for, and nobody who is not signed in is given the list", "v163_directory_is_one_organisation.py"),
    ("U172 a dataset is registered, active or deleted, worked out from its versions: deleted ones stay out of the list unless asked for and are counted, each state filter returns its own, a dataset with one readable version is active, the record of a deleted one is kept, and another organisation sees none of them", "v164_dataset_state.py"),
    ("U173 the purge of an organisation knows every table that holds its data without an organisation column of its own, so the next new table is caught the day it is added", "v165_purge_knows_every_table.py"),
    ("U174 a person brings in an ordinary table (a CSV, a TSV, a JSON list or a JSONL file) and it is sealed as a table nobody can read yet, with every problem named and nothing sealed by a refusal, and a contract that is neither a table of people nor an ordinary table is refused in words", "v166_plain_tables.py"),
    ("U178 the limits on one person's uploads and on an organisation's tables count what they say, and turn a request away in words with a time to wait", "v170_table_upload_limits.py"),
    ("U169 the locked data audit is read a page at a time and can be filtered: every list is walked page by page with none missing or twice, in time order, with a page past the end empty; the page size is held to its limits; person, table, date, result and free text each narrow only the lists that take them, alone and together, a percent sign is text and not a wildcard; only the privacy auditor and the data protection officer read it, each read and refusal is recorded, nothing outside the organisation or any file name appears", "v162_audit_filters.py"),
    ("U166 a person reads the few real files an approver allowed them to open in a notebook, through the client library and nothing else: a lease for a notebook is separate from one for the page, the token is for one version and never outlives the lease, the library lists and reads exactly what the lease names (whole, by range, seeking), a file outside it is refused, every opening is in the log as a notebook's, and ending the lease or its time running out stops the next request", "v159_notebook_files.py"),
    ("U165 the way to ask for a version follows what the version holds: what is locked and what a permission to read the whole of it is worth are one answer; a request to read the whole of a version of locked files is refused in words, also when it was already waiting, and an approver is told what approving gives; the mark of locked files says what the person holds of them; the file door does not blame a role where an approver decides", "v158_ways_to_ask.py"),
    ("U161 a version says what it holds (kinds, formats, structure, files and size, or its rows when it is a table, and whether the platform read the files or went by their names), the same as the datasets list says, never names a file, and is not found by another organisation", "v155_version_holds.py"),
    ("U159 a storage permission update is always written, and what is odd about it (no bucket-wide permission, removals nothing explains, a permission that traces to nothing) is raised as an alert in one file, on the update's own row, and on the status the console reads", "v153_storage_alerts.py"),
    ("U157 what a dataset holds (kind, format, structure) is worked out from its files when each version is sealed, not declared, and the list shows and filters by it", "v152_data_kinds.py"),
    ("U155 a result waiting for a decision can be looked at by the person who decides it, for a set number of files, with every file checked first, and cannot be released blind", "v151_release_review.py"),
    ("U152 a de-identification result says who it is waiting on from the roles held now, and the departments a person approves for are only those they can act for now", "v148_waiting_on.py"),
    ("U151 what was made for development ends with its sample or its days: the files are deleted, the row kept and marked, the real files untouched, a hold stops it", "v147_development_outputs.py"),
    ("U144 a pipeline version in development runs only on development data, and a development sample is drawn by the platform as pointers, never copies", "v142_development_samples.py"),
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


# `--only U128,U129` runs those checks and nothing else, for the quick loop (see affects.py and quick-verify.ps1). A partial run says so
# loudly, and its results line carries "partial": true, because a run of two checks must never be read as a run of all of them.
ONLY = None
if "--only" in sys.argv:
    ONLY = {part.strip().upper() for part in sys.argv[sys.argv.index("--only") + 1].split(",") if part.strip()}
    known = {label.split(None, 1)[0].upper() for label, _ in SCRIPTS}
    if ONLY - known:
        print(f"No such check: {', '.join(sorted(ONLY - known))}. The ones that exist: {', '.join(sorted(known))}")
        sys.exit(2)
SELECTED = [(label, script) for label, script in SCRIPTS if ONLY is None or label.split(None, 1)[0].upper() in ONLY]

run_started = datetime.now(timezone.utc)
run_clock = time.monotonic()

for label, script in SELECTED:
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
if ONLY is not None:
    print(f"\n  PARTIAL RUN: {len(results)} of {len(SCRIPTS)} checks. This is not a verification of the platform.")
    print("  The full suite is still needed before anything is pushed; CI runs it.")
skipped_checks = sum(counts["skipped"] for _, _, _, _, counts in results if counts)
if skipped_checks:
    print(f"  {skipped_checks} individual checks were skipped across the suite; none is counted as a pass")

if ONLY is None:
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
    print("  U121 drives scripts/admin/close-finished-pipeline-runs.py, the same reason:")
    print("    .venv\\Scripts\\python.exe verify\\v121_closing_runs_twice_changes_nothing.py")
    print("  U122 calls the scheduled sweep's own function and reads its schedule from Temporal, so it runs on the host:")
    print("    .venv\\Scripts\\python.exe verify\\v122_run_endings_say_where_they_came_from.py")
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
    print("  U62 runs a whole de-identification against three real files, so it")
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
    print("  U68 runs a real file with no answer key through the committed")
    print("  redact-without-scoring template -- real transcribe/detect/redact, so")
    print("  it needs python -m worker.main on the machine with the GPU. Several")
    print("  minutes:")
    print("    .venv\\Scripts\\python.exe verify\\v68_production_case_no_ground_truth.py")
    print("  U132 calls the practice step directly and loads the models, so it runs on the host with the graphics card, a couple of minutes:")
    print("    .venv\\Scripts\\python.exe verify\\v132_practice_run.py")
    print("  U138 imports the worker's own client and opens locked files through the platform, so it runs on the host, about half a minute:")
    print("    .venv\\Scripts\\python.exe verify\\v138_worker_opens_locked_files.py")
    print("  U140 runs the whole de-identification over two patients' locked files and again after one is erased, so it needs the worker, about five minutes:")
    print("    .venv\\Scripts\\python.exe verify\\v139_locked_files_pipeline.py")
    print("  U142 runs a real custom pipeline step over locked files in the sandbox worker, so it needs both workers, about two minutes:")
    print("    .venv\\Scripts\\python.exe verify\\v141_custom_step_given_locked_files.py")
    print("  U153 runs the pipeline's decision step itself and shows a development run is never put in front of a reviewer, so it runs on the host, seconds:")
    print("    .venv\\Scripts\\python.exe verify\\v149_development_gate.py")
    print("  U160 reads the repository (the compose file, the start scripts, every settings file), which the API container does not hold, so it runs on the host, no database, seconds:")
    print("    .venv\\Scripts\\python.exe verify\\v154_settings_documented.py")
    print("  U175 makes test tables in every format and proves they are reproducible, safe, realistic, correctly described and checkable, so it runs on the host, no database, under a minute:")
    print("    .venv\\Scripts\\python.exe verify\\v167_testdata_generator.py")
    print("  U176 sends the generated tables, in every text layout, through the person route and a worker job and compares the stored tables with their truth files, so it runs on the host, a few minutes:")
    print("    .venv\\Scripts\\python.exe verify\\v168_table_text_formats.py")
    print("  U177 brings a table in through the platform in parts, resumes, cancels, expires, refuses by limit and writes four tables two at a time, so it runs on the host, a few minutes:")
    print("    .venv\\Scripts\\python.exe verify\\v169_table_upload.py")
    print("  U182 sends the generated tables in every binary layout (Parquet, Arrow, Feather, ORC, Avro, Excel), their faulty copies and hostile files through the person route, so it runs on the host, about ten minutes:")
    print("    .venv\\Scripts\\python.exe verify\\v172_binary_formats.py")
    print("  U186 runs the database layer (pgdb, over pg8000) through every kind of value, refusal, transaction and pool use, and the agent's checkpointer, against the live database, so it runs on the host, about 30 seconds:")
    print("    .venv\\Scripts\\python.exe verify\\v175_pgdb.py")
    print("  U187 looks for a package under a copyleft licence in the repository's requirements and code and in every environment (this Python, the API and table worker images, the WSL environments), so it runs on the host, no database, about 20 seconds:")
    print("    .venv\\Scripts\\python.exe verify\\v176_licences.py")
    print("  U188 reads the page catalogue (platform/policy/screens.rego), the API's code and the committed reference to prove each route behind a limited page refuses through the catalogue, and that no route does so unnamed, so it runs on the host, no database, seconds:")
    print("    .venv\\Scripts\\python.exe verify\\v177_screens_cover_routes.py")
    print("  U189 reads the policy engine's two modes (folder and bundle) and proves the Compose override, the engine's config, the publish script and the start scripts name the same things, so it runs on the host, no database, seconds:")
    print("    .venv\\Scripts\\python.exe verify\\v178_opa_policy_modes.py")
    print("  U190 tries the policy engine's store from the platform's own networks (it cannot be reached) and from its own (the engine's key reads the one package and nothing else), so it runs against a stack in bundle mode with a package published, in the API container and in a throwaway container, seconds:")
    print("    wsl -d Munitas -- bash verify/policy_store_check.sh")
    print("  U193 calls the worker's record_gate_decision to prove a run keeps the counts for each kind of identifier and is judged by the platform's built-in bar and not a default of its own, so it runs on the host, about 20 seconds:")
    print("    .venv\\Scripts\\python.exe verify\\v182_gate_records_per_kind.py")
    print("  Also tried against a running stack in bundle mode, never part of a quick run: verify/policy_signing_attacks.sh (an unsigned, wrongly signed or altered package is refused) and verify/policy_persistence_check.sh (the engine starts from its saved package with the store down).")
    print("  U183 reads the repository to prove the large tier (verify/large, run_large.py) is kept out of this suite and plans before it acts, so it runs on the host, no database, under a minute:")
    print("    .venv\\Scripts\\python.exe verify\\v173_large_tier_guard.py")
    print("  The large tier itself is never run here. verify\\run_large.py lists it and says what each check would cost.")
    print("  U184 brings 10,000 people and 5,000 admissions in through the person route and a worker, locks them, reads them a page at a time, finds a lease's sample and erases one person, so it runs on the host, about four minutes:")
    print("    .venv\\Scripts\\python.exe verify\\v174_people_tables.py")
    print("  U180 reads tables that were brought in back through a real lease with DuckDB, PyIceberg and a notebook signed in through the real client, and looks at their first rows, so it runs on the host, a minute:")
    print("    .venv\\Scripts\\python.exe verify\\v171_read_back.py")
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
    "partial": ONLY is not None,
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
