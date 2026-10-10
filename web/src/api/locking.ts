/**
 * Locked columns: a table sealed under a privacy contract, who may read what was locked, and the evidence of how it was used.
 *
 * Nothing here returns a value from a table except `usePersonRows`, which returns rows only to somebody the platform has decided may
 * read them, and says how many. Everything else is counts, column names, people and dates.
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
  /** The table this lease is about, by name and version number. */
  dataset_name?: string | null;
  dataset_version?: number | null;
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
  /** How many rows are on this page. */
  count: number;
  mode: "full" | "sample";
  lease_id: string | null;
  /** Where the next page starts, or null at the end of the table. */
  next: string | null;
  /** How many rows the whole table holds. */
  total: number;
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
  /** How many people the rows are about; null for an ordinary table, which is about nobody. */
  people: number | null;
  group_size_report: GroupSizeReport | null;
  /** True for an ordinary table: nothing in it is locked. */
  plain?: boolean;
}

export interface AuditEvidence {
  reads: {
    at: string;
    principal: string;
    principal_label: string;
    principal_roles: string[];
    dataset_version_id: string | null;
    table: string | null;
    purpose: string;
    allowed: boolean;
    reasons: string[];
    locked_column_lease_id: string | null;
  }[];
  /** `reason` says in words why a key was opened, and `table` which table when that can be found. The platform's own text is never passed on, because it can name a file. */
  key_uses: { at: string; person_code: string | null; action: string; actor: string; actor_label: string; reason: string | null; table: string | null; file: string | null }[];
  leases: LockedLease[];
  contracts: {
    name: string;
    version: string;
    schema_id: string;
    /** The latest tables sealed under this contract, by name and version, and how many there are in all. */
    tables: string[];
    table_count: number;
    sealed_under_since: string;
    treatments: LockedColumn[];
  }[];
  erasures: { at: string; record_id: string; requested_by: string; requested_by_label: string }[];
  group_sizes: GroupSizeEvidence[];
  /** Every opening of a locked file, and every refusal to open a file of a version that holds locked files. Never a file name or identifier. */
  file_reads: {
    at: string;
    principal_label: string;
    table: string | null;
    allowed: boolean;
    basis: string | null;
    via: string | null;
    lease_id: string | null;
    reason: string | null;
    /** The file, by name, for the privacy auditor and the data protection officer. Null for a refusal recorded before refusals named the file. */
    file: string | null;
  }[];
  file_leases: {
    id: string;
    requested_at: string;
    principal_label: string;
    kind: "console" | "notebook";
    purpose: string;
    requested_count: number;
    hours: number;
    status: "requested" | "approved" | "declined" | "ended";
    granted_count: number | null;
    granted_hours: number | null;
    decided_by_label: string | null;
    decided_at: string | null;
    ended_by_label: string | null;
    ended_at: string | null;
    expires_at: string | null;
    dataset_name: string | null;
    dataset_version: number | string | null;
  }[];
}

/** What the audit evidence says about one table's group sizes: counts and column names, never a value. */
export interface GroupSizeEvidence {
  dataset_version_id: string;
  table: string | null;
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

/** One page of a table's locked rows. `cursor` is where the page starts: null for the first, and the previous page's `next` for each after it. */
export function usePersonRows(versionId: string | undefined, cursor: string | null = null) {
  return useQuery({
    queryKey: ["person-rows", versionId, cursor],
    queryFn: () => api.get<PersonRows>(`/dataset-versions/${versionId}/person-rows`, cursor ? { cursor } : undefined),
    enabled: Boolean(versionId),
    retry: false,
    placeholderData: keepPreviousData,
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

/** One list of the locked data audit, a page at a time. */
export type AuditSection = "reads" | "column_leases" | "file_reads" | "file_leases" | "keys" | "erasures" | "small_groups" | "contracts";

export interface AuditPage<T> {
  section: AuditSection;
  rows: T[];
  /** How many lines match the filters, not only the ones on this page. */
  total: number;
  page: number;
  page_size: number;
  pages: number;
  /** The filters this list takes (person, table, date, result, q). The others are ignored for it. */
  can_filter: string[];
  results: string[];
}

export interface AuditQuery {
  person?: string;
  table?: string;
  file?: string;
  date_from?: string;
  date_to?: string;
  result?: string;
  q?: string;
  page: number;
  page_size?: number;
}

export function useAuditPage<T>(section: AuditSection, query: AuditQuery) {
  return useQuery({
    queryKey: ["locked-data-audit", section, query],
    queryFn: () => api.get<AuditPage<T>>("/audit/locked-columns", { section, ...query }),
    placeholderData: keepPreviousData,
    retry: false,
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
    mutationFn: ({ contract, rows, options }: { contract: File; rows: File; options?: { delimiter?: string; encoding?: string; header?: boolean; null_values?: string[] } }) => {
      const form = new FormData();
      form.append("contract", contract);
      form.append("rows", rows);
      if (options?.delimiter) form.append("delimiter", options.delimiter);
      if (options?.encoding) form.append("encoding", options.encoding);
      if (options?.header === false) form.append("header", "false");
      if (options?.null_values?.length) form.append("null_values", options.null_values.join(","));
      return api.postForm<SealedTable>(`/datasets/${datasetId}/seal-table-files`, form);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["versions"] });
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
}
