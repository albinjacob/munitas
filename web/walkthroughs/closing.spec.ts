/**
 * Captures the screenshots behind docs/public/walkthroughs/closing-an-organisation-walkthrough.html.
 *
 * Drives the real console against the live stack, signing in as six people in turn: the clinic's data
 * custodian who closes it, an ordinary member who cannot stop it, two platform administrators who
 * place and approve a legal hold, and the clinic's data protection officer who is named its temporary
 * custodian. Nothing is mocked. The only thing this changes outside the console is the clock: closing
 * takes 15 days and then 15 more, so twice the two dates the platform works the phase out from are
 * brought forward by scripts/admin/advance-closing-clock.py, which changes nothing else.
 *
 * Needs Harbour Clinic open and holding data, and ends by deleting it, so to run it again rebuild it:
 *   .\scripts\seed\reseed-tenant.ps1 -Tenant harbour -Force
 *
 * Writes into walkthroughs/shots/closing/, which is gitignored. The HTML page carries the images
 * base64-encoded, so the page is the committed artefact and these files are scratch.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts closing
 *   ..\.venv\Scripts\python.exe ..\docs\tools\build_closing_walkthrough.py
 */

import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "../tests/auth-helpers";
import { settled } from "./settled";

const SHOTS = join(process.cwd(), "walkthroughs", "shots", "closing");
const PYTHON = join(process.cwd(), "..", ".venv", "Scripts", "python.exe");
const CLOCK = join(process.cwd(), "..", "scripts", "admin", "advance-closing-clock.py");

const TENANT = "harbour";
const CUSTODIAN = "cust-dunmore";
const MEMBER = "ana-quinn";
const RECORDS_OFFICER = "dpo-adeyemi";
const ADMIN_A = "ops-priya";
const ADMIN_B = "ops-ravi";

let step = 0;

async function shot(page: Page, name: string): Promise<void> {
  await settled(page);
  step += 1;
  const n = String(step).padStart(2, "0");
  await page.screenshot({ path: join(SHOTS, `${n}-${name}.png`) });
}

function advance(flag: "--end-retiring" | "--end-closing"): void {
  execFileSync(PYTHON, [CLOCK, "--tenant", TENANT, flag], { stdio: "pipe" });
}

async function sweep(): Promise<{ purged: string[] }> {
  const headers = await bearerFor(ADMIN_A);
  const r = await fetch(`${API_BASE}/lifecycle/sweep`, { method: "POST", headers });
  if (!r.ok) throw new Error(`the sweep was refused: HTTP ${r.status}`);
  return r.json();
}

async function standing(): Promise<string> {
  const headers = await bearerFor(ADMIN_A);
  const body = await fetch(`${API_BASE}/lifecycle/organisations`, { headers }).then((r) => r.json());
  const row = body.organisations.find((o: { tenant_id: string }) => o.tenant_id === TENANT);
  return row ? row.phase : "missing";
}

const REASON = "Harbour Clinic is winding down. The last patient was seen on 30 September.";

