/**
 * The identity seam.
 *
 * Every part of the console that needs to know who is acting reads it from
 * here, and nowhere else reads it from anywhere else. U7 checks that by
 * grepping the source rather than trusting the convention to hold.
 *
 * `principal` and `authenticated` come from a real Kratos session, verified
 * server-side by `GET /auth/whoami` (platform/api/app/auth.py). Nobody is
 * offered a picker; signing in *is* choosing who you are. This used to have
 * a second mode (a locally-picked id remembered in localStorage, proving
 * nothing), kept for local exploration without Kratos running. It was
 * retired once every session-gated endpoint (`request_lease` and friends,
 * platform/api/app/auth.py's `current_session`) required a real session
 * regardless: the picker could no longer exercise those flows at all, so
 * keeping it around meant a button that looked clickable and silently
 * 401'd. One identity mechanism, matching what the API actually accepts.
 *
 * If components pulled the acting principal from localStorage, a global, or
 * a prop threaded down from somewhere, moving between identity mechanisms,
 * or adding a third, would mean touching every one of them, and the ones
 * that get missed keep serving a stale identity.
 *
 * The list of people comes from the platform rather than from a constant.
 * That is the difference between a console that reports an organisation and
 * one that asserts an organisation, and only the first can be wrong in a way
 * you can see.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, API_BASE } from "../api/client";
import { describe, isAppointed, type DirectoryEntry, type Principal } from "./principals";
import { currentSession, logout } from "./kratos";

interface IdentityValue {
  /**
   * Everyone registered, including people who cannot currently act.
   *
   * Split into `principals` and `retained` here rather than exporting the rule
   * that separates them. A consumer importing that rule would be reaching into
   * the seam, which U7 forbids and which caught this once already.
   */
  everyone: Principal[];
  /** Registered, but holding no appointment, so unable to act. */
  retained: Principal[];
  /**
   * Null until a real session resolves one. Never defaulted to the first
   * person: a default means the console acts as somebody nobody signed in
   * as and writes that name into the audit log.
   */
  principal: Principal | null;
  principals: Principal[];
  /**
   * A real Kratos logout. "Sign out" and "switch to someone else" are the
   * same action: there is no switching without signing out first: you are
   * whoever you can actually log in as.
   *
   * Returns a promise deliberately, not fire-and-forget: a caller that
   * navigates away immediately after calling this, without awaiting it,
   * can land on a page that reloads before Kratos has actually invalidated
   * the session server-side, and finds itself still signed in. Await it.
   */
  clear: () => Promise<void>;
  /**
   * Re-checks the session. Login.tsx calls this right after a successful
   * sign-in so the rest of the console picks up the new identity without a
   * full reload.
   */
  refreshSession: () => void;
  /**
   * The organisation whose data the console should be showing, or undefined
   * before anybody has signed in.
   *
   * Read from here rather than from each screen, so that scoping a list to one
   * tenant is one decision rather than a habit every new screen has to pick up.
   * Screens that forgot would show another organisation's datasets, which is
   * the failure this platform exists to prevent.
   */
  tenant: string | undefined;
  loading: boolean;
  error: unknown;
  /** True only when a real, Kratos-verified session resolved to somebody. */
  authenticated: boolean;
}

const IdentityContext = createContext<IdentityValue | null>(null);

export function IdentityProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();

  const session = useQuery({
    queryKey: ["auth", "whoami"],
    queryFn: () => currentSession(API_BASE),
    retry: false,
    staleTime: 0,
  });

  // Everyone, from every organisation. The session lookup above narrows this
  // to one person.
  //
  // Fetching only the offerable people would mean a session-resolved id from
  // another organisation resolved to nobody, and the console would quietly
  // become signed out rather than saying whose data it was showing. The
  // seam's job is to answer who somebody is; deciding who should be offered
  // is a separate question with a separate answer.
  //
  // `enabled` on a real session existing, not fired unconditionally on
  // mount: the endpoint requires a session server-side, so calling it before
  // one exists only spends this query's retries on 401s that can never
  // succeed, and once they are exhausted nothing here re-triggers a fetch
  // when a session later appears. That left `principal` stuck null forever
  // after a real, successful login, which App.tsx's `!principal` guard read
  // as still signed out and bounced back to /auth/login indefinitely.
  const directory = useQuery({
    queryKey: ["directory", "human", "all"],
    queryFn: () =>
      api.get<DirectoryEntry[]>("/directory", { kind: "human", purpose: "all" }),
    enabled: Boolean(session.data),
    staleTime: 60_000,
  });

  const everyone = useMemo(
    () => (directory.data ?? []).map(describe),
    [directory.data],
  );

  // Kept even though nothing offers a chooser from it any more: other
  // screens (the roles page, the directory) still read `principals` and
  // `retained` regardless of how identity itself was established.
  const principals = useMemo(
    () =>
      everyone.filter((p) => p.tenant_purpose === "production" && isAppointed(p)),
    [everyone],
  );
  const retained = useMemo(
    () => everyone.filter((p) => !isAppointed(p)),
    [everyone],
  );

  const refreshSession = useCallback(async () => {
    // Awaits the refetch itself, not just the invalidation. A caller that
    // only awaited `invalidateQueries` scheduling the refetch, rather than
    // the refetch completing, could still observe stale `session.data` for
    // one more render, exactly the gap `clear()` below depends on not
    // having.
    await queryClient.invalidateQueries({ queryKey: ["auth", "whoami"] });
  }, [queryClient]);

  const clear = useCallback(async () => {
    // Best effort on the logout call itself: even if it fails (the session
    // was already gone, say), re-checking whoami afterward converges on the
    // truth either way. What must not be best-effort is waiting for that
    // recheck: a caller that navigates or reloads before this resolves can
    // land back on a still-valid session.
    await logout().catch(() => {});
    await refreshSession();
  }, [refreshSession]);

  const value = useMemo<IdentityValue>(() => {
    const resolvedId = session.data?.id ?? null;
    const principal = resolvedId
      ? (everyone.find((p) => p.id === resolvedId) ?? null)
      : null;
    return {
      principal,
      principals,
      everyone,
      retained,
      clear,
      refreshSession,
      tenant: principal?.tenant_id,
      loading: directory.isLoading || session.isLoading,
      error: directory.error ?? session.error,
      authenticated: Boolean(session.data),
    };
  }, [
    principals, retained, everyone, clear, refreshSession,
    directory.isLoading, directory.error,
    session.data, session.isLoading, session.error,
  ]);

  return (
    <IdentityContext.Provider value={value}>{children}</IdentityContext.Provider>
  );
}

export function useIdentity(): IdentityValue {
  const value = useContext(IdentityContext);
  if (!value) {
    throw new Error("useIdentity must be used inside IdentityProvider");
  }
  return value;
}
