/**
 * Typed access to the control plane.
 *
 * Two rules the rest of the console depends on:
 *
 * 1. Errors carry their body. The API returns structured reasons on a denial,
 *    and a client that throws `Error("403")` discards the only part worth
 *    reading. A denial without its reason is indistinguishable from a bug, and
 *    this system's whole argument is that denials are informative.
 *
 * 2. Nothing here reads the acting principal. Callers pass whatever identity a
 *    request body still needs; sourced from the identity seam, never from this
 *    module. `credentials: "include"` below sends the browser's Kratos session
 *    cookie on every call, the same way identity/kratos.ts's own client
 *    already does, so an endpoint that requires a real session
 *    (auth.current_session) can see it. That is proof of identity, not a
 *    second place identity lives: the seam still decides who is acting, this
 *    is only how that gets carried to the server.
 */

import { API_BASE } from "../config/ports";

const BASE = API_BASE;

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  /** Reasons from the policy engine, when the failure was a policy decision. */
  readonly reasons: string[];

  constructor(status: number, body: unknown) {
    super(ApiError.describe(status, body));
    this.status = status;
    this.body = body;
    this.reasons = ApiError.extractReasons(body);
  }

  private static extractReasons(body: unknown): string[] {
    if (typeof body !== "object" || body === null) return [];
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "object" && detail !== null) {
      const reasons = (detail as { reasons?: unknown }).reasons;
      if (Array.isArray(reasons)) return reasons.map(String);
    }
    return [];
  }

  private static describe(status: number, body: unknown): string {
    const reasons = ApiError.extractReasons(body);
    if (reasons.length) return reasons.join("; ");
    if (typeof body === "object" && body !== null) {
      const detail = (body as { detail?: unknown }).detail;
      if (typeof detail === "string") return detail;
    }
    return `request failed with ${status}`;
  }
}

async function request<T>(
  path: string,
  init?: RequestInit & { query?: Record<string, string | number | boolean | undefined> },
): Promise<T> {
  const url = new URL(BASE + path);
  for (const [k, v] of Object.entries(init?.query ?? {})) {
    if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
  }

  const response = await fetch(url, {
    ...init,
    credentials: "include",
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  const text = await response.text();
  const body = text ? safeParse(text) : null;

  if (!response.ok) throw new ApiError(response.status, body);
  return body as T;
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export const api = {
  get: <T,>(path: string, query?: Record<string, string | number | boolean | undefined>) =>
    request<T>(path, { method: "GET", query }),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  delete: <T,>(path: string) => request<T>(path, { method: "DELETE" }),
};

export { BASE as API_BASE };
