/**
 * Captures the screenshots behind docs/public/walkthroughs/before-any-data-walkthrough.html.
 *
 * Drives the real console against the live stack as Priya, the platform administrator of the Health organisation. Nothing here is mocked: if a step
 * cannot be reached, this fails rather than producing a picture of something that did not happen. The story is what an organisation looks like before
 * anybody brings data in: who owns what, who holds which role, what each role may read, and which limits the organisation has set. The platform's own
 * parts are deliberately not shown: the walkthroughs do not introduce Munitas's infrastructure. Every screen is one that exists in the console for any
 * signed-in person of the organisation.
 *
 * Writes into walkthroughs/shots/before-any-data/, which is gitignored. The HTML page carries the images base64-encoded, so the page is the
 * committed artefact and these files are scratch.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts before-any-data
 *   ..\.venv\Scripts\python.exe ..\docs\tools\build_before_any_data_walkthrough.py
 */

import { mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { EMAIL_BY_DIRECTORY_ID, PASSWORD, loginAs } from "../tests/auth-helpers";
import { settled } from "./settled";
import { snap } from "./snap";

const SHOTS = join(process.cwd(), "walkthroughs", "shots", "before-any-data");
const ADMIN = "ops-priya";

let step = 0;

async function shot(page: Page, name: string, fullPage = false): Promise<void> {
  await settled(page);
  step += 1;
  await snap(page, { path: join(SHOTS, `${String(step).padStart(2, "0")}-${name}.png`), fullPage });
}

test("capture: before any data arrives", async ({ page }) => {
  rmSync(SHOTS, { recursive: true, force: true });
  mkdirSync(SHOTS, { recursive: true });

  // ---- Priya signs in ---------------------------------------------------
  await page.goto("/auth/login");
  await page.getByTestId("login-identifier").fill(EMAIL_BY_DIRECTORY_ID[ADMIN]);
  await page.getByTestId("login-password").fill(PASSWORD);
  await shot(page, "priya-signs-in");
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("current-persona")).toBeVisible();

  // ---- Who owns what ----------------------------------------------------
  await page.goto("/departments");
  await expect(page.getByRole("heading", { name: "Departments", exact: true })).toBeVisible();
  await expect(page.getByText("Cardiology").first()).toBeVisible();
  await expect(page.getByText("Radiology").first()).toBeVisible();
  await shot(page, "the-departments");

  // ---- Who exists, and what each person holds ---------------------------
  await page.goto("/directory");
  await expect(page.getByTestId("directory-rows")).toBeVisible();
  await shot(page, "the-people");

  // ---- What each role can read ------------------------------------------
  await page.goto("/roles");
  await expect(page.getByTestId("roles-page")).toBeVisible();
  await shot(page, "what-each-role-can-do", true);

  // ---- The limits the organisation has set ------------------------------
  await page.goto("/access-rules");
  await expect(page.getByRole("heading", { name: /access rules/i }).first()).toBeVisible();
  await shot(page, "the-limits-the-organisation-has-set", true);
});
