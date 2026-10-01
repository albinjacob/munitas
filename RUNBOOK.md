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
approved lease; the storage key the catalog hands out lives as long as that
lease does, and stops working when it is revoked or runs out.

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

---

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
