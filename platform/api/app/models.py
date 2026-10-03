"""Request and response shapes.

Validation lives here rather than in prose. A purpose that may not be empty is a
constraint the schema enforces, the policy enforces, and this enforces, because
the same rule stated in three places that can each reject is not duplication, it
is depth.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

CLASSES = Literal["RAW", "UNDER_REVIEW", "OPEN_FOR_ANNOTATION", "OPEN_FOR_TRAINING", "PUBLISHED"]


class Field_(BaseModel):
    name: str
    type: str
    format: str | None = None
    sensitivity: Literal["none", "quasi", "direct", "phi"] = "none"
    added_by: str


class SchemaContractIn(BaseModel):
    tenant_id: str
    name: str
    fields: list[Field_]
    primary_key: list[str] = Field(min_length=1)


class DatasetIn(BaseModel):
    tenant_id: str
    name: str


class DatasetVersionIn(BaseModel):
    tenant_id: str
    dataset_id: str
    schema_id: str
    visibility_class: CLASSES
    object_manifest: list[dict] = Field(default_factory=list)
    record_count: int = 0
    produced_by_run: str | None = None
    # Literal default, not tenant-resolved: every existing fixture and
    # pipeline caller of this lower-level endpoint keeps working
    # unmodified, while a caller that wants a specific backend (verify's
    # own U57 checks, a future console flow) can still name one explicitly.
    storage_backend: Literal["seaweedfs", "r2"] = "seaweedfs"
    # Which object in `object_manifest` holds this version's records, shaped by
    # `schema_id`'s contract. When it is given, the platform also writes the
    # version as an Iceberg table (iceberg.py), so a standard tool can read it.
    # Optional, because a version made of files has no rows to put in a table,
    # and every existing caller leaves it out. The name chooses the shape.
    records_key: str | None = Field(default=None, description=(
        "The object in object_manifest that holds this version's rows, which the platform also writes as a table. Its name "
        "chooses the shape: a name ending in .parquet is a Parquet file, one ending in .ndjson or .jsonl is one JSON row per "
        "line, and any other name is a JSON list of rows. A JSON list is read whole and is limited to 32 MB; the other two are "
        "read in batches and are limited to 2 GB, so a large table is sent as one of them. Parquet columns are matched to the "
        "contract by name and must have a fitting type. Leave it out for a version made of files."))
    # Whether this version must be a table. Left out, the platform's default applies: a version that names a records
    # file is refused when its table cannot be written because of the data or a fault, and nothing is left behind.
    # `false` seals the files without a table and records why not.
    table_required: bool | None = None
    # Where the table is written. "inline" writes it while this request waits, and refuses a records file too large for
    # that. "background" hands it to a table worker and answers 202 with a job. Left out, the platform decides by the size
    # of the records file.
    table_mode: Literal["inline", "background"] | None = None


class ActionRunIn(BaseModel):
    tenant_id: str
    action_id: str
    code_hash: str
    image_digest: str
    operator: str
    idempotency_key: str
    input_versions: list[str] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)
    trigger_kind: Literal["manual", "scheduled"] = "manual"
    # A registered human for a manual run, null for a scheduled one. Validated
    # against the directory in the endpoint, not trusted here: this field says
    # what was claimed, not what is true.
    triggered_by: str | None = None
    schedule_id: str | None = None
    # Which pipeline run this step belongs to, so the steps of one run can be
    # found together afterwards. Optional: plenty of runs are started outside a
    # pipeline, and those genuinely belong to none.
    pipeline_run_id: str | None = None


class GateDecisionIn(BaseModel):
    # Required, and not defaulted. A decision to widen access to clinical data
    # with no reason recorded is exactly what the gate screen exists to stop.
    reason: str = Field(min_length=1)
    grant_roles: list[str] = Field(default_factory=list)


class PipelineRunIn(BaseModel):
    tenant_id: str
    dataset: str
    workflow_id: str
    trigger_kind: str = "manual"
    triggered_by: str | None = None
    schedule_id: str | None = None
    # Which dataset version(s) this run was actually started against, set
    # once and never updated (see pipeline_run.input_versions's comment in
    # schema.sql). Empty for a corpus run, which reads no existing version.
    input_versions: list[str] = []
    # The registered pipeline_action workload this run's task_credential.py
    # token should be minted for. Optional: a caller that never presents a
    # token back to /credentials (nothing today but this endpoint's one real
    # caller does) has no reason to name one, and gets no token back.
    principal: str | None = None


class CredentialRequest(BaseModel):
    principal: str
    principal_kind: Literal["human", "workload"]
    roles: list[str] = Field(min_length=1)
    tenant_id: str
    dataset_version_id: str
    purpose: str = ""
    agent_run_id: UUID | None = None
    # Proof of possession for an agent_runtime principal naming a real run:
    # a signed task_credential.py token, verified by signature and expiry in
    # main.py's run-scope block, not compared against a stored value. The
    # name stayed `run_secret` rather than being renamed to `task_credential`
    # to avoid touching agent/identity.py and worker/sandbox_run.py's
    # existing MUNITAS_RUN_SECRET plumbing for a change that is internal to
    # what the string contains, not what carries it.
    run_secret: str | None = None
    # The same proof-of-possession token, for any other Type A workload
    # (currently `pipeline_action`; see task_credential.py). Kept as its own
    # field rather than overloading `run_secret`, since `agent_run_id` above
    # is agent-specific and a pipeline task has no equivalent id to pair it
    # with -- the token itself carries which action_run it is.
    task_credential: str | None = None
    # Ask for the decision only, and no storage key. For a person reaching data
    # through a workspace that issues its own scoped key (the Iceberg catalog),
    # so the roles that hold no key themselves, such as analyst, are decided on
    # their real role and not refused at the key. The workspace records the
    # grant row itself once its key exists. People only.
    decide_only: bool = False


class WriteCredentialRequest(BaseModel):
    """A registered pipeline_action workload asking to write into the next
    version-location for a dataset it names.

    `roles` stays required, the same shape as CredentialRequest, but is only
    ever the unregistered-caller fallback that endpoint already documents --
    the endpoint below requires a *registered* pipeline_action workload
    before any of this is even read, so that fallback never actually fires
    here. Narrower than CredentialRequest otherwise: no dataset_version_id
    (the version being written does not exist yet, that is the point), no
    agent_run_id or run_secret, since only pipeline_action -- a Type A
    workload with a task_credential.py token -- can ever hold write access at
    all. `dataset_id` names which dataset's next version; the prefix itself
    is computed server-side from it, never taken from the caller.
    """

    principal: str
    principal_kind: Literal["human", "workload"]
    roles: list[str] = Field(min_length=1)
    tenant_id: str
    dataset_id: str
    purpose: str = ""
    task_credential: str


class LeaseRequestIn(BaseModel):
    tenant_id: str
    principal: str
    dataset_version_id: str
    purpose: str = Field(min_length=1)
    justification: str = Field(min_length=1)
    # A standing request carries no TTL, and a bounded one always does; the
    # validator below keeps the two from drifting apart before either reaches
    # the database, where the same shape is enforced as a constraint.
    standing: bool = False
    ttl_hours: int | None = Field(default=None, ge=1, le=720)

    @model_validator(mode="after")
    def _ttl_matches_standing(self) -> "LeaseRequestIn":
        if self.standing and self.ttl_hours is not None:
            raise ValueError("a standing request carries no ttl_hours")
        if not self.standing and self.ttl_hours is None:
            raise ValueError("ttl_hours is required unless the request is standing")
        return self


class LeaseRejection(BaseModel):
    # Required. A refusal without a reason tells the person who asked nothing
    # about whether to ask again, ask differently, or stop asking.
    reason: str = Field(min_length=1)


class LeaseApprovalIn(BaseModel):
    # "strict" (the only shape this platform had) covers the one purpose it
    # names, and asks again the moment that purpose changes. "simple" is the
    # custodian's own choice to trust this principal with this dataset
    # version for any purpose while the lease lasts -- never the platform's
    # default, and refused outright against RAW data regardless of what the
    # custodian wants, the same guardrail the database itself enforces.
    pattern: Literal["strict", "simple"] = "strict"


class PromotionIn(BaseModel):
    to_class: CLASSES
    decided_by: str
    decided_by_kind: Literal["human", "workload"]
    # Score card ids in MLflow. Required, and not free text: a promotion without
    # evidence is an assertion, and the promotion gate must point at a
    # measurement.
    gate_evidence: dict = Field(min_length=1)
    grant_roles: list[str] = Field(default_factory=list)


class RecordSealIn(BaseModel):
    tenant_id: str
    record_id: str
    plaintext: str


class RecordDestroyIn(BaseModel):
    tenant_id: str
    reason: str = Field(min_length=1)
    requested_by: str = Field(min_length=1)


class EndPipelineRun(BaseModel):
    """How a pipeline run ended, as its own workflow reports it."""

    status: Literal["succeeded", "failed", "cancelled", "unknown"]
    error: str | None = Field(default=None, max_length=2000)


class CatalogTokenIn(BaseModel):
    """What a person says when they ask for a token for their own tools.

    The purpose is the same sentence a lease is approved for. A table opened
    with this token is decided on it, exactly as a credential request is, so a
    token whose purpose is not the purpose of the person's lease opens nothing
    that lease covers.
    """
    purpose: str = Field(min_length=3, max_length=200)
    hours: float = Field(default=8, gt=0, le=72)


class AccessPreviewIn(BaseModel):
    dataset_ids: list[UUID] = Field(default_factory=list)
    version_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _something_to_preview(self) -> "AccessPreviewIn":
        if not self.dataset_ids and not self.version_ids:
            raise ValueError("name at least one dataset or version")
        return self


class VersionAccess(BaseModel):
    mark: Literal["removed", "role", "lease", "pending", "ended", "ask", "no_approver"]
    purpose: str | None = None
    until: datetime | None = None
    ended: Literal["expired", "revoked"] | None = None
    ended_at: datetime | None = None
    decider_label: str | None = None


class DatasetAccess(BaseModel):
    readable: int
    total: int


class AccessPreview(BaseModel):
    versions: dict[str, VersionAccess]
    datasets: dict[str, DatasetAccess]


class DerivationInput(BaseModel):
    """One dataset a derivation reads. The SQL refers to it by its alias, which
    defaults to the dataset's name with anything that is not a letter or digit
    turned into an underscore."""
    dataset: str = Field(min_length=1, max_length=120)
    version: int | None = Field(default=None, ge=1)
    alias: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class DerivationDraftIn(BaseModel):
    """A request to make a new dataset from a query, before anything is run."""
    inputs: list[DerivationInput] = Field(min_length=1, max_length=8)
    sql: str = Field(min_length=1, max_length=20000)
    target_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,80}$")
    primary_key: list[str] = Field(min_length=1, max_length=8)
    purpose: str = Field(min_length=3, max_length=200)


class DerivationConfirmIn(BaseModel):
    """The person's decision on a draft. Sensitivities may be raised and never
    lowered: a field already carries at least the sensitivity of what it was
    computed from."""
    sensitivities: dict[str, Literal["none", "quasi", "direct", "phi"]] = Field(default_factory=dict)

