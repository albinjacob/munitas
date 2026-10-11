# Munitas: runbook

Operational procedures for an administrator or a support agent: not how the
platform works, only what to run and when. For how it works, see
[README.md](README.md) and the
[walkthroughs](https://albinjacob.github.io/munitas/walkthroughs/feature-walkthrough.html). For what has
been verified, read the scripts in [verify/](verify/).

This file is the living reference for admin- and support-facing operations.
Update it in the same change whenever a new operational script is added, or
an existing one's behaviour changes. A runbook that falls behind the scripts
it describes is worse than no runbook, because it is trusted anyway.

Every script below runs on the host, not inside a container. The first three
connect directly to Postgres and SeaweedFS using the same `.env` credentials
the platform itself uses (`MUNITAS_PG_DSN`, `MUNITAS_S3_ADMIN_KEY`, `MUNITAS_S3_ADMIN_SECRET`,
overridable as environment variables; see `.env.example`). The last one only
talks to the control plane's own HTTP API, the same as the console does.

Every `localhost` port that appears below is a default from `config.json`.
If a port conflicts with something else already running on your machine,
change it there and re-run `start-dev.ps1` (or `scripts/render_ports_env.py`
by hand) rather than editing any command below.

---

## First-time setup

Everything else in this file assumes these are already in place. None of
it happens automatically, and `start-dev.ps1` warns rather than failing
outright if a piece is missing, so the stack can still come up partially
without it.

1. **WSL2, with a distro installed.** Every command in this file assumes
   `Munitas`; if yours is named differently, pass `-WslDistro <name>`
   to `start-dev.ps1`, `stop-dev.ps1`, and `run-verification.ps1`.
2. **Docker running inside that distro, not Docker Desktop.** This
   project reaches Docker through `wsl -d <distro> -- docker ...`; a
   Docker Desktop install's `docker.exe` on the Windows PATH is not used
   and does not need to work. Install Docker Engine inside the distro
   itself, following Docker's own Linux install instructions for Ubuntu,
   then confirm it: `wsl -d Munitas -- docker ps`.
3. **An NVIDIA GPU with a current Windows driver**, for the real
   de-identification worker (`worker/main.py`), which runs natively on
   Windows rather than inside a container.
4. **The host worker's own Python environment**, at the repository root:

   ```powershell
   python -m venv .venv
   .venv\Scripts\python -m pip install -r worker\requirements.txt
   ```

   `start-dev.ps1` looks for `.venv\Scripts\python.exe` and warns, rather
   than failing, if it is missing; the rest of the stack still comes up,
   just without this worker running.
5. **The sandbox worker's own Python environment, inside WSL2** (only
   needed for sandboxed agent runs; everything else works without it):

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   uv venv --python 3.12 ~/.munitas/sandbox-venv
   uv pip install --python ~/.munitas/sandbox-venv/bin/python \
       -r worker/requirements-sandbox.txt
   ```

   `start-dev.ps1` prints these same three commands itself if it finds
   this venv missing, so this step is self-documenting at runtime too.

Then `.\start-dev.ps1` brings up the rest.

---

## Bringing up a new tenant

**Script:** `scripts/admin/create-tenant.py`

**When to use it:** standing up a new tenant (a fresh customer
organisation, or a local scratch environment) needs departments, people
and workload identities to exist before anything else can be registered
through the console, and nothing in the API can create the first one of
those on its own.

**What it does:** reads an onboarding file (`scripts/admin/onboarding-template.json` is
a starting point, copy and edit it) naming departments, people and
workload identities by a local `key`, validates every declared role
against the policy engine first, then creates the tenant and everything
the file names. Idempotent row by row: rerunning is safe, and editing the
file to add one more person and rerunning creates only that person.

```bash
python scripts/admin/create-tenant.py --tenant acme --config scripts/admin/onboarding-template.json
```

---

## Connecting real logins to directory rows

**Script:** `infra/kratos/seed-identities.py`

**When to use it:** after a fresh volume, or after registering a new human
in `directory` by hand. Real authentication is the console's only identity
mechanism (there is no local-picker fallback; see `web/README.md`), so a
human with no linked Kratos identity has no way to sign in at all.

**What it does:** loops over every human listed in
`infra/kratos/identities.json` (currently thirty-four: twelve in `health`,
ten in `canary`, four in `neighbour`, three in `harbour`, five in
`finance`, each organisation's people coming from its own seed file
under `infra/postgres/`), creates a Kratos identity with a password for each,
and sets `directory.kratos_identity_id` so `platform/api/app/auth.py` can
resolve a session back to it. One shared dev password across all of them,
printed at the end of the run. Idempotent per identity: re-running only
fills in whichever rows are still unlinked.

**Enforcement covers the human-decision endpoints.** Signing in resolves a
real session (`GET /auth/whoami`), and `approve_lease`, `reject_lease`,
`revoke_lease`, `request_lease`, and the agent
`deploy`/`start_run`/`approve_run` trio all require it, so a
caller-supplied identity does not work for any of them. `POST /credentials`'s
workload path and `promote` still trust whatever identity the request body
claims, on purpose: workloads have no Kratos session to present.

```bash
.venv\Scripts\python.exe infra/kratos/seed-identities.py
```

**Pipeline steps for the test organisation.** The speech and redaction steps
(`ingest`, `transcribe`, `detect`, `handoff`, `redact`) are registered per
organisation, and a run that finds one missing stops with "the dataset action ... is
not registered". The health example registers them when it is seeded. `canary`
has them once `v139` or `v62` has run (both register them first), or by hand:

```bash
.venv\Scripts\python.exe -c "from worker.run_pipeline import ensure_actions; ensure_actions('canary')"
```

---

## Registering an agent version from outside this repository

**Scripts:** `agent/register_version.py` (this repo's own built-in agent),
`scripts/admin/register-agent-version.py` (any other agent)

**When to use it:** a team's agent code lives in its own repository, not
inside `munitas/`. `agent/register_version.py` defaults `model_id` and
`tool_scope` from this repo's own `agent/model.py` and `agent/tools.py`,
which only makes sense for the agent that ships with this platform.
`scripts/admin/register-agent-version.py` has no such defaults: `--model-id` and `--tool`
are required, because an external team's model and tools are not this
codebase's to guess. It is deliberately self-contained (no imports from
`agent/`), so it can be copied into another repository on its own.

**Safety boundary:** same as every other agent version: sealed on
registration, immutable from then on (`agent_version`'s rewrite rules in
`platform/schema.sql`). The platform never inspects or verifies the
declared code; `code_hash`, `model_id` and `tool_scope` are trusted the way
`dataset.provenance` already is, from whoever registered the version.

**What `source_path` should be:** the repository's own remote URL
(`git remote get-url origin`), not a local disk path. A local path is only
meaningful on the machine that produced it.

```bash
# From an external agent's own checkout, with Python available.
python scripts/admin/register-agent-version.py \
  --agent-id <uuid> --registered-by <directory-id> \
  --model-id gpt-4.1 --tool search_docs --tool summarise \
  --source-path "$(git remote get-url origin)"

# From any language or CI system, with no dependency on this repo at all.
curl -X POST http://localhost:8000/agents/<uuid>/versions \
  -H "content-type: application/json" \
  -d '{"code_hash": "'"$(git log -1 --format=git:%H -- .)"'",
       "source_path": "https://github.com/example/their-agent",
       "model_id": "gpt-4.1", "tool_scope": ["search_docs"],
       "registered_by": "some-directory-id"}'
```

**A note on `tool_scope`:** it is declarative. Nothing checks it when the
agent runs, so it records what the version was said to need rather than
limiting what it can call. Every tool call is policy-checked under the
agent's own identity, which is what actually constrains it.

---

## Checking the platform still does what it claims

**Script:** `run-verification.ps1`

**When to use it:** after any change to the platform, and on whatever rhythm
suits you otherwise. This is the way to run the suite: running
`verify/run_all.py` directly still works, but skips the record keeping and the
cleanup below.

**What it does:** three things that belong together.

1. Runs `verify/run_all.py` inside the `munitas-api` container, where the
   scripts sign S3 requests properly and see the platform's own network. Every
   script is timed.
2. Appends the run to `verify/history/runs.jsonl` and rewrites
   `verify/history/index.html`.
3. Frees the storage of sealed canary versions older than one day.

**The history page** is the answer to "when did this last fail". Open
`verify/history/index.html` in a browser. The top table is one row per check,
carrying its latest result, the date it last failed, how often it has failed,
and its median and slowest times. The lower table is every individual result,
filtered by free text, by pass, fail or skipped, and by a date range. Both sort
on any column. Nothing is served: it is a file on disk that each run rewrites,
so it works offline and needs nothing running.

**Skipped checks are not passes, and are shown that way.** A check skips when it
cannot run here, for example one that needs real Cloudflare R2 keys on a machine
that has none. A script that passed some checks and skipped others shows as a
pass with its skip count beside it (`pass · 2 skipped`). A script that passed
nothing shows as `SKIP`, in amber, and the filter's "Skipped only" option finds
them. A script that exits cleanly without reporting its counts also shows as
`SKIP`, because nothing is known to have passed. A skip never changes the exit
code: only a failure does. Runs recorded before skips were counted carry no
counts and show a plain pass.

**Why cleanup is not gated on a clean run.** A one-day floor leaves a failing
run's data in place until tomorrow, which is when you would want to look at it,
while yesterday's leftovers go. Reclaiming only when everything passes would
mean a green run today wipes the evidence under a failure you are still
reading, because the reclaimer selects by tenant and age rather than by which
run produced a version.

**Safety boundary:** step 3 is `scripts/admin/reclaim-storage.py`, so everything true of it
is true here: no `DELETE` against any table, no reach into a
`production`-purpose tenant, only objects removed and the removal recorded in
`storage_reclamation`.

```powershell
# The normal case.
.\run-verification.ps1

# Record the run and free nothing.
.\run-verification.ps1 -NoReclaim

# Sweep every eligible canary version now, not just those over a day old.
.\run-verification.ps1 -ReclaimOlderThanDays 0
```

#### The quick loop, between full runs

The whole suite takes about 15 minutes. While changing code, run only what a change needs:

```powershell
# What changed since the last full run that passed decides the checks, and each choice is explained.
.\quick-verify.ps1

# Say what it would run, and run nothing.
.\quick-verify.ps1 -List

# Exactly these checks.
.\quick-verify.ps1 -Only U128,U129
```

A quick run is not recorded on the history page, says so when it ends, and does not replace the full suite, which is what CI runs and what
must pass before anything is pushed. A file that no rule or check covers makes it run the whole suite rather than guess. The cheap checks
that a change anywhere can break by rule are always added. A full run that passes is what later quick runs measure change against.

Both this and `start-dev.ps1` reach Docker through WSL2, using the shared
helpers in `wsl-docker.ps1`. Pass `-WslDistro` to either if the distro is not
named `Munitas`.

### Proving that a killed worker resumes, by hand

**Script:** `verify/v6_durable_retry.py`

**When to use it:** after changing the worker, the pipeline workflow or how
activities retry, and whenever you want direct evidence that a crash in the
middle of a run loses no work and duplicates none. `run_all.py` cannot do this
itself, because it runs inside a container and has no worker process to kill,
so it reports this check as skipped by name. This procedure is that check.

A faster companion, `verify/v6c_retry_policy.py`, runs in CI and takes about
half a minute on a laptop. It runs the real pipeline workflow against an
in-process Temporal test server, with fake steps, one of which stops
heartbeating the way a killed worker does. It proves the workflow's retry and
heartbeat settings: a lost step is noticed within minutes, retried with the
same idempotency key, every other step runs once, and a step that never
recovers fails the run after a bounded number of attempts. It does not prove
that the real steps are safe to repeat, which is what the procedure below is
for.

The first run downloads Temporal's test server, a binary of about 60 MB, from
`temporal.download`. Set `MUNITAS_TEST_SERVER_DIR` to a folder to keep it
somewhere of your choosing; CI does this and keeps the file in GitHub's cache,
keyed on the pinned `temporalio` version, so later runs do not depend on that
host. If the server cannot be obtained, the script says so and exits 3, which
means nothing was tested and the workflow is not at fault. Exit 1 means a check
failed.

**What it costs:** a real de-identification of a few files, so the GPU is
busy for several minutes, and the run writes real dataset versions into the
tenant the worker is configured for (`canary` unless `MUNITAS_TENANT` says
otherwise). Use a fresh workflow id each time.

**Before you start:** the stack and the worker are up (`.\start-dev.ps1`), and
the Temporal UI answers at the address `start-dev.ps1` printed.

1. **Start a run** in one terminal. It stays open and reports when the run
   ends. `MUNITAS_DATA` must be set in any terminal you open yourself:
   `start-dev.ps1` only passes it to the windows it opens.

   ```powershell
   $env:MUNITAS_DATA = "<your data folder>"
   $wf = "kill-test-$(Get-Date -Format yyyyMMddHHmmss)"
   .\.venv\Scripts\python.exe -m worker.run_pipeline --limit 4 --triggered-by canary-engineer --workflow-id $wf
   ```

2. **Wait until transcription is running.** Open the workflow in the Temporal
   UI (the run prints its address) and look for a pending activity named
   `transcribe`. Killing earlier or later than this proves nothing, because
   the interruption has to land inside a step.

3. **Kill the worker, and only the worker.** Leave Temporal and the containers
   running. In a second terminal:

   ```powershell
   $w = Get-CimInstance Win32_Process -Filter "name='python.exe'" |
       Where-Object { $_.CommandLine -match 'worker\.main' -and $_.ExecutablePath -like '*\.venv\*' } |
       Select-Object -First 1
   taskkill /PID $w.ProcessId /T /F
   ```

   `/T` also ends the launcher's child process, which is part of the same
   worker. The workflow must still show as running afterwards.

4. **Start the worker again**, the way `start-dev.ps1` does:

   ```powershell
   $env:MUNITAS_DATA = "<your data folder>"
   .\.venv\Scripts\python.exe -m worker.main
   ```

   Nothing else is needed. Temporal notices the lost step only after its
   heartbeat timeout expires, so expect a couple of minutes before it is
   retried, then the run carries on from there. The first terminal reports
   `COMPLETED` when it is done.

5. **Check the result** with the workflow id from step 1:

   ```powershell
   .\.venv\Scripts\python.exe verify\v6_durable_retry.py $wf
   ```

**What a pass means.** Every line is `PASS`, and specifically:

- the workflow completed although its worker died;
- the history shows a step that ran a second time, with the reason its first
  attempt was lost (for a killed worker this reads `activity Heartbeat
  timeout`);
- each step of the run produced exactly one dataset version, so the retry
  resumed the work rather than repeating it. Only this run's own steps are
  counted, so other runs in the database do not affect the result.

**What a failure means.** `every activity ran once` means the worker was not
actually interrupted during a step, so the run proves nothing: repeat from step
1 and kill it while `transcribe` is pending. Any `exactly one dataset version`
failure is the real thing this check exists to catch.

**Checking that the check can fail.** If you change the script, run it once
against a workflow that was never interrupted (repeat step 1 with `--limit 1`
and skip steps 2 to 4). Only the interruption line may fail.

---

## Clearing one unsealed dataset's leftover data

**Script:** `scripts/admin/cleanup-dataset.py`

**When to use it:** a dataset was registered and then abandoned, or picked up
test data by mistake (a wrong HuggingFace repo, an integration test's
leftovers) and needs to go back to empty before real data is brought in. The
console itself has no way to remove a file or a fetch job once it exists;
this is that missing action, run by hand.

**Safety boundary:** refuses outright if the dataset has any sealed version.
It never deletes a `dataset_version` row, or anything that references one
(`class_transition`, `lease_request`, `access_lease`, `access_decision`),
which are the lineage and access-audit trail, and this script does not touch
them under any flag. A dataset with a sealed version needs *scripts/admin/reclaim-storage.py*
below instead.

**What it does:** deletes the dataset's `dataset_source` and
`huggingface_fetch_job` rows and every object under its storage prefix. The
dataset itself stays registered, empty, ready for a fresh upload or fetch.

```bash
# See what's there first. Changes nothing.
python scripts/admin/cleanup-dataset.py --tenant health --name raphaelmerx/openwho --dry-run

