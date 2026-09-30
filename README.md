# Munitas

**Most platforms govern datasets, AI agents, and pipelines with three
different tools. Munitas governs all three with one.**

Upload a dataset and it's locked until someone reviews it. Register an
agent and it can't run until it's locked the same way. A pipeline's output
gets locked too before anyone can call it real. From there, reading it,
running it, or releasing it more widely all need someone else to sign off,
on the record, with a stated reason. One governance model, used everywhere,
instead of a different tool for each piece.

---

## Basic concepts

- **Dataset.** A named, owned collection of data the platform tracks:
  audio recordings, transcripts, CSVs, whatever a department registers.
- **Agent.** An AI system registered on the platform the same way a
  dataset is: named, owned, and versioned before it can run.
- **Pipeline.** A sequence of steps that processes data or runs an agent,
  either built into the platform or authored by an operator as a DAG.
- **Department.** The team that owns a dataset, agent, or pipeline, and
  whose custodian approves access to it.
- **Tenant.** One organisation's isolated space on the platform. The two
  worked examples further down, `health` and `finance`, are each a
  separate tenant.
- **Version, and sealing.** Nothing is edited in place. Uploading content
  creates a version; sealing it makes that version immutable and
  content-addressed, so a later change always creates a new version
  instead of altering the old one.
- **Role.** The job a person or an automated workload does on the
  platform: data custodian, researcher, pipeline worker, and so on. A
  role sets the most sensitive access level it may read without a lease,
  and what, if anything, it may approve.
- **Access level.** Every dataset version sits at one of five levels, from
  most to least restricted: `RAW`, `UNDER_REVIEW`, `OPEN_FOR_ANNOTATION`,
  `OPEN_FOR_TRAINING`, `PUBLISHED`. New data always starts at `RAW`. Moving
  up a level is a gate decision someone else has to make; a dataset never
  promotes itself.
- **Custodian.** The person in a department authorised to approve requests
  to read that department's data.
- **Lease.** A temporary grant of access to a specific dataset version,
  bound to a stated purpose, that expires on its own.
- **Gate decision.** The point where a pipeline run's output, or an
  agent's readiness, is explicitly cleared or held back by someone other
  than whoever ran it, recorded on the record.

## How it works

- **Register, then seal.** A dataset, an agent, or a pipeline is named,
  given an owning department, and uploaded. Nothing is readable or runnable
  until it's sealed as a version: content-addressed, immutable, no editing
  in place.
- **A sensitivity lattice, not a binary ACL.** Data moves through access
  levels one step at a time. Every role has a ceiling on what it can read
  without asking; reading anything above it always requires an explicit
  request, a named custodian's grant, a stated purpose, and a lease that
  expires on its own.
- **Every pipeline ends in a gate decision.** Built-in pipelines and
  operator-authored DAGs alike terminate in a real gate decision, not a
  side quest, part of the execution graph itself. Whoever started the run
  cannot be the one who clears it; that's checked in code, not policy.
