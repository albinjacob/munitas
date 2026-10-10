/**
 * Captures the console screens of Story A, the Health researcher who finds a table, asks to read it and makes a new dataset from it
 * (docs/public/walkthroughs/find-and-ask-walkthrough.html and make-a-dataset-walkthrough.html).
 *
 * The story is one timeline: a Python session (scripts/demo/derive-demo.py) and the console take turns. The session stops after each of its steps
 * and waits for a signal; this file is what runs in between, one PART at a time, and is started by scripts/walkthroughs/capture-health-story.py.
 * Run on its own, a part does only its own screens. Nothing here is mocked: if a step cannot be reached, it fails rather than producing a picture
 * of something that did not happen.
 *
 *   $env:DERIVE_SYNC = "<folder the session writes target.txt into>"; $env:DERIVE_PART = "2"
 *   npx playwright test --config=walkthroughs/playwright.config.ts story-a-derive
 *
 * Parts: 1 (after the session's first step) the open lookup; 2 the closed table, the request, the custodian's decision; 6 the new dataset;
 * 7 where it came from; 10 the decision log; 11 the new dataset after the access was withdrawn.
 */

import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { bearerFor, loginAs } from "../tests/auth-helpers";
import { API_BASE } from "../config/ports";
import { settled } from "./settled";
import { snap } from "./snap";

const PART = process.env.DERIVE_PART ?? "";
const SHOTS = join(process.cwd(), "walkthroughs", "shots", "derive-health");
const RESEARCHER = "sam-researcher";
const CUSTODIAN = "cust-hartley";
const RAW = "admissions";
const LOOKUP = "diagnosis_codes";
// The same words the session uses, so that the lease the custodian grants here is the one the session's token reads under.
const PURPOSE = "readmission study";
const JUSTIFICATION = "Compare readmission rates for older patients with a chronic heart condition.";

function target(): string {
  const folder = process.env.DERIVE_SYNC;
  if (!folder) throw new Error("DERIVE_SYNC must name the folder the session writes target.txt into");
  return readFileSync(join(folder, "target.txt"), "utf8").trim();
}

async function shot(page: Page, number: string, name: string): Promise<void> {
  await settled(page);
  await snap(page, { path: join(SHOTS, `${number}-${name}.png`) });
}

async function api(path: string, who: string, init: RequestInit = {}): Promise<any> {
  const headers = { ...(await bearerFor(who)), "Content-Type": "application/json" };
  const r = await fetch(`${API_BASE}${path}`, { ...init, headers });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`${init.method ?? "GET"} ${path} -> ${r.status} ${JSON.stringify(body).slice(0, 300)}`);
  return body;
}

async function versionIdOf(who: string, dataset: string): Promise<string> {
  const found = await api(`/datasets?q=${encodeURIComponent(dataset)}`, who);
  const row = found.datasets.find((d: { name: string }) => d.name === dataset);
  expect(row, `${dataset} is not listed for ${who}`).toBeTruthy();
  const versions = await api(`/datasets/${row.id}/versions`, who);
  return versions[0].dataset_version_id;
}

async function searchDatasets(page: Page, text: string): Promise<void> {
  await page.goto("/datasets");
  await page.getByTestId("dataset-search").fill(text);
  // The count of what the person can read is worked out after the row appears, so wait for it, or the picture shows an empty cell.
  await expect(page.getByRole("row").filter({ hasText: text }).first()).toContainText(/\d+ of \d+/);
}

test.beforeAll(() => mkdirSync(SHOTS, { recursive: true }));

test("part 1: Sam finds the open lookup", async ({ page }) => {
  test.skip(PART !== "1");
  await loginAs(page, RESEARCHER);
  await searchDatasets(page, LOOKUP);
  await expect(page.getByRole("row").filter({ hasText: LOOKUP }).first()).toContainText("1 of 1");
  await shot(page, "01", "sam-finds-the-open-lookup");
});

test("part 2: Sam finds the closed table, asks, and Hartley decides", async ({ page }) => {
  test.skip(PART !== "2");
  const raw = await versionIdOf(CUSTODIAN, RAW);
  await loginAs(page, RESEARCHER);
  await searchDatasets(page, RAW);
  await expect(page.getByRole("row").filter({ hasText: RAW }).first()).toContainText("0 of 1");
  await shot(page, "02", "sam-finds-admissions-closed");

  await page.goto(`/versions/${raw}`);
  await expect(page.getByTestId("request-access")).toBeVisible();
  await page.getByTestId("request-purpose").fill(PURPOSE);
  await page.getByTestId("request-justification").fill(JUSTIFICATION);
  await shot(page, "03", "sam-fills-in-the-request");
  await page.getByTestId("request-submit").click();
  await expect(page.getByTestId("request-sent")).toBeVisible();
  await shot(page, "04", "request-sent");

  await loginAs(page, CUSTODIAN);
  await page.goto("/");
  await expect(page.getByTestId("pending-requests")).toContainText(PURPOSE);
  await shot(page, "05", "hartley-sees-the-request");
  const row = page.getByTestId("pending-requests").locator("li", { hasText: PURPOSE });
  await row.getByRole("button", { name: "Grant access" }).click();
  await expect(row).toHaveCount(0);
  await shot(page, "06", "after-granting");
});

test("part 6: Sam finds the new dataset", async ({ page }) => {
  test.skip(PART !== "6");
  await loginAs(page, RESEARCHER);
  await searchDatasets(page, target());
  await expect(page.getByRole("row").filter({ hasText: target() }).first()).toContainText("1 of 1");
  await shot(page, "07", "sam-finds-the-new-dataset");
});

test("part 7: where the new dataset came from", async ({ page }) => {
  test.skip(PART !== "7");
  const made = await versionIdOf(RESEARCHER, target());
  await loginAs(page, RESEARCHER);
  await page.goto(`/versions/${made}`);
  await expect(page.getByText("Where this came from")).toBeVisible();
  await page.getByText("Where this came from").scrollIntoViewIfNeeded();
  await shot(page, "08", "where-it-came-from");
});

test("part 10: every refusal is on record", async ({ page }) => {
  test.skip(PART !== "10");
  await loginAs(page, CUSTODIAN);
  await page.goto("/audit");
  await expect(page.getByText("Access decisions").first()).toBeVisible();
  // The newest rows are the platform's own reads of the inputs. The refusals are what this step is about, so show the log filtered to them.
  await page.getByRole("button", { name: /^Refused/ }).click();
  await page.waitForTimeout(1500);
  await shot(page, "09", "the-decision-log");
});

test("part 11: the new dataset after the access is withdrawn", async ({ page }) => {
  test.skip(PART !== "11");
  await loginAs(page, RESEARCHER);
  await searchDatasets(page, target());
  await expect(page.getByRole("row").filter({ hasText: target() }).first()).toContainText("0 of 1");
  await shot(page, "10", "sam-after-the-lease-is-withdrawn");
});