# Clear it, with a typed confirmation.
python scripts/admin/cleanup-dataset.py --tenant health --name raphaelmerx/openwho

# Or by id, and skip the prompt for scripted/support-tool use.
python scripts/admin/cleanup-dataset.py --dataset-id <uuid> --force
```

---

## Freeing a sealed version's stored bytes

**Script:** `scripts/admin/reclaim-storage.py`

**When to use it:** storage cost needs to come down and a version's data is
no longer needed to be readable. The usual case is a canary/test tenant's
old verification runs, or a retired tenant whose organisation is gone and
whose storage nobody wants to keep paying for.

**Safety boundary:** contains no `DELETE` against any table, verified
directly by a check that reads the script's own source to confirm it. It only removes objects
from SeaweedFS and records that it did so in `storage_reclamation`. The
version row, its lineage, its class history and every access decision made
about it all survive untouched: only the files go. It also cannot reach a
`production`-purpose tenant: the selection query excludes one even before the
named-tenant guard runs, so there are two independent reasons it can't
happen, not one.

```bash
# See what would be freed across every eligible canary/retired version.
python scripts/admin/reclaim-storage.py --dry-run

# Free canary storage older than 14 days (the default), on schedule.
python scripts/admin/reclaim-storage.py

# A specific tenant, offboarded, freed regardless of age.
python scripts/admin/reclaim-storage.py --tenant t1 --older-than 0 --reason "tenant offboarded, storage no longer needed"
```

---

## Sweeping throwaway probe tenants

**Script:** `scripts/admin/tidy-probes.py`, scheduled nightly through
`worker/schedule_tidy_probes.py` (Temporal, task queue
`munitas-housekeeping`, workflow `TidyProbesWorkflow`).

**When to use it by hand:** right after running `verify/`, to clear the
fresh `scratch`-purpose tenant(s) it minted before they sit around. Its
`--min-age-hours` defaults to 0 for this case, since a human who just watched
the suite finish knows nothing is still using them.

**Safety boundary:** only ever touches `purpose = 'scratch'` tenants, which
the database itself only allows to be declared before a tenant holds
anything (`refuse_late_disposability`). A second, independent id-based guard
(`NEVER_TOUCH` in the script) refuses `health`, `finance` and `canary`
regardless of what the query returns. The scheduled run additionally never
acts on a tenant younger than its age floor (24h by default), so a verify
run still mid-flight cannot have its tenant deleted out from under it.
Immediately before each delete, the tenant's purpose is re-read and
re-checked inside the same transaction, in case anything changed between
selection and action.

```bash
# See what's there. Changes nothing.
python scripts/admin/tidy-probes.py

# Clear everything found, right now (manual, post-verify use).
python scripts/admin/tidy-probes.py --apply

# What the nightly schedule actually runs.
python scripts/admin/tidy-probes.py --min-age-hours 24 --apply
```

Register or re-register the schedule with:

```bash
python -m worker.schedule_tidy_probes --schedule-id nightly-tidy-probes
temporal schedule describe nightly-tidy-probes
```

---

## Test tenants left behind by a verification run

`run-verification.ps1` ends with `scripts/admin/check-leftover-tenants.py`, after the canary tidy and the probe sweep, so it only sees what
no cleanup could remove. It exits 1 and names each leftover (what it holds, and why it counts) when a check made a tenant and did not remove it, and
exits 2 when it could not look. A run whose checks all passed but that left tenants behind, or could not check, exits 3.

The result is also kept on the verification history page. The run is recorded before any cleanup (cleanup deletes data), so the result goes in a second line
of `verify/history/runs.jsonl` tied to that run (`verify/report.py --add-after`), and the page shows it as the `LEAK` check and in the card "After the last run's cleanup".
If that line cannot be recorded the run exits 3 as well.

A tenant counts when it is not one of the standing set (`STANDING` in the script: health, finance, harbour, canary, r2-probe-a, r2-probe-b) and it is
disposable (`scratch`), a `canary` fixture, or named like a test fixture. Any other `production` tenant is never flagged. Keeping a tenant on purpose means
adding it to `STANDING`, which is the decision that it is permanent. To clear a leftover: `scripts/admin/tidy-probes.py --apply` removes disposable
ones; one that holds sealed versions and is not disposable needs `scripts/admin/nuke-tenant.py`, which asks for the name typed back. The fix for the
cause is in the check that made it: create the tenant through `fixture_tenant` with a prefix listed in `common._DISPOSABLE_PREFIXES` (U124 fails if that
list and the script's disagree), or remove it in a `finally`.

---

## Custom pipeline steps that are given the files

A pipeline an operator registers can have script steps. A script step is normally given only the small JSON file of what earlier steps produced
(`/data/inputs.json`), and returns one JSON object as the last line it prints. A step that needs the sound itself (its own speech model, its own way
of finding where a name is spoken) says so in the pipeline file:

```yaml
steps:
  - name: inspect
    kind: script
    script: steps/inspect.py
    reads_files: true    # the step may read the files
    writes_files: true  # the step may write files, which become one new version
    memory_mb: 2048     # optional: default 1024, at most 4096
    scratch_mb: 512     # optional: default 256, at most 2048 and never more than memory_mb
```

**How the step reads its files.** It imports the library the platform gives it, `munitas_step`, and asks the platform for them. It does not
read files from a folder, and nothing is copied onto the machine that runs it:

```python
import munitas_step as step

for item in step.list_files():              # record_id, bytes, erased
    if item["erased"]:
        continue                                 # the patient was erased: left out
    f = step.open_file(item["record_id"])   # a seekable file, read 8 MiB at a time, never held whole
    data = step.read_file(item["record_id"])        # or all of it, in memory
    step.save_file(item["record_id"], "/tmp/x.wav") # or saved to the container's own scratch space
step.result({"recommendation": "not_measured", "reason": "..."})   # the small JSON result
```

**What the step's container is.** It runs as an unprivileged user, with every Linux capability dropped, on a read-only root filesystem. Its only
place to write is `/tmp`, which is memory-backed and size-limited (`scratch_mb`) and goes with the container. It has a memory limit (`memory_mb`),
a process limit and a time limit. A step with no `reads_files: true` has no network at all. A step with it joins `stepnet`, an internal network with
no route to the internet, whose only other member is `step-gateway`. The gateway forwards the platform's `/step/v1/` routes and answers 404 to
everything else. The step cannot even resolve the name of storage, the database, the platform or any other service. Its environment holds its pass and
the gateway's address, and no storage address, no key, no password and no worker token. The patients' keys never leave the platform.

**What the platform checks, on every request and not only the first.** The pass is real, unexpired and made for a step (a run's own credential, or a
pass presented anywhere else, is refused); the pipeline run is still going; the version of the pipeline that run came from declares this step as one
that is given the files; the version is still in production (not returned to development, not retired); and the patient was not erased. A file is opened before it is read.
Opening is the one write to the key log (`open a file for custom step inspect of pipeline <name> version <n>`), and it returns a handle that every
read must carry. A patient erased while their file is being read makes the next block that has to be fetched raise `FileErased`.

**The life of a version.** A pipeline version is in one of three stages, and the pipeline's page shows which beside the version number.

| Stage | What it means | How it gets there |
| --- | --- | --- |
| In development | Runs only on development data: files declared synthetic, or a development sample drawn for the person who runs it. | A version with a script step that is given the files or writes files starts here. |
| In production | Runs on any data the department allows, and its steps may be served. | A version made only of the platform's own steps starts here. A version with such a script step gets here when an approver of the department that owns the pipeline, who is not the person who uploaded it, moves it (button **Move to production**, or `POST /pipelines/{id}/versions/{version_id}/promote`). |
| Retired | Cannot be run, and cannot come back. | An approver of the owning department, or the person who uploaded the version, retires it (button **Retire this version**, which asks first, or `POST .../retire`). A later version replaces it. |

**Return to development** (`POST .../demote`, an approver of the owning department) undoes a promotion. It applies only to a version that was
promoted. Retiring and returning only tighten, which is why the person who uploaded a version may retire it and needs nobody else to agree.

**When it takes effect.** At the start of a run (the version is refused with the stage named) and on every request a step makes (a pass already
issued stops working at its next request, and a step still writing has everything it wrote discarded when it finishes). Each decision keeps who made it
and when: the promotion in `pipeline_version_approval` (a returned one stays as a record), the retirement in `pipeline_version_retirement`.

**Development samples.** A version in development may run only on development data, and for locked files that is one of two things.

- *A sample.* A person who holds the data engineer role (or the de-identification reviewer role) asks on the page of a sealed version of locked
  files (`POST /dataset-versions/{id}/development-samples`): a kind, a purpose, how many files (1 to 50) and for how many days (1 to 14).
  An approver of the department that owns the files, who is not the person asking, decides (`POST /development-samples/{id}/approve` or
  `/decline`). The approver may lower the number or the days and never raise them, and cannot choose the files.
- *Files declared synthetic.* An approver of the owning department declares a version synthetic, with a reason
  (`POST /dataset-versions/{id}/declare-synthetic`). Any person in the organisation may then run a version in development on it.

The platform draws a sample by one fixed method (`platform/api/app/development_samples.py`): files of erased patients are left out, one file
for each patient (the one whose hash ranks first), three length bands each holding a third of what is left, the number asked for divided across the
bands in proportion (the shorter bands take the remainder first), and in each band the patients whose hash ranks first. The hash is SHA-256 of a salt
(the version, the person asking, the kind and the method version), the patient's code and the file's identifier. So the same person asking for
the same kind of sample of the same version is given the same files, and asking again collects nothing new. A debug sample names the files
instead: the approver may lower how many of them are given, and the platform gives the first ones named.

A sample is a new sealed version, in a dataset named after the original with `-dev-sample-` and a short code. It holds a short list and the rows
that say which patient each file is about, and its list of files names the stored files the original already names. Nothing is copied and
nothing is stored in the clear, so erasing a patient makes their file unreadable in the sample too. The draw is kept with the request (the
method, the salt, the identifiers chosen, how many came from each band) so that it can be reproduced. A person asking for a sample sees counts, never a
file or a patient.

A run of a version in development is refused at the start, and a step it already runs is refused at its next request, unless the version it reads is
declared synthetic or is a sample that is in force for the person who started the run. A sample ends on its own after the days granted, or sooner when
the person it was drawn for or an approver ends it (`POST /development-samples/{id}/end`).

Checks: U144 (the platform's half, in the container: the draw by itself, who may ask and decide, pointers and not copies, what a run may read, erasure,
the declaration) and U145 (the loop for real on the host, about four minutes: a version in development refused on the real files and run on a
sample in the sandbox, reading exactly the sample's files byte for byte, a writing version, an ended sample refused, an erased patient left out).
The console check is U146.

**What the stages do not cover today.** A version made only of the platform's own steps is in production from the start, so the stages limit only
versions that have a custom step. The limits on a sample (50 files, 14 days) are constants in the policy and are not yet settings for an
organisation or a department. Outputs of a run in development are sealed under review like any other and are not yet removed when the sample ends.

**Writing files back.** A step with `writes_files: true` (it can have this with or without `reads_files: true`) writes through the same door, with the
same library, and the same pass. It never holds a storage address or key:

```python
step.save_output("notes.txt", b"...")                                             # a whole file from bytes
step.copy_to_output("visit-1.wav", "/tmp/out.wav", from_record="r-0001")           # a file from the container's scratch space, sent in pieces
with step.write_output("visit-2.wav", from_record="r-0002", size=len(data)) as out:  # or written as a stream; an error inside the block discards the file
    out.write(data)
