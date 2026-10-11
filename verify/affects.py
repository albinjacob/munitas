"""Which checks does a change need? The quick loop's answer, with its reasons.

The full suite takes about 13 minutes, which is too long to run after every edit. This picks a smaller set from what changed, and says why each
check is in it, so a reader can disagree. It is a speed-up for the loop and never a substitute for the full suite: CI runs the whole of it, and so
must anybody before pushing.

How a set is chosen, in this order:

  1. A changed check script is run (its own id).
  2. Shared test code (common.py, the fixtures, this runner) changes what every check does, so the answer is the full suite.
  3. A table below names the checks for the files that have an obvious home.
  4. Any other changed file picks the checks whose script mentions the file's module name. A check that imports or names the code it covers
     is the best evidence there is of what it covers.
  5. A source file that none of those places answers for means nobody has said what covers it, so the answer is the full suite, and the file is
     named. Silence is not taken to mean "nothing needs checking".
  6. Always added, because they are the ones that caught the surprises: the cheap checks that a change anywhere can break by rule (the
     retired-organisation guard, the API reference, every route knowing its caller, no tenant left behind, immutability of deletion). About
     fifteen seconds in all.

Not part of any set, deliberately: nothing under docs/, web/ or .github/ needs the back-end checks. The console has its own build and tests.

What "changed" means: since the last full run that passed, not since a commit. At the start of a full run the tree is photographed
(`--snapshot FILE`: a fingerprint of every file git knows or would add); when that run passes, the photograph is kept (`--accept FILE`) as
`verify/history/last-full-pass.json`. A change is any file whose fingerprint differs from it, or that is new, or that is gone. Taken at the
start, so an edit made while the suite was running counts as unchecked. With no photograph yet, it falls back to the difference from `main`.

    python verify/affects.py                       prints one check id per line (or FULL), then a reason per line starting with "#"
    python verify/affects.py --snapshot FILE       photographs the tree into FILE
    python verify/affects.py --accept FILE         keeps FILE as the last full pass
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Cheap, and broken by rule from anywhere: see 6 above.
RIPPLE = ["V10", "U22", "U33", "U67", "U113", "U124"]

# Files that make every check behave differently. run_all.py and report.py are not here: they run and record the checks, and what a check
# does does not depend on them. A change to either is tested by using it, which every quick run does.
# The database layer is under everything: a change to it is a change to every check.
SHARED = ["verify/common.py", "verify/lifecycle_fixture.py", "verify/markers.py", "platform/pgdb.py", "platform/api/app/db.py"]

# Where a file has an obvious home. The first rule that matches a file decides it; its checks are added to the set.
TABLE: list[tuple[str, list[str], str]] = [
    ("platform/api/app/people_lock.py", ["U128", "U129"], "locking a batch of a table about people and the bulk key service; U184 runs on the host (RUNBOOK)"),
    ("platform/api/app/locked_table_rows.py", ["U129", "U134"], "reading a table about people a page at a time; U184 runs on the host (RUNBOOK)"),
    ("platform/tablewriter/peoplerows.py", ["U128", "U130"], "the two-pass writer of a table about people and its group-size report; U184 runs on the host (RUNBOOK)"),
    ("web/tests/people-table-upload.spec.ts", [], "the console test of a large table about people, U185 (RUNBOOK)"),
    ("worker/pgdb.py", [], "the worker's copy of the database layer; scripts/check_pins_agree.py fails when it differs from platform/pgdb.py, and U186 runs on the host (RUNBOOK)"),
    ("worker/pg_saver.py", [], "the agent's checkpointer on the database layer; U186 and V6b run on the host (RUNBOOK)"),
    ("verify/v175_pgdb.py", [], "the database layer's check, which runs on the host"),
    ("verify/v176_licences.py", [], "the licence check, which runs on the host"),
    ("docker-compose.opa-bundle.yml", [], "the policy engine's bundle mode and its store; U189 runs on the host (RUNBOOK, \"The policy engine\")"),
    ("platform/policy-bundle/*", [], "the policy engine's bundle config, its publish steps and the helper for its store; U189 runs on the host, and policy_store_check.sh, policy_signing_attacks.sh and policy_persistence_check.sh try them live (RUNBOOK, \"The policy engine\")"),
    ("publish-policy.ps1", [], "builds and publishes the policy package; U189 runs on the host (RUNBOOK, \"The policy engine\")"),
    ("platform/policy/system_authz*.rego", [], "who may call the policy engine; U189 runs on the host, and policy_signing_attacks.sh tries it live (RUNBOOK, \"The policy engine\")"),
    ("verify/v179_policy_store_access.py", [], "the check of who can reach the policy store; verify/policy_store_check.sh runs it in both places against a running stack (RUNBOOK, \"The policy engine\")"),
    ("verify/policy_store_check.sh", [], "tried by hand against a running stack in bundle mode (RUNBOOK, \"The policy engine\")"),
    ("verify/policy_signing_attacks.sh", [], "tried by hand against a running stack in bundle mode (RUNBOOK, \"The policy engine\")"),
    ("verify/policy_persistence_check.sh", [], "tried by hand against a running stack in bundle mode (RUNBOOK, \"The policy engine\")"),
    ("verify/v178_opa_policy_modes.py", [], "the check that the policy engine's two modes are wired consistently, which runs on the host"),
    ("verify/v177_screens_cover_routes.py", [], "the check that the page catalogue and the API refuse the same people, which runs on the host"),
    ("platform/policy/screens.rego", [], "the page catalogue; U188 runs on the host (RUNBOOK) and the policy tests cover it"),
    ("verify/v180_policy_down_is_503.py", [], "the check that a policy engine that cannot answer is a 503 with one plain sentence; it runs in the API container"),
    ("platform/gate_bars.py", ["U192"], "the judgement of a result against a release bar; U193 runs on the host (RUNBOOK) and scripts/check_pins_agree.py keeps the worker's copy the same file"),
    ("worker/gate_bars.py", ["U192"], "the worker's copy of the judgement; U193 runs on the host (RUNBOOK)"),
    ("platform/api/app/gate_purposes.py", ["U192"], "the release bars an organisation keeps, and the judgement of a waiting result under one"),
    ("platform/api/app/release_bar_routes.py", ["U192"], "the routes for release bars and the judgement of a waiting result"),
    ("verify/v181_release_bars.py", [], "the check of release bars; it runs in the API container"),
    ("web/tests/release-bars.spec.ts", [], "the console test of release bars, U194 (RUNBOOK)"),
    ("verify/v182_gate_records_per_kind.py", [], "the check that a run keeps the counts for each kind of identifier; it runs on the host (RUNBOOK)"),
    ("platform/api/app/screens.py", [], "the page catalogue's route and helper; U188 runs on the host (RUNBOOK)"),
    ("pgdb.py", [], "the loader at the repository's root; U186 runs on the host (RUNBOOK)"),
    ("verify/pgdb.py", [], "the loader beside the checks; U186 runs on the host (RUNBOOK)"),
    ("scripts/admin/pgdb.py", ["U119", "U124", "U168"], "the loader beside the operator scripts the container checks drive"),
    ("verify/large/*", ["U183"], "the large tier; its checks run only on demand through verify/run_large.py (RUNBOOK) and are never part of a quick run"),
    ("verify/run_large.py", ["U183"], "the large tier's runner; U183 checks that it plans before it acts and that nothing in it is in the ordinary suite"),
    ("web/tests/large/*", ["U183"], "the console's large test, a Playwright project that exists only when MUNITAS_LARGE=1"),
    ("platform/crypto/*", ["U128", "U129", "U136", "U137"], "the key service and the locking of columns and files"),
    ("platform/api/app/person_*.py", ["U128", "U129", "U137"], "locked columns and their routes, and what erasing a person lists"),
    ("platform/api/app/locked_file*.py", ["U136", "U137", "U141"], "files locked per person; U138, U140 and U142 run on the host (RUNBOOK)"),
    ("platform/api/app/dag_pipelines.py", ["U141"], "registering a pipeline version and approving it; U66 and U142 run on the host (RUNBOOK)"),
    ("platform/api/app/pipeline_dag.py", ["U141"], "what a pipeline file may say; U66 runs on the host (RUNBOOK)"),
    ("platform/api/app/development_sample*.py", ["U144", "U141"], "development samples and what a version in development may run on; U145 runs a real step on the host (RUNBOOK)"),
    ("worker/activities.py", ["U153"], "the decision step; U153 runs on the host (RUNBOOK) because the worker code is not in the container"),
    ("platform/api/app/release_review*.py", ["U155", "U149", "U152", "U113", "U67"], "the evidence and the limits on a release; U156 is the console test (RUNBOOK)"),
    ("platform/api/app/grants.py", ["U159", "U64", "U75", "U113", "U69"], "the storage permission update and its alerts; U64 and U75 drive the update itself"),
    ("platform/api/app/alerts.py", ["U159", "U69"], "where alerts are written, and the log fields they use"),
    ("scripts/admin/_canary_reprint.py", ["U119", "U159"], "the plain reprint the canary tidy runs"),
    ("platform/api/app/config.py", [], "a setting added or changed here must be in docs/tools/settings_reference.json, and the generated files regenerated; U160 runs on the host (RUNBOOK)"),
    ("worker/config.py", [], "the same, for the worker; U160 runs on the host"),
    ("docs/tools/settings_*", [], "the settings catalogue, scanner and generator; U160 runs on the host"),
    ("docker-compose.yml", [], "env_file and the fixed addresses under environment; U160 runs on the host"),
    ("start-dev.ps1", [], "what the host worker is given from .env; U160 runs on the host"),
    ("platform/api/app/kinds.py", ["U157"], "what a file is, worked out from its bytes when a version is sealed"),
    ("platform/api/app/notebook_sign_in*.py", ["U167", "U166"], "the notebook sign-in, its routes, and the one place a notebook's pass is accepted"),
    ("platform/api/app/auth.py", ["U167"], "the one place a console session or a notebook's pass is resolved"),
    ("infra/hydra/*", ["U167"], "the notebook sign-in service's config"),
    ("platform/api/app/notebook_routes.py", ["U166", "U164", "U149"], "the files token and the notebook routes"),
    ("scripts/client/munitas_client.py", ["U166"], "the client library, run for real by U166"),
    ("worker/step_sdk/munitas_step.py", ["U166", "U141"], "the one reader of a file, shared by custom steps and the client library"),
    ("platform/api/app/readability.py", ["U165", "U164", "U61"], "what is locked in a version and what the whole of it is worth: the request, the approval, the agent start, the marks"),
    ("platform/api/app/access_preview.py", ["U165", "U78"], "the access mark of each version, including versions of locked files"),
    ("platform/api/app/file_lease*.py", ["U164", "U149"], "asking for, deciding and ending permission to open real files; U150 is the console test (RUNBOOK)"),
    ("platform/api/app/record_door.py", ["U164", "U149"], "the file door: what it lists, opens, refuses and writes down, including under a lease"),
    ("platform/api/app/pipeline.py", ["U163", "U61"], "the run read: its steps and, for a custom pipeline, the shape of its steps"),
    ("platform/api/app/people.py", ["U72", "U161"], "roles held and requested; U161 checks that each role says when it was approved"),
    ("platform/api/app/versions.py", ["U157"], "every version is sealed here, and its files are given their kind and format here"),
    ("platform/api/app/review_access.py", ["U155", "U149"], "who may open a result's files to judge it"),
    ("platform/api/app/read_models.py", ["U152", "U157", "U161", "U165", "U113", "U67"], "who a result waits on and the approver label; U152 makes its own people in the verification organisation"),
    ("platform/api/app/development_outputs.py", ["U151", "U149"], "what is made for development ends and its files are deleted; U145 runs a real step on the host (RUNBOOK)"),
    ("platform/api/app/record_*.py", ["U149", "U151", "U113"], "the door a person opens files through; U145 and U142 run on the host (RUNBOOK)"),
    ("platform/api/app/step_outputs.py", ["U148", "U141"], "what a step writes, locked under its patient's key; U142 and U145 run real steps on the host (RUNBOOK)"),
    ("platform/api/app/step_routes.py", ["U141", "U137", "U144", "U148"], "what a custom step is given, through its pass; U142 runs a real step on the host (RUNBOOK)"),
    ("platform/api/app/task_credential.py", ["U141", "U114", "U116"], "the passes and credentials a task holds"),
    ("worker/step_sdk/*", ["U141"], "the library a custom step uses; U142 runs a real step on the host (RUNBOOK)"),
    ("platform/api/app/audit_pages.py", ["U169", "U164"], "the locked data audit, a page of one list at a time with filters"),
    ("platform/api/app/access_rules*.py", ["U168", "U129", "U164"], "the access rules of an organisation, read by the lease requests"),
    ("scripts/admin/set-access-rule.py", ["U168"], "the operator script that sets an access rule, run for real by U168"),
    ("platform/api/app/locked_leases.py", ["U128", "U129", "U164", "U166"], "leases on locked columns, and the audit evidence that also reports locked files"),
    ("platform/api/app/privacy_contract.py", ["U128", "U129"], "the privacy contract"),
    ("platform/api/app/key_store.py", ["U128", "U129"], "where keys are kept"),
    ("platform/api/app/opa.py", ["U72", "U78", "U98", "U99", "U126", "U127", "U128", "U129", "U130"],
     "the client the platform asks the policy through: the checks that ask for access, roles, leases and reports"),
    ("platform/policy/*.rego",["U72", "U78", "U98", "U99", "U126", "U127", "U128", "U129"],
     "a policy change can reach every decision; this is the set that asks people for access or roles. Run the policy tests too"),
    ("platform/schema.sql", ["V1", "V4", "U98", "U99", "U100", "U137"],
     "a table or rule in the schema: immutability, classes, closing, holds and the purge, which deletes whatever has a tenant id"),
    ("docs/*", [], "documentation"),
    ("web/*", [], "the console, which has its own build and tests"),
    (".github/*", [], "the CI workflow"),
    ("*.md", [], "documentation"),
]

# A source file under one of these has to be answered for by a rule above or by a check that mentions it.
SOURCE_ROOTS = ("platform/", "worker/", "agent/", "infra/", "scripts/", "docker-compose", "start-dev", "stop-dev", "run-verification", "wsl-docker")


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


LAST_PASS = ROOT / "verify" / "history" / "last-full-pass.json"


def tree() -> dict[str, str]:
    """A fingerprint of every file git knows or would add, so a change to any of them can be told from a file that stayed as it was."""
    files = set(git("ls-files")) | set(git("ls-files", "--others", "--exclude-standard"))
    out = {}
    for f in sorted(files):
        path = ROOT / f
        if path.is_file() and not f.startswith("verify/history/"):
            # Line endings do not count: git rewrites them on checkout, and a file restored to what it was is not a change.
            out[f] = hashlib.sha1(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return out


def changed(since: str) -> tuple[list[str], str]:
    """The files that differ from the last full pass (or from `since`, with none recorded), and which of those it was."""
    if LAST_PASS.exists():
        raw = json.loads(LAST_PASS.read_text(encoding="utf-8"))
        was = {k: v for k, v in raw.items() if not k.startswith("#")}
        now = tree()
        return sorted({f for f in set(was) | set(now) if was.get(f) != now.get(f)}), f"the last full pass, {raw.get('#taken', 'time unknown')}"
    files = set(git("diff", "--name-only", since)) | set(git("ls-files", "--others", "--exclude-standard"))
    return sorted(files), f"{since}, because no full run has passed on this machine yet"


def scripts() -> dict[str, str]:
    """script file -> check id, read from the runner's own list, so there is one list."""
    text = (ROOT / "verify" / "run_all.py").read_text(encoding="utf-8")
    return {m.group(2): m.group(1).split()[0] for m in re.finditer(r'\("([^"]+)",\s*"(v[^"]+\.py)"\)', text)}


