/**
 * Captures the screenshots behind docs/people-administration-walkthrough.html.
 *
 * Drives the real console against the live stack, signing in as three people
 * in turn, because the whole point of the flow is that no single person can
 * complete it. Nothing here is mocked: if a step cannot be reached, this
 * fails rather than producing a picture of something that did not happen.
 *
 * Writes into walkthroughs/shots/people-administration/, which is gitignored. The HTML page carries
 * the images base64-encoded, so the page is the committed artefact and these
 * files are scratch.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts
 *   ..\.venv\Scripts\python.exe ..\docs\tools\build_people_walkthrough.py
 */

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { loginAs } from "../tests/auth-helpers";
import { settled } from "./settled";
import {
  ADMIN,
  ASKER,
  CUSTODIAN,
  WANTED,
  clearTheRole,
} from "../tests/role-fixtures";

const SHOTS = join(process.cwd(), "walkthroughs", "shots", "people-administration");

// The console shows a role by its readable name, not the stored one.
const WANTED_SHOWN = "De-identification reviewer";

const WHY = "covering de-identification reviews while Imani is away";

let step = 0;

// Puts the "Roles held" heading at the top of the picture, so the table under it is whole.
async function showRolesHeld(page: Page): Promise<void> {
  await page.getByRole("heading", { name: "Roles held" }).evaluate((e) => e.scrollIntoView({ block: "start" }));
}

async function shot(page: Page, name: string): Promise<void> {
  await settled(page);
  step += 1;
  const n = String(step).padStart(2, "0");
  await page.screenshot({ path: join(SHOTS, `${n}-${name}.png`) });
}

test("capture: a role is asked for, refused the wrong way, then granted", async ({
  page,
}) => {
  mkdirSync(SHOTS, { recursive: true });
  // A capture that inherited a grant from an earlier run would photograph
  // two identical rows and call one of them new.
  await clearTheRole();

  // ---- Sam asks -------------------------------------------------------
  await loginAs(page, ASKER);
  await page.goto("/directory");
  // The list loads after the page does, so wait for it. A picture taken before it arrives shows
  // "Loading the directory" and proves nothing.
  await expect(page.getByTestId("ask-role")).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "Hartley" })).toBeVisible();
  await shot(page, "sam-opens-the-directory");

  await page.getByTestId("ask-role").fill(WANTED);
  await page.getByTestId("ask-reason").fill(WHY);
  await page.getByTestId("ask-reason").scrollIntoViewIfNeeded();
  await shot(page, "sam-fills-in-the-ask");

  await page.getByTestId("ask-submit").click();
  const asks = page.getByTestId("role-asks");
  await expect(asks).toContainText(WANTED_SHOWN);
  await shot(page, "the-ask-is-waiting");

  // The same shot already shows that Sam's own ask carries no buttons, so
  // there is no second capture of it here: a walkthrough that shows the same
  // screen twice reads as padding.
  await expect(asks).toContainText("your own ask");

  // ---- Priya, the administrator, is refused ---------------------------
  await loginAs(page, ADMIN);
  await page.goto("/directory");
  await page.getByTestId("role-asks").scrollIntoViewIfNeeded();
  await shot(page, "the-administrator-sees-the-ask");

  await page
    .getByTestId("role-asks")
    .getByTestId("decide-reason")
    .first()
    .fill("seems reasonable");
  await page
    .getByTestId("role-asks")
    .getByTestId("decide-approve")
    .first()
    .click();
  await expect(page.getByTestId("failure")).toContainText(
    "holds no role that may decide who holds a role",
  );
  await page.getByTestId("failure").scrollIntoViewIfNeeded();
  await shot(page, "the-administrator-is-refused");

  // ---- Hartley, the custodian, decides --------------------------------
  await loginAs(page, CUSTODIAN);
  await page.goto("/directory");
  await page
    .getByTestId("role-asks")
    .getByTestId("decide-reason")
    .first()
    .fill("Imani is away and reviews cannot wait");
  await page.getByTestId("role-asks").scrollIntoViewIfNeeded();
  await shot(page, "the-custodian-records-a-reason");

  await page
    .getByTestId("role-asks")
    .getByTestId("decide-approve")
    .first()
    .click();

  const held = page.getByTestId("roles-held");
  await expect(held).toContainText(WANTED_SHOWN);
  await expect(held).toContainText("never checked");
  await showRolesHeld(page);
  await shot(page, "granted-but-never-checked");

  const row = held.locator("tr").filter({ hasText: WANTED_SHOWN }).first();
  await row.getByTestId("attest-keep").click();
  // Waiting on the absence, not on "checked": "never checked" contains it,
  // so asserting the presence alone would pass against the stale row and
  // capture a screenshot of the state before the click landed.
  await expect(row).not.toContainText("never checked");
  await expect(row).toContainText("checked");
  await showRolesHeld(page);
  await shot(page, "somebody-confirms-it-is-still-needed");

  // Also the teardown: a withdrawn grant leaves the list, so the next
  // capture run starts where this one did.
  await row.getByTestId("attest-withdraw").click();
  await expect(
    held.locator("tr").filter({ hasText: WANTED_SHOWN }),
  ).toHaveCount(0);
  await showRolesHeld(page);
  await shot(page, "withdrawn-and-gone");
});
