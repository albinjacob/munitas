/**
 * Release bars: the limits an organisation holds a de-identified result to, kept under names (a purpose), each change a new version.
 *
 * Anybody signed in may read them. Only the data protection officer sets one, and the platform refuses the rest. The bar a result is released
 * under is chosen by the person who decides it, and the platform works out the recommendation under that bar again at that moment.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

/** One kind of identifier and the limits a bar may put on it. Either limit may be empty: that limit is then not applied. */
export interface KindLimit {
  recall: number | null;
  max_leaks: number | null;
}

export interface Bars {
  /** The least share of identifiers that survived transcription and were removed, over all kinds together. Empty for no overall limit. */
  overall_recall: number | null;
  /** Limits by kind of identifier. A kind that is not here has no limit of its own. */
  types: Record<string, KindLimit>;
}

export interface ReleaseBar {
  /** Empty for the platform's own built-in bar. */
  id: string | null;
  name: string;
  /** Empty for the built-in bar. */
  version: number | null;
  bars: Bars;
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

export interface ReleaseBars {
  release_bars: ReleaseBar[];
  platform_default: ReleaseBar;
  kinds: Kind[];
  /** Labels a run keeps its counts under that stand for one of the kinds above (a name labelled by the part the person plays is a person's name). */
  aliases: Record<string, string>;
  may_set: boolean;
  may_set_reasons: string[];
}

export interface BarVersion {
  version: number;
  bars: Bars;
  reason: string;
  set_by: string;
  set_by_label: string | null;
  set_at: string;
}

/** What a waiting result would be recommended under a bar: the answer the decision itself is made on. */
export interface Judgement {
  bar: Pick<ReleaseBar, "id" | "name" | "version" | "bars" | "built_in">;
  recommendation: "pass" | "fail" | "not_measured";
  reason: string;
  /** False where the answer is what the run recorded, true where it was worked out again under the chosen bar. */
  worked_out: boolean;
}

export function useReleaseBars() {
  return useQuery({ queryKey: ["release-bars"], queryFn: () => api.get<ReleaseBars>("/release-bars") });
}

export function useBarVersions(barId: string | undefined) {
  return useQuery({
    queryKey: ["release-bar-versions", barId],
    queryFn: () => api.get<{ release_bar: ReleaseBar; versions: BarVersion[] }>(`/release-bars/${barId}/versions`),
    enabled: Boolean(barId),
  });
}

export function useJudgement(decisionId: string | undefined, barId: string | null) {
  return useQuery({
    queryKey: ["gate-judgement", decisionId, barId],
    queryFn: () => api.get<Judgement>(`/gate-decisions/${decisionId}/judgement`, barId ? { release_bar_id: barId } : undefined),
    enabled: Boolean(decisionId),
    retry: false,
  });
}

export function useCreateBar() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { name: string; bars: Bars; reason: string }) => api.post<{ release_bar: ReleaseBar }>("/release-bars", v),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["release-bars"] }),
  });
}

export function useChangeBar(barId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { bars: Bars; reason: string }) => api.post<{ release_bar: ReleaseBar }>(`/release-bars/${barId}/versions`, v),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["release-bars"] });
      queryClient.invalidateQueries({ queryKey: ["release-bar-versions", barId] });
    },
  });
}