```

**Files made from a file are locked.** A redacted file still carries the patient's voice, so what a step makes from a file is the
patient's data. A step says which file a file was made from (`from_record`) and, for a stream, its size up front, because the lock is fitted to
the size. The platform checks that this step opened that file in this run (it keeps a record of what each step opened), locks each part under that
patient's key as it arrives (one line in the key log for each part, naming the file and the step), and records the file as belonging to that patient, so
erasing the patient makes it unreadable together with the file and lists the version that holds it. No plain copy is ever stored. A file that looks
like sound (wav, mp3, flac, ogg, mp4 audio), from a step that was given files, with no `from_record`, is refused and what was stored of it is
thrown away, with a reason that says how to open it. A step that is not given files may write sound: it cannot have made it from one.

Each call returns the size and fingerprint of the file. The library sends each file to the platform in parts of at most 8 MiB, and the platform writes
them to storage and reads the finished file back to check its checksum. Limits: 500 files, 512 MiB for one file, 1 GiB in all, and the name `records.json` is reserved. When the container ends, the
worker tells the platform (`/internal/dag-step/finish`). If the step succeeded, the platform seals everything that was completed as one new version
in a dataset named after the run's dataset and the step, owned by the same department, at the access level **under review**, so nobody may use it
until a reviewer decides. The version records the step, the code version and the version it was made from, so erasing a patient later lists it. If the step failed,
or the version was returned to development or retired during the run, everything it wrote is discarded and nothing is sealed. Finishing twice does nothing the second time.

**A step that goes quiet.** A script step sends a heartbeat to Temporal every 15 seconds while it runs, and the workflow allows 90 seconds without
one. A result that is lost on its way back, or a sandbox worker that dies, is therefore noticed within a minute and a half and not after the whole 30
minutes. A step that only reads is then run again, up to three times in all. A step that writes files is not run again: a second run would meet the first
one's half-finished files. It fails at once, and the person runs it again. (The sandbox worker has logged "Network error while completing activity" three
times in a day; the cause was not found, and this makes it harmless rather than rare.)

**What a step cannot do.** Anything the platform does not offer as a route. If a step copies audio into its JSON, the approver is the safeguard, not the
platform. Review and testing on synthetic data are the operator's.

## Opening the files of a version

A person opens the files of a version (a file, or a file a step made) through the platform, never through storage. `GET
/dataset-versions/{id}/records` lists them and says whether the caller may open them and why not; `POST .../records/{record}/open` opens one and
returns a handle; `GET /record-stream/{handle}` returns the bytes, whole or by range (`Range: bytes=a-b`, 206 with `Content-Range`), unlocked inside the
platform as they are sent. A sound player in the console reads the handle's address directly. Every opening is written to `record_read_log` (who, which
file, why, how many bytes were streamed) and, for a locked file, once to the key log; ranges are not logged again.

The platform works out why a person may open a version's files from what it holds, and the policy (`open_record_decision`) turns it into an answer:
`own_sample` (a development sample drawn for this person, in force), `synthetic` (declared synthetic), `own_development_output` (made by a run this
person started, on development data still in force for them), or `none`. With `none`, which is every real file and everything made from one, the
answer is that a person opens them only under a lease, which is not yet something the platform offers. The roles that may open are the data engineer, the
de-identification reviewer and the data custodian. The handle lasts 15 minutes, is the only thing the stream route takes (a sound player cannot send a
header), is refused by every route that takes a run's credential, and is asked about again on every request: so a sample that ends, a person who leaves
or a patient who is erased stops what is playing at its next request. There is no download in the console.

Checks: U148 (locked outputs, in the container), U149 (the door, in the container), U150 (playing in the console), and the host loop U145, which opens what
a real step wrote and compares it, byte for byte, with what the same change makes of the original sound.

## What was made for development ends

A development sample, and what a run makes on a sample or on files declared synthetic, is noted in `development_output` (when the sample is
approved, and when a step seals a version after a run on development data). It ends at the earlier of `development_output_days` (7, in the policy) and
the end of the sample it came from. A run on real files in production is never noted: what it makes is held under review and does not expire.

A timer inside the API (`MUNITAS_DEVELOPMENT_SWEEP_SECONDS`, 60; 0 turns it off) ends what is due, ten at a time: it deletes the objects under the
version's own folder, writes a `storage_reclamation` record (by `platform`, with the reason), and marks `expired_at`. The version's row stays, because a
sealed version is never edited or deleted. Only the version's own folder is touched: a sample holds a short list and a small table, and the stored
files the list names belong to the original version and stay. Nothing is deleted while a legal hold is pending or active, or while the
organisation is not open (retired, closing). A version that has ended is listed with basis `expired` and the reason in words, and nothing in it opens.

Checks: U151 (in the container, with its mutations: no hold check, no development-data guard, a wrong folder, ignoring a sample that ended) and U150
(the page says so, waiting for the live timer).

Checks: U141 (the platform's half, in the container) and U142 (a real run in the sandbox worker, on the host, about two minutes, which also reports from
inside the container what the step could and could not do):

```powershell
.venv\Scripts\python.exe verify\v141_custom_step_given_locked_files.py
```

## Pipeline runs that stopped without file it

A pipeline run's own workflow writes its ending as it finishes. A workflow killed from outside, or one whose worker
died, cannot, so its record stays open and the console refuses to start that version again. Temporal knows the run
stopped, but only remembers a finished workflow for the namespace's retention period (24 hours here; check with
`temporal operator namespace describe default`). After that it can no longer say how the run ended.

**Script:** `scripts/admin/close-finished-pipeline-runs.py` (preview, then `--apply`).
**Schedule:** `close-stopped-pipeline-runs`, every six hours, run by the housekeeping worker on the host.

```bash
# What is open and what Temporal says about each. Changes nothing.
python scripts/admin/close-finished-pipeline-runs.py
python scripts/admin/close-finished-pipeline-runs.py --apply

# The schedule (register once; the host worker must be running the current code)
python -m worker.schedule_close_finished_runs --schedule-id close-stopped-pipeline-runs
temporal schedule describe --schedule-id close-stopped-pipeline-runs
temporal schedule trigger  --schedule-id close-stopped-pipeline-runs      # run it now
```

- A run is open when it has no end time, and only then is it looked at. A run with an end time is never touched again.
- A run Temporal reports as still running is left alone. If Temporal cannot be reached nothing is changed, and the
  scheduled run fails visibly in Temporal's list of workflows rather than passing as a run that found nothing.
- The interval must stay well inside the retention period. Once a day would reach a run with barely a minute to
  spare. Check U122 after changing either: it fails if the schedule fires fewer than twice per retention period.
- A row the database refuses to change (for example an organisation that has been retired) is reported and left
  open, and the others still close.

**Where an ending came from.** Every closed run carries `ended_source`, set by code in the same statement as the end time
and required by the database whenever there is an end time:

| `ended_source` | Meaning |
|---|---|
| `workflow` | the pipeline's own workflow reported it as it finished |
| `job_runner` | the workflow never reported; Temporal's account of it was written in its place |
| `job_runner_no_record` | Temporal had already forgotten the run, so the status is `unknown` |
| `start_failed` | the platform could not start the workflow, so the run never began |
| `not_recorded` | the run ended before this column existed and nothing recorded how it was found out |

---

## Clearing the canary tenant's old fixtures

**Script:** `scripts/admin/tidy-canary.py`, run automatically at the end of `run-verification.ps1`
(skip it with `-NoTidyCanary`; change the age with `-TidyCanaryOlderThanHours`, default 2).

**Why:** the verification suite writes its fixtures into the `canary` tenant. A sealed version cannot be
deleted through the platform (check V1 proves it), so those rows used to pile up for ever, thousands of
them, and they show in screens: a custodian's queue that lists the oldest 100 arrivals stops showing new
ones once the suite has left 100 behind. `reclaim-storage.py` frees the files and keeps the rows; this
removes the rows and the files of canary datasets that are old enough. It is test-harness housekeeping, not
platform behaviour: nothing in the API calls it.

**It works on the canary tenant and on nothing else.** It takes no tenant argument (`--tenant` is
refused), and the tenant is a constant in `scripts/admin/_canary_purge.py`. The guards are checked at every
step, and the tool prints `[guard ok]` for the first three:

| Guard | What is checked |
|---|---|
| G1 | the tenant is the constant `canary`, never read from an argument or the environment |
| G2 | the database row has the id `canary` and the purpose `canary` |
| G3 | the storage binding is SeaweedFS and the bucket is exactly `munitas-canary` |
| G4, G5 | every dataset is read back from the database and belongs to canary, then locked and checked again inside the transaction that deletes it |
| G6 | every delete statement is scoped to canary in its own SQL wherever the table has a tenant column |
| G7 | the number of rows every other tenant has in the tables touched is the same before and after, or the whole batch is rolled back |
| G8 | every stored object is in canary's bucket under `canary/<a verified dataset>/` before it is deleted |

| G9 | an agent is taken only when it, its versions, runs and deployments are all old; its stored code is taken only from `canary/agents/<a verified agent>/`; the immutability rules on agent versions are off only inside the deleting transaction and are on again before it commits |
| G10 | a person is taken only when they have no login, are not one of the identities `infra/postgres/seed-canary.sql` creates (read from that file; the tool refuses to go on if it finds fewer than 11), and no row refers to them |

The tool works in three stages, in this order: old datasets, then old agents (the checks register agents, and registering one
creates a directory identity for its runtime), then the invented identities nothing refers to any more. The seeded people
and anyone with a login always stay. The caps are `--max-datasets` (3000), `--max-agents` (3000) and `--max-people` (5000).

Check U119 proves each guard refuses, and that canary datasets, agents and identities are deleted while a second
organisation's dataset, version, object, agent and identity are untouched.

```bash
# What would go. Changes nothing.
python scripts/admin/tidy-canary.py

# Delete what is older than 2 hours.
python scripts/admin/tidy-canary.py --apply

