/**
 * Development samples: the few files a pipeline version in development may run on.
 *
 * A person asks for a sample of a version of locked files, an approver of the department that owns them decides, and the platform draws it.
 * A sample names the same stored files as the original and copies none. Nothing here returns a file or says who a patient is: only
 * counts, band by band, people who asked and decided, and dates.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

export interface Strata {
  band: string;
  pool: number;
  chosen: number;
}

export interface DevelopmentSample {
  id: string;
  source_version_id: string;
  sample_version_id: string | null;
  principal: string;
  principal_label: string;
  kind: "develop" | "debug";
  purpose: string;
  requested_count: number;
  granted_count: number | null;
  days: number;
  granted_days: number | null;
  named_records: string[] | null;
  status: "requested" | "approved" | "declined" | "ended";
  decided_by: string | null;
  decided_by_label: string | null;
  decided_at: string | null;
  decision_note: string | null;
  expires_at: string | null;
  ended_by: string | null;
  ended_by_label: string | null;
  ended_at: string | null;
  strata: Strata[] | null;
  requested_at: string;
}

export interface DevelopmentSamples {
  version_id: string;
  /** Set when this version is itself a sample: whose it is, where it came from and when it ends. */
  is_sample: { source_version_id: string; kind: "develop" | "debug"; status: string; expires_at: string | null; records: number | null; mine: boolean } | null;
  synthetic: { declared_by_label: string; note: string; declared_at: string } | null;
  may_ask: boolean;
  may_ask_kinds: ("develop" | "debug")[];
  may_decide: boolean;
  may_declare_synthetic: boolean;
  mine: DevelopmentSample[];
  to_decide: DevelopmentSample[];
  granted: DevelopmentSample[];
  limits: { days: number; records: number };
}

export function useDevelopmentSamples(versionId: string | undefined) {
  return useQuery({
    queryKey: ["development-samples", versionId],
    queryFn: () => api.get<DevelopmentSamples>(`/dataset-versions/${versionId}/development-samples`),
    enabled: Boolean(versionId),
    retry: false,
  });
}

function useRefresh() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["development-samples"] });
  };
}

export function useAskForSample(versionId: string) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (body: { kind: "develop" | "debug"; purpose: string; days: number; records: number; named_records?: string[] }) =>
      api.post<DevelopmentSample>(`/dataset-versions/${versionId}/development-samples`, body),
    onSuccess: refresh,
  });
}

export function useDecideSample() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (body: { id: string; verb: "approve" | "decline"; note?: string; records?: number; days?: number }) =>
      api.post<DevelopmentSample>(`/development-samples/${body.id}/${body.verb}`, { note: body.note, records: body.records, days: body.days }),
    onSuccess: refresh,
  });
}

export function useEndSample() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (id: string) => api.post<DevelopmentSample>(`/development-samples/${id}/end`),
    onSuccess: refresh,
  });
}

export function useDeclareSynthetic(versionId: string) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (note: string) => api.post(`/dataset-versions/${versionId}/declare-synthetic`, { note }),
    onSuccess: refresh,
  });
}
