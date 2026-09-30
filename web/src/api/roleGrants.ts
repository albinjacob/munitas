/**
 * Who holds which role, and how that changes.
 *
 * Separate from `people.ts`, which turns a principal id into something a
 * person would say. Naming somebody and deciding what they may do are
 * different questions, and this project already keeps that line elsewhere.
 *
 * A role is asked for by the person who would hold it, decided by somebody
 * else, and lapses. The console offers all three and enforces none of them:
 * the policy engine refuses a caller regardless of which buttons this file
 * chooses to draw, and the reason it gives is what the screen shows.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

/** A role somebody holds, for a while. */
export interface RoleHeld {
  id: string;
  principal: string;
  label: string;
  role: string;
  approved_by: string;
  expires_at: string;
  /** Null until somebody has confirmed it is still needed. */
  attested_at: string | null;
  attested_by: string | null;
  revoked: boolean;
  expired: boolean;
}

/** Somebody's ask, waiting for a decision from anybody but them. */
export interface RoleAsk {
  id: string;
  principal: string;
  label: string;
  role: string;
  justification: string;
  requested_days: number;
  created_at: string;
}

export interface RolesPage {
  held: RoleHeld[];
  pending: RoleAsk[];
}

export function useRoles(enabled = true) {
  return useQuery({
    queryKey: ["people", "roles"],
    queryFn: () => api.get<RolesPage>("/people/roles"),
    enabled,
    retry: false,
  });
}

export function useAskForRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { role: string; justification: string; days?: number }) =>
      api.post<{ id: string; state: string }>("/people/role-requests", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["people", "roles"] });
    },
  });
}

export function useDecideRole(requestId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { outcome: "approve" | "reject"; reason: string }) =>
      api.post<{ state: string; grant_id: string | null }>(
        `/people/role-requests/${requestId}/decide`,
        body,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["people", "roles"] });
      // Somebody's roles changed, so who the console thinks they are changed.
      queryClient.invalidateQueries({ queryKey: ["directory"] });
    },
  });
}

/**
 * Confirm a role is still needed, or withdraw it.
 *
 * Confirming renews nothing. It records that somebody looked, which is the
 * whole point: an entitlement nobody has to look at is one nobody removes.
 */
export function useAttestRole(grantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { still_needed: boolean }) =>
      api.post<{ still_needed: boolean }>(
        `/people/roles/${grantId}/attest`,
        body,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["people", "roles"] });
    },
  });
}