# A first clearing of a large backlog (the default cap is 3000 datasets).
python scripts/admin/tidy-canary.py --apply --max-datasets 6000
```

- Only datasets older than `--older-than-hours` go, so the run just made is still there to look at when it fails.
- Datasets that cannot be deleted without each other (a pipeline run's source and the outputs of its steps)
  are deleted together, and only when every one of them is old enough. A group with a young member is left whole.
- It leaves alone the three canary datasets whose files were freed most recently, and the oldest dataset that has a sealed
  version and has not been freed yet, so that the console's "the files were freed" screen (test U32) always has a version to
  look at once one exists. Files are freed a day after a version is made, so that test skips until the first one is.
- `--max-datasets` (default 3000) is a circuit breaker: a selection bigger than it is refused outright, so a
  mistake in the age calculation cannot turn into a mass delete. It is not a platform limit.
- Exit codes: 0 done or nothing to do, 1 a batch failed (nothing in that batch was changed), 2 a guard refused.

**The storage permissions.** Deleting a dataset removes its rows at once, but the permissions document in
storage keeps its folder grants until it is printed again, and nothing else would print it soon. So after every
batch the tool runs `scripts/admin/_canary_reprint.py` inside the API container. An update is always written,
whatever it removes: a lease that ended or data that was deleted is routine and is only summarised on the
update's row, and anything odd is raised as an alert (see "Storage permission alerts" below). If `/health`
shows `storage_permissions` failing after a canary clear, print it by hand:

```bash
wsl -d Munitas -- docker exec munitas-munitas-api-1 python /scripts-admin/_canary_reprint.py
```

---

## Closing a tenant

**Script:** `scripts/admin/retire-tenant.py`

**When to use it:** a tenant's real-world lifecycle has ended: an
organisation the platform served for a while has gone, or a scratch
environment is no longer needed. This is the correct way to close one:
everything stays readable, nothing is deleted, and `scripts/admin/reclaim-storage.py`
can free its storage afterward.

**Safety boundary:** one `UPDATE`, enforced everywhere by the same
database trigger that already closed `t1`. Refuses a tenant that does not
exist or is already retired.

```bash
python scripts/admin/retire-tenant.py --tenant acme
```

---

## Permanently deleting a tenant (personal, local use only)

**Script:** `scripts/admin/nuke-tenant.py`

**When to use it:** almost never. This is not the real-world pattern for
closing a tenant; `scripts/admin/retire-tenant.py` is. This exists for a local scratch
tenant that genuinely needs to be gone, not merely closed, on a
pre-production machine.

**Safety boundary:** refuses a `production`-purpose tenant outright, no
override. Everything else is a real, permanent, irreversible delete across
every table carrying that tenant's data, including bypassing the rewrite
rules that make a sealed `dataset_version` or `agent_version` undeletable
everywhere else in this platform. Dry run by default; the tenant id must
be typed back to proceed.

```bash
python scripts/admin/nuke-tenant.py --tenant scratch-1
python scripts/admin/nuke-tenant.py --tenant scratch-1 --force
```

---

## Wiping the entire local database

**Script:** `scripts/seed/reset-platform.ps1`

**Last resort. Almost never the right tool.** It drops the Postgres volume,
which holds every database the stack uses: every organisation, dataset,
version, lease and audit record, every login, and every workflow's history.
Sealed rows cannot be deleted any other way, and that is the guarantee, not a
limitation to work around.

Before changing anything it prints what will be deleted, counted from the live
database, what comes back on its own, and what it does not touch, then asks you
to type `reset`. There is no flag to skip that question. After the reset it
reconnects every seeded person to a login and prints what came back.

Use `scripts/seed/reseed-tenant.ps1` to rebuild one worked example,
`scripts/admin/cleanup-dataset.py` to clear one dataset, or
`scripts/admin/reclaim-storage.py` to free storage, instead.

A rebuild with `reseed-tenant.ps1` keeps the people that another organisation's records point at, and with them the organisation's own row, and deletes everything else
(`scripts/admin/nuke-tenant.py --keep-referenced-people`). The platform administrator is one person kept in one organisation who acts in the others, and a legal hold
placed in another organisation names that person, so the database refuses to delete them; the seed then fills in everything else around them. Run a script through
`scripts/run-logged.ps1 -Name <what> -Command "<the command>"` when it can fail: it keeps all of the output in `docs/internal/run-logs/`, so a failure is read from the
file and not found by running the script again.

```powershell
.\scripts\seed\reset-platform.ps1
```

---

## Running a command inside the API

To run a script or a command with the API's own code and dependencies, run it in the container that is already up:

```
docker compose exec -T munitas-api python <script>
```

Never use `docker compose run` on `munitas-api`. It looks like a disposable container that runs one command and exits, but the image's entrypoint
ignores the command and starts a whole second API server beside the real one, on the same database and with whatever settings the `run` was
given. Anything that then reaches "the API" can land on the second one. When it was given a different master key, it wrote encrypted storage
credentials the real API could not open, and every later access request failed with `InvalidTag` until those rows were ended by hand.

To check for a stray one, list the API containers. There should be exactly one:

```
docker ps --filter label=com.docker.compose.service=munitas-api --format "{{.Names}}"
```

If there is a second, remove it with `docker rm -f <name>` before running anything else. The verification wrapper (`run-verification.ps1`) checks
this itself and refuses to start while a second one is there.

## Troubleshooting: containers stuck in a crash-recovery loop (Windows + WSL2)

**Symptom:** `postgres` logs "database system was not properly shut down"
or "the database system is in recovery mode", `temporal` restarts, and
`docker ps` hangs or answers slowly. `docker inspect`'s `StartedAt` for
every container jumps to the same moment even though nothing restarted
them on purpose. From the containers' point of view the machine lost
power, because it effectively did.

**Cause.** The Docker engine runs inside the WSL2 distro (`Munitas` on this machine),
so it stops whenever the distro or its VM stops, taking every container
down uncleanly. Two separate things stop them:

- **The VM.** WSL2's `vmIdleTimeout` (about 60 seconds by default) shuts
  the VM down when it looks idle from Windows, even with containers
  running. That shutdown can hit a Hyper-V VmSwitch IOCTL timeout bug
  ([microsoft/WSL#40363](https://github.com/microsoft/WSL/issues/40363)),
  turning a clean restart into a hang and then a restart under load.
- **The distro.** Even with the VM kept up, WSL stops a distro that
  nothing on the Windows side is attached to. Seen on 2026-09-25 with the
  `.wslconfig` fix below in place: the VM had been up for over a day, yet
  the distro and dockerd stopped between two `wsl` commands.

**Fix, part 1: `.wslconfig`.** In `%UserProfile%\.wslconfig` (create it if
it does not exist; it is machine-wide, not per project):

```ini
[wsl2]
vmIdleTimeout=-1

[experimental]
autoMemoryReclaim=disabled
```

`vmIdleTimeout=-1` turns off the VM's idle shutdown.
`autoMemoryReclaim=disabled` stops the VM reclaiming memory mid-workload.
Run `wsl --shutdown` for the change to take effect, then start the stack
again.

**Fix, part 2: a keepalive.** A process attached to the distro for as long
as you work, so it never looks idle:

```powershell
wsl -d Munitas -- sleep infinity
```

It ends at a reboot or when its window closes.

**Both are checked for you.** `start-dev.ps1` and `run-verification.ps1`
call `Confirm-WslStaysUp` (in `wsl-docker.ps1`) before touching Docker:

- If `.wslconfig` is missing either line, they stop and say which one.
  They never edit it themselves, because it affects every WSL project on
  the machine and needs a `wsl --shutdown`.
- If no keepalive is running, they start one in a hidden window and print
  its process id. If one is already running, they say so and start
  nothing. (Task Manager shows each keepalive as two `wsl.exe` processes,
  a parent and its child. That is one keepalive, not two.)

**How to confirm it is fixed:** read the same container's `StartedAt`
twice, a few minutes apart. Unchanged means fixed. A single clean read
proves nothing.

---

## The policy engine: how it is protected, its two modes, and what to do when it is down

**When to use it:** you are changing the rules in `platform/policy`, preparing a deployment, calling the policy engine (`opa`) by hand, or it is not answering.

**What it does when it is down.** Every decision is asked of the engine, and the platform refuses when it cannot get an answer. Every route that asks it answers 503 with
one plain sentence ("The platform's policy engine did not answer, so this was refused. Try again in a moment."), the console shows nothing for the pages that check who
may open them rather than a wrong answer, and what went wrong is in the API's log under the request's reference. Nobody is let in by default.

### Who may call the engine

The engine answers only a caller that presents the platform's token (`MUNITAS_OPA_TOKEN`, sent as `Authorization: Bearer ...`), and then only for decisions: a `GET`
or `POST` under `/v1/data`. It will not list policies, accept a new one, delete one or answer an ad-hoc query, even with the token, and it answers nothing at all if
the token is not set in its environment (`platform/policy/system_authz.rego`). Without this, any container on the network could send one request that makes every
page open to everybody. A production start (`MUNITAS_ENV=production`) refuses the development token. To ask a question by hand:

```bash
curl -s -H "Authorization: Bearer $MUNITAS_OPA_TOKEN" -X POST localhost:8181/v1/data/munitas/screens/screen_decision \
  -d '{"input": {"screen": "services", "roles": ["analyst"]}}'
```

The health check (`GET /health`) needs no token. The engine runs as an ordinary user, not as root.

### The two modes

`MUNITAS_OPA_POLICY_SOURCE` in `.env` chooses how the engine gets its policy. The start scripts and `Get-ComposePrefix` read it, so every Compose command follows it.
Anything other than `folder` or `bundle` is refused by name, and a production start refuses `folder`.

- **`folder` (the default).** The engine reads `platform/policy` and reloads when a file changes, so a change takes effect within a second or two and nothing needs
  restarting. A change that does not compile is logged and the last good policy keeps answering. The tests (`*_test.rego`) are not loaded, so a broken test cannot
  stop the engine. To see why a change was not taken up: `docker logs munitas-opa-1`, and look for `rego_parse_error` or `rego_compile_error`. Nothing is signed in
  this mode, so use it on your own machine only.
- **`bundle`.** The engine fetches one signed package from the policy store and switches to each new one in a single step. Nothing reaches it except through
  `.\publish-policy.ps1`, which runs every test, builds the package, signs it, and puts it in the store. A policy that fails its tests or does not compile is never
  published.

### The policy store

The store (`policy-store`, started by the bundle-mode Compose file) is a small object store that holds policy packages and nothing else. It is on a network of its
own, `policy`, shared with the engine and with no other service, because the store's file service answers without a sign-in: anything on the same network could
overwrite the package through it. It publishes no port. The tenant data store (SeaweedFS on the `data` network) does not know it exists, and the platform's own
services cannot resolve its name. So the policy does not depend on the data it governs, and the data store's permissions are not involved in how policy is delivered.

| Who | What they can do |
|---|---|
| the policy engine, with its read-only key (`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, from `opa.env`) | read the one package; not overwrite, delete or list it, not read another bucket, not see any bucket name |
| anybody on the store's network without that key | nothing |
| anything on the platform's other networks | cannot reach the store at all |
| whoever can run a command in the store's container (the person who runs this machine) | put a package in. There is no write key anywhere |

`verify/policy_store_check.sh` (U190) tries each of these.

### Why the store is not the thing to trust

The package is signed. The private key stays in the WSL distro at `~/.munitas/policy-signing/private.pem`, never in the repository, in the store, or in a container the
engine runs in. The public half is `platform/policy-bundle/signing-public.pem`, which the engine trusts. A package that is unsigned, signed by another key, or
changed after signing is refused, the reason is logged (`Bundle load failed: ...`), and the last good package keeps answering. So reaching the store cannot change
policy. What a signature does not stop is putting back an older package that was signed correctly (a rollback); keep the store's access as small as it is now.

### First time on a machine

```powershell
.\publish-policy.ps1 -NewSigningKey     # makes the signing key pair once, and the store's read-only key
```

This writes the public key into `platform/policy-bundle/signing-public.pem`, which you commit, and the store's read-only key into the data folder
(`<MUNITAS_DATA>/policy-store/s3.json` for the store and `opa.env` for the engine, neither in the repository). It starts the store, publishes the first package, and
says what it did. Then start the engine in bundle mode (below). Replacing the signing key, or deleting the store's key files to make new ones, means starting the
store and the engine again so that they use them.

### Switching

Set the value in `.env`, then bring the engine up again with the prefix that follows it:

```powershell
. .\wsl-docker.ps1; $c = Get-ComposePrefix -WslDistro Munitas -ProjectRoot (Get-Location).Path
.\publish-policy.ps1                                  # bundle mode only: publish a package first
wsl.exe -d Munitas -- bash -lc "$c up -d --no-deps policy-store opa"
```

Going back to `folder` takes the store out of the prefix, so remove its container once: `docker rm -f munitas-policy-store-1`. To confirm the mode that is running:
`docker inspect -f '{{index .Config.Labels "munitas.policy-source"}}' munitas-opa-1`.

### What is kept, and what is not protected

- The engine keeps the last package it accepted (`persist`, in `MUNITAS_DATA/opa-bundles`), so it starts with a policy even when the store is not up.
  `verify/policy_persistence_check.sh` proves it by stopping the store and restarting the engine.
- Before the very first package has been published, a bundle-mode engine has no policy and refuses every decision. Publish first.
- Restarting the engine while a *policy* file (not a test) does not compile still stops it in `folder` mode, because there is no earlier good policy to keep until it
  has started once. Run the policy tests (below) before restarting it by hand.
- A rollback to an older correctly signed package is not detected. The engine does not report a failed load anywhere but its own log.
- The signature proves who made the package, not that the policy in it is right. Review a rule change as you would any code.

### In a real deployment

