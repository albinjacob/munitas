/**
 * Closing an organisation, and the legal holds that can stop it being deleted.
 *
 * The console offers every step and enforces none of them: the policy engine
 * refuses a caller regardless of which buttons this draws, and the reason it
 * gives is what the screen shows. Phases, dates and holds are worked out by the
 * platform (`tenant_phase` in platform/schema.sql), never here.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

/** Where an organisation is in its closing. */
export type Phase = "active" | "retiring" | "closing" | "purge_due" | "purged" | "retired";

export interface Closing {
  tenant_id: string;
  phase: Phase;
  /** Whether a legal hold stands over it: none, pending a second administrator, or in force. */
  hold: "none" | "pending" | "active";
  retire_reason: string | null;
  requested_by: string | null;
  retired_at: string | null;
  retiring_until: string | null;
  closing_until: string | null;
  /** When the current period ends. Null when it is not closing. */
  phase_ends_at: string | null;
  days_left: number | null;
  can_cancel: boolean;
  purged_at: string | null;
}

export interface OrganisationRow extends Closing {
  note: string | null;
  purpose: string;
}

export interface Hold {
  id: string;
  tenant_id: string;
  status: "proposed" | "active" | "declined" | "lapsed" | "released";
  matter_name: string;
  matter_number: string;
  description: string;
  triggering_event: string;
  issuing_authority: string;
  authority_reference: string;
  attorney_name: string;
  attorney_email: string;
  notice_received_on: string;
  preserve: string;
  data_from: string | null;
  data_to: string | null;
  custodian_id: string;
  custodian_label: string;
  custodian_acknowledged_at: string | null;
  placed_by: string;
  placed_by_label: string;
  placed_at: string;
  expires_unapproved_at: string;
  decided_by: string | null;
  decided_by_label: string | null;
  decided_at: string | null;
  decision_note: string | null;
  review_due_on: string;
  released_by: string | null;
  released_by_label: string | null;
  released_at: string | null;
  release_reason: string | null;
}

export interface NewHold {
  tenant_id: string;
  matter_name: string;
  matter_number: string;
  description: string;
  triggering_event: string;
  issuing_authority: string;
  authority_reference: string;
  attorney_name: string;
  attorney_email: string;
  notice_received_on: string;
  preserve: string;
  data_from?: string;
  data_to?: string;
  custodian_id: string;
}

export interface DeletionRecord {
  /** The name the record and the audit rows are filed under: the old name, then `~deleted-`, a date and a few letters. */
  tenant_id: string;
  /** What the organisation was called. The name is free to be used again. */
  original_tenant_id: string | null;
  retire_reason: string | null;
  retire_requested_by: string | null;
  retired_at: string | null;
  closing_ended_at: string | null;
  purged_at: string;
  purged_by: string;
  holds: {
    matter_number: string;
    issuing_authority: string;
    authority_reference: string;
    status: string;
    placed_on: string;
    approved_on: string | null;
    released_on: string | null;
    release_reason: string | null;
  }[];
  rows_removed: Record<string, number>;
  files_removed: number;
  buckets_removed: string[];
  buckets_left: { bucket: string; backend: string; why: string }[];
  /** The audit rows kept after the deletion, until the date given, when they are removed too. */
  audit_rows_kept: number;
  audit_kept_until: string | null;
  audit_removed_at: string | null;
  identities_removed: number;
}

const KEY = ["lifecycle"] as const;

/** One organisation's own closing. `enabled` lets a caller wait for a signed-in person. */
export function useClosing(tenantId?: string, enabled = true) {
  return useQuery({
    queryKey: [...KEY, "organisation", tenantId ?? "own"],
    queryFn: () => api.get<Closing>("/lifecycle/organisation", { tenant_id: tenantId }),
    enabled,
    refetchInterval: 60_000,
  });
}

export function useOrganisations(enabled: boolean) {
  return useQuery({
    queryKey: [...KEY, "organisations"],
    queryFn: () =>
      api.get<{ organisations: OrganisationRow[]; periods: { retiring_days: number; closing_days: number } }>(
        "/lifecycle/organisations",
      ),
    enabled,
  });
}

export function useHolds(enabled = true) {
  return useQuery({
    queryKey: [...KEY, "holds"],
    queryFn: () => api.get<{ holds: Hold[] }>("/lifecycle/holds"),
    enabled,
  });
}

export function useDeletions(enabled: boolean) {
  return useQuery({
    queryKey: [...KEY, "deletions"],
    queryFn: () => api.get<{ deletions: DeletionRecord[] }>("/lifecycle/deletions"),
    enabled,
  });
}

function useRefresh() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: KEY });
    // Whether the person may still act is part of who they are.
    queryClient.invalidateQueries({ queryKey: ["auth", "whoami"] });
  };
}

export function useRetire() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (body: { tenant_id?: string; reason: string }) =>
      api.post<Closing>("/lifecycle/organisation/retire", body),
    onSuccess: refresh,
  });
}

export function useCancelClosing() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (body: { tenant_id?: string }) =>
      api.post<Closing>("/lifecycle/organisation/cancel", body),
    onSuccess: refresh,
  });
}

export function usePlaceHold() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (body: NewHold) => api.post<Hold>("/lifecycle/holds", body),
    onSuccess: refresh,
  });
}

export function useDecideHold(holdId: string) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (body: { approve: boolean; note: string }) =>
      api.post<Hold>(`/lifecycle/holds/${holdId}/decide`, body),
    onSuccess: refresh,
  });
}

export function useReleaseHold(holdId: string) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (body: { reason: string }) =>
      api.post<Hold>(`/lifecycle/holds/${holdId}/release`, body),
    onSuccess: refresh,
  });
}

export function useAcknowledgeHold(holdId: string) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: () => api.post<Hold>(`/lifecycle/holds/${holdId}/acknowledge`),
    onSuccess: refresh,
  });
}

/** What each phase means to the people it affects, in plain words. */
export const PHASE_COPY: Record<Phase, { label: string; tone: string; plain: string }> = {
  active: {
    label: "Open",
    tone: "bg-green-100 text-green-900",
    plain: "The organisation is running normally.",
  },
  retiring: {
    label: "Closing down",
    tone: "bg-amber-100 text-amber-900",
    plain:
      "The closing down has started. Nothing can be added or changed, people can still read what the organisation holds, and the closing down can still be cancelled.",
  },
  closing: {
    label: "Closed to its people",
    tone: "bg-red-100 text-red-900",
    plain:
      "The time to cancel has passed. Nobody in the organisation can do anything. Everything inside it will be deleted when this period ends, unless a legal hold stands over it.",
  },
  purge_due: {
    label: "Waiting to be deleted",
    tone: "bg-red-100 text-red-900",
    plain: "Both periods have ended. Everything inside it is deleted at the next sweep, unless a legal hold stands over it.",
  },
  purged: {
    label: "Deleted",
    tone: "bg-slate-200 text-slate-800",
    plain: "Everything inside the organisation has been deleted. A short record of the deletion is kept.",
  },
  retired: {
    label: "Closed",
    tone: "bg-slate-200 text-slate-800",
    plain: "Closed before closing dates were recorded. It takes no writes and is not deleted automatically.",
  },
};
