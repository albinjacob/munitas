/** Shapes returned by the control plane. */

export type VisibilityClass =
  | "RAW"
  | "UNDER_REVIEW"
  | "OPEN_FOR_ANNOTATION"
  | "OPEN_FOR_TRAINING"
  | "PUBLISHED";

/**
 * Ordered least visible first, matching `class_order` in access.rego.
 *
 * The console must never invent its own ordering. If this disagreed with the
 * policy, a screen could show one class as safer than another while the engine
 * treated them the other way round, and nobody would notice until an audit.
 */
export const CLASS_ORDER: VisibilityClass[] = [
  "RAW",
  "UNDER_REVIEW",
  "OPEN_FOR_ANNOTATION",
  "OPEN_FOR_TRAINING",
  "PUBLISHED",
];

export const CLASS_LABEL: Record<VisibilityClass, string> = {
  RAW: "Raw",
  UNDER_REVIEW: "Under review",
  OPEN_FOR_ANNOTATION: "Open for annotation",
  OPEN_FOR_TRAINING: "Open for training",
  PUBLISHED: "Published",
};

export interface Summary {
  datasets: number;
  versions: number;
  promotions: number;
  pending_requests: number;
  active_leases: number;
  decisions: number;
  denials: number;
  tombstones: number;
  versions_by_class: Partial<Record<VisibilityClass, number>>;
}

export interface DatasetRow {
  id: string;
  name: string;
  tenant_id: string;
  created_at: string;
  version_count: number;
  latest_version: number | null;
  widest_class: VisibilityClass | null;
  modality: string[] | null;
  provenance: string;
  department_id: string | null;
  /** What a HuggingFace fetch found; null for anything uploaded by hand. */
  license_tag: string | null;
  license_export_unmodified: boolean | null;
  license_export_modified: boolean | null;
  /** Set only when a fetched licence corrected what was registered. */
  provenance_registered_as: string | null;
  provenance_overridden_at: string | null;
}

/** One dataset's own record. What `GET /datasets/{id}` returns: the same
 * row `ingest.py`'s internal `_dataset()` already reads, not a second
 * shape invented for the console. */
export interface DatasetDetail {
  id: string;
  tenant_id: string;
  name: string;
  department_id: string | null;
  department_name: string | null;
  custodian: string | null;
  provenance: string;
  declared_class: VisibilityClass;
}

export interface VersionRow {
  dataset_version_id: string;
  dataset_id: string;
  dataset_name?: string;
  tenant_id: string;
  version: number;
  storage_prefix: string;
  sealed_class: VisibilityClass;
  current_class: VisibilityClass;
  class_changed_at: string | null;
  class_decided_by: string | null;
  content_hash?: string;
  record_count?: number;
  created_at?: string;
  sealed?: boolean;
  /**
   * When the stored files were deleted to free space, or null.
   *
   * The version is still a version: its record, its release history and its
   * lineage are all intact. Only the data itself is gone. A screen that ignored
   * this would offer to open something that is no longer there.
   */
  reclaimed_at?: string | null;
}

export interface Transition {
  id: string;
  from_class: VisibilityClass;
  to_class: VisibilityClass;
  decided_by: string;
  decided_by_kind: "human" | "workload";
  gate_evidence: Record<string, unknown>;
  at: string;
}

export interface Lineage {
  dataset_version_id: string;
  dataset_name: string;
  version: number;
  visibility_class: VisibilityClass;
  content_hash: string;
  run_id: string | null;
  action_name: string | null;
  code_hash: string | null;
  image_digest: string | null;
  operator: string | null;
  input_versions: string[] | null;
  started_at: string | null;
  ended_at: string | null;
}

export interface AccessDecision {
  id: number;
  at: string;
  principal: string;
  principal_kind: "human" | "workload";
  principal_roles: string[];
  tenant_id: string | null;
  dataset_version_id: string | null;
  requested_class: VisibilityClass | null;
  purpose: string | null;
  allowed: boolean;
  reasons: string[];
  lease_id: string | null;
  /**
   * Which question this row answers.
   *
   * `policy` is what the engine decided. `grant` is whether the access it
   * authorised could actually be applied. They can disagree, and a log that
   * recorded only the first once said "allowed" for requests where nothing
   * was granted at all.
   */
  phase: "policy" | "grant";
  agent_run_id: string | null;
  /**
   * On an allowed `grant` row only: whether the access is in effect yet.
   * False while the platform is still updating storage permissions, which it
   * finishes by itself. Null on every other row.
   */
  active: boolean | null;
}

export interface LeaseRequest {
  id: string;
  tenant_id: string;
  principal: string;
  /**
   * Set only when a human requested this on a workload's behalf. `principal`
   * stays the reading identity regardless; this is provenance, never a second
   * way to say who is acting.
   */
  requested_by: string | null;
  requested_by_label: string | null;
  dataset_version_id: string;
  dataset_name: string | null;
  version: number | null;
  current_class: VisibilityClass | null;
  purpose: string;
  justification: string;
  /** A standing request carries no TTL; see `standing`. */
  requested_ttl_hours: number | null;
  /** Asks for a lease that lasts until revoked, only ever for a workload. */
  standing: boolean;
  state: "pending" | "approved" | "rejected";
  decided_by: string | null;
  decided_at: string | null;
  lease_id: string | null;
  created_at: string;
}

export interface Lease {
  id: string;
  tenant_id: string;
  principal: string;
  requested_by: string | null;
  requested_by_label: string | null;
  dataset_version_id: string;
  dataset_name: string | null;
  version: number | null;
  current_class: VisibilityClass | null;
  purpose: string;
  /**
   * "strict" (every lease before this field existed) covers only its own
   * purpose; "simple" is the custodian's own choice, made at approval, to
   * cover any purpose for as long as the lease lasts. Never "simple" against
   * RAW data -- the database refuses to create that combination.
   */
  pattern: "strict" | "simple";
  approved_by: string;
  revoked: boolean;
  /** Null means standing: no expiry, active until revoked. */
  expires_at: string | null;
  created_at: string;
  active: boolean;
  standing: boolean;
}

export interface ActionRun {
  id: string;
  status: "running" | "succeeded" | "failed";
  operator: string;
  code_hash: string;
  image_digest: string;
  started_at: string;
  ended_at: string | null;
  output_version: string | null;
  input_versions: string[];
  action_name: string;
  output_class: VisibilityClass | null;
  trigger_kind: "manual" | "scheduled";
  triggered_by: string | null;
  triggered_by_label: string | null;
  schedule_id: string | null;
}

/** What the platform will let the signed-in person do with a version. */
export type AccessMarkKind =
  | "removed"
  | "role"
  | "lease"
  | "pending"
  | "ended"
  | "ask"
  | "no_approver";

export interface VersionAccess {
  mark: AccessMarkKind;
  purpose: string | null;
  until: string | null;
  ended: "expired" | "revoked" | null;
  ended_at: string | null;
  decider_label: string | null;
}

export interface AccessPreview {
  versions: Record<string, VersionAccess>;
  datasets: Record<string, { readable: number; total: number }>;
}
