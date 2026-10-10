/**
 * U167 in the console: a notebook signs in on behalf of a person, through the pages a person actually sees.
 *
 * The claims these tests carry are about what reaches the person, on the live platform and the live sign-in service:
 *   - a person who is not yet signed in to the console is asked to sign in, and is brought back to the code page, not to the home page;
 *   - the code page takes the code the notebook shows, and says so plainly when it is wrong;
 *   - the approval page says which notebook, from where, and what it will and will not be able to do, and Approve gives the notebook its pass;
 *   - Refuse tells the notebook at once and gives it nothing;
 *   - "Your notebooks" lists the sign-in with when and until when, and End now stops the pass at its next request;
 *   - the data protection officer may see everybody's, and an ordinary person sees only their own.
 */

import { expect, test, type Page } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { EMAIL_BY_DIRECTORY_ID, PASSWORD, loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial" });

const RESEARCHER = "canary-researcher";
const ENGINEER = "canary-engineer";
const DPO = "canary-dpo";
const stamp = Date.now();

interface Started {
  user_code: string;
  verification_uri_complete: string;
  device_code: string;
}

async function startNotebook(label: string): Promise<Started> {
  const r = await fetch(`${API_BASE}/notebook/v1/sign-in`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ label }) });
  expect(r.status).toBe(201);
  return r.json();
}

async function waitForPass(device: string): Promise<{ access_token: string; refresh_token: string }> {
  for (let i = 0; i < 8; i++) {
    const r = await fetch(`${API_BASE}/notebook/v1/sign-in/token`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ device_code: device }) });
    if (r.status === 200) return r.json();
    await new Promise((resolve) => setTimeout(resolve, 5500));
  }
  throw new Error("the notebook never received its pass");
}

async function pollOnce(device: string) {
  const r = await fetch(`${API_BASE}/notebook/v1/sign-in/token`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ device_code: device }) });
  return { status: r.status, body: await r.json() };
}

/** A notebook signs in through the pages, as a person who is already signed in to the console. Returns the notebook's pass. */
async function signNotebookIn(page: Page, label: string): Promise<string> {
  const started = await startNotebook(label);
  await page.goto(started.verification_uri_complete);
  await expect(page.getByTestId("notebook-code-form")).toBeVisible({ timeout: 20_000 });
  await page.getByTestId("notebook-code-input").fill(started.user_code);
  await page.getByTestId("notebook-code-submit").click();
  await expect(page.getByTestId("notebook-consent")).toBeVisible({ timeout: 20_000 });
  await page.getByTestId("notebook-approve").click();
  await expect(page.getByTestId("notebook-done")).toBeVisible({ timeout: 20_000 });
  return (await waitForPass(started.device_code)).access_token;
}