- **One policy engine, every request.** Every read and every write is
  checked against [Open Policy Agent](https://www.openpolicyagent.org/)
  before it is allowed: who is asking, what they are asking for, and why,
  evaluated against the same rules every time. No endpoint decides access
  on its own or has a way around this check.

For the services and how they fit together, see
[docs/public/architecture.md](docs/public/architecture.md). For who's
accountable for what and why, see
[docs/public/design/governance-model.md](docs/public/design/governance-model.md). For every route the
control plane exposes, see the [API Reference](https://albinjacob.github.io/munitas/reference/api-reference.html),
generated straight from the API's own route definitions, not hand-written,
and checked automatically so the two can never drift apart.

## See it in action

[Walkthrough: what the platform does &rarr;](https://albinjacob.github.io/munitas/walkthroughs/feature-walkthrough.html)
is real screenshots of a real flow, start to finish: a recording brought
in, a researcher asking for access, a custodian granting it, a pipeline
running and stopping at a gate a human has to clear. Four more walkthroughs
cover finance, healthcare, role administration, and a custom pipeline an
operator builds themselves, each one narrated step by step for someone who
has never seen the platform before.

## What makes this different

Large cloud data-and-AI platforms increasingly claim to govern data,
agents, and pipelines together, and on feature breadth alone, several of
them now do more than Munitas. The honest comparison is not "who governs
more."

**What sets Munitas apart is where it runs and how its guarantees are
enforced, not what it governs.**

- **It runs entirely on infrastructure you control, free and open
  source.** The equivalent governance control plane in a large cloud
  platform typically runs only in that vendor's own cloud, with no
  self-hosted or air-gapped option, or is offered on-premises only as a
  large dedicated-hardware deployment aimed at the biggest regulated
  enterprises and governments. Munitas is Apache 2.0 licensed and runs on
  a single machine with `docker compose up`.
- **Its core guarantees are enforced unconditionally, not left as
  configurable policy.** A runner never approving their own work is a
  Postgres check constraint here, not a setting an administrator can
  leave switched off, the way an equivalent self-approval restriction is
  a togglable option in some mainstream CI platforms. A lease is
  purpose-bound and expires on its own by default, rather than being a
  manually issued temporary credential an administrator has to remember
  to revoke.
- **Trying it needs no account, no cloud tenant, and no trial clock.**
  Comparable enterprise platforms typically require a cloud sign-up, an
  existing admin role or tenant, and a time-limited trial before metered
  billing starts. Munitas is `git clone` and `docker compose up`: no
  sign-up, no time limit, and nothing metering usage once you keep it
  running on your own machine.

Below that sits a set of narrower, more specialized categories of tool,
which is what most teams evaluating a smaller governance layer actually
run into day to day:

| | Data catalogs | Agent governance tools | **Munitas** |
|---|---|---|---|
| Governs datasets | Yes | No | Yes |
| Governs AI agents as registered, versioned artifacts | Some do now (see below) | Yes, as runtime policy | Yes |
| Governs operator-authored pipelines the same way | No | No | Yes |
| Sensitivity lattice with self-expiring, purpose-bound leases | Role-based access, not this | No | Yes |
| Terminal gate decision built into the execution engine | No | No | Yes |
| Runner can never be the approver, enforced in code | No | No | Yes |

Some data catalogs now catalogue and version AI agents the way they
already catalogue data, which is why that row says "some do now" instead
of "no." None of them run pipelines, enforce a sensitivity lattice with
leases that expire on their own, or keep a runner from approving their
own work. Agent governance tools govern what a running agent may do,
live, but do not register or version anything, and do not touch data at
all. None of these categories combines all six rows in one system.

De-identification tools aren't in the table above: they solve a different
problem (detecting and redacting personal data) rather than governing who
may access it, so comparing them on these rows would not be a fair test.
Munitas's own de-identification pipeline uses one such library
internally, wrapped in the governance layer these tools don't provide on
their own.

## Status

This is a working, single-machine platform, not a spec. Every claim below
about what works is backed by an automated check in the
[verify/](verify/) suite, not just stated.

**Known limits:**
- GPU support only runs on Windows + WSL2 today. The quickstart below has
  a GPU-free path that works on any OS. Mac GPU support is not implemented
  yet: the transcription library in use does not support Apple's Metal
  device at all, so it needs a different backend, a known, scoped piece
  of work rather than an open question. `worker/transcribe_backend.py`'s
  docstring lays out exactly what that backend would need.
- CI ([.github/workflows/verify.yml](.github/workflows/verify.yml)) runs the
  real `verify/` suite against the actual Compose stack on every push and
  PR, and a separate job builds and lints the console. It has not run yet,
  because this repository has not been pushed to GitHub.
- No deployment story past one machine yet (Postgres HA, SeaweedFS
  clustering, secrets management).

## Quickstart

There are two ways to run this, depending on what you want to see.

### Any OS, no GPU

This path shows the governance mechanism itself: register a dataset,
request access, have it granted, run a pipeline, and watch it end in a
real gate decision. None of it needs a GPU, because no audio model runs
in this path. The built-in `count_records` starter pipeline and any
operator-authored DAG pipeline both work this way already, and it is the
same governance mechanism the two worked examples below use for every
pipeline run.

```bash
git clone <this-repo>
cd munitas
cp .env.example .env
# edit .env: set MUNITAS_HOST_WORK_DIR to this machine's own absolute
# path to a scratch directory (see the comment above it in .env.example)
docker compose --profile quickstart up
```

This starts the eight services the governance model needs, plus
`worker-lite` (`worker/lite_worker.py`), a worker built specifically to
need no GPU and no host/WSL2 split, so it runs as an ordinary container
on any OS Docker runs on. See that file's own docstring for exactly what
does and does not run there, and `docker-compose.yml`'s `worker-lite`
service comment for why `MUNITAS_HOST_WORK_DIR` has to be a real path on
your machine, not inside the container.

| Service | Purpose |
|---|---|
| Postgres | System of record: datasets, leases, gate decisions, everything governed |
| SeaweedFS | Object storage for the actual bytes (recordings, files) |
| Temporal | Runs and tracks pipeline and agent workflows |
| Temporal UI | Browsable view of running and past workflow executions |
| OPA (Open Policy Agent) | Evaluates every access decision |
| Kratos | Human sign-in |
| Kratos migration | One-time job that sets up Kratos's own database schema |
| API | The control plane everything else talks to |

Plain `docker compose up` (no `--profile`) starts just the eight services
above, with no worker at all, for looking at an already-seeded tenant
without running anything.

Add `--profile full` instead of, or alongside, `quickstart` for four more
services. None of them are needed to see the governance model work; they
add observability and machine-learning tooling on top of it.

| Service | Purpose |
|---|---|
| Jaeger | Distributed tracing UI |
| OTel (OpenTelemetry) collector | Collects traces from the platform and forwards them to Jaeger |
| MLflow | Experiment registry for pipeline and model runs |
| Label Studio | Human annotation UI |

### Windows + WSL2, with a GPU

This path runs the real de-identification pipeline: real audio
transcription, real identifier detection, a redacted output. It uses a
different worker (`worker/main.py`), running natively on the host rather
than in a container, because CUDA-in-Docker has real friction on Windows
that native Linux does not have. Mac GPU use is a different, unfinished
piece of work rather than a limitation of this approach; see
`worker/transcribe_backend.py`'s docstring for what it needs.

**Tested configuration:** NVIDIA RTX 2070 SUPER (8GB VRAM), Windows 11 +
WSL2. No lower-spec GPU has been verified; if yours has less VRAM, expect
to find the actual floor yourself for now.

```powershell
git clone <this-repo>
cd munitas
copy .env.example .env
.\start-dev.ps1
```

See [RUNBOOK.md](RUNBOOK.md) for what each piece does and how to run a
demo tenant end to end.

### Worked examples

Two worked examples are seeded automatically on a fresh volume, in
separate tenants: `health`, a hospital (consultation audio across several
specialties, including cardiology, oncology, and orthopaedics, plus
radiology reports), and `finance`, a card-payments company (transaction
records, a fraud operations department, and KYC identity documents). Both
go through the same mechanism, with nothing industry-specific changed for
either. See
[infra/postgres/seed-finance.sql](infra/postgres/seed-finance.sql) and
[scripts/seed/seed-finance-example.py](scripts/seed/seed-finance-example.py).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Please also read the
[Code of Conduct](CODE_OF_CONDUCT.md) and, for anything security-sensitive,
[SECURITY.md](SECURITY.md) before opening an issue.

## License

[Apache 2.0](LICENSE).