The store becomes the organisation's own object storage or artifact registry, holding policy packages and nothing else, in the control plane's own account or
compartment, and the engine reaches it over a private path with a read-only identity. On OCI that path is a Service Gateway from the control plane's private subnet to
Object Storage, with an IAM policy that allows read on one bucket and no more; the data plane reaches its own buckets through the same kind of gateway under a different
policy. The same shape works with an S3 gateway endpoint and a bucket policy on AWS. The private key moves to a key service and the package is written by the build
pipeline's identity, not from a machine. Turn on object versioning or retention on the bucket to cover rollback. Only the address and the credentials in
`platform/policy-bundle/opa.yaml` change; the signing, the token and `persist` stay as they are. (Whether OPA's S3 request signing works unchanged against an object
store's S3-compatible API is something to check on the one you use.)

### Trying it against a running stack

With the stack in bundle mode and a package published, these check the protection against a live engine and store. None is part of a quick run:

```bash
wsl -d Munitas -- bash /mnt/c/AIProjects/ClaudeProjects/Munitas/verify/policy_signing_attacks.sh   # unsigned, wrongly signed and altered packages are refused
wsl -d Munitas -- bash /mnt/c/AIProjects/ClaudeProjects/Munitas/verify/policy_persistence_check.sh # the engine starts from its saved package with the store down
wsl -d Munitas -- bash /mnt/c/AIProjects/ClaudeProjects/Munitas/verify/policy_store_check.sh       # who can reach the store (U190)
```

`verify/v178_opa_policy_modes.py` (U189, no database, seconds) checks that the files holding all this together name the same things.

### If the engine keeps restarting

`docker logs munitas-opa-1` names the file and line. Fix it, or put back the last good copy of that file, and the engine starts by itself within a few seconds because
Docker keeps restarting it. A message about the authorization policy means `system_authz.rego` is missing from the policy (or from the package); in bundle mode, publish
again. A message about `opa.env` not being found means `.\publish-policy.ps1` has not been run on this machine yet.

**Policy tests.** `docker run --rm -v "$PWD/platform/policy:/policy:ro" openpolicyagent/opa:0.68.0 test /policy`, run in the WSL distro from the repository folder. It works in either mode: in bundle mode the engine has no policy folder to run them in, so they run in a container of their own. `publish-policy.ps1` runs the same tests first.

---

## An agent run that is waiting rather than working

**When to use it:** somebody reports a run that has not finished. There are
two waiting states and they need different people, so read the status first.

- **`awaiting_access`**: the agent may not read the dataset the run targets.
  A lease request was filed when the run started, naming the agent as the
  reader and the person who started it as the asker, and the run holds that
  request's id. It moves the moment the owning custodian answers: approving
  starts the run, refusing fails it with the refusal as its reason. Nobody
  needs to press start again.
- **`awaiting_approval`**: the run has done its work and stopped at the
  sign-off gate. Somebody other than the person who requested it has to
  accept the findings, on the agent's page in the console.
- **`awaiting_activation`**: its access is approved, but storage permissions
  could not be updated yet. Nobody acts on the run: the platform retries and
  resumes it by itself. If it has been waiting long, the cause is an outage;
  see "New storage access is not taking effect" below.

Which request a waiting run is behind, and whose queue it is sitting in:

```bash
wsl -d Munitas -- docker exec -i munitas-postgres-1 psql -U munitas -d platform -c "select r.id, r.status, r.purpose, lr.state, lr.principal, lr.requested_by from agent_run r left join lease_request lr on lr.id = r.lease_request_id where r.status in ('awaiting_access','awaiting_approval','awaiting_activation') order by r.started_at desc;"
```

This is the one command in this file that goes through Docker, and on the
current development machine Docker runs inside WSL2, so it has to be run from
there: `wsl-docker.ps1` builds the working Compose prefix. Every other script
below runs on Windows unchanged, because it reaches Postgres and SeaweedFS on
`localhost` and WSL2 forwards the published ports.

The same limit applies to any `verify/` script that talks to Docker itself,
not only to `docker`/`docker compose` on the command line. `v55_sandboxed_
agent_run.py`'s container-reaping section and `v76_activation_live.py`
(both use `docker.from_env()` or shell `docker` directly) must run from
inside the WSL distro with `$HOME/.munitas/docker-checks-venv/bin/python`, not the
plain Windows `.venv` -- see each script's own docstring for the exact
invocation. The reason is the same as above: Docker itself runs inside
WSL2 on this machine, not Docker Desktop, so anything that talks to
Docker's own API, not only the `docker` CLI, only reaches the real engine
from inside the distro.

That venv is separate from `sandbox-venv` (the one the real sandbox worker runs
in) on purpose, so nothing only a test uses is added to the environment that
runs agent code. Create it once, inside the distro:

```bash
uv venv --python 3.12 ~/.munitas/docker-checks-venv
uv pip install --python ~/.munitas/docker-checks-venv/bin/python -r worker/requirements-docker-checks.txt
```

A run stuck at `awaiting_access` with no `lease_request_id` is a bug, not a
pending decision: nothing will ever arrive to release it.

---

## New storage access is not taking effect

**When to use it:** the storage housekeeping screen shows a red banner, the
API logs "new storage access is not taking effect", or runs sit at
`awaiting_activation`.

Access that policy allowed only works once the storage permissions document
is rebuilt with it. The platform rebuilds it on every request and retries by
itself when that fails, so the job here is to fix the cause, never to
activate or resume anything by hand. What is failing and why:

```bash
curl -s http://localhost:8000/health
```

`storage_permissions.reason` names the cause and `retryable` says whether the
platform is still retrying.

- **`retryable: true`** (storage, the policy service or the database was
  unreachable, or the document was busy): bring the service back. The
  platform retries within 5 minutes at most, and every waiting run carries on
  by itself.
- **`retryable: false`** (the platform could not work out what the list should be: the policy's roles
  could not be read, or a role has no configured key): retrying cannot help. Fix the cause, then print once:

```bash
wsl -d Munitas -- docker exec munitas-munitas-api-1 python /app/reconcile-grants.py
```

  A list that can be computed is always written. There is no safety limit to override: what is odd about
  an update (many removals nothing explains, a list with no bucket-wide permission, a permission that traces
  to no version) is raised as an alert instead, and the update goes ahead.

The banner clears on the next successful print and waiting runs resume
within 10 seconds. The history of every print is in
`storage_permission_print`.

---

## Tests fail at random, and screens sometimes wait five seconds

**Look at the disk first.** On 2026-10-08 the console tests that failed at random (a different few each run, each passing alone) were traced to the disk under the WSL virtual machine, and not to the platform. The old distro's virtual disk (`P:\docker\wsl\Ubuntu-20.04\ext4.vhdx`, with Postgres inside it) and the stored files (`O:\munitas-data`) were on a WDC WD20EARS, a 5400 rpm hard drive. A plain disk flush on it stalls for 2 to 6 seconds every so often, while the same test on the NVMe drive (`C:`) never stalls. The current distro, `Munitas`, keeps its virtual disk in `C:\Work\wsl\Munitas` and its data in `C:\Work\munitas\data`, both on the NVMe drive, so this section applies to a setup that has either of them on a hard drive. Postgres waits for that flush at every commit, so any request that writes can wait five seconds, several at once, and a test that gives a screen five seconds fails.

How it was shown, and how to see it again:

1. **The platform names its slow requests.** A request of `MUNITAS_API_SLOW_REQUEST_SECONDS` (default 2) or longer is logged as a warning, "a slow request", with its method, route pattern and seconds: `wsl -d Munitas -- docker logs munitas-munitas-api-1 2>&1 | grep "a slow request"`.
2. **Ask Postgres what it waited for.** Run `alter system set log_min_duration_statement = 500; alter system set log_lock_waits = on; select pg_reload_conf();`, run the tests, then read `docker logs munitas-postgres-1`. Slow `COMMIT` lines, many ending in the same second, mean the disk. Put the settings back afterwards (`alter system reset log_min_duration_statement; alter system reset log_lock_waits;`).
3. **Flush a file in a loop, with no platform involved.** Write 8 KB and `os.fsync` it every 50 ms for a minute, on each disk, and print the worst time. The hard drive showed stalls of up to 6 seconds, the NVMe drive none.

What fixes it is putting the virtual disk and the data folder on the NVMe drive: stop the stack, then `wsl --manage <distro> --move <folder on the NVMe drive>` (the old distro was about 91 GB), and change `MUNITAS_DATA` in `.env` (about 5 GB, copy it first). Until then, expect an occasional test to miss its five seconds: the failure names a request that was waiting, and the slow-request log shows the same moment.

## Checking that every setting is documented

U160 (`verify/v154_settings_documented.py`) reads the repository (the compose file, the start scripts, the settings catalogue and the two generated files), which the API container does not hold, so it is not in the container run. Run it on the host, no database needed, seconds:

```bash
MUNITAS_PG_DSN=postgresql://munitas:munitas@localhost:5432/platform MUNITAS_VERIFY_KRATOS=http://localhost:4433 MUNITAS_S3_ENDPOINT=http://localhost:8333 MUNITAS_DATA='O:\munitas-data' .venv/Scripts/python.exe verify/v154_settings_documented.py
```

## What a version holds

A version's page has a section, "What it holds", and a sealed date and time. It answers from `GET /dataset-versions/{id}/holds`: the kinds (audio, image, text, tabular, other), the formats, the structure (structured, semi-structured, unstructured), the files and their size, or the rows when the version is stored as a table. A version with more than one kind is called multimodal, and more than one structure, mixed. The platform worked these out from the first bytes of each file when the version was sealed. When a file could not be read (it is locked to the person it is about, or the version was sealed before files were read) the answer is worked out from the file names, and the page says so. No file name is returned, because a file name can identify a person. The same figures are in the datasets list, and U161 (`verify/v155_version_holds.py`, in the API container) checks the two agree.

## Storage permission alerts

An update to the storage permissions is never refused for being large. What is odd about one is written down
as an alert, in three places:

- **The alerts file**, `alerts.jsonl` in the `logs` folder of the data folder (`<MUNITAS_DATA>/logs/alerts.jsonl`;
  inside the API container it is `/logs/alerts.jsonl`, from the setting `MUNITAS_ALERTS_FILE`, which is empty to
  switch the file off). One line of JSON per alert, in one file for every part of the platform that raises
  alerts, with a `category` (today `storage-permissions`), an `event`, a `severity`, a `message` that is a
  full sentence, and `details` that name identifiers and numbers and never contents. It rotates at 10 MB and
  keeps five old files. Read it with, for example, `grep '"category": "storage-permissions"' alerts.jsonl`.
- **The update's own row** in `storage_permission_print` (column `alerts`), which is the durable record: a file
  that rotates is not an audit trail.
- **The Storage housekeeping page**, an amber notice naming when the update ran and what it noticed, for a day.
  `GET /health` carries the same under `storage_permissions.latest_alerts`.

The events, and what to do:

| Event | Severity | Raised when | What to look at |
|---|---|---|---|
| `no-bucket-wide-permissions` | critical | the new list has no bucket-wide permission at all | Nothing can read or write until a correct list is written. The register or the policy is wrong; fix it and print again. |
| `unexplained-removal` | warning | five or more folder grants were removed that no lease or allowed request ever justified | Someone edited the document by hand, or an older mechanism wrote it. The grants are gone; find out how they got there. |
| `unjustified-permission` | critical | a permission names a folder that belongs to no dataset version in the register | A bug in how the list is built. The permission was written; find it in `details.examples` and remove its cause. |
| `permission-in-another-organisations-bucket` | critical | a version's folder is opened in a bucket belonging to a different organisation | The same, with the added risk of crossing organisations. Treat it as urgent. |

To add a new kind of alert elsewhere, call `alerts.raise_alert(category, event, message, severity=..., **details)`;
it writes to the same file with its own `category`, and a later metrics exporter can count lines by category and
severity.

**Rebuilding the API now needs the data folder translated.** The API mounts the logs folder, so
`docker compose up` must see `MUNITAS_DATA` as a path inside the distro. Use the same prefix `start-dev.ps1` uses:

```powershell
. .\wsl-docker.ps1; $compose = Get-ComposePrefix -WslDistro Munitas -ProjectRoot (Get-Location).Path
wsl -d Munitas -- bash -lc "$compose build munitas-api; $compose up -d --no-deps munitas-api"
```

---

## Reading governed tables from DuckDB or PyIceberg

Every sealed version whose rows have a schema contract is also written as an
Iceberg table, and a catalog at `/iceberg` on the API lists and opens only what
the person asking may read. A person needs a token for their own tools, nothing
else.

1. Ask for a token in a session of your own (the console session, or any
   Kratos session). The purpose is the sentence your leases are approved for:

   ```bash
   curl -s -X POST http://localhost:8000/iceberg/tokens      -H "Authorization: Bearer <session>" -H "Content-Type: application/json"      -d '{"purpose":"readmission study","hours":8}'
   ```

   The token is shown once. `GET /iceberg/tokens` lists yours without showing
   them, and `POST /iceberg/tokens/<id>/revoke` ends one.

2. Connect. The warehouse is the organisation's name, for example `canary`:

   ```sql
   INSTALL iceberg; LOAD iceberg;
   ATTACH 'canary' AS lake (TYPE ICEBERG, ENDPOINT 'http://localhost:8000/iceberg', TOKEN '<token>');
   SELECT count(*) FROM lake."<dataset name>".v1;
   ```

   ```python
   from pyiceberg.catalog.rest import RestCatalog
   cat = RestCatalog("munitas", uri="http://localhost:8000/iceberg", token="<token>", warehouse="canary")
   cat.load_table(("<dataset name>", "v1")).scan().to_arrow()
   ```

Each version is a table named `v<N>`. A table the person may not read is not
listed, and opening it is refused with the reason. Opening a raw table needs an
approved lease; the storage key it hands out expires, as described below.

Two settings matter when a client runs on another machine. The catalog tells
the client where storage is from `MUNITAS_PUBLIC_S3_ENDPOINT` (default
`http://localhost:8333`), so set it to an address the client can reach. Newer
S3 clients send uploads that SeaweedFS stores wrongly unless
`AWS_REQUEST_CHECKSUM_CALCULATION=when_required` and
`AWS_RESPONSE_CHECKSUM_VALIDATION=when_required` are set; the API container
has both already.

Checks. U90 and U91 run with the rest of the suite. U92 uses the real tools,
so it runs on the host:

```bash
.venv\Scripts\python.exe -m pip install -r worker\requirements-host-checks.txt
.venv\Scripts\python.exe verify\v92_iceberg_real_clients.py
```

A database created before the Iceberg tables existed gets them with
`.venv\Scripts\python.exe scripts\admin\apply-schema.py`, which is safe to
run again. Projection can be turned off with `MUNITAS_ICEBERG_PROJECTION=off`; a
version that cannot be projected is still sealed, and the reason is logged.

### How long a storage key lasts, and long reads

The key the catalog hands out for a table expires. It lives between half and
all of `MUNITAS_CATALOG_KEY_SECONDS` (default 3600, so 30 to 60 minutes) and the
table's configuration says exactly when (`s3.session-token-expires-at-ms`).
Revoking a lease ends its keys at once, whatever time they had left. A key that
leaks is useful for at most that long, not for as long as a lease lasts.

A read that outlasts its key has to ask for the next one, and every ask decides
access again. If access has ended, the ask is refused with the reason and the
read stops.

- **DuckDB** asks again by itself, by loading the table again. Nothing to do.
- **PyIceberg** does not. It opens each data file with the key it held when the
  table was loaded, and on a table too large to read ahead of itself that fails
  on a later file with an access error. Read with the helper, which loads the
  table again whenever the key is about to run out:

  ```python
  import sys; sys.path.insert(0, "scripts/client")
  from iceberg_reader import read_batches
  for batch in read_batches(catalog, ("my_dataset", "v1")):
      ...   # a pyarrow RecordBatch
  ```

- Anything else that reads the files itself: load the table again, or call
  `GET /iceberg/v1/<organisation>/namespaces/<dataset>/tables/v<N>/credentials`,
  before `s3.session-token-expires-at-ms`.

The catalog offers both ways of asking, and each client may use whichever it
supports. Both run the same decision and mint the same kind of key.

- Loading the table again is what DuckDB 1.5.6 did in U93, every time, even
  though the catalog also advertises the credentials endpoint. A newer DuckDB
  Iceberg extension is documented to prefer the credentials endpoint when it is
  advertised and to load the table again when it is not, so this may change
  with the extension version. Both work. U93 asserts the behaviour (the tool
  asked again, and a revoke stopped it), not which of the two it used.
- The credentials endpoint is the one the Iceberg REST specification defines
  (version 1.9 onward), and it is advertised in `/iceberg/v1/config` and in the
  table's `client.refresh-credentials-endpoint` property, as a path relative to
  the catalog's address, as Java-based clients expect. Of the clients tried,
  none relied on it: PyIceberg 0.12 has a call for it but its file reader does
  not use it, and only U93's own explicit calls exercised it. It is kept so that
  clients which do use it (Spark, Flink and other Java-based ones are the usual
  ones) work as the specification describes. That is untested here.
- Some clients do not renew vended keys at all. Public issue trackers show this
  for Trino and Unity Catalog, and refresh failures in the Java client. A read
  on such a client stops at the first expiry. For those, raise
  `MUNITAS_CATALOG_KEY_SECONDS` above the longest read, accepting that a leaked
  key then lasts that long, or read in pieces and open the table again for each.

The lifetime has a floor of 40 seconds. Raising it costs nothing; lowering it
makes the API rewrite the storage permissions more often (once per half
lifetime, while any key is in use).

Check U93 proves all of this with real tools, and takes about six minutes
because it needs the API started with short keys and small files:

```bash
$env:MUNITAS_CATALOG_KEY_SECONDS = "60"; $env:MUNITAS_ICEBERG_ROW_GROUP_ROWS = "100"; $env:MUNITAS_ICEBERG_FILE_BYTES = "6000"
# restart the API with those set, then:
.venv\Scripts\python.exe -m pip install -r worker\requirements-host-checks.txt
.venv\Scripts\python.exe verify\v93_catalog_key_expiry.py
# then restart the API without them
```

With any other settings, U93 reports every check as skipped and names the
setting to change.

---

## Running a query to make a new dataset

A person can make a new dataset from a query over datasets they may already
read. The platform runs the query for them, in a container with no network and
no credentials, checks the result against the shape they confirmed, and seals
it as an ordinary dataset version. They never run the query that produces the
dataset on their own machine.

**Once, per machine:** build the image the query runs in, in the distro that
holds Docker. It needs the internet for two package installs and nothing after.

```bash
wsl -d Munitas -- bash -lc "cd /mnt/c/AIProjects/ClaudeProjects/Munitas && docker build -t munitas-derive-runner:1 worker/derive"
```

Both workers must be running, because the query container is started by the
sandbox worker and the result is sealed by the host worker (`start-dev.ps1`
starts both). If the image is missing the run says so and is retried.

**The flow**, with a session token for the person (the same one used to mint a
catalog token):

1. `POST /derivations` with the datasets the query reads, the query, a name for
   the result, the primary key and a purpose. Nothing runs. The answer is a
   draft: the version each input resolved to, the columns the query would
   produce with their types, and the sensitivity each must carry.

   ```json
   {"inputs": [{"dataset": "admissions", "alias": "a"}],
    "sql": "SELECT age, diagnosis_code FROM a WHERE age > 65",
    "target_name": "admissions-over-65", "primary_key": ["age"],
    "purpose": "readmission study"}
   ```

   The query refers to each input by its alias (by default the dataset's name
   with anything but letters and digits turned into an underscore). `version`
   may be given per input and defaults to the newest sealed one.
2. `POST /derivations/<id>/confirm`, optionally with `{"sensitivities": {...}}`
   to raise a field's sensitivity. This registers the dataset and its schema and
   starts the run. A draft expires after an hour.
3. `GET /derivations/<id>` until `status` is `succeeded` or `failed`. A failure
   says why, by kind of error and never by quoting a value.

**What is enforced, and where**
- Every input must be readable by the person at the moment of the draft and again
  at the confirm, by the same decision a table open makes.
- Only a single `SELECT` over the declared inputs runs. The platform checks it,
  and so does the runner inside the container, which also locks DuckDB to the
  copied input files so a query cannot read a file, a web address or anything else.
- A field may not carry less sensitivity than the fields it was computed from.
  Where a query cannot be traced column by column (a subquery, a `WITH`, a
  `UNION`) every field takes the highest sensitivity of any input. Lowering is
  refused. It is not offered yet, because lowering is a claim that needs somebody
  other than the person who wrote the query.
- The result is registered at the strictest class of any input, and sealed there.
  The person is given a lease on it at once, which ends when the earliest lease
  they hold on an input ends and is revoked with it. Anybody else needs access to
  it in the usual way.
- The same query over the same input versions with the same shape returns the
  result it already made and does not run again.

**Limits (phase one):** inputs are copied into the container, so together they
may not exceed 1 GB; a result may not exceed 5 million rows or 512 MB; a query is
stopped after 15 minutes; the container has 512 MB of memory. Only SeaweedFS
storage is served. The result must have a primary key, with no empty and no
repeated values. A query that produces no rows seals nothing.

**If a run seems stuck.** The sandbox worker sends a heartbeat every 20 seconds.
If it dies, or finishes but cannot report (a network error to Temporal), Temporal
notices after 90 seconds and runs the activity again. That is safe: the run is
recorded under a fixed key, the upload overwrites the same object, and sealing
reuses a version that already exists. Check `docker ps` in the distro for a query
container and the sandbox worker's own log (`MUNITAS_LOG_DIR`,
`sandbox-worker.log`). The worker removes containers a previous process left
behind when it starts.