test.describe("U167: signing a notebook in", () => {
  const label = `U167 console ${stamp}`;
  let signIn = "";
  let pass = "";

  test("a person not yet signed in is asked to sign in and is brought back to the code page", async ({ page }) => {
    const started = await startNotebook(label);
    await page.context().clearCookies();
    await page.goto(started.verification_uri_complete);
    await expect(page.getByTestId("login-form")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("login-identifier").fill(EMAIL_BY_DIRECTORY_ID[RESEARCHER]);
    await page.getByTestId("login-password").fill(PASSWORD);
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("notebook-code-form")).toBeVisible({ timeout: 20_000 });
    expect(page.url()).toContain("/notebook-sign-in/code");

    await page.getByTestId("notebook-code-input").fill("WRONGCODE");
    await page.getByTestId("notebook-code-submit").click();
    await expect(page.getByTestId("notebook-code-error")).toContainText("not a code a notebook is waiting on");

    await page.getByTestId("notebook-code-input").fill(started.user_code);
    await page.getByTestId("notebook-code-submit").click();
    const consent = page.getByTestId("notebook-consent");
    await expect(consent).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("notebook-consent-label")).toHaveText(label);
    await expect(consent).toContainText("Once approved, the notebook can, as you:");
    await expect(consent).toContainText("decide anybody's request");
    await expect(consent).toContainText("8 hours");
    await page.getByTestId("notebook-approve").click();
    await expect(page.getByTestId("notebook-done")).toBeVisible({ timeout: 20_000 });

    const got = await waitForPass(started.device_code);
    pass = got.access_token;
    const whoami = await fetch(`${API_BASE}/auth/whoami`, { headers: { authorization: `Bearer ${pass}` } });
    expect(whoami.status).toBe(200);
    const who = await whoami.json();
    expect(who.id).toBe(RESEARCHER);
    signIn = who.session_id;
  });

  test("Your notebooks lists it, and End now stops the pass at its next request", async ({ page }) => {
    await loginAs(page, RESEARCHER);
    await page.goto("/notebook-sign-ins");
    const row = page.getByTestId("notebook-sign-ins").locator("tr", { hasText: label });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText("In force");
    await expect(row).toContainText("from ");
    await row.getByRole("button", { name: "End now" }).click();
    // It was used a moment ago (whoami above), so the page asks first.
    await expect(row.getByRole("alertdialog")).toContainText("may be in the middle of work");
    await row.getByRole("button", { name: "Yes, end it" }).click();
    await expect(row).toContainText("Ended by the person", { timeout: 15_000 });
    const after = await fetch(`${API_BASE}/auth/whoami`, { headers: { authorization: `Bearer ${pass}` } });
    expect(after.status).toBe(401);
    expect(signIn.length).toBeGreaterThan(10);
  });

  test("Refuse tells the notebook at once and gives it nothing", async ({ page }) => {
    const started = await startNotebook(`U167 refused ${stamp}`);
    await loginAs(page, RESEARCHER);
    await page.goto(started.verification_uri_complete);
    await expect(page.getByTestId("notebook-code-form")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("notebook-code-input").fill(started.user_code);
    await page.getByTestId("notebook-code-submit").click();
    await expect(page.getByTestId("notebook-consent")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("notebook-refuse").click();
    await expect(page.getByTestId("notebook-refused")).toContainText("nothing was given to it");
    await new Promise((resolve) => setTimeout(resolve, 5500));
    const told = await pollOnce(started.device_code);
    expect(told.status).toBe(400);
    expect(told.body.error).toBe("access_denied");
  });

  test("ending one or all asks first when a notebook may be in the middle of work", async ({ page }) => {
    const busy = `U167 busy ${stamp}`;
    const idle = `U167 idle ${stamp}`;
    await loginAs(page, RESEARCHER);
    const busyPass = await signNotebookIn(page, busy);
    const idlePass = await signNotebookIn(page, idle);
    expect((await fetch(`${API_BASE}/auth/whoami`, { headers: { authorization: `Bearer ${busyPass}` } })).status).toBe(200);

    await page.goto("/notebook-sign-ins");
    const busyRow = page.getByTestId("notebook-sign-ins").locator("tr", { hasText: busy });
    const idleRow = page.getByTestId("notebook-sign-ins").locator("tr", { hasText: idle });
    await expect(busyRow).toContainText("in use now", { timeout: 15_000 });
    await expect(idleRow).not.toContainText("in use now");

    // One in use: asked first, and Keep leaves it alone.
    await busyRow.getByRole("button", { name: "End now" }).click();
    const confirm = page.locator('[data-testid^="notebook-confirm-"]').first();
    await expect(confirm).toContainText("may be in the middle of work");
    await expect(confirm).toContainText(busy);
    await confirm.getByRole("button", { name: "Keep it" }).click();
    await expect(confirm).toHaveCount(0);
    await expect(busyRow).toContainText("In force");
    expect((await fetch(`${API_BASE}/auth/whoami`, { headers: { authorization: `Bearer ${busyPass}` } })).status).toBe(200);

    // One not in use: ended at once, without a question.
    await idleRow.getByRole("button", { name: "End now" }).click();
    await expect(idleRow).toContainText("Ended by the person", { timeout: 15_000 });
    expect((await fetch(`${API_BASE}/auth/whoami`, { headers: { authorization: `Bearer ${idlePass}` } })).status).toBe(401);
    expect((await fetch(`${API_BASE}/auth/whoami`, { headers: { authorization: `Bearer ${busyPass}` } })).status).toBe(200);

    // All: asked first, naming what may be mid-run.
    await page.getByTestId("notebook-revoke-all").click();
    const all = page.getByTestId("notebook-confirm-all");
    await expect(all).toContainText("may be in the middle of work");
    await expect(all.getByTestId("notebook-confirm-all-in-use").first()).toContainText(busy);
    await all.getByTestId("notebook-confirm-all-yes").click();
    await expect(busyRow).toContainText("Ended by the person", { timeout: 15_000 });
    expect((await fetch(`${API_BASE}/auth/whoami`, { headers: { authorization: `Bearer ${busyPass}` } })).status).toBe(401);
    await expect(page.getByTestId("notebook-revoke-all")).toHaveCount(0);
  });

  test("the code page without a sign-in in progress says where it opens from", async ({ page }) => {
    await loginAs(page, RESEARCHER);
    await page.goto("/notebook-sign-in/code");
    await expect(page.getByTestId("notebook-code-missing")).toContainText("opens from the address the notebook shows");
  });

  test("an ordinary person sees only their own sign-ins; the data protection officer may see everybody's", async ({ page }) => {
    await loginAs(page, ENGINEER);
    await page.goto("/notebook-sign-ins");
    await expect(page.getByRole("heading", { name: "Your notebooks" })).toBeVisible();
    await expect(page.getByText(label)).toHaveCount(0);
    await expect(page.getByTestId("notebooks-everyone")).toHaveCount(0);

    await loginAs(page, DPO);
    await page.goto("/notebook-sign-ins");
    await expect(page.getByText(label)).toHaveCount(0);
    await page.getByTestId("notebooks-everyone").check();
    const row = page.getByTestId("notebook-sign-ins").locator("tr", { hasText: label });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText("Canary researcher");
    await expect(row.getByRole("button", { name: "End now" })).toHaveCount(0);
  });
});
