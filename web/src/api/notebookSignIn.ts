/**
 * A notebook signs in on behalf of a person: the person types the code the notebook shows, is signed in by this console, and approves.
 *
 * The notebook's own calls (start, ask whether approved, renew) are made by the client library and are not here. What is here is the person's side, and the list of
 * a person's notebook sign-ins. Each step returns an address to go on to (Hydra, the service that issues the notebook's pass, is the one that says what comes next),
 * and the page follows it.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

export interface NotebookSignIn {
  id: string;
  person: string;
  principal: string | null;
  label: string;
  status: "approved" | "denied" | "revoked" | "expired" | "pending";
  in_force: boolean;
  created_at: string;
  created_ip: string | null;
  approved_at: string | null;
  expires_at: string | null;
  last_used_at: string | null;
  last_ip: string | null;
  address_changes: number;
  ended_at: string | null;
  /** Used within the last `in_use_minutes`: it may be in the middle of work, and the page says so before ending it. */
  in_use: boolean;
  in_use_minutes: number;
}

export interface ConsentDetails {
  sign_in_id: string;
  label: string;
  address: string | null;
  program: string | null;
  asked_at: string;
  hours: number;
  may: string[];
  may_not: string[];
}

export function useAcceptCode() {
  return useMutation({
    mutationFn: (v: { device_challenge: string; user_code: string }) => api.post<{ redirect_to: string }>("/notebook-sign-in/code", v),
  });
}

export function useSignInStep() {
  return useMutation({ mutationFn: (login_challenge: string) => api.post<{ redirect_to: string }>("/notebook-sign-in/login", { login_challenge }) });
}

export function useConsentDetails(challenge: string | null) {
  return useQuery({
    queryKey: ["notebook-consent", challenge],
    queryFn: () => api.get<ConsentDetails>("/notebook-sign-in/consent", { consent_challenge: challenge! }),
    enabled: Boolean(challenge),
    retry: false,
  });
}

export function useDecideSignIn() {
  return useMutation({
    mutationFn: (v: { consent_challenge: string; approve: boolean }) => api.post<{ redirect_to: string; approved: boolean }>("/notebook-sign-in/consent", v),
  });
}

export function useNotebookSignIns(everyone: boolean) {
  return useQuery({
    queryKey: ["notebook-sign-ins", everyone],
    queryFn: () => api.get<{ sign_ins: NotebookSignIn[] }>("/notebook-sign-ins", everyone ? { everyone: "true" } : undefined),
  });
}

export function useRevokeAllNotebookSignIns() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ ended: number; sign_ins: { id: string; label: string }[] }>("/notebook-sign-ins/revoke-all"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notebook-sign-ins"] }),
  });
}

export function useRevokeNotebookSignIn() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<NotebookSignIn>(`/notebook-sign-ins/${id}/revoke`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notebook-sign-ins"] }),
  });
}
