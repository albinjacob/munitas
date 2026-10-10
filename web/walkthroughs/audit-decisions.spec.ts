/**
 * Captures the screenshots of "Read every access decision, including every refusal"
 * (docs/public/walkthroughs/audit-decisions-walkthrough.html), the Audit page of the Health walkthroughs.
 *
 * Drives the real console against the live Health organisation as Nakamura, the data protection officer. Nothing is mocked: if a step cannot be reached it
 * fails rather than producing a picture of something that did not happen. It reads what the organisation's own activity has put on record, so it is best run
 * after the other Health captures, when there are refusals to read. It makes no decisions of its own; reading the audit is itself recorded, which is what the
 * newest rows of the first list show.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts audit-decisions
 */

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { loginAs } from "../tests/auth-helpers";
import { settled } from "./settled";
import { snap } from "./snap";

const NAKAMURA = "dpo-nakamura";
const MARLOW = "aud-marlow";
const DIR = "audit-decisions";

async function shot(page: Page, number: string, name: string): Promise<void> {
  await settled(page);
  const dir = join(process.cwd(), "walkthroughs", "shots", DIR);
  mkdirSync(dir, { recursive: true });
  await snap(page, { path: join(dir, `${number}-${name}.png`) });
}

test("Nakamura reads the record of every access decision", async ({ page }) => {
  test.setTimeout(180_000);

  // ---- The home page of the data protection officer -----------------------------------------------------------------------
  await loginAs(page, NAKAMURA);
  await page.goto("/");
  await expect(page.getByText("Requests recorded")).toBeVisible({ timeout: 20000 });
  await expect(page.getByText("Latest refusals")).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "01", "nakamura-sees-the-counts-and-the-latest-refusals");

  // ---- The auditors read the Locked data audit. Reading it is itself recorded, and these reads are the newest rows of the list below, whatever else
  //      the organisation has been doing, so this page does not depend on what was captured before it.
  await loginAs(page, MARLOW);
  await page.goto("/locking-audit");
  await expect(page.getByTestId("audit-reads")).toBeVisible({ timeout: 20000 });
  await loginAs(page, NAKAMURA);
  await page.goto("/locking-audit");
  await expect(page.getByTestId("audit-reads")).toBeVisible({ timeout: 20000 });
  await page.goto("/");
  await expect(page.getByText("Requests recorded")).toBeVisible({ timeout: 20000 });

  // ---- Every request, newest first: the first rows are the auditors' own reads ---------------------------------------------------------------------
  await page.getByRole("link", { name: "See every decision" }).click();
  await expect(page.getByTestId("audit-rows")).toBeVisible({ timeout: 20000 });
  await expect(page.getByRole("heading", { name: "Access decisions" })).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "02", "every-request-and-what-happened-to-it");

  // ---- Only the refusals, with the reason for each -----------------------------------------------------------------------------
  await page.getByRole("button", { name: /^Refused/ }).click();
  await expect(page.getByTestId("audit-rows")).toBeVisible();
  const refused = page.getByTestId("audit-rows").locator("tr");
  expect(await refused.count(), "there are no refusals to read").toBeGreaterThan(0);
  for (const row of await refused.all()) {
    await expect(row).toHaveAttribute("data-denied", "true");
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "03", "only-the-refusals-with-the-reason-for-each");
});