Checks. U94 proves the draft and confirm rules and runs with the suite. U95 runs
real queries through the real worker and container, so it needs the workers and
the image, and takes about two minutes. U96 proves the worker starts when a
leftover container has already exited:

```bash
wsl -d Munitas -- bash -lc "cd /mnt/c/AIProjects/ClaudeProjects/Munitas && docker compose exec -T munitas-api python /verify/v95_derivation_run.py"
.venv\Scripts\python.exe verify\v96_reap_leftover_containers.py
```

---

## Writing a large table: the table worker

A version whose rows are in a records file is also written as a table, so that
a standard tool can read it. A small file is written while the request waits.
A file over 32 MB (`MUNITAS_TABLE_JOB_INLINE_BYTES`), or a seal that sends
`"table_mode": "background"`, becomes a **job**: the request is answered at once
with `202` and the job, and a **table worker** writes the table. The version is
sealed when the worker has finished, with its table inside it, and does not
exist before. Send the records as `.parquet` or `.ndjson` (one JSON row per
line) for anything large; a JSON list is read whole and is limited to 32 MB.

**Start it.** The shared worker is part of the stack, and `docker compose up -d`
starts it (`table-worker`, one job at a time, 1.5 GB). More work is more
containers, not more jobs in one: `docker compose up -d --no-deps --scale table-worker=2 table-worker`
starts a second, and the two take jobs from the same line at the same time. An
organisation's tables are handed out only as fast as its own limit allows
(`table_uploads.jobs_at_once`, two by default), so two workers serve one
organisation fully and a third helps only when several organisations are busy.

**A person's large file.** A person who cannot send a file in one request opens
an upload (`POST /datasets/<id>/table-uploads`), sends it in parts of 8 MB
(`PUT /table-uploads/<id>/parts/<n>`, each of which can be sent again), and
finishes it (`POST /table-uploads/<id>/finish`). The upload is a table job that
is waiting for its file: it holds the next version number and folder, no worker
is given it and no storage key is made for it until it is finished, and one that
nobody finishes is thrown away after a day (`MUNITAS_TABLE_UPLOAD_LIFE_HOURS`).
A job waiting for a worker, or being written, survives a restart of the API or
of the worker and is handed out again. A person may cancel an upload, or a table
being written, at any time (`POST /table-uploads/<id>/cancel`). The limits that
keep one person from slowing everybody else down are the `MUNITAS_TABLE_UPLOAD_*`
settings and, for each organisation, five access rules under "Bringing in tables".

**Follow a job.** The answer to the seal has `status_url`. `GET /table-jobs/<id>`
says `pending`, `running`, `sealed` (with the version), `refused` (a table was
required and could not be written, with the reason; the version number is free)
or `expired` (nobody finished it in four hours). The housekeeping screen has a
"Tables being written" section, and raises an alert when a job has waited more
than ten minutes for a worker.

**An organisation's own worker.** A platform administrator can give an
organisation a worker that serves nobody else:

```bash
python scripts/admin/table-worker.py start harbour
```

then `PUT /tenants/harbour/table-worker` with `{"dedicated": true}`. Start the
worker first. A job made while the organisation is on its own line waits for that
worker however long it takes and never moves to the shared pool, so if the worker
is not running the job is reported as stalled. `stop` removes the worker; set
`dedicated` back to `false` to return the organisation to the shared pool (only
jobs made afterwards use it).

**If the worker dies.** Temporal notices a missing heartbeat after 90 seconds and
hands the work out again; the platform clears what the dead attempt wrote before it
does. A container killed by hand (`docker kill`) is not restarted by Docker's
restart policy, so start it again; a crash is restarted. `verify/v110_table_worker_resilience.py`
does this on purpose (host only).

**What the worker holds.** No database, no master key, no standing storage key.
For each job it is given one key, made for that job, which reads and writes the one
folder the version will have and lists object names in the organisation's own
bucket. It stops working when the job ends.

**Binary table files.** A plain table may also be sent as `.parquet`, `.avro`, `.orc`,
`.arrow`, `.feather` or `.xlsx`. These always go in parts and are read by the worker,
whatever their size, because the API never opens one. Each is read a row group, stripe,
block or record batch at a time; a column's type in the file must fit the contract; a
workbook is read from the first sheet, or the one named in the upload's `sheet` option, from
a copy on the worker's own disk, and is refused if it holds
a macro, is encrypted, or expands past 8 GB or 100 times its size. The worker image
carries `fastavro`, `openpyxl` (with `defusedxml`) and `tzdata` for these; rebuild it
(`docker compose build table-worker`) after pulling a change to its requirements.
`verify/v172_binary_formats.py` (U182, host only, about ten minutes) sends the generated
tables in every binary layout, their faulty copies and hostile files through the real route.

**A table about people, of any size.** A table whose contract says how to find the person each row is about is brought in through the same door
(`POST /datasets/<id>/table-uploads`), in any layout above. The worker reads the whole file and checks every row first, including that each row names a
person, so a file that is wrong at its last row makes no key. It then reads the file again and sends the cells to be locked to the platform
(`POST /table-jobs/<id>/lock`, `MUNITAS_TABLE_LOCK_BATCH_ROWS` rows at a time), which finds or makes each person's key, locks the cells and gives them
back: the worker never holds a key. The table is stored as a table: readable columns keep their types, a locked column is locked text, the column that
identified the person holds the person code, and a last column `person_code` holds it too. The key log gets a line for each batch with the count, not
a line for each person. The group-size report is worked out by the worker while it writes, with a query that spills to disk (`duckdb` is in the worker
image), and recorded by the platform. The primary key must be one column, left readable. **The file that was sent is deleted when the table is sealed**
(and when the job is refused, cancelled or runs out of time): it holds the identifiers as they were written, and for as long as the job lasts it sits in
the organisation's bucket, readable by anybody who may read that bucket. The locking rate on this machine was measured at about 7,600 rows a second
(100,000 rows, three locked columns), so a million people take a few minutes and ten million about twenty-two.

An approver reads the locked rows of such a table a page at a time (`GET /dataset-versions/<id>/person-rows?limit=&cursor=`): 200 rows unless asked, at
most 1,000, and the answer's `next` is the cursor for the page after. A person with a lease gets the same few rows the small route gives, found in one pass
over the key column. A version sealed by the small route (`table.jsonl`) is read whole, as before. Erasing a person is still one key destroyed, and the
table is not rewritten. `verify/v174_people_tables.py` (U184, host only, about four minutes) proves all of this at 10,000 people; the checks at a million
and ten million people are L6 and L7 in the large tier.

## The large tier: checks that take hours

Some checks need millions of rows or gigabytes: five million rows with the worker's memory
measured, a gigabyte sent in parts and carried on after a break, a worker killed in the middle
of a table, the console sending a gigabyte. They are kept apart from the ordinary suite and run
only when somebody chooses them:

```bash
.venv\Scripts\python.exe verify\run_large.py                 # list them, with what each costs; changes nothing
.venv\Scripts\python.exe verify\run_large.py --plan L1 L3    # the same for the ones named, and whether the disk is enough
.venv\Scripts\python.exe verify\run_large.py --go L3         # run them, one after another, and record the run
```

`--go` needs the checks named; there is no "all". The made files go under
`C:\Work\testdata-large`, each check removes what it made, and one that would not fit on
the disk stops before it writes anything. The runs are recorded in
`verify/history/large-runs.jsonl` and shown on `verify/history/large.html`, apart from the
ordinary history. The time given for each check is an estimate until it has been run once.
`verify/v173_large_tier_guard.py` (U183, host only, no database) fails if a large check is
ever listed in `run_all.py` or `affects.py`, or if a check the ordinary suite runs asks for
more than 100,000 rows. Setting `MUNITAS_LARGE_DIVISOR=50` makes a large check's sizes fifty
times smaller, to see that it works before giving it the hours; nothing proved that way counts.
The console's large test (`web/tests/large`) is a Playwright project that exists only when
`MUNITAS_LARGE=1`, which `run_large.py` sets for the one check that needs it.

## The database driver

