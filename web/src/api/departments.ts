/**
 * A department's approvers: the data custodians who may approve access to its data and confirm claims about it.
 *
 * The platform decides who may change the list and refuses with its reasons, which the forms show. The list a person can choose from is
 * only a convenience: somebody who holds the Data custodian role through an approved request and not by being set up with it can still be added,
 * and the platform checks the role itself.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

export interface ApproverRow {
  person_id: string;
  label: string;
  added_by: string;
  added_by_label: string | null;
  added_at: string;
  /** Set for temporary cover, which ends by itself. */
  valid_until: string | null;
  reason: string;
}

export interface ApproverHistoryRow extends ApproverRow {
  removed_by: string | null;
  removed_by_label: string | null;
  removed_at: string | null;
  removal_reason: string | null;
}

/** The history of one department's approvers. Only asked for by an approver or the data protection officer; the platform refuses anybody else. */
export function useApproverHistory(departmentId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["department-approvers", departmentId, "history"],
    queryFn: () =>
      api.get<{ history: ApproverHistoryRow[] }>(`/departments/${departmentId}/approvers`, { history: true }),
    enabled,
  });
}

function useRefreshApprovers() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["organisation"] });
    queryClient.invalidateQueries({ queryKey: ["department-approvers"] });
    queryClient.invalidateQueries({ queryKey: ["awaiting-confirmation"] });
  };
}

export function useAddApprover() {
  const refresh = useRefreshApprovers();
  return useMutation({
    mutationFn: ({
      departmentId,
      personId,
      reason,
      validUntil,
    }: {
      departmentId: string;
      personId: string;
      reason: string;
      /** An ISO moment; leave out for a permanent approver. */
      validUntil?: string;
    }) =>
      api.post(`/departments/${departmentId}/approvers`, {
        person_id: personId,
        reason,
        valid_until: validUntil,
      }),
    onSuccess: refresh,
  });
}

export function useRemoveApprover() {
  const refresh = useRefreshApprovers();
  return useMutation({
    mutationFn: ({ departmentId, personId, reason }: { departmentId: string; personId: string; reason: string }) =>
      api.post(`/departments/${departmentId}/approvers/${personId}/remove`, { reason }),
    onSuccess: refresh,
  });
}