test("capture: an organisation is closed, held for a legal matter, then deleted", async ({ page }) => {
  test.setTimeout(300_000);
  mkdirSync(SHOTS, { recursive: true });

  const start = await standing();
  if (start !== "active") {
    throw new Error(
      `Harbour Clinic is ${start}, not open. Rebuild it first: .\\scripts\\seed\\reseed-tenant.ps1 -Tenant harbour -Force`,
    );
  }

  // ---- Part one: the data custodian closes the clinic -----------------------
  await loginAs(page, CUSTODIAN);
  await page.goto("/closing");
  await expect(page.getByTestId("closing-status")).toContainText("Open");
  await expect(page.getByTestId("closing-stages")).toBeVisible();
  await shot(page, "dunmore-opens-closing-the-organisation");

  await page.getByTestId("close-organisation").click();
  await page.getByTestId("confirm-dialog-reason").fill(REASON);
  await shot(page, "dunmore-says-why");

  await page.getByTestId("confirm-dialog-confirm").click();
  await expect(page.getByTestId("closing-status")).toContainText("Being closed");
  await expect(page.getByTestId("closing-banner")).toBeVisible();
  await shot(page, "the-clinic-is-being-closed");

  // ---- Part two: a member can read, and cannot stop it ----------------------
  await loginAs(page, MEMBER);
  await page.goto("/");
  await expect(page.getByTestId("closing-banner")).toBeVisible();
  await shot(page, "quinn-sees-the-banner");

  await page.getByTestId("closing-banner").getByRole("link", { name: "See where it stands" }).click();
  await expect(page.getByTestId("cancel-closing")).toBeVisible();
  await page.getByTestId("cancel-closing").click();
  await expect(page.getByTestId("failure")).toBeVisible();
  await shot(page, "quinn-is-refused");

  // ---- Part three: the 15 days pass, and the clinic's people can do nothing -
  advance("--end-retiring");
  await loginAs(page, CUSTODIAN);
  await expect(page.getByTestId("closing-notice")).toContainText("Closing");
  await shot(page, "dunmore-finds-the-clinic-closed");

  // ---- Part four: a platform administrator records a legal hold -------------
  await loginAs(page, ADMIN_A);
  await page.goto("/closing");
  await expect(page.getByTestId("all-organisations")).toContainText(TENANT);
  await page.getByRole("heading", { name: "Every organisation" }).evaluate((e) => e.scrollIntoView({ block: "start" }));
  await shot(page, "priya-sees-it-closing");

  await page.goto("/legal-holds");
  await expect(page.getByTestId("place-hold")).toBeVisible();
  await page.getByTestId("hold-tenant").selectOption(TENANT);
  await page.getByTestId("hold-custodian").selectOption(RECORDS_OFFICER);
  await page.getByTestId("hold-matter-name").fill("Alder v Harbour Clinic");
  await page.getByTestId("hold-matter-number").fill("HC-2026-0417");
  await page.getByTestId("hold-authority").fill("Aldous and Brennan LLP, for the claimant");
  await page.getByTestId("hold-reference").fill("AB/2026/17");
  await page.getByTestId("hold-attorney").fill("Ruth Aldous");
  await page.getByTestId("hold-attorney-email").fill("ruth.aldous@aldousbrennan.example");
  await page.getByTestId("hold-trigger").fill("Letter before claim received on 1 October 2026");
  await page.getByTestId("hold-received").fill("2026-10-01");
  await page.getByTestId("hold-description").fill("A patient claim about a procedure carried out in 2024.");
  await page.getByTestId("hold-preserve").fill("Every record of the claimant, and the audit trail of who read them.");
  await page.getByRole("heading", { name: "Record a legal hold" }).evaluate((e) => e.scrollIntoView({ block: "start" }));
  await shot(page, "priya-fills-in-the-notice");

  await page.getByTestId("hold-submit").click();
  await expect(page.getByTestId("holds-waiting")).toContainText("HC-2026-0417");
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "priya-records-the-hold");

  await page.getByTestId("hold-approve").click();
  await expect(page.getByTestId("failure")).toBeVisible();
  await expect(page.getByTestId("failure")).toContainText("different platform administrator");
  await shot(page, "priya-cannot-approve-her-own");

  // ---- Part five: a second administrator approves ---------------------------
  await loginAs(page, ADMIN_B);
  await page.goto("/legal-holds");
  await expect(page.getByTestId("holds-waiting")).toContainText("HC-2026-0417");
  await page.getByTestId("hold-note").fill("Notice checked against the issuing firm's reference.");
  await shot(page, "ravi-reads-the-notice");

  await page.getByTestId("hold-approve").click();
  await expect(page.getByTestId("holds-in-force")).toContainText("HC-2026-0417");
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "the-hold-is-in-force");

  // ---- Part six: the temporary custodian acknowledges -----------------------
  await loginAs(page, RECORDS_OFFICER);
  await expect(page.getByTestId("hold-for-custodian")).toBeVisible();
  await shot(page, "adeyemi-is-named-custodian");

  await page.getByTestId("hold-acknowledge").click();
  await expect(page.getByTestId("hold-for-custodian")).toContainText("You acknowledged this hold");
  await shot(page, "adeyemi-acknowledges");

  // ---- Part seven: the time is up, and the hold stands ----------------------
  advance("--end-closing");
  const held = await sweep();
  expect(held.purged).not.toContain(TENANT);
  await loginAs(page, ADMIN_A);
  await page.goto("/closing");
  await expect(page.getByTestId("all-organisations")).toContainText("Held, time is up");
  await page.getByRole("heading", { name: "Every organisation" }).evaluate((e) => e.scrollIntoView({ block: "start" }));
  await shot(page, "the-time-is-up-and-nothing-is-deleted");

  // ---- Part eight: the matter ends, the hold is released --------------------
  await loginAs(page, ADMIN_B);
  await page.goto("/legal-holds");
  await page.getByTestId("hold-release").click();
  await page.getByTestId("confirm-dialog-reason").fill("Matter settled. Written confirmation received from Aldous and Brennan LLP.");
  await shot(page, "ravi-releases-the-hold");

  await page.getByTestId("confirm-dialog-confirm").click();
  await expect(page.getByText("Released", { exact: true }).first()).toBeVisible();
  await page.goto("/closing");
  await expect(page.getByTestId("all-organisations")).toContainText("Deleted on");
  await page.getByRole("heading", { name: "Every organisation" }).evaluate((e) => e.scrollIntoView({ block: "start" }));
  await shot(page, "the-closing-period-starts-again");

  // ---- Part nine: nothing stands in the way, and the clinic is deleted ------
  advance("--end-closing");
  const done = await sweep();
  expect(done.purged).toContain(TENANT);
  await loginAs(page, ADMIN_A);
  await page.goto("/legal-holds");
  await expect(page.getByTestId("deletion-records")).toContainText(TENANT);
  await page.getByRole("heading", { name: "Deleted organisations" }).evaluate((e) => e.scrollIntoView({ block: "start" }));
  await shot(page, "harbour-clinic-is-deleted");
});
