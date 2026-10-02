/**
 * Producing an organisation's records for a legal matter.
 *
 * The console offers every step and enforces none of them: the policy engine refuses a caller regardless of which
 * buttons this draws, and the reason it gives is what the screen shows. Three different people are needed (a
 * platform administrator asks, a different one approves, the custodian the hold names confirms the scope), and
 * none of them reads the contents. The package is encrypted, and only the custodian is given its passphrase.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

export type ExportStatus =
  | "requested" | "approved" | "confirmed" | "producing" | "ready" | "expired" | "refused" | "failed";

export interface LegalExport {
  id: string;
  tenant_id: string;
  hold_id: string;
  status: ExportStatus;
  demand_authority: string;
  demand_reference: string;
  demanded_on: string;
  demand_text: string;
  dataset_ids: string[];
  include_audit: boolean;
  /** The tables that are filtered to the rows for named people, and by which column. The people are never shown here. */
  filters: { dataset_id: string; column: string }[];
  /** After production: for each filtered dataset, how many rows matched, out of how many. Counts only. */
  filter_results: Record<string, { column: string; values_given: number; rows_matched: number; rows_total: number; values_unmatched: number }> | null;
  recipient_name: string;
  recipient_organisation: string;
  recipient_email: string;
  requested_by: string;
  requested_by_label: string;
  requested_at: string;
  approved_by_label: string | null;
  approved_at: string | null;
  approval_note: string | null;
  confirmed_by_label: string | null;
  confirmed_at: string | null;
  confirm_note: string | null;
  refused_at: string | null;
  refusal_reason: string | null;
  produced_at: string | null;
  failure: string | null;
  package_bytes: number | null;
  package_sha256: string | null;
  manifest_sha256: string | null;
  file_count: number | null;
  passphrase_revealed_at: string | null;
  expires_at: string | null;
  expired_at: string | null;
  matter_number: string;
  matter_name: string;
  custodian_id: string;
  links_made: number;
}

export interface ScopeDataset {
  id: string;
  name: string;
  versions: number;
  bytes: number;
  /** True when every sealed version is stored as a table, which is what a filter needs. */
  tabular: boolean;
  /** The plain columns that can identify a row, common to every version. Null when it is not a table. */
  columns: string[] | null;
}

export interface FilterPreview {
  dataset_id: string;
  column: string;
  values_given: number;
  rows_matched: number;
  rows_total: number;
  unmatched_values: string[];
}

export interface NewExport {
  hold_id: string;
  demand_authority: string;
  demand_reference: string;
  demanded_on: string;
  demand_text: string;
  dataset_ids: string[];
  include_audit: boolean;
  recipient_name: string;
  recipient_organisation: string;
  recipient_email: string;
  filters: { dataset_id: string; column: string }[];
}

export interface ManifestFile {
  path: string;
  dataset: string;
  version: number;
  bytes: number;
  sha256: string;
}

const KEY = ["legal-exports"] as const;

export function useExports(enabled = true) {
  return useQuery({
    queryKey: [...KEY, "list"],
    queryFn: () => api.get<{ exports: LegalExport[] }>("/legal-exports"),
    enabled,
    refetchInterval: 10_000,
  });
}

export function useScope(holdId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...KEY, "scope", holdId],
    queryFn: () => api.get<{ datasets: ScopeDataset[] }>("/legal-exports/scope", { hold_id: holdId }),
    enabled,
  });
}

export function useManifest(exportId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...KEY, "manifest", exportId],
    queryFn: () => api.get<{ files: ManifestFile[]; manifest_sha256: string | null }>(`/legal-exports/${exportId}/manifest`),
    enabled,
  });
}

function useRefresh() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: KEY });
}

export function useRequestExport() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (body: NewExport) => api.post<LegalExport>("/legal-exports", body),
    onSuccess: refresh,
  });
}

export function useApproveExport(id: string) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (body: { approve: boolean; note: string }) => api.post<LegalExport>(`/legal-exports/${id}/approve`, body),
    onSuccess: refresh,
  });
}

export function useConfirmExport(id: string) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (body: { approve: boolean; note: string; values?: Record<string, string[]> }) =>
      api.post<LegalExport>(`/legal-exports/${id}/confirm`, body),
    onSuccess: refresh,
  });
}

/** Counts only: how many rows each filter would match, and which values matched none. Never the rows. */
export function useFilterPreview(id: string) {
  return useMutation({
    mutationFn: (values: Record<string, string[]>) =>
      api.post<{ results: FilterPreview[] }>(`/legal-exports/${id}/filter-preview`, { values }),
  });
}

export function useReadPassphrase(id: string) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: () => api.post<{ passphrase: string; note: string }>(`/legal-exports/${id}/passphrase`),
    onSuccess: refresh,
  });
}

export function useMakeLink(id: string) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: () =>
      api.post<{ token: string; download_path: string; expires_at: string; uses: number; note: string }>(
        `/legal-exports/${id}/links`,
      ),
    onSuccess: refresh,
  });
}

/** What each step means to the people it affects, in plain words. */
export const EXPORT_COPY: Record<ExportStatus, { label: string; tone: string }> = {
  requested: { label: "Waiting for a second administrator", tone: "bg-amber-100 text-amber-900" },
  approved: { label: "Waiting for the custodian to confirm the scope", tone: "bg-amber-100 text-amber-900" },
  confirmed: { label: "Being prepared", tone: "bg-sky-100 text-sky-900" },
  producing: { label: "Being prepared", tone: "bg-sky-100 text-sky-900" },
  ready: { label: "Ready", tone: "bg-green-100 text-green-900" },
  expired: { label: "Deleted after its retention", tone: "bg-slate-200 text-slate-800" },
  refused: { label: "Refused", tone: "bg-slate-200 text-slate-800" },
  failed: { label: "Could not be produced", tone: "bg-red-100 text-red-900" },
};