def mentions(stem: str, by_script: dict[str, str]) -> list[str]:
    """The checks whose script names this module.

    Only for a name distinctive enough to mean the module: one with an underscore, or nine letters or more. A name like `main`, `db` or
    `config` appears in half the suite's prose, and matching on it would pick checks that have nothing to do with the file.
    """
    if "_" not in stem and len(stem) < 9:
        return []
    found = []
    for script, check_id in by_script.items():
        try:
            if re.search(rf"\b{re.escape(stem)}\b", (ROOT / "verify" / script).read_text(encoding="utf-8")):
                found.append(check_id)
        except OSError:
            pass
    return found


def choose(files: list[str]) -> tuple[list[str] | None, list[str]]:
    """(check ids, or None for the full suite) and the reasons, one per line."""
    by_script, ids, why = scripts(), set(), []
    for f in files:
        if f in SHARED:
            return None, [f"{f} is shared test code that changes what every check does, so the full suite"]
    unanswered = []
    for f in files:
        name = Path(f).name
        if f.startswith("verify/") and name in by_script:
            ids.add(by_script[name])
            why.append(f"{f}: the check itself changed")
            continue
        rule = next(((p, c, r) for p, c, r in TABLE if fnmatch(f, p)), None)
        if rule:
            ids.update(rule[1])
            why.append(f"{f}: {rule[2]}" + (f" ({', '.join(rule[1])})" if rule[1] else ""))
            continue
        if f.endswith(".py") and not f.startswith("verify/"):
            hits = mentions(Path(f).stem, by_script)
            if hits:
                ids.update(hits)
                why.append(f"{f}: named by {', '.join(sorted(set(hits)))}")
                continue
        if f.startswith(SOURCE_ROOTS):
            unanswered.append(f)
        elif not f.startswith("verify/"):
            why.append(f"{f}: nothing to check")
    if unanswered:
        return None, [f"no quick set is known for {', '.join(unanswered)}, so the full suite"] + why
    if ids:
        ids.update(RIPPLE)
        why.append(f"always added: {', '.join(RIPPLE)} (cheap, and broken by rule from anywhere)")
    return sorted(ids, key=lambda i: (i[0], int(re.sub(r"\D", "", i) or 0))), why


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--snapshot" in args:
        target = Path(args[args.index("--snapshot") + 1])
        target.write_text(json.dumps({**tree(), "#taken": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")}), encoding="utf-8")
        sys.exit(0)
    if "--accept" in args:
        LAST_PASS.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args[args.index("--accept") + 1], LAST_PASS)
        sys.exit(0)
    since = args[args.index("--since") + 1] if "--since" in args else "main"
    files, base = changed(since)
    ids, reasons = choose(files)
    if ids is None or ids:
        print("FULL" if ids is None else "\n".join(ids))
    print(f"# changed since {base}: {len(files)} file(s)")
    for r in reasons:
        print("# " + r)
