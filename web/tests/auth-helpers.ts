/**
 * Shared login mechanics for every Playwright suite that acts as somebody.
 *
 * Every identity here is read from infra/kratos/identities.json rather than
 * duplicated as a literal map. That file is the single source
 * infra/kratos/seed-identities.py itself reads from, so a tenant added there
 * needs no matching edit here: the same "adding a tenant is a JSON edit,
 * not a code edit" property that file exists to give infra/kratos/
 * seed-identities.py.
 *
 * The console has one identity mechanism now: a real Kratos session. There
 * used to be a second, a locally-picked id in localStorage that proved
 * nothing, and it was retired once every session-gated endpoint
 * (request_lease and friends, platform/api/app/auth.py's current_session)
 * required a real session regardless. This is the one login path everything now goes through, browser and API
 * both.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, type Page } from "@playwright/test";

import { KRATOS_PUBLIC_URL } from "../config/ports";

export const KRATOS = KRATOS_PUBLIC_URL;
export const PASSWORD = "dev-password-not-for-production";

interface IdentitiesFile {
  tenants: {
    tenant: string;
    identities: { directory_id: string; email: string; name: string }[];
  }[];
}

function loadEmails(): Record<string, string> {
  const path = join(process.cwd(), "..", "infra", "kratos", "identities.json");
  const data = JSON.parse(readFileSync(path, "utf8")) as IdentitiesFile;
  return Object.fromEntries(
    data.tenants.flatMap((t) => t.identities.map((i) => [i.directory_id, i.email])),
  );
}

/** directory_id -> seeded Kratos email, read once per test run. */
export const EMAIL_BY_DIRECTORY_ID: Record<string, string> = loadEmails();

function emailFor(directoryId: string): string {
  const email = EMAIL_BY_DIRECTORY_ID[directoryId];
  if (!email) {
    throw new Error(
      `${directoryId} has no seeded email in infra/kratos/identities.json`,
    );
  }
  return email;
}

/**
 * A real Kratos session's bearer header for a seeded identity, for setup
 * calls made directly against the API rather than through the browser (the
 * same convention verify/common.py's bearer_for() uses).
 */
export async function bearerFor(directoryId: string): Promise<Record<string, string>> {
  const email = emailFor(directoryId);
  const flow = await fetch(`${KRATOS}/self-service/login/api`).then((r) => r.json());
  const r = await fetch(`${KRATOS}/self-service/login?flow=${flow.id}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ method: "password", identifier: email, password: PASSWORD }),
  });
  if (!r.ok) throw new Error(`Kratos login failed for ${directoryId}: HTTP ${r.status}`);
  const { session_token } = await r.json();
  return { Authorization: `Bearer ${session_token}` };
}

/**
 * Ends whatever session the page's browser context currently holds, if any.
 * `page.request` shares cookies with the page, so this reaches the same
 * session the browser is using without a UI round-trip.
 */
async function signOut(page: Page): Promise<void> {
  const flow = await page.request.get(`${KRATOS}/self-service/logout/browser`, {
    headers: { Accept: "application/json" },
  });
  if (flow.status() === 401) return; // nothing to sign out of
  if (!flow.ok()) throw new Error(`could not start logout (HTTP ${flow.status()})`);
  const { logout_url } = await flow.json();
  await page.request.get(logout_url);
}

/**
 * Signs in as a seeded identity through the real login form, replacing
 * whichever session (if any) the page already held.
 *
 * Always signs out first rather than assuming no session is active: a test
 * that switches between two personas (ask as the researcher, then approve
 * as the custodian) needs a clean login each time, and Kratos's own browser
 * login flow does not reliably offer a second login on top of a first.
 */
export async function loginAs(page: Page, directoryId: string): Promise<void> {
  const email = emailFor(directoryId);
  await signOut(page);
  await page.goto("/auth/login");
  await page.getByTestId("login-identifier").fill(email);
  await page.getByTestId("login-password").fill(PASSWORD);
  await page.getByTestId("login-submit").click();
  // See real-auth.spec.ts's own loginAs for why this is 15s rather than the
  // 5s expect default: a real Kratos round trip plus the console's own
  // session fetch and render, not a mock, occasionally slower than 5s under
  // real backend load rather than actually broken.
  await expect(page.getByTestId("current-persona")).toBeVisible({ timeout: 15000 });
}