The project talks to PostgreSQL through `pg8000` (BSD 3, pure Python), behind one layer, `platform/pgdb.py`, which offers what the code used of psycopg: `connect`, a
connection that is a context manager, `transaction()` blocks that nest as savepoints, a pool, rows as tuples or dictionaries, and the exception classes by SQLSTATE
(`pgdb.errors.CheckViolation` and the rest). psycopg is LGPL 3 and is not installed anywhere: `verify/v176_licences.py` fails if it, or anything else under the GPL
family, turns up in this Python, the API or table worker images or the WSL environments.

* **Where it lives.** `platform/pgdb.py` is the file. `worker/pgdb.py` is an identical copy, because the worker's images are built from another folder
  (`scripts/check_pins_agree.py` fails when the two differ: edit one and copy it over the other). The API image copies it to `/app/pgdb.py`. A script at the
  repository root, or one that puts the root on its path, finds it through `pgdb.py` at the root, and the checks and `scripts/admin` have a small `pgdb.py` of their
  own that loads the real one, so `import pgdb` works from the host and from inside the container.
* **Set up an environment.** `pip install pg8000==1.31.5` (it is in every requirements file). For the two WSL environments, `uv pip install --python
  ~/.munitas/sandbox-venv/bin/python -r worker/requirements-sandbox.txt` and the same with `docker-checks-venv` and `requirements-docker-checks.txt`.
