/**
 * Captures the screenshots behind docs/public/walkthroughs/locked-columns-walkthrough.html.
 *
 * Drives the real console against the live stack as Devi (a data engineer), Sam (a researcher), Hartley (the data custodian for Cardiology) and Marlow
 * (a privacy auditor), all in the Health organisation. Nothing here is mocked: if a step cannot be reached, this fails rather than producing a picture
 * of something that did not happen. The table is sealed through the console itself, from the two files in
 * walkthroughs/fixtures/locked-appointments/ (every person and number in them is invented).
 *
 * Writes into walkthroughs/shots/locked-columns/, which is gitignored. The HTML page carries the images base64-encoded, so the page is the committed
 * artefact and these files are scratch.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts locked-columns
 *   ..\.venv\Scripts\python.exe ..\docs\tools\build_locked_columns_walkthrough.py
 */

import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { bearerFor, loginAs } from "../tests/auth-helpers";
import { API_BASE } from "../config/ports";
import { settled } from "./settled";

const SHOTS = join(process.cwd(), "walkthroughs", "shots", "locked-columns");
const FIXTURES = join(process.cwd(), "walkthroughs", "fixtures", "locked-appointments");
const CONTRACT = readFileSync(join(FIXTURES, "contract.yaml"));
const ROWS = readFileSync(join(FIXTURES, "rows.csv"));

const DEVI = "eng-devi";
const SAM = "sam-researcher";
const HARTLEY = "cust-hartley";
const MARLOW = "aud-marlow";

const PURPOSE = "to see what the postcode column really holds before I write the rule that locks it";

let step = 0;

async function shot(page: Page, name: string): Promise<void> {
  await settled(page);
  step += 1;
  await page.screenshot({ path: join(SHOTS, `${String(step).padStart(2, "0")}-${name}.png`) });
}

// Puts the named part of a page at the top of the picture, so the whole of it is in view.
async function shotAt(page: Page, testid: string, name: string, offset = 90): Promise<void> {
  await settled(page);
  await page.getByTestId(testid).first().evaluate((e, y) => {
    const top = e.getBoundingClientRect().top + window.scrollY - y;
    window.scrollTo(0, Math.max(0, top));
  }, offset);
  await page.waitForTimeout(250);
  step += 1;
  await page.screenshot({ path: join(SHOTS, `${String(step).padStart(2, "0")}-${name}.png`) });
}

async function headingAt(page: Page, heading: string, name: string, offset = 90): Promise<void> {
  await settled(page);
  await page.getByRole("heading", { name: heading, exact: true }).first().evaluate((e, y) => {
    const top = e.getBoundingClientRect().top + window.scrollY - y;
    window.scrollTo(0, Math.max(0, top));
  }, offset);
  await page.waitForTimeout(250);
  step += 1;
  await page.screenshot({ path: join(SHOTS, `${String(step).padStart(2, "0")}-${name}.png`) });
}

