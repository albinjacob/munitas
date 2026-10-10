/**
 * Permission to open a few of a version's real files: asked for, decided by an approver of the department that owns them, and in force for a few hours.
 *
 * The files are chosen by the platform when the request is approved and never change. While it is in force the version's page lists and opens exactly
 * those files; when it ends or runs out they close again, at the next request.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

export interface FileLease {
  id: string;
  dataset_version_id: string;
  dataset_name: string | null;
  dataset_version: number | null;
  principal: string;
  principal_label: string;
  /** console: listening in the page. notebook: reading through the client library. */
  kind: "console" | "notebook";
  purpose: string;
  requested_count: number;
  hours: number;
  status: "requested" | "approved" | "declined" | "ended";
  granted_count: number | null;
  granted_hours: number | null;
  /** The files it opens, fixed when it was approved. */
  records: string[];
  decided_by_label: string | null;
  decided_at: string | null;
  decision_note: string | null;
  expires_at: string | null;
  ended_by_label: string | null;
  ended_at: string | null;
  requested_at: string;
}

export interface FileLeaseLists {
  mine: FileLease[];
  to_decide: FileLease[];
  granted: FileLease[];
}

export interface VersionFileLease {
  version_id: string;
  leases: FileLease[];
  /** The ways the files can be opened that can be asked for now. */
  kinds: ("console" | "notebook")[];
  caps: Record<string, { records: number; hours: number }>;
  has_approver: boolean;
  approver_count: number;
}

export function useFileLeases() {
  return useQuery({ queryKey: ["file-leases"], queryFn: () => api.get<FileLeaseLists>("/file-leases") });
}

export function useVersionFileLease(versionId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ["version-file-lease", versionId],
    queryFn: () => api.get<VersionFileLease>(`/dataset-versions/${versionId}/file-lease`),
    enabled: Boolean(versionId) && enabled,
    retry: false,
  });
}

function useRefresh(versionId?: string) {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["file-leases"] });
    queryClient.invalidateQueries({ queryKey: ["version-file-lease"] });
    queryClient.invalidateQueries({ queryKey: ["record-files", versionId] });
    queryClient.invalidateQueries({ queryKey: ["record-files"] });
  };
}

export function useRequestFileLease(versionId: string) {
  const refresh = useRefresh(versionId);
  return useMutation({
    mutationFn: (body: { kind: "console" | "notebook"; purpose: string; records: number; hours: number }) =>
      api.post<FileLease>(`/dataset-versions/${versionId}/file-leases`, body),
    onSuccess: refresh,
  });
}

export function useDecideFileLease() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (v: { id: string; verb: "approve" | "decline"; note?: string; records?: number; hours?: number }) =>
      api.post<FileLease>(`/file-leases/${v.id}/${v.verb}`, { note: v.note, records: v.records, hours: v.hours }),
    onSuccess: refresh,
  });
}

export function useEndFileLease() {
  const refresh = useRefresh();
  return useMutation({ mutationFn: (id: string) => api.post<FileLease>(`/file-leases/${id}/end`), onSuccess: refresh });
}
