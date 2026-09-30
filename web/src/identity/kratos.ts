/**
 * The two Kratos calls the login page needs, and nothing else.
 *
 * Deliberately not folded into ../api/client.ts: that client talks to the
 * Munitas control plane and never sends cookies (see its own docstring on
 * why identity never lives there). This talks to a different service, for a
 * different reason, and needs `credentials: "include"` on every call so the
 * session Kratos sets is actually stored and later sent back. Keeping the
 * two apart means neither client's contract has to bend for the other.
 */

import { KRATOS_PUBLIC_URL } from "../config/ports";

const KRATOS_PUBLIC = KRATOS_PUBLIC_URL;

export interface LoginFlowNode {
  attributes: {
    name: string;
    type: string;
    value?: string;
    required?: boolean;
  };
  messages: { id: number; text: string; type: string }[];
  meta: { label?: { text: string } };
}

export interface LoginFlow {
  id: string;
  ui: {
    action: string;
    method: string;
    nodes: LoginFlowNode[];
    messages?: { id: number; text: string; type: string }[];
  };
}

export class LoginFailed extends Error {
  readonly flow: LoginFlow;
  constructor(flow: LoginFlow) {
    const messages = allMessages(flow).join("; ") || "sign-in failed";
    super(messages);
    this.flow = flow;
  }
}

function allMessages(flow: LoginFlow): string[] {
  const top = flow.ui.messages ?? [];
  const perNode = flow.ui.nodes.flatMap((n) => n.messages ?? []);
  return [...top, ...perNode].map((m) => m.text);
}

export async function startLoginFlow(): Promise<LoginFlow> {
  const r = await fetch(`${KRATOS_PUBLIC}/self-service/login/browser`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!r.ok) throw new Error(`could not start a login flow (HTTP ${r.status})`);
  return r.json();
}

export async function submitLogin(
  flow: LoginFlow,
  identifier: string,
  password: string,
): Promise<void> {
  const csrfToken =
    flow.ui.nodes.find((n) => n.attributes.name === "csrf_token")?.attributes.value ?? "";

  const r = await fetch(flow.ui.action, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ method: "password", identifier, password, csrf_token: csrfToken }),
  });

  const body = await r.json();
  if (!r.ok) throw new LoginFailed(body as LoginFlow);
}

export interface Session {
  id: string;
  tenant_id: string;
  label: string;
  kind: string;
  roles: string[];
  session_id: string;
  authenticated_at: string;
}

/**
 * The resolved identity behind whatever session cookie the browser is
 * holding, or null if there is none. Talks to the Munitas API, not Kratos
 * directly; `/auth/whoami` is what actually resolves a session to a
 * `directory` row (platform/api/app/auth.py); Kratos alone only knows the
 * session is valid, not who that is in this platform's own terms.
 */
export async function currentSession(apiBase: string): Promise<Session | null> {
  const r = await fetch(`${apiBase}/auth/whoami`, { credentials: "include" });
  if (r.status === 401) return null;
  if (!r.ok) throw new Error(`could not check the current session (HTTP ${r.status})`);
  return r.json();
}

/**
 * Ends the Kratos session. A real logout, not a local state reset: without
 * this, `clear()` would forget who the console thinks is acting while the
 * cookie Kratos issued stays valid, so reloading the page would just log
 * the same person back in.
 */
export async function logout(): Promise<void> {
  const flow = await fetch(`${KRATOS_PUBLIC}/self-service/logout/browser`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (flow.status === 401) return; // already signed out
  if (!flow.ok) throw new Error(`could not start logout (HTTP ${flow.status})`);
  const { logout_url } = await flow.json();
  await fetch(logout_url, { credentials: "include" });
}
