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
the platform itself uses (`PG_DSN`, `S3_ADMIN_KEY`, `S3_ADMIN_SECRET`,
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
   `Ubuntu-20.04`; if yours is named differently, pass `-WslDistro <name>`
   to `start-dev.ps1`, `stop-dev.ps1`, and `run-verification.ps1`.
2. **Docker running inside that distro, not Docker Desktop.** This
   project reaches Docker through `wsl -d <distro> -- docker ...`; a
   Docker Desktop install's `docker.exe` on the Windows PATH is not used
   and does not need to work. Install Docker Engine inside the distro
   itself, following Docker's own Linux install instructions for Ubuntu,
   then confirm it: `wsl -d Ubuntu-20.04 -- docker ps`.
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
`infra/kratos/identities.json` (currently twenty-two: ten in `health`,
seven in `canary`, five in `finance`, sourced from that tenant's own
`infra/postgres/seed-organisation.sql`, `seed-canary.sql`, or
`seed-finance.sql`), creates a Kratos identity with a password for each,
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

Both this and `start-dev.ps1` reach Docker through WSL2, using the shared
helpers in `wsl-docker.ps1`. Pass `-WslDistro` to either if the distro is not
named `Ubuntu-20.04`.

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

**What it costs:** a real de-identification of a few recordings, so the GPU is
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

## Pipeline runs that stopped without recording it

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
storage keeps its folder grants until it is printed again, and the platform's safety guard refuses a print
that removes more than 40% of them (a print that is refused also leaves new keys unable to activate, which
shows as 503 errors). So after every batch the tool runs `scripts/admin/_canary_reprint.py` inside the API
container. That relaxes the guard only when it can prove that everything the print removes is a folder grant,
in canary's bucket, of a dataset that no longer exists, and that every identity that disappears is a lease,
catalog, task or table job key. If any of that fails, nothing is printed and it names what it found. If
`/health` shows `storage_permissions` failing after a canary clear, run it by hand:

```bash
wsl -d Ubuntu-20.04 -- docker exec munitas-munitas-api-1 python /scripts-admin/_canary_reprint.py
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

**Cause.** The Docker engine runs inside the WSL2 distro (Ubuntu-20.04),
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
wsl -d Ubuntu-20.04 -- sleep infinity
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
wsl -d Ubuntu-20.04 -- docker exec -i munitas-postgres-1 psql -U munitas -d platform -c "select r.id, r.status, r.purpose, lr.state, lr.principal, lr.requested_by from agent_run r left join lease_request lr on lr.id = r.lease_request_id where r.status in ('awaiting_access','awaiting_approval','awaiting_activation') order by r.started_at desc;"
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
inside the WSL distro with `$HOME/.munitas/verify-venv/bin/python`, not the
plain Windows `.venv` -- see each script's own docstring for the exact
invocation. The reason is the same as above: Docker itself runs inside
WSL2 on this machine, not Docker Desktop, so anything that talks to
Docker's own API, not only the `docker` CLI, only reaches the real engine
from inside the distro.

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
- **`retryable: false`** (the safety guard refused a print that would remove
  too much access, or a role has no configured key): retrying cannot help.
  Fix the cause, then print once:

```bash
wsl -d Ubuntu-20.04 -- docker exec munitas-munitas-api-1 python /app/reconcile-grants.py
```

  If the guard refused because the change is deliberate (for example the first
  print after a release that stops a role holding standing storage access, which
  can remove more than 40% of the prefix grants at once), read the numbers in the
  refusal, and when they are what the release intended, print it with:

```bash
wsl -d Ubuntu-20.04 -- docker exec munitas-munitas-api-1 python /app/reconcile-grants.py --allow-shrink
```

The banner clears on the next successful print and waiting runs resume
within 10 seconds. The history of every print is in
`storage_permission_print`.

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
.venv\Scripts\python.exe -m pip install duckdb "pyiceberg[pyarrow]"
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
.venv\Scripts\python.exe -m pip install duckdb numpy "pyiceberg[pyarrow]" boto3
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
wsl -d Ubuntu-20.04 -- bash -lc "cd /mnt/c/AIProjects/ClaudeProjects/Munitas && docker build -t munitas-derive-runner:1 worker/derive"
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
wsl -d Ubuntu-20.04 -- bash -lc "cd /mnt/c/AIProjects/ClaudeProjects/Munitas && docker compose exec -T munitas-api python /verify/v95_derivation_run.py"
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
containers, not more jobs in one.

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

## Secrets before a real deployment

Two values have a development default in this repository and in `docker-compose.yml`:
the storage administrator's secret (`S3_ADMIN_SECRET`) and the worker token
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
