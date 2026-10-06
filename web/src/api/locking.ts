/**
 * Locked columns: a table sealed under a privacy contract, who may read what was locked, and the evidence of how it was used.
 *
 * Nothing here returns a value from a table except `usePersonRows`, which returns rows only to somebody the platform has decided may
 * read them, and says how many. Everything else is counts, column names, people and dates.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

export type Classification = "none" | "quasi" | "direct" | "phi";
export type Treatment = "lock" | "readable" | "generalise" | "replaced by the person code";

export interface LockedColumn {
  name: string;
  classification: Classification;
  treatment: Treatment;
}

export interface LockedLease {
  id: string;
  principal: string;
  principal_label: string;
  dataset_version_id: string;
  purpose: string;
  days: number;
  row_limit: number;
  status: "requested" | "approved" | "declined" | "revoked";
  requested_at: string;
  decided_by: string | null;
  decided_by_label: string | null;
  decided_at: string | null;
  decision_note: string | null;
  expires_at: string | null;
  revoked_by: string | null;
  revoked_by_label: string | null;
  revoked_at: string | null;
}

export interface Locking {
  version_id: string;
  records: number;
  columns: LockedColumn[];
  /** full: every row, for the owning department's approvers. sample: the rows a lease opens. none: nothing yet. */
  mode: "full" | "sample" | "none";
  may_ask: boolean;
  lease: LockedLease | null;
  waiting: LockedLease | null;
  limits: { days: number; rows: number };
}

export interface LeaseLists {
  mine: LockedLease[];
  to_decide: LockedLease[];
  granted: LockedLease[];
}

export interface PersonRows {
  rows: Record<string, unknown>[];
  count: number;
  mode: "full" | "sample";
  lease_id: string | null;
}

export interface GroupSizeReport {
  id: string;
  dataset_version_id: string;
  threshold: number;
  columns: string[];
  people: number;
  groups: number;
  smallest_group: number | null;
  groups_below: number;
  people_in_small_groups: number;
  advice: { lock: string; people_in_small_groups_after: number }[];
  created_at: string;
  decision: "accepted" | "will_change" | null;
  decided_by: string | null;
  decided_by_label: string | null;
  decided_at: string | null;
  decision_note: string | null;
}

export interface SealedTable {
  id: string;
  version: number;
  records: number;
  people: number;
  group_size_report: GroupSizeReport;
}

export interface AuditEvidence {
  reads: {
    at: string;
    principal: string;
    principal_label: string;
    principal_roles: string[];
    dataset_version_id: string | null;
    purpose: string;
    allowed: boolean;
    reasons: string[];
    locked_column_lease_id: string | null;
  }[];
  key_uses: { at: string; person_code: string | null; action: string; actor: string; actor_label: string; purpose: string | null }[];
  leases: LockedLease[];
  contracts: {
    name: string;
    version: string;
    schema_id: string;
    sealed_under_since: string;
    treatments: LockedColumn[];
  }[];
  erasures: { at: string; record_id: string; requested_by: string; requested_by_label: string }[];
  group_sizes: GroupSizeEvidence[];
}

/** What the audit evidence says about one table's group sizes: counts and column names, never a value. */
export interface GroupSizeEvidence {
  dataset_version_id: string;
  threshold: number;
  columns: string[];
  people: number;
  groups: number;
  smallest_group: number | null;
  groups_below: number;
  people_in_small_groups: number;
  advice: GroupSizeReport["advice"];
  decision: GroupSizeReport["decision"];
  decided_by: string | null;
  decided_by_label: string | null;
  decided_at: string | null;
  created_at: string;
}

export function useLocking(versionId: string | undefined) {
  return useQuery({
    queryKey: ["locking", versionId],
    queryFn: () => api.get<Locking>(`/dataset-versions/${versionId}/locking`),
    enabled: Boolean(versionId),
    // A table that was not sealed under a privacy contract answers 404. That is the usual case, not a fault.
    retry: false,
  });
}

export function useLeases() {
  return useQuery({ queryKey: ["locked-leases"], queryFn: () => api.get<LeaseLists>("/locked-column-leases") });
}

function useRefreshLocking() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["locked-leases"] });
    queryClient.invalidateQueries({ queryKey: ["locking"] });
    queryClient.invalidateQueries({ queryKey: ["person-rows"] });
    queryClient.invalidateQueries({ queryKey: ["locking-audit"] });
  };
}

export function useRequestLease() {
  const refresh = useRefreshLocking();
  return useMutation({
    mutationFn: (body: { dataset_version_id: string; purpose: string; days: number; rows: number }) =>
      api.post<LockedLease>("/locked-column-leases", body),
    onSuccess: refresh,
  });
}

export function useDecideLease() {
  const refresh = useRefreshLocking();
  return useMutation({
    mutationFn: (body: { id: string; verb: "approve" | "decline"; note?: string }) =>
      api.post<LockedLease>(`/locked-column-leases/${body.id}/${body.verb}`, { note: body.note }),
    onSuccess: refresh,
  });
}

export function useRevokeLease() {
  const refresh = useRefreshLocking();
  return useMutation({
    mutationFn: (id: string) => api.post<LockedLease>(`/locked-column-leases/${id}/revoke`),
    onSuccess: refresh,
  });
}

export function usePersonRows(versionId: string | undefined) {
  return useQuery({
    queryKey: ["person-rows", versionId],
    queryFn: () => api.get<PersonRows>(`/dataset-versions/${versionId}/person-rows`),
    enabled: Boolean(versionId),
    retry: false,
  });
}

export function useGroupSizeReport(versionId: string | undefined) {
  return useQuery({
    queryKey: ["group-size-report", versionId],
    queryFn: () => api.get<GroupSizeReport>(`/dataset-versions/${versionId}/group-size-report`),
    enabled: Boolean(versionId),
    retry: false,
  });
}

export function useDecideGroupSize(versionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { decision: "accepted" | "will_change"; note: string }) =>
      api.post<GroupSizeReport>(`/dataset-versions/${versionId}/group-size-report/decision`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["group-size-report", versionId] });
      queryClient.invalidateQueries({ queryKey: ["locking-audit"] });
    },
  });
}

export function useLockingAudit(enabled: boolean) {
  return useQuery({
    queryKey: ["locking-audit"],
    queryFn: () => api.get<AuditEvidence>("/audit/locked-columns", { limit: 200 }),
    enabled,
    retry: false,
  });
}

/** Seal a table from two files: the privacy contract (YAML or JSON) and the rows (a CSV with a header). */
export function useSealTableFiles(datasetId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ contract, rows }: { contract: File; rows: File }) => {
      const form = new FormData();
      form.append("contract", contract);
      form.append("rows", rows);
      return api.postForm<SealedTable>(`/datasets/${datasetId}/seal-table-files`, form);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["versions"] });
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
}
