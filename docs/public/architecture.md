# Munitas: system architecture

What the platform is built from, and how each piece enforces governance
rather than just describing it.

Companion reading: [the governance model](design/governance-model.md) for
who is accountable for what, and the
[walkthroughs](walkthroughs/feature-walkthrough.html) for these guarantees
in action, screen by screen.

---

## 1. The services, and the trust boundaries between them

Every request into the platform passes through one control plane, the API,
which checks every read and every write against a separate policy engine
before doing anything else. Sandboxed agent runs sit on their own isolated
network, with no direct route to the internet or to storage, and still have
every tool call checked against that same policy engine.

[Diagram: services and network boundaries →](diagrams/system-architecture.html)

| Service | Role |
| --- | --- |
| Console | Where a person signs in and works: registers data, requests access, approves it, reviews pipeline output |
| API | The control plane. Every request passes through here; it decides nothing about access on its own |
| Policy engine (Open Policy Agent) | Evaluates every access, approval, export, and release decision, and logs a reason for every allow and deny |
| Worker | Runs pipelines and AI agents; the only thing that reads or writes a dataset's actual bytes on the platform's behalf |
| Database | Records every dataset version, every access decision (including denials), and the append-only history that answers what was allowed on any past date |
| Object storage | Where the underlying files live, reached only through prefix-scoped, time-limited credentials the API mints, never a standing key |
| Sandboxed agent run | Executes an agent's own uploaded code on a network with no route out, so what it may reach is a property of the network itself, not a rule that has to be remembered |

---

## 2. Core concepts

**A sensitivity lattice, not a binary ACL.** Every dataset version sits at
one of five access levels, from most to least restricted: `RAW`,
`UNDER_REVIEW`, `OPEN_FOR_ANNOTATION`, `OPEN_FOR_TRAINING`, `PUBLISHED`.
Every role has a ceiling on what it can read without asking; reading
anything above that ceiling always requires an explicit request, a named
custodian's approval, a stated purpose, and a lease that expires on its
own. Promoting a version to a higher access level grants a new credential;
it never copies or moves the underlying bytes.

**Immutable once sealed.** A dataset version, once sealed, cannot be
edited or deleted at the database level, not by application convention.
The access level a version currently holds is derived from an append-only
log of promotions, so history is never rewritten and the access level held
on any past date stays answerable.

**Lineage, in one hop.** Every pipeline step records the exact code and
inputs that produced its output, with a unique key so a retried step
produces exactly one result. What produced any given version, from what,
with which code, is one query away rather than a graph traversal.

**Tenancy.** Every organisation's data sits in its own isolated space:
its own storage bucket, its own rows, resolved from who is signed in, never
from anything a request claims about itself.

**One policy engine, every decision.** Access, approval, export, and
release are each answered by the same policy engine rather than decided
inline by whichever endpoint happens to handle the request, so there is one
place, not many, where "who may do this" is defined and tested.

**An agent's identity never comes from what it reads or writes.** The
identity a running agent's tool calls are checked against is frozen by the
runtime before the agent starts, and nothing inside the agent's own
reasoning or the documents it reads can change it. An instruction planted
in a document the agent reads can change what the agent asks to do, but it
cannot change which identity that request is checked against, so there is
nothing for a prompt-injection attempt to escalate into.

**Deletion without rewriting history.** Each record is protected by its
own encryption key. Deleting a record destroys that key rather than
rewriting any sealed version: the record becomes permanently unreadable
everywhere it was ever included, while the version history, lineage, and
audit trail that referenced it stay intact. A record of the deletion
itself is kept, so erasure does not erase the fact that an erasure
happened.

---

## 3. Technology

Every component is MIT, Apache 2.0, BSD, or PostgreSQL licensed: nothing
in the stack requires a commercial licence to run or extend.

**Storage and data**

| Component | Role |
| --- | --- |
| SeaweedFS or Cloudflare R2 | S3-compatible object storage, selected per organisation. Credentials are scoped to a prefix, which is how an access level is enforced physically, not just by convention |
| PostgreSQL (with pgvector) | Dataset versions, access-level history, pipeline runs, leases, and the audit log; also the vector store for near-duplicate and contamination checks |

**Control and policy**

| Component | Role |
| --- | --- |
| Temporal | Durable workflow execution for pipeline runs and background ingestion, with retry, timeout, and resume-after-crash as properties of the engine rather than something each workflow has to implement |
| Open Policy Agent | Evaluates every access, approval, export, and release decision, and logs a reason for every allow and deny |
| Ory Kratos | Real sign-in and session verification; every registered person authenticates for real, with no shared or assumed identity |
| FastAPI | The control plane: register schemas, create dataset versions, mint scoped credentials, approve leases, promote access levels, start ingestion |

**De-identification pipeline**

| Component | Role |
| --- | --- |
| faster-whisper | Speech recognition with word-level timings |
| pyannote.audio | Speaker diarisation |
| Microsoft Presidio, spaCy, GLiNER | Three independent identifier-detection approaches, combined because they fail differently, so their union catches more than any one alone |
| Piper | Speech synthesis, for rendering redacted spans as matched-duration surrogate audio |
| Label Studio | Human annotation and correction, with model output pre-filled so a reviewer corrects rather than starts from nothing |

**Agents and observability**

| Component | Role |
| --- | --- |
| LangGraph | An agent's execution graph, with checkpointed state and an explicit pause for human approval before a consequential action |
| OpenTelemetry and Jaeger | Distributed tracing, with the access level of the data touched carried as a span attribute |
| MLflow | Registry for pipeline runs, models, and the evidence a promotion decision points to |

---

## Learn more

- [The governance model](design/governance-model.md): who owns the data,
  who decides who may read it, and how that decision is made trustworthy.
- [API reference](reference/api-reference.html): every route the control
  plane exposes, generated directly from the API's own route definitions.
- [Walkthroughs](walkthroughs/feature-walkthrough.html): real screenshots
  of these guarantees end to end, for someone who has never seen the
  platform before.