test("capture: lock what identifies a person, and read it only with permission", async ({ page }) => {
  mkdirSync(SHOTS, { recursive: true });

  const hartleyAuth = await bearerFor(HARTLEY);
  const organisation = await fetch(`${API_BASE}/organisation?tenant_id=health`, { headers: hartleyAuth }).then((r) => r.json());
  const cardiology = organisation.departments.find((d: { name: string }) => d.name === "Cardiology").id;

  // Leases that an earlier capture left in force or waiting would sit in these pictures as part of the story, so they are ended first.
  const left = await fetch(`${API_BASE}/locked-column-leases`, { headers: hartleyAuth }).then((r) => r.json());
  for (const lease of left.granted) await fetch(`${API_BASE}/locked-column-leases/${lease.id}/revoke`, { method: "POST", headers: hartleyAuth });
  for (const lease of left.to_decide) {
    await fetch(`${API_BASE}/locked-column-leases/${lease.id}/decline`, {
      method: "POST",
      headers: { ...hartleyAuth, "content-type": "application/json" },
      body: JSON.stringify({ note: "Left over from an earlier capture" }),
    });
  }

  // Registered through the API, as the story starts after it: this page is about what happens to the rows once they arrive.
  const registered = await fetch(`${API_BASE}/datasets/register`, {
    method: "POST",
    headers: { ...(await bearerFor(DEVI)), "content-type": "application/json" },
    body: JSON.stringify({
      tenant_id: "health",
      name: `cardiology-appointments-${Date.now()}`,
      department_id: cardiology,
      registered_by: DEVI,
      provenance: "internal_regulated",
      declared_class: "RAW",
      source_kind: "upload",
    }),
  });
  expect(registered.status).toBe(201);
  const datasetId = (await registered.json()).id as string;

  // PART ONE. Devi seals a table from two files.
  await loginAs(page, DEVI);
  await page.goto(`/datasets/${datasetId}/ingest`);
  await expect(page.getByTestId("seal-table-section")).toBeVisible();
  await shotAt(page, "seal-table-section", "devi-opens-the-table-option", 40);

  const broken = ROWS.toString("utf8").replace("2026-09-03", "not a date").replace("HC-4101,Tomas Byrne", ",Tomas Byrne");
  await page.getByTestId("seal-table-contract").setInputFiles({ name: "clinic-appointments-contract.yaml", mimeType: "application/x-yaml", buffer: CONTRACT });
  await page.getByTestId("seal-table-rows").setInputFiles({ name: "appointments-with-mistakes.csv", mimeType: "text/csv", buffer: Buffer.from(broken) });
  await page.getByTestId("seal-table-submit").click();
  await expect(page.getByTestId("seal-table-refused")).toBeVisible();
  await shotAt(page, "seal-table-section", "a-bad-file-is-refused", 40);

  await page.getByTestId("seal-table-rows").setInputFiles({ name: "appointments.csv", mimeType: "text/csv", buffer: ROWS });
  await page.getByTestId("seal-table-submit").click();
  // The first table in an organisation makes its keys and writes its first file, which takes a few seconds.
  await expect(page.getByTestId("table-sealed")).toBeVisible({ timeout: 30_000 });
  await shotAt(page, "seal-table-section", "the-table-is-sealed", 40);

  // PART TWO. What the table says about itself, and what Devi may do with it.
  await page.getByTestId("open-sealed-table").click();
  await expect(page.getByRole("heading", { name: "Locked columns" })).toBeVisible();
  const versionUrl = page.url();
  const versionId = versionUrl.split("/versions/")[1];
  await headingAt(page, "Locked columns", "the-locked-columns", 24);

  await page.goto(`/versions/${versionId}/locked-rows`);
  await expect(page.getByTestId("failure")).toBeVisible();
  await shot(page, "devi-is-refused-the-rows");

  // PART THREE. Devi asks, and the request waits. Sam, a researcher, has no way to ask.
  await page.goto(versionUrl);
  await expect(page.getByTestId("ask-lease")).toBeVisible();
  await page.getByTestId("lease-purpose").fill(PURPOSE);
  await page.getByTestId("lease-days").fill("3");
  await page.getByTestId("lease-rows").fill("5");
  await headingAt(page, "Your access to the locked values", "devi-asks-for-five-rows", 60);
  await page.getByTestId("lease-submit").click();
  await expect(page.getByTestId("locked-access")).toContainText("waiting for an approver");
  await headingAt(page, "Your access to the locked values", "the-request-waits", 60);

  await loginAs(page, SAM);
  await page.goto(versionUrl);
  await expect(page.getByTestId("locked-access")).toContainText("cannot read the locked values and cannot ask");
  await headingAt(page, "Your access to the locked values", "sam-has-no-way-to-ask", 60);

  // PART FOUR. Hartley, who answers for Cardiology, decides, and records what to do about small groups.
  await loginAs(page, HARTLEY);
  await page.goto("/locked-column-access");
  await expect(page.getByTestId("leases-to-decide")).toContainText(PURPOSE);
  await shot(page, "hartley-sees-the-request");

  const row = page.getByTestId("leases-to-decide").locator("tr", { hasText: PURPOSE });
  await row.locator("input").fill("Five rows is enough for that.");
  await shot(page, "hartley-writes-a-note");
  await row.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("leases-granted")).toContainText("In force");
  await shot(page, "the-lease-is-in-force");

  await page.goto(versionUrl);
  await expect(page.getByTestId("group-size-finding")).toBeVisible();
  await headingAt(page, "Small groups", "hartley-sees-the-small-groups", 60);
  await page.getByTestId("group-size-note").fill("The next contract version will lock the postcode.");
  await page.getByTestId("group-size-submit").click();
  await expect(page.getByTestId("group-size-decided")).toBeVisible();
  await headingAt(page, "Small groups", "hartley-records-a-decision", 60);

  // PART FIVE. Devi reads the five rows the lease opens.
  await loginAs(page, DEVI);
  await page.goto(versionUrl);
  await expect(page.getByTestId("locked-access")).toContainText("You hold a lease");
  await headingAt(page, "Your access to the locked values", "devi-holds-a-lease", 60);
  await page.getByTestId("read-locked-rows").click();
  await expect(page.getByTestId("locked-rows").locator("tbody tr")).toHaveCount(5);
  await shot(page, "devi-reads-five-rows");

  // PART SIX. Marlow checks how it was used, and sees no name.
  await loginAs(page, MARLOW);
  await page.goto("/locking-audit");
  await expect(page.getByTestId("audit-reads")).toBeVisible();
  await shot(page, "marlow-reads-the-audit");
  await headingAt(page, "How each table's columns were treated", "marlow-sees-how-columns-were-treated");
  await headingAt(page, "Keys", "marlow-sees-the-keys");
});
