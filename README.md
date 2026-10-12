# Munitas

**One governance model for data, AI agents and pipelines.
Open source, and yours to run.**

- **Any kind of data, checked on the way in.** Audio, images, text and tables,
  each described from its own files.
- **Access that moves in steps and expires on its own.** Five access levels,
  and every wider read needs someone else's yes.
- **Notebooks and Python,** with one `pip install` from the platform itself.
- **Your own pipelines and steps,** approved before they touch real data.
- **Personal data removed, kept apart, and erasable** one person at a time.
- **Oversight for AI agents.** Hold them to written risk levels, with changes
  approved and output reviewed.

Apache 2.0, one machine, `docker compose up`. No account, no trial clock, no
meter.

Details are under [How it works](#how-it-works), and the steps to try it are in the [Quickstart](#quickstart).

*Munitas* is built from the Latin *munire*, "to fortify or protect", in the pattern of *veritas* and
*libertas*: the state of being protected. It names what the platform is for. Datasets, AI agents and the
record of what happened to them are protected by guarantees that cannot be quietly bypassed.

---

## Basic concepts

The terms the rest of this page relies on, grouped by what they describe.

### What the platform holds

- **Dataset.** A named, owned collection of data the platform tracks:
  audio files, transcripts, CSVs, whatever a department registers.
- **Agent.** An AI system registered on the platform the same way a
  dataset is: named, owned, and versioned before it can run.
- **Pipeline.** A sequence of steps that processes data or runs an agent,
  either built into the platform or authored by a team as a DAG, including
  steps of its own code.
- **Version and sealing.** Nothing is edited in place. Uploading content
  creates a version; sealing it makes that version immutable and
  content-addressed, so a later change always creates a new version
  instead of altering the old one.
- **Development and production.** The two stages a pipeline version moves
  through. A version that includes code the platform did not write starts in
  development and can run only on development data. An approver of the owning
  department who did not write it moves it to production.
- **Derived dataset.** A new dataset made from a query over existing ones.
  The platform drafts it, the person confirms, the query runs in an isolated
  sandbox, and the result is sealed as restricted as the data it came from.
  Withdrawing access to that data closes the new dataset too.
- **Locked data.** Columns and files that identify a person are held under a
  key of that person. Reading them needs a lease, and every opening of a
  key is recorded with its reason.

### Who is who

- **Tenant.** One organisation's isolated space on the platform. The
  worked examples further down, `health`, `finance` and `harbour`, are each
  a separate tenant.
- **Department.** The team that owns a dataset, agent, or pipeline. Its
  approvers decide who may read what it owns.
- **Role.** The job a person or an automated workload does on the
  platform: data custodian, researcher, pipeline worker, and so on. A
  role sets the most sensitive access level it may read without a lease,
  and what, if anything, it may approve.
- **Custodian.** A person who holds the data custodian role, which allows
  deciding who may read data. A custodian decides for the departments they
  are an approver of.
- **Approver.** A data custodian listed for a department. A department can
  have any number of approvers, and any one of them can approve a request or
  confirm a claim about its data. A person counts only while they hold the
  custodian role, and every change to the list is recorded with a reason.

### Who may read what

- **Access level.** Every dataset version sits at one of five levels, from
  most to least restricted: `RAW`, `UNDER_REVIEW`, `OPEN_FOR_ANNOTATION`,
  `OPEN_FOR_TRAINING`, `PUBLISHED`. Data brought in through the platform
  starts at `RAW`. Moving up a level is a gate decision someone else has to
  make; a dataset never promotes itself.
- **Lease.** A temporary grant of access to a specific dataset version,
  bound to a stated purpose, that expires on its own.
- **Limits.** What an organisation lets people ask for (rows, days, files,
  hours, and tables brought in), each inside a maximum the platform sets.
- **Gate decision.** The point where a pipeline run's output, or an
  agent's readiness, is explicitly cleared or held back by someone other
  than whoever ran it, and recorded.

### Holding agents and releases to account

- **Risk level.** One of the levels an organisation writes to say how
  strictly an agent is held: whether a change that lets it do more needs a
  second approver, how often it must be confirmed again, and whether it may
  be deployed at all.
- **AI risk owner.** The role that writes the risk levels and confirms each
  agent's level.
- **Approved tools list.** Tools an organisation has approved in advance. A
  version that adds only listed tools needs nothing more, and a first version
  is always decided by people.
- **De-identification standard.** A named, versioned set of limits for one
  audience, saying how much identifying detail a de-identified result may
  still hold.
- **Answer key.** A list, prepared beforehand, of the identifying details that
  really are in the data. A pipeline that removes identifying details is
  measured against it.
- **Sponsor.** The person who answers for an agent. A named sponsor confirms
  the agent again on the schedule its risk level sets.
- **Evidence pack.** What the platform holds about one agent over a period:
  identifiers, counts, hashes and times, and no personal data.

### Ending an organisation

- **Closing down and legal hold.** An organisation that leaves is closed in
  two 15-day stages and then deleted, unless a legal hold stands. A hold
  needs two different administrators, and records for a legal matter are
  produced as an encrypted, signed package by three different people.

## How it works

Each part below matches a line in the opening, in the same order.

### Any kind of data, checked on the way in

A dataset can hold audio, images, text and tables, and a single version can
mix them. Tables arrive as CSV, TSV, JSON or JSONL, compressed or not, or as
Parquet, Avro, ORC, Arrow, Feather or Excel files, from an upload or from
Hugging Face. When a version is sealed, the platform works out what it holds,
the kinds, the structure and the formats, from the files themselves. Nothing
there is taken from what the uploader says.

- **Register, then seal.** A dataset, an agent, or a pipeline is named,
  given an owning department, and uploaded. Nothing is readable or runnable
  until it is sealed as a version: content-addressed, immutable, no editing
  in place.
- **Open tables.** A sealed table is also stored as an Apache Iceberg
  table, and a read-only catalog lists only the tables a person may read,
  with short-lived storage keys, so tools such as DuckDB open governed data
  directly.

### Access that moves in steps and expires on its own

- **A sensitivity lattice, not a yes-or-no access list.** Data moves through
  access levels one step at a time. Every role has a ceiling on what it can
  read without asking; reading anything above it always requires an explicit
  request, a named custodian's grant, a stated purpose, and a lease that
  expires on its own.
- **One policy engine, every request.** Every read and every write is
  checked against [Open Policy Agent](https://www.openpolicyagent.org/)
  before it is allowed: who is asking, what they are asking for, and why,
  evaluated against the same rules every time. No endpoint decides access
  on its own or has a way around this check.

An organisation also sets limits on what people may ask for, such as how many
rows, for how many days, how many files and hours, and how much may be brought
in as tables, each inside a maximum the platform sets.

### Notebooks and Python

A person approves a notebook with a short code, and the notebook gets a
short-lived pass. The pass reaches only what the client library does: find
datasets, ask for access, open the few files it was allowed, and make a
dataset from a query. The platform refuses it anything else.

The library installs from the platform itself, so there is no public package
index to depend on:

```bash
pip install <platform address>/client/<file>.whl
```

Tools that read open table formats, such as DuckDB and PyIceberg, read
governed tables directly, as described under open tables above.

- **Derived datasets.** A query over existing datasets becomes a new
  dataset only after the platform drafts it and the person confirms it. It
  runs in a sandbox with no network and no passwords.

### Your own pipelines and steps

A team can register a pipeline of its own, including steps of code the
platform did not write. Because that code is not the platform's, a new version
starts in development, where it can run only on development data, such as a
small sample the platform draws. An approver of the owning department who did
not write the version moves it to production. A running step reaches the
platform only through the step gateway and reads data only through the
platform. What it writes becomes a new version that waits for review and
records exactly what made it. A retired version can no longer be run.

- **Every pipeline ends in a gate decision.** Built-in pipelines and
  operator-authored DAGs alike end in a real gate decision that is part of
  the execution graph itself, not an optional extra. Whoever started the run
  cannot be the one who clears it; a database constraint enforces that, not a
  configurable policy.

### Personal data

A pipeline that strips identifying details is measured against an answer key,
a list of the identifying details that really are in the data. A reviewer who
did not run the pipeline decides whether the result may be released. The
reviewer picks one of the de-identification standards the organisation wrote.
The platform works out the recommendation again at that moment from the counts
the run kept, and the decision keeps the standard and its version.

Columns and files that identify a person are held under a key for each person.
Every opening of a key is recorded with its reason, and one person can be
erased without touching anyone else.

### Oversight for AI agents

An agent is registered, owned by a department and versioned, the same way a
dataset is. Holding agents to risk levels is a switch an organisation turns
on. Once it is on, an agent needs a level the AI risk owner has confirmed
before it can start a run, a named sponsor confirms it again on the schedule
its level sets, and a version that would let it do more waits for the
approvals its level asks for: a new tool, a new outside host, a copy of the
data, or a different model.

A version that adds only tools on the approved list is approved in advance,
and a first version is always decided by people. An agent that a vendor runs
outside the platform is listed in the registry and flagged.

An agent reads data only with an approved lease, and calls only the tools and
outside hosts an approver allowed. What it drafts is reviewed by someone other
than whoever started the run. Any agent can be suspended. An agent's evidence
pack lists what the platform holds about it over a period: identifiers,
counts, hashes and times, and no personal data.

For more detail:

- [Architecture](docs/public/architecture.md): the services and how they fit together.
- [Governance model](docs/public/design/governance-model.md): who is accountable for what, and why.
- [Configuration reference](https://albinjacob.github.io/munitas/reference/configuration.html): every setting and its default.
- [API Reference](https://albinjacob.github.io/munitas/reference/api-reference.html): every route the control plane exposes, generated from the API's own route definitions and checked automatically so the two cannot drift apart.
- [What each role can do](https://albinjacob.github.io/munitas/reference/roles.html): generated from the policy and checked the same way.

## See it in action

[The feature tour &rarr;](https://albinjacob.github.io/munitas/walkthroughs/feature-walkthrough.html)
explains what the platform does, who uses it, and why it is built the way
it is. [The walkthroughs &rarr;](https://albinjacob.github.io/munitas/walkthroughs/index.html)
follow real stories step by step, with real screens from the console, for a
hospital group, a card-payments company and a small clinic: twenty-seven
walkthroughs in all, each written for someone who has never seen the platform
before.

The hospital group's walkthroughs run in order:

- who owns what, and the limits an organisation sets
- bringing in regular data and asking to read it
- patient data held under a key for each person, erased one person at a time
- the standards a de-identified result is released under, and a reviewer's decision
- roles and approvers
- AI agents held to written risk levels, with approved tools and reviewed output
- the external hosts an agent may call
- custom pipelines

The others cover scoring card transactions, closing an organisation down, and
handing over its records for a legal case.

## What makes this different

Large cloud data-and-AI platforms increasingly govern data, agents, and
pipelines together, inside their own cloud.

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
  configurable policy.** That a runner can never approve their own work
  is a Postgres check constraint here, not a setting an administrator can
  leave switched off, as the equivalent self-approval restriction is an
  optional setting in some mainstream CI platforms. A lease is
  purpose-bound and expires on its own by default, rather than being a
  manually issued temporary credential an administrator has to remember
  to revoke.
- **Trying it needs no account, no cloud tenant, and no trial clock.**
  Comparable enterprise platforms typically require a cloud sign-up, an
  existing admin role or tenant, and a time-limited trial before metered
  billing starts. Munitas is `git clone` and `docker compose up`: no
  sign-up, no time limit, and nothing that meters usage.

Below that sits a set of narrower, more specialised categories of tool,
which is what most teams evaluating a smaller governance layer actually
run into day to day:

| | Data catalogues | Agent governance tools | **Munitas** |
|---|---|---|---|
| Governs datasets | Yes | No | Yes |
| Governs AI agents as registered, versioned artifacts | Some do now (see below) | Yes, as runtime policy | Yes |
| Governs operator-authored pipelines the same way | No | No | Yes |
| Sensitivity lattice with self-expiring, purpose-bound leases | Role-based access, not this | No | Yes |
| Terminal gate decision built into the execution engine | No | No | Yes |
| Runner can never be the approver, enforced in code | No | No | Yes |

Some data catalogues now catalogue and version AI agents the way they
already catalogue data, which is why that row says "some do now" instead
of "no." None of them run pipelines, enforce a sensitivity lattice with
leases that expire on their own, or keep a runner from approving their
own work. Agent governance tools govern what a running agent may do,
live, but do not register or version anything, and do not touch data at
all. None of these categories combines all six rows in one system.

De-identification tools are not in the table above: they solve a different
problem (detecting and redacting personal data) rather than governing who
may access it, so comparing them on these rows would not be a fair test.
Munitas's own de-identification pipeline uses one such library
internally, wrapped in the governance layer these tools do not provide on
their own.

## Status

This is a working, single-machine platform, not a spec. Every claim below
about what works is backed by an automated check in the
[verify/](verify/) suite, not just stated. That suite has nearly a hundred
scripts, each reporting its own checks individually. The policy has more than
300 tests of its own, and the console has forty test files driven in a real
browser.

CI ([.github/workflows/verify.yml](.github/workflows/verify.yml)) runs the
real `verify/` suite against the actual Compose stack on every push to
`main` and every pull request, and a separate job builds and lints the
console.

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
git clone https://github.com/albinjacob/munitas.git
cd munitas
cp .env.example .env
# edit .env: set MUNITAS_HOST_WORK_DIR to this machine's own absolute
# path to a scratch directory (see the comment above it in .env.example)
docker compose --profile quickstart up
```

This starts the twelve services the governance model needs, plus
`worker-lite` (`worker/lite_worker.py`), a worker built specifically to
need no GPU and no host/WSL2 split, so it runs as an ordinary container
on any OS Docker runs on. See that file's own docstring for exactly what
does and does not run there, and `docker-compose.yml`'s `worker-lite`
service comment for why `MUNITAS_HOST_WORK_DIR` has to be a real path on
your machine, not inside the container.

| Service | Purpose |
|---|---|
| Postgres | System of record: datasets, leases, gate decisions, everything governed |
| SeaweedFS | Object storage for the actual bytes of files and tables |
| Temporal | Runs and tracks pipeline and agent workflows |
| Temporal UI | Browsable view of running and past workflow executions |
| OPA (Open Policy Agent) | Evaluates every access decision |
| Kratos | Human sign-in |
| Kratos migration | One-time job that sets up Kratos's own database schema |
| Hydra | Signs a notebook in on a person's behalf, with a short-lived pass |
| Hydra migration | One-time job that sets up Hydra's own database schema |
| API | The control plane everything else talks to |
| Table worker | Writes large tables in a background job |
| Step gateway | The only route a custom pipeline step has to the API; it answers 404 to everything else |

Plain `docker compose up` (no `--profile`) starts just the twelve services
above, with no pipeline or agent worker, for looking at an already-seeded
tenant without running anything.

Add `--profile full` instead of, or alongside, `quickstart` for four more
services. None is needed to see the governance model work; they
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
that native Linux does not have.

**Tested configuration:** NVIDIA RTX 2070 SUPER (8 GB VRAM), Windows 11 with
WSL2.

```powershell
git clone https://github.com/albinjacob/munitas.git
cd munitas
copy .env.example .env
.\start-dev.ps1
```

See [RUNBOOK.md](RUNBOOK.md) for what each piece does and how to run an
example organisation end to end.

### Worked examples

Three worked examples live in separate tenants:

- `health`: a hospital, with consultation audio across several specialities,
  including cardiology, oncology and orthopaedics, plus radiology reports.
- `finance`: a card-payments company, with transaction records, a fraud
  operations department and KYC (know your customer) identity documents.
- `harbour`: a small clinic, used to show an organisation being closed down
  and its records handed over for a legal matter.

All three go through the same mechanism, with nothing industry-specific
changed for any of them.

On a fresh volume the database creates each organisation, its departments and
its people from [infra/postgres/](infra/postgres/).
[infra/kratos/seed-identities.py](infra/kratos/seed-identities.py) gives each
person a login, and the datasets come from
[scripts/seed/seed-health-example.py](scripts/seed/seed-health-example.py),
[scripts/seed/seed-finance-example.py](scripts/seed/seed-finance-example.py)
and
[scripts/seed/seed-harbour-example.py](scripts/seed/seed-harbour-example.py).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Please also read the
[Code of Conduct](CODE_OF_CONDUCT.md) and, for anything security-sensitive,
[SECURITY.md](SECURITY.md) before opening an issue.

## License

[Apache 2.0](LICENSE).
