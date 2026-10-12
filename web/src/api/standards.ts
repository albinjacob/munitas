/**
 * De-identification standards: the limits an organisation holds a de-identified result to, kept under names, each change a new version.
 *
 * Anybody signed in may read them. Only the data protection officer sets one, and the platform refuses the rest. The standard a result is released
 * under is chosen by the person who decides it, and the platform works out the recommendation under that standard again at that moment.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

/** One kind of identifier and the limits a standard may put on it. Either limit may be empty: that limit is then not applied. */
export interface KindLimit {
  recall: number | null;
  max_leaks: number | null;
}

export interface StandardLimits {
  /** The least share of identifiers that survived transcription and were removed, over all kinds together. Empty for no overall limit. */
  overall_recall: number | null;
  /** Limits by kind of identifier. A kind that is not here has no limit of its own. */
  types: Record<string, KindLimit>;
}

export interface Standard {
  /** Empty for the platform's own built-in standard. */
  id: string | null;
  name: string;
  /** Empty for the built-in standard. */
  version: number | null;
  limits: StandardLimits;
  reason: string | null;
  set_by: string | null;
  set_by_label: string | null;
  set_at: string | null;
  built_in: boolean;
  versions: number;
}

export interface Kind {
  code: string;
  label: string;
  group: string;
}

export interface Standards {
  standards: Standard[];
  platform_default: Standard;
  kinds: Kind[];
  /** Labels a run keeps its counts under that stand for one of the kinds above (a name labelled by the part the person plays is a person's name). */
  aliases: Record<string, string>;
  may_set: boolean;
  may_set_reasons: string[];
}

export interface StandardVersion {
  version: number;
  limits: StandardLimits;
  reason: string;
  set_by: string;
  set_by_label: string | null;
  set_at: string;
}

/** What a waiting result would be recommended under a standard: the answer the decision itself is made on. */
export interface Judgement {
  standard: Pick<Standard, "id" | "name" | "version" | "limits" | "built_in">;
  recommendation: "pass" | "fail" | "not_measured";
  reason: string;
  /** False where the answer is what the run recorded, true where it was worked out again under the chosen standard. */
  worked_out: boolean;
}

export function useStandards() {
  return useQuery({ queryKey: ["standards"], queryFn: () => api.get<Standards>("/deidentification-standards") });
}

export function useStandardVersions(standardId: string | undefined) {
  return useQuery({
    queryKey: ["standard-versions", standardId],
    queryFn: () => api.get<{ standard: Standard; versions: StandardVersion[] }>(`/deidentification-standards/${standardId}/versions`),
    enabled: Boolean(standardId),
  });
}

export function useJudgement(decisionId: string | undefined, standardId: string | null) {
  return useQuery({
    queryKey: ["gate-judgement", decisionId, standardId],
    queryFn: () => api.get<Judgement>(`/gate-decisions/${decisionId}/judgement`, standardId ? { standard_id: standardId } : undefined),
    enabled: Boolean(decisionId),
    retry: false,
  });
}

export function useCreateStandard() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { name: string; limits: StandardLimits; reason: string }) => api.post<{ standard: Standard }>("/deidentification-standards", v),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["standards"] }),
  });
}

export function useChangeStandard(standardId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { limits: StandardLimits; reason: string }) => api.post<{ standard: Standard }>(`/deidentification-standards/${standardId}/versions`, v),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["standards"] });
      queryClient.invalidateQueries({ queryKey: ["standard-versions", standardId] });
    },
  });
}