* **The agent's checkpointer** is `worker/pg_saver.py`: LangGraph's Postgres checkpointer with its driver calls moved onto `pgdb` (the tables and stored forms are
  the library's, so a thread saved by one is read by the other). The package `langgraph-checkpoint-postgres` is not installed, because it needs psycopg.
* **MLflow** reaches its own database through `postgresql+pg8000://` (the container installs `pg8000` when it starts).
* **After changing the layer, restart what has it loaded:** the API (rebuild the image), the host worker and the sandbox worker. `verify/v175_pgdb.py` (U186, host
  only, about 30 seconds) proves the layer against the live database, and the whole suite is the proof that nothing that used it noticed.

## Licences

`docs/internal/LICENCES.md` lists the licence of every component and what is still not permissive. `verify/v176_licences.py` (U187, host only, about 20 seconds) fails on a GPL-family
package, on a library or vendored module under the LGPL inside any package, and on an MPL-2.0 package nobody has looked at. Run it after changing a requirements file, a Dockerfile
or an image tag. After installing the host worker's requirements run `python -m pip uninstall -y setuptools`: it vendors an LGPL module, nothing at run time needs it, and torch asks for it,
so pip puts it back. Audio is read and written by `worker/wavio.py` (WAV only); there is no `soundfile`.

## What the stack publishes on this machine, and how to look inside storage

Every port the Compose file publishes is bound to this machine only, on both loopback addresses (`127.0.0.1` and `[::1]`), never on a network
interface. Both are needed: `localhost` resolves to the two, and a port published on only one makes a connection from Windows to `localhost` wait
about a minute for the other before it tries the first. If a connection to `localhost` is mysteriously slow after a Compose change, check that each
published port has both lines.

SeaweedFS publishes one port, the S3 service on 8333, which needs a key. Its filer (8888) and master (9333) are **not published**. The filer answers
without any sign-in and holds the identity document with every organisation's storage keys, so only the platform's own containers reach it, on the
data network. Nothing that runs a customer's code is ever attached to that network.

To look at storage by hand, run a command in the container, through WSL where Docker lives:

```powershell
wsl -d Munitas -- docker exec munitas-seaweedfs-1 wget -q -O - http://seaweedfs:9333/dir/status
```

The master and the filer listen on the container's own name (`seaweedfs`), not on `localhost`. `scripts/admin/reclaim-storage.py` runs its
vacuum this way.

Still open, and recorded here so it is not forgotten: the filer has no sign-in even on the data network (JWT signing on the filer, and certificates
on its gRPC channel). It is acceptable while only the platform's own containers can reach it. It must be done before the platform runs anywhere
other than one machine, or if anything that runs customer code ever needs the data network.

## Secrets before a real deployment

Two values have a development default in this repository and in `docker-compose.yml`:
the storage administrator's secret (`MUNITAS_S3_ADMIN_SECRET`) and the worker token
(`MUNITAS_WORKER_TOKEN`). Both matter more than they look. Every storage key the
platform issues (one per role per organisation, one per table job, the catalog's
rolling keys) is derived from the administrator's secret, so anybody who has read
the source can compute them all if it is left in place. The worker token is what lets
a caller seal a version and run the platform's own endpoints.

Set `MUNITAS_ENV=production` and the control plane refuses to start while either still
holds the published value, naming the variable and never printing the value. On a
laptop leave it unset and the defaults work. Changing the administrator's secret later
changes every derived key at the next print of the storage permissions (the control plane
prints at start-up), so restart the workers afterwards: a worker keeps the key it was given
for as long as it runs.

## Local HTTPS between the worker and the API

**When to use it:** the worker (`worker/main.py`) needs to reach the API
over an encrypted connection instead of plain HTTP, or `worker/main.py`
refuses to start with "MUNITAS_API is ... which sends every request this
worker makes ... over plain HTTP" (`config.require_secure_api()`).

Two calls the worker makes to the API carry a real secret: fetching a
person's connected HuggingFace token
(`worker/hf_ingest_activities.py`'s `_token_for`) and the shared worker
token sent as `x-worker-token`
(`worker/dag_activities.py`, `worker/sandbox_run.py`). On one machine,
`MUNITAS_API`'s default of `http://localhost:8000` is fine: that traffic
never reaches a real network. It stops being fine the moment the worker
and the API run on separate machines, because the same requests then
cross a real network in cleartext.

The console and every `verify/` script stay on plain
`http://localhost:8000` regardless of any of this: nothing they carry is a
secret worth protecting (see `external_accounts.py`'s
`huggingface_secret()` docstring on this platform's current, deliberate
lack of endpoint authentication generally), switching them to HTTPS would
show a self-signed-certificate warning in every browser tab, and it would
break the Playwright suite's direct `fetch()` calls unless every test file
also learned to ignore certificate errors. HTTPS here is scoped to the one
leg that actually carries something sensitive.

**Generate a local certificate** (one command, `openssl` already ships
with Git Bash on this machine, nothing else to install):

```bash
infra/tls/generate-local-cert.sh
```

Writes `infra/tls/out/localhost.pem` and `localhost-key.pem`, gitignored.
Not `mkcert`: that would install a new root CA into this machine's OS and
browser trust store, trusted by every app on the machine, to solve a
problem (browser trust) this setup does not have. This cert is instead
trusted by exactly one thing, explicitly: the worker, via
`worker/config.py`'s `api_verify()`, which points `httpx`'s own `verify=` at
this one file (`MUNITAS_API_CA_CERT`, defaulted to
`infra/tls/out/localhost.pem`) rather than the system trust store.

**Rebuild and restart the API** so it picks up the mounted certificate:

```bash
docker compose up -d --build munitas-api
```

`docker-entrypoint.sh` (`platform/api/`) starts a second `uvicorn` process
on 8443 with TLS only when it finds both files under `/tls`; port 8000
keeps running exactly as before. Confirm both are answering:

```bash
curl -s http://localhost:8000/health
curl -s --cacert infra/tls/out/localhost.pem https://localhost:8443/health
```

**Point the worker at it.** Set before starting `worker/main.py` (e.g. in
the PowerShell window `start-dev.ps1` opens, before the `python -m
worker.main` line):

```powershell
$env:MUNITAS_API = 'https://localhost:8443'
```

With no certificate generated, `worker/config.py`'s `api_verify()` falls
back to the system trust store, which is also exactly correct for
`http://` (where `verify` is not consulted at all) and for a real
deployment's CA-signed certificate.

**A real, multi-machine deployment should not use this certificate at
all.** It is self-signed and scoped to `localhost`; a genuine deployment
needs a reverse proxy (nginx, Caddy) in front of the API with a real,
CA-issued certificate (for example from Let's Encrypt), terminating TLS
for the console as well as the worker. That reverse-proxy setup is not
built yet.

## Who a de-identification result is waiting on

`GET /gate-decisions` and `GET /gate-decisions/{id}` say, for each pending result, who may decide it (`waiting_on`: people who hold a role the policy names
as a decider, now or through a live grant, and have not left, other than whoever started the run) and whether the caller is one of them (`can_decide`).
`?started_by=<person>` lists the runs one person started, which is how the console's Your runs list is built. Nothing is stored: it is worked out on each
request from `decider_roles` in the policy and `person_holds_live_role`, so a role that is revoked or runs out stops being listed at once. The sign-in
panel's list of departments a person approves for (`approver_of` in `GET /directory`) uses `active_department_approvers`, the same function every approver
check uses, so it lists only departments the person can act for now. The console reads the directory again every minute, when the window comes back,
and after any approver or role change. Check: U152.

## The step gateway and the platform's address

The step gateway looks the platform up by name as requests arrive (Docker's resolver, answers kept five seconds), so replacing or rebuilding the API
container needs no gateway restart. Before this it kept the address it found at start, and after a rebuild every step failed with "the process exited 1"
while the API itself looked healthy. If steps fail that way again, read `docker logs munitas-step-gateway-1` for "Host is unreachable".

## Releasing what a run made

A step that writes files makes a version "under review". The pipeline's decision step now makes the decision about that version, the one the run made, and
not about the files the run was given (which nobody reviewed). A run that made several versions has one decision for each; a run that made nothing has one
for its source, as before. A run on development data makes none.

**What a reviewer sees.** `GET /gate-decisions/{id}/review` lists the version's files with the checks the platform ran on each before anyone looked: the file
is what its name says (sound, JSON), it is not empty or more than twice the size of what it was made from, and it is not a byte-for-byte copy of the original
it was made from. A failed type or copy check stops a release; holding back is always possible. The reviewer may open up to `release_review_records` (25, in the
policy) different files of a result through the file door (basis `review`), while it waits for them, and no longer: the right is worked out again on every
request, so it ends when the result is decided. A file that is neither sound nor text opens as its first 256 bytes.

**Who may decide.** A holder of a deciding role (`decider_roles`), never the person who started the run and never the person who uploaded the version of the
pipeline that made it. A result nothing measured (or whose measurement failed) cannot be released by somebody who opened none of its files.

**What is kept.** The decision keeps `handoff.review`: the outcome, the number of files checked, which failed and why, and which files were opened. Released
files stay locked under their person's key: release changes who may be shown them, and a person who was erased still makes them unreadable.

**Not built.** A released version's files are not yet opened by the people it was released to (annotators): that is a lease or a role through the door, a
separate step. Checks: U155 (container), U153 (host: the decision step), U156 (console, `web/tests/release-review.spec.ts`, which makes its own results with
`verify/seed_release_review.py`).

## Opening a few real files with an approver's permission

Real files (and files made from them) are opened by a person only while an approver of the owning department, who is not that person, has allowed it.
A person with any role asks on the version's page (the form under Files): what the files are for, how many (console: up to 10) and for how long
(console: up to 1 hour). The request appears for the approvers on the page "Locked data requests", under Files. They allow it, allow fewer files or hours,
or decline with a note. When it is allowed, the platform draws the files with the same method as a development sample and fixes them. While it is in force
the file door lists and opens exactly those files and writes each opening to the log with the lease it was made under. It ends when its time runs out or
when either side ends it, and what was playing stops at its next request. A lease never makes a version development data.

- Check: U164 (`verify/v157_file_leases.py`, 55 checks, run with `docker compose exec -T munitas-api python /verify/v157_file_leases.py`).
  The console test is `web/tests/file-leases.spec.ts` (4 tests). Policy: `file_lease_*` in `platform/policy/access.rego`; in folder mode the engine reloads by itself, in bundle mode publish first ("The policy engine").
- Only the console kind can be asked for. The notebook kind (up to 200 files, 8 hours) is declared in the policy but refused until the client library exists.
- The design note comparing every kind of lease is `docs/internal/design/leases.html`.

## Asking for access: the way follows what the version holds

The version page has one place to ask, the section "Your access". What it offers depends on `GET /dataset-versions/{id}/readability`, which says what is
locked in the version and what a permission to read the whole of it is worth (`platform/api/app/readability.py`, worked out from the register of locked
files and the privacy contract, never typed):

- **files** (every file locked): one ask, to open a few files. A request to read the whole version is refused with `400` and a reason, because what storage
  holds of locked files cannot be read by anybody outside the platform.
- **columns** (a table with locked columns): both asks, under "The readable columns" and "The locked values". Reading the whole version gives the readable
  columns only.
- **plain** (nothing locked): the whole-version ask, as before.

The same refusal is applied when a request already waiting is approved (the approver can still reject it) and when a run of an agent would park behind such
a request. The access marks of a version of locked files say what the person holds of the files, never what their role covers. The file door adds its
role sentence only when a role is what decides (a development sample or synthetic files).

- Check: U165 (`verify/v158_ways_to_ask.py`, run with `docker compose exec -T munitas-api python /verify/v158_ways_to_ask.py`). The console test is
  `web/tests/ways-to-ask.spec.ts`. A change to the readability rules means rebuilding the API (`up -d --no-deps --build munitas-api`) and, for the door
  sentence, publishing the policy ("The policy engine"; in folder mode it reloads by itself).

## Signing a notebook in

A notebook signs in as a person with a short code, not a pasted token. In the notebook: `me = Munitas.sign_in("https://...")` (the client library, `scripts/client/munitas_client.py`)
prints an address and a code. The person opens the address in the browser they are signed in to the console with, types the code, and approves on the page that says what the
notebook will and will not be able to do. The notebook then holds a pass that lasts 15 minutes and renews itself for up to 8 hours. It reaches only the routes the library calls
(`ALLOWED` in `platform/api/app/notebook_sign_in.py`): finding datasets, asking for access, the files token, derivations, lineage and ending its own sign-in. Everything else, such as
deciding a request, is refused with a reason even for a data custodian. The person sees and ends their notebooks under "Your notebooks"; the data protection officer and the privacy
auditor may see everybody's.

- **What runs.** Hydra (`oryd/hydra:v26.2.0`, services `hydra-migrate` and `hydra` in `docker-compose.yml`, config in `infra/hydra/hydra.yml`) issues, renews and ends the pass. Its public side is
  published on `MUNITAS_PORT_HYDRA_PUBLIC` (4444), because the person's browser is sent there once; its admin side (4445) is not published. Kratos is not changed: the console's own session is the sign-in.
- **An install that already has a Postgres volume** does not run the database init again, so the `hydra` database must be added once:
  `docker exec munitas-postgres-1 psql -U munitas -d postgres -c "CREATE DATABASE hydra"`. Then `docker compose up -d --no-deps hydra-migrate hydra`, apply `platform/schema.sql`
  (the table `notebook_sign_in`), and rebuild the API. Never `up` without `--no-deps` while only adding Hydra: it would recreate Postgres.
- **Settings.** `MUNITAS_NOTEBOOK_SIGN_IN_HOURS` (8), `MUNITAS_NOTEBOOK_ACCESS_TOKEN_MINUTES` (15), `MUNITAS_HYDRA_SYSTEM_SECRET` (set your own outside development), `MUNITAS_HYDRA_ADMIN_URL`,
  `MUNITAS_HYDRA_PUBLIC_URL`, `MUNITAS_NOTEBOOK_CLIENT_ID`; all in the settings catalogue.
- **Limits that are ours, not Hydra's.** Thirty sign-ins started per address in ten minutes (an office shares one address), five wrong codes per person in ten minutes, one poll per five
  seconds. A person who presses Refuse is told at once by the platform; Hydra alone would leave the notebook waiting ten minutes (free releases to v26.2.0 do).
- **Ending.** "End now" on a row ends only that notebook's sign-in (Hydra ends one approval by its own id, kept in `notebook_sign_in.consent_request_id`); the person's other notebooks keep working. The pass and the renewal token stop at the next request. A sign-in recorded without the id can only be ended together with the person's others. "End all of my notebook sign-ins" (`POST /notebook-sign-ins/revoke-all`, console only: a notebook's own pass cannot call it) ends every one at once, for a lost laptop or when the person cannot tell which. Both buttons ask first when a notebook was used in the last 10 minutes (`IN_USE_MINUTES` in `notebook_sign_in.py`), naming it, because it stops at its next request and anything it was doing is cut off; a notebook not used lately is ended without a question.
- **A files token** made by a signed-in notebook carries its sign-in and stops with it.
- **Check:** U167 (`verify/v160_notebook_sign_in.py`, 52 checks: plays both the notebook and the person against the live platform and Hydra, and runs the real library). The console test is
  `web/tests/notebook-sign-in.spec.ts`. U166 also signs in this way in its last section.
- **Behind a proxy or in Docker on this machine** the address a request seems to come from is the proxy's or the Docker gateway's, so "used from another address" only means something when the platform
  sees real client addresses.


## Access rules: what people may ask for, and how much

An organisation's limits on requests for real values are settings, not constants. There are seven: who may ask to read rows of locked columns, the most rows and the most days, the most files and hours to open locked files in the console page, and the same two for a notebook. Each has a default (what the platform did before there were settings) and a most the platform allows; an organisation that sets nothing gets the defaults.

- **See them.** The console page Access rules (under The organisation) shows each rule's value in force, its default, the most the platform allows, and who changed it, when and why. Its "What would happen" box tries a request of a given size from a given role and says whether it would be accepted and, if not, which rule says no. The answer comes from the same decision a real request gets. API: `GET /access-rules`, `POST /access-rules/what-would-happen`.
- **Change one** (until the page edits). Run `scripts/admin/set-access-rule.py` where the platform's database is reachable, for example in the API container:

      docker compose exec -T munitas-api python /scripts-admin/set-access-rule.py --tenant health --list
      docker compose exec -T munitas-api python /scripts-admin/set-access-rule.py --tenant health --rule locked_rows.most_rows --value 30 --by "Priya Shah" --reason "the audit asked for 30"
      docker compose exec -T munitas-api python /scripts-admin/set-access-rule.py --tenant health --rule locked_rows.most_rows --reset --by "Priya Shah" --reason "back to the default"

  `--dry-run` says what would be written and writes nothing. A list of roles is comma-separated; an empty value means nobody may ask. The script refuses a value above the most the platform allows and a role that is not offered. There is no second person: whoever runs it holds the database, and every use is a row that says who, when and why.
- **How it is kept.** Each change is a new row in `access_rule_version`, never an edit (the database refuses one). The latest row for a rule is in force. It is held to the platform's maximum again every time it is read, so a row written by hand cannot exceed it. A lease already granted keeps what it was granted.
- **Where it is used.** `locked_leases.py` and `file_leases.py` hand the values in force to the policy engine as `input.limits`; `access.rego` falls back to the default when none is given. The defaults in `access_rules.py` and the constants in `access.rego` must agree; U168 compares them.
- **Check:** U168 (`verify/v161_access_rules.py`, 39 checks, against the live platform and policy engine). The console test is `web/tests/access-rules.spec.ts`. The design, and what is not built yet, is `docs/internal/design/access-rules.html`.


## Release bars: what a de-identified result is held to

The bar a de-identified result must meet before it is released is the organisation's decision, not the platform's. A **release bar** is a named set of limits (for example "Public release" or "Project A, closed research team"): one overall figure for how much identifying detail was removed, and for each kind of identifier (names, dates, phone numbers and the others) the least removed and the most left in the clear. A limit left empty is not applied.

- **See them.** The console page Release bars (under The organisation) lists the organisation's bars at their latest version, each with who set it, when and why, and the earlier versions. Everyone signed in can read it.
- **Set one.** Only the data protection officer creates a bar or adds a version to one (`POST /release-bars`, `POST /release-bars/{id}/versions`). The rule is `set_release_bar_decision` in `access.rego`, and the platform refuses anybody else and says why in the words of the role table. A new bar in the console starts from the Platform default bar, so making one never drops a limit it has.
- **The Platform default bar.** It is not stored: it is `default_bars()` in `platform/gate_bars.py` (98 percent overall, and none left in the clear of the kinds that name a person directly). It is a starting point to argue with, not a standard. It is the bar a run's own recommendation is made under when the run finishes, and the bar used when nobody chooses another.
- **Choosing one.** The person who decides a waiting result chooses a bar on the De-identification results page. The recommendation under it is worked out by the platform when the decision is made, from the counts for each kind of identifier that the run kept (`GET /gate-decisions/{id}/judgement` shows it without recording anything), and a bar the caller names is the only thing taken from the caller. A result nothing measured stays "not measured" under every bar. A result measured before the counts were kept can only be judged by the Platform default bar, and says so.
- **What a decision keeps.** The bar's name and version, its limits as they were, the recommendation under it and the recommendation the run recorded, on the decision row (`purpose_name`, `purpose_version`, `bars_used`, `release_recommendation`, `release_reason`). A later version of a bar never changes what an earlier decision says.
- **How a bar is kept.** `gate_purpose` holds the name, which is never changed, and `gate_purpose_version` holds each version, never edited (the database refuses an edit). An existing database gets the tables by applying `platform/schema.sql`, which is safe to run again; the policy engine must be restarted to read the changed `access.rego` and `screens.rego` when they were edited from Windows, because `--watch` does not see those edits.
- **The judgement is shared.** `platform/gate_bars.py` and `worker/gate_bars.py` are the same file, because the API image and the worker are built from different folders. `scripts/check_pins_agree.py` fails when they differ.
- **Checks:** U192 (`verify/v181_release_bars.py`, in the API container: the judgement, who may set a bar, versions, the judgement of a waiting result, and what a decision keeps), U193 (`verify/v182_gate_records_per_kind.py`, on the host: a run keeps the counts for each kind and is judged by the Platform default bar) and U194 (`web/tests/release-bars.spec.ts`, the console).


## The two audit pages

Under **Audit** in the console menu there are two read-only pages, for different questions.

- **Access decisions** (`/audit`, roles: data protection officer, data custodian, platform administrator). Every access decision on the platform: datasets, agents, files, refusals, each with who, when, what and why. Use it to answer "was this person allowed to do this?".
- **Locked data audit** (`/locking-audit`, roles: privacy auditor, data protection officer only; custodians and administrators are refused on purpose). How locked data was actually used, for locked columns and for locked files: who read locked rows and who was refused, the leases for rows, how each table's columns were treated, small-group findings, who opened locked files and who was refused (in the page or in a notebook, and why the platform allowed it), the leases for files, the key log, and who was erased. It shows no person's name and no value from any table. Files are named on it, because the page is for these two roles only (nobody else can open it, and every read and search is recorded). Opening the page is itself recorded as an access decision.

The Locked data audit opens with a contents line and a bar of filters (person, table, file, from and to, and a search of the words). Put part of a file name in File to see everyone who opened that file (Opens), every lease that named it (Leases for locked files), and every key opened to lock it or to open it (Keys). Each list asks the platform for one page at a time (25 lines, 5 contracts), newest first, and shows how many lines match, its own choice of result, and Previous and Next. A list that cannot use a filter says so (erasures have no table or file, contracts have no person). Changing a filter starts every list at its first page, and Clear filters also puts every list's result back to All. Its data is `GET /audit/locked-columns?section=<list>` with `person`, `table`, `date_from`, `date_to` (the end is exclusive), `result`, `q`, `page` and `page_size` (at most 100), served by `audit_pages.py`; without `section` the call returns the latest lines of every list, as before. Each call is one line in the access log, `read audit evidence: <list>`, with what was searched for in brackets and `page N` after the first page, so who looked at which list and searched for what is on record; the Access decisions page shows it in words. The locked-file lists are `file_reads` (from `record_read_log` and the refusals of `open a record` on versions that hold locked files) and `file_leases`. Each key line says why in words (`reason`), the `table` where it can be found, and the `file` where one was locked or opened; the platform's own text is never passed on. What is recorded: since 2026-10-08 a refusal to open a file records which file (`access_decision.record_id`), and the key lines for sealing a table and for locking an uploaded file carry the dataset (`seal a table (dataset <id>)`, `lock part N of file <name> (dataset <id>)`). Lines written before then have no table or no file where it was not recorded, and the page says Not recorded. The keys a custom step opens to lock its outputs carry no table. A refusal to open a file is not recorded with the way it was asked (page or notebook), so that cell reads "Not recorded". Checks: U169 (`verify/v162_audit_filters.py`, every list walked page by page, each filter, who may read it), U164 and U166 (the evidence, including that no file is named), U135, `web/tests/locked-data-audit.spec.ts` and `web/tests/locked-data-audit-filters.spec.ts` (the page).

## Recapturing the walkthroughs

Each walkthrough page under `docs/public/walkthroughs/` is built from real screenshots. A capture (`web/walkthroughs/<name>.spec.ts`) drives the console and writes a picture and a `.txt` beside it (the words on that screen, including what is typed or chosen in a box). A builder (`docs/tools/build_<name>_walkthrough.py`) turns the pictures and its notes into the page. **Every phrase a note sets in bold or as code is checked against the saved words, and the build fails when one is not on that screen**; `MUNITAS_NOTES_WARN=1` lists them without failing, which is how to review a page after the console changed. A phrase that is a date or a count made up at capture time is written outside the bold.

```
cd web
npx playwright test --config=walkthroughs/playwright.config.ts <name>          # one capture
cd ../docs/tools
..\.venv\Scripts\python.exe build_<name>_walkthrough.py                         # its page
..\.venv\Scripts\python.exe build_walkthrough_index.py                          # the index and the tour's list, once at the end
```

What each capture needs (checked 2026-10-09; times are one run on this machine):

| Capture | Needs | Time |
|---|---|---|
| `people-administration`, `department-approvers`, `locked-columns`, `locked-recordings` | the health organisation seeded | 10 to 25 s |
| `development-sample`, `custom-pipeline`, `custom-step` | the above, the host worker, and the image `python:3.11-slim` in the Docker of the `Munitas` WSL distro (`docker pull python:3.11-slim`) | 20 to 30 s |
| `derive` (health), then `DERIVE_TENANT=finance` for finance | the image `munitas-derive-runner:1` (`docker build -t munitas-derive-runner:1 worker/derive`) | 20 s each |
| `finance` | the finance organisation seeded | 30 s |
| `healthcare` (the builder makes four pages from this one capture: `bring-in-a-recording`, `ask-to-read`, `agent-asks`, `remove-names`) | the host worker and a GPU: it runs the real transcription | about 4 minutes |
| `legal-export`, then `closing` | Harbour open with its example data. `legal-export` leaves Harbour closing and `closing` deletes it: rebuild it before `closing` and again after it with `powershell .\scripts\seed\reseed-tenant.ps1 -Tenant harbour -Force` | 1 minute, 25 s |

The pictures are the whole 1920 by 1080 screen (the kit no longer crops them): the menu with the signed-in person at its foot is part of the picture, and notes say "the panel at the bottom left of the menu". Tests run in the canary organisation; these captures are the one place health, finance and harbour are used.

