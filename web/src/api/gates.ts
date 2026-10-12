/**
 * The de-identification gate: reading a decision and making one.
 *
 * Kept out of queries.ts because a gate decision is the one read in this
 * console that returns unredacted personal data. The leak list holds the
 * identifiers that escaped redaction, which is exactly what the reviewer has
 * to look at to judge severity, and keeping that in its own module makes the
 * boundary something a reader can see rather than infer.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { VisibilityClass } from "./types";

/** One identifier that survived redaction, with where it came from. */
export interface GateLeak {
  record_id: string;
  span_start: number;
  span_end: number;
  entity: string;
  identifier: string;
  left_in_the_clear: string;
  /** Postgres numeric arrives as a string. */
  coverage: string | number;
  direct: boolean;
  /** Found by a practice run on files with known contents, not in the batch this decision is about. */
  practice: boolean;
}

/** One step of the pipeline run that produced this decision. */
export interface GateStep {
  id: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  action_name: string;
  /** The step's own name in the pipeline, when it has one. */
  step_name: string | null;
}

/** The run that made a result, and what it was made with. A built-in pipeline has no uploaded version, so those parts are null. */
export interface GateRun {
  id: string;
  trigger_kind: string | null;
  started_at: string;
  ended_at: string | null;
  triggered_by_label: string | null;
  pipeline_id: string | null;
  pipeline_name: string | null;
  pipeline_version: number | null;
  uploaded_at: string | null;
  uploaded_by_label: string | null;
  approved_at: string | null;
  approved_by_label: string | null;
  source_version_id: string | null;
  source_dataset_name: string | null;
  source_version: number | null;
}

export interface GateRow {
  id: string;
  tenant_id: string;
  dataset_version_id: string;
  dataset_name: string;
  version: number;
  visibility_class: VisibilityClass;
  to_class: VisibilityClass;
  score_card_id: string;
  metrics: Record<string, number>;
  recommendation: "pass" | "fail" | "not_measured";
  recommendation_reason: string;
  state: "pending" | "promoted" | "refused";
  triggered_by: string | null;
  triggered_by_label: string | null;
  decided_by: string | null;
  decided_by_label: string | null;
  /** When it was decided, or null while it waits. */
  decided_at: string | null;
  decision_reason: string | null;
  /** The de-identification standard the result was judged by when it was decided, and what that standard asked then. Empty on a decision made before standards were chosen. */
  standard_name: string | null;
  standard_version: number | null;
  limits_used: Record<string, unknown> | null;
  release_recommendation: "pass" | "fail" | "not_measured" | null;
  release_reason: string | null;
  created_at: string;
  pipeline_run_id: string | null;
  leak_count: number;
  /** Who may decide this now, other than whoever started the run. Empty once it is decided. */
  waiting_on: { id: string; label: string }[];
  /** Whether the person asking may decide it. */
  can_decide: boolean;
}

export interface GateDetail extends Omit<GateRow, "leak_count"> {
  storage_prefix: string;
  run: GateRun | null;
  leaks: GateLeak[];
  steps: GateStep[];
}

/** One check the platform ran on a file before anyone looked. A blocking check that fails stops a release. */
export interface ReviewCheck {
  check: string;
  ok: boolean;
  blocking: boolean;
  detail: string;
}

export interface ReviewFile {
  record_id: string;
  bytes: number;
  locked: boolean;
  content_type: string;
  checks: ReviewCheck[];
}

/** The files a result holds, the checks on each, what the caller has opened and what stops them releasing it now. */
export interface ReviewEvidence {
  decision_id: string;
  version_id: string;
  state: GateRow["state"];
  files: ReviewFile[];
  checks_failed: number;
  opened: string[];
  limit: number;
  may_review: boolean;
  blockers: string[];
  not_allowed: string[];
  recorded: { outcome: string; files: number; checks_failed: number; opened: string[]; limit: number } | null;
}

export interface GateDecisionPage {
  gate_decisions: GateRow[];
  shown: number;
  total: number;
  limit: number;
  offset: number;
}

export function useGateDecisions(
  tenantId: string | undefined,
  reviewer?: string,
  limit = 15,
  offset = 0,
  state?: string,
  startedBy?: string,
) {
  return useQuery({
    queryKey: ["gate-decisions", tenantId, reviewer, limit, offset, state, startedBy],
    queryFn: () =>
      api.get<GateDecisionPage>("/gate-decisions", { reviewer, started_by: startedBy, limit, offset, state }),
    // Who a result waits on changes when roles change, so it is looked at again, not remembered.
    refetchInterval: 30_000,
    // Scoped server-side to the caller's own tenant now; `tenantId` still
    // gates the query itself (nothing to fetch before identity resolves),
    // it just no longer travels as a query parameter nothing reads.
    enabled: Boolean(tenantId),
  });
}

export function useGateDecision(decisionId: string | undefined) {
  return useQuery({
    queryKey: ["gate-decision", decisionId],
    queryFn: () => api.get<GateDetail>(`/gate-decisions/${decisionId}`),
    enabled: Boolean(decisionId),
  });
}

export function useReview(decisionId: string | undefined, standardId: string | null = null) {
  return useQuery({
    queryKey: ["gate-review", decisionId, standardId],
    queryFn: () => api.get<ReviewEvidence>(`/gate-decisions/${decisionId}/review`, standardId ? { standard_id: standardId } : undefined),
    enabled: Boolean(decisionId),
    retry: false,
  });
}

export function useDecideGate(decisionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { outcome: "promote" | "refuse"; reason: string; standardId?: string | null }) =>
      api.post<{ decided: boolean; state: string }>(
        `/gate-decisions/${decisionId}/${body.outcome}`,
        { reason: body.reason, standard_id: body.standardId ?? null },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["gate-decision", decisionId] });
      queryClient.invalidateQueries({ queryKey: ["gate-review", decisionId] });
      queryClient.invalidateQueries({ queryKey: ["gate-decisions"] });
      // The class it moves to is shown on the version screen too.
      queryClient.invalidateQueries({ queryKey: ["versions"] });
    },
  });
}
