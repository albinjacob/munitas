/**
 * Agent egress approvals: which external hosts a version may call, and who
 * decided.
 *
 * Kept out of `agents.ts`, the same reason `gates.ts` is kept out of
 * `queries.ts`: this is its own decision, made by its own role
 * (`network_architect`), not a fact about the agent itself.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

export interface EgressApprovalRow {
  id: string;
  tenant_id: string;
  agent_id: string;
  agent_version_id: string;
  agent_name: string;
  version: number;
  requested_hosts: string[];
  state: "pending" | "approved" | "refused";
  submitted_by: string;
  submitted_by_label: string | null;
  decided_by: string | null;
  decided_by_label: string | null;
  decision_reason: string | null;
  created_at: string;
}

export interface EgressApprovalDetail extends EgressApprovalRow {
  model_id: string;
  tool_scope: string[];
}

export interface EgressApprovalPage {
  egress_approvals: EgressApprovalRow[];
  shown: number;
  total: number;
  limit: number;
  offset: number;
}

export function useEgressApprovals(state?: string, limit = 15, offset = 0) {
  return useQuery({
    queryKey: ["egress-approvals", state, limit, offset],
    queryFn: () =>
      api.get<EgressApprovalPage>("/egress-approvals", { state, limit, offset }),
  });
}

export function useEgressApproval(approvalId: string | undefined) {
  return useQuery({
    queryKey: ["egress-approval", approvalId],
    queryFn: () => api.get<EgressApprovalDetail>(`/egress-approvals/${approvalId}`),
    enabled: Boolean(approvalId),
  });
}

export function useDecideEgress(approvalId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { outcome: "approve" | "refuse"; reason: string }) =>
      api.post<{ decided: boolean; state: string }>(
        `/egress-approvals/${approvalId}/${body.outcome}`,
        { reason: body.reason },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["egress-approval", approvalId] });
      queryClient.invalidateQueries({ queryKey: ["egress-approvals"] });
      // Deploy eligibility for the version this decided is shown on the
      // agent screen too.
      queryClient.invalidateQueries({ queryKey: ["agent"] });
    },
  });
}
