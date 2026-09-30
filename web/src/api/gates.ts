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
}

/** One step of the pipeline run that produced this decision. */
export interface GateStep {
  id: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  action_name: string;
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
  recommendation: "pass" | "fail";
  recommendation_reason: string;
  state: "pending" | "promoted" | "refused";
  triggered_by: string | null;
  triggered_by_label: string | null;
  decided_by: string | null;
  decided_by_label: string | null;
  decision_reason: string | null;
  created_at: string;
  pipeline_run_id: string | null;
  leak_count: number;
}

export interface GateDetail extends Omit<GateRow, "leak_count"> {
  storage_prefix: string;
  leaks: GateLeak[];
  steps: GateStep[];
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
) {
  return useQuery({
    queryKey: ["gate-decisions", tenantId, reviewer, limit, offset, state],
    queryFn: () =>
      api.get<GateDecisionPage>("/gate-decisions", { reviewer, limit, offset, state }),
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

export function useDecideGate(decisionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { outcome: "promote" | "refuse"; reason: string }) =>
      api.post<{ decided: boolean; state: string }>(
        `/gate-decisions/${decisionId}/${body.outcome}`,
        { reason: body.reason },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["gate-decision", decisionId] });
      queryClient.invalidateQueries({ queryKey: ["gate-decisions"] });
      // The class it moves to is shown on the version screen too.
      queryClient.invalidateQueries({ queryKey: ["versions"] });
    },
  });
}
