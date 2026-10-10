/**
 * Captures the console screens of the notebook page of Story A (docs/public/walkthroughs/work-from-a-notebook-walkthrough.html): Sam types the code
 * a Python notebook shows, approves it, and later ends the notebook's sign-in.
 *
 * Like story-a-derive.spec.ts, this runs in turns with a Python session (scripts/demo/notebook-demo.py), started by
 * scripts/walkthroughs/capture-health-story.py. The notebook is waiting for the approval while PART "approve" runs.
 *
 *   $env:NOTEBOOK_SYNC = "<folder>"; $env:NOTEBOOK_PART = "approve"
 *   npx playwright test --config=walkthroughs/playwright.config.ts story-a-notebook
 */

import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { loginAs } from "../tests/auth-helpers";
import { settled } from "./settled";
import { snap } from "./snap";

const PART = process.env.NOTEBOOK_PART ?? "";
const SHOTS = join(process.cwd(), "walkthroughs", "shots", "notebook-health");
const PERSON = "sam-researcher";
const LABEL = "Sam's readmission notebook";

async function shot(page: Page, number: string, name: string): Promise<void> {
  await settled(page);
  await snap(page, { path: join(SHOTS, `${number}-${name}.png`) });
}

test.beforeAll(() => mkdirSync(SHOTS, { recursive: true }));

test("approve: Sam types the code and approves the notebook", async ({ page }) => {
  test.skip(PART !== "approve");
  const folder = process.env.NOTEBOOK_SYNC;
  if (!folder) throw new Error("NOTEBOOK_SYNC must name the folder the session writes signin.json into");
  const began = JSON.parse(readFileSync(join(folder, "signin.json"), "utf8"));

  await loginAs(page, PERSON);
  await page.goto(began.verification_uri_complete);
  await expect(page.getByTestId("notebook-code-form")).toBeVisible({ timeout: 20_000 });
  await page.getByTestId("notebook-code-input").fill(began.user_code);
  await shot(page, "01", "sam-types-the-code");
  await page.getByTestId("notebook-code-submit").click();
  await expect(page.getByTestId("notebook-consent")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("notebook-consent-label")).toHaveText(LABEL);
  await shot(page, "02", "sam-checks-and-approves");
  await page.getByTestId("notebook-approve").click();
  await expect(page.getByTestId("notebook-done")).toBeVisible({ timeout: 20_000 });
  await shot(page, "03", "the-notebook-is-signed-in");
});

test("end: Sam sees the sign-in listed and ends it", async ({ page }) => {
  test.skip(PART !== "end");
  await loginAs(page, PERSON);
  await page.goto("/notebook-sign-ins");
  const row = page.getByTestId("notebook-sign-ins").locator("tr", { hasText: LABEL });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await expect(row).toContainText("In force");
  await shot(page, "04", "your-notebooks");
  await row.getByRole("button", { name: "End now" }).click();
  // The notebook was used a moment ago, so the page asks first.
  await expect(row.getByRole("alertdialog")).toContainText("may be in the middle of work");
  await shot(page, "05", "end-it-asks-first");
  await row.getByRole("button", { name: "Yes, end it" }).click();
  await expect(row).toContainText("Ended by the person", { timeout: 15_000 });
  await shot(page, "06", "the-sign-in-has-ended");
});
