/**
 * Captures the screenshots of "Set the risk levels for AI agents"
 * (docs/public/walkthroughs/agent-levels-walkthrough.html), the first page of AI agents in the Health walkthroughs.
 *
 * Drives the real console against the live Health organisation as Okafor (the AI risk owner) and Imani (the de-identification reviewer). Nothing is mocked.
 *
 * What it does in Health: Okafor changes how often an agent at Moderate has to be re-confirmed, from 180 days to 120, and saves the list as a new version. Imani reads the list and is
 * offered no way to change it. Afterwards the capture writes the original list back as one more version, with the same levels and the same switch, so that the other Health pages
 * still read as they were captured; the history then holds one version more than the capture shows.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts agent-levels
 */

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "../tests/auth-helpers";
import { settled } from "./settled";
import { snap } from "./snap";

const OKAFOR = "risk-okafor";
const IMANI = "rev-imani";
const DIR = "agent-levels";
const AGENT = "care-gap-finder";   // an Oncology agent held at Moderate
const MODERATE = 1; // the second level of four, counting from zero
const WHY = "Agents at Moderate were going a long time between checks, so they are now re-checked every 120 days instead of 180.";

async function shot(page: Page, number: string, name: string): Promise<void> {
  await settled(page);
  const dir = join(process.cwd(), "walkthroughs", "shots", DIR);
  mkdirSync(dir, { recursive: true });
  await snap(page, { path: join(dir, `${number}-${name}.png`) });
}

/** Puts a part of the page just below the page header, so that the whole of it is in view. */
async function toTop(page: Page, part: Locator, offset = 110): Promise<void> {
  await part.first().evaluate((e, y) => window.scrollTo(0, Math.max(0, e.getBoundingClientRect().top + window.scrollY - y)), offset);
  await page.waitForTimeout(250);
}

async function toSection(page: Page, heading: string, offset = 90): Promise<void> {
  await toTop(page, page.getByRole("heading", { name: heading, exact: true }), offset);
}

interface Level {
  name: string;
  description: string;
  framework_label: string | null;
  second_approver: boolean;
  blocks: boolean;
  reconfirm_days: number | null;
}

/** The levels in force, in the shape the platform takes when they are written. */
async function currentLevels(): Promise<{ levels: Level[]; enforce: boolean; version: number }> {
  const page = await fetch(`${API_BASE}/risk-levels`, { headers: await bearerFor(OKAFOR) }).then((r) => r.json());
  const levels = (page.current.levels as Level[]).map((l) => ({
    name: l.name,
    description: l.description,
    framework_label: l.framework_label ?? null,
    second_approver: l.second_approver,
    blocks: l.blocks,
    reconfirm_days: l.reconfirm_days ?? null,
  }));
  for (const l of levels) expect(Object.values(l).every((v) => v !== undefined), `a level is missing a field: ${JSON.stringify(l)}`).toBe(true);
  return { levels, enforce: Boolean(page.current.enforce), version: Number(page.current.version) };
}

/** Picture 08: an agent held at Moderate, with its re-confirmation clock. Nothing is changed. */
async function agentPicture(page: Page): Promise<void> {
  await loginAs(page, OKAFOR);
  await page.goto("/agent-registry");
  await page.getByTestId("registry-search").fill(AGENT);
  await expect(page.getByTestId("registry-count")).toContainText("Showing 1 of ", { timeout: 20000 });
  await page.getByRole("link", { name: AGENT, exact: true }).click({ timeout: 15000 });
  await expect(page.getByTestId("reconfirm-clock")).toContainText("every 120 days", { timeout: 20000 });
  await toSection(page, "Sponsor and re-confirmation", 90);
  await shot(page, "08", "the-change-reaches-an-agent-held-at-moderate");
}

test("the AI risk owner changes a level and saves it as a new version, the history keeps both, a reviewer cannot change it, and the effect shows on an agent held at that level", async ({ page }) => {
  test.setTimeout(240_000);
  const original = await currentLevels();
  expect(original.levels[MODERATE].name).toBe("Moderate");
  expect(original.levels[MODERATE].reconfirm_days).toBe(180);
  expect(original.enforce).toBe(true);

  // ---- Okafor opens the list and the editor -----------------------------------------------------------------------------------
  await loginAs(page, OKAFOR);
  await page.goto("/risk-levels");
  await expect(page.getByTestId("levels-set")).toBeVisible({ timeout: 20000 });
  await expect(page.getByTestId("levels-edit")).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "01", "okafor-opens-the-levels");

  await page.getByTestId("levels-edit").click();
  await expect(page.getByTestId(`level-reconfirm-${MODERATE}`)).toHaveValue("180");
  const block = page.locator("fieldset", { has: page.getByTestId(`level-name-${MODERATE}`) });
  await toTop(page, block, 110);
  await shot(page, "02", "okafor-opens-the-editor");

  // ---- Okafor changes one number and says why ---------------------------------------------------------------------------------------
  await page.getByTestId(`level-reconfirm-${MODERATE}`).fill("120");
  await page.getByTestId("levels-reason").fill(WHY);
  await expect(page.getByTestId("levels-save")).toBeEnabled();
  await toTop(page, block, 110);
  await shot(page, "03", "okafor-changes-moderate-to-120-days");
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));   // the reason and the button to save are at the foot of the editor
  await shot(page, "04", "okafor-says-why-and-saves");

  await page.getByTestId("levels-save").click();
  await expect(page.getByTestId("levels-set")).toContainText(`Version ${original.version + 1} by Okafor on `, { timeout: 20000 });
  await expect(page.getByTestId("level-moderate")).toContainText("120 days");
  await page.goto("/risk-levels");   // a fresh page, so that the "saved" notice is not in the pictures
  await expect(page.getByTestId("levels-set")).toContainText(`Version ${original.version + 1} by Okafor on `, { timeout: 20000 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "05", "the-new-version-is-saved");

  await page.getByTestId("levels-history-toggle").click();
  await expect(page.getByTestId("levels-history")).toBeVisible();
  await toSection(page, "Earlier versions", 150);
  await shot(page, "06", "the-history-keeps-both-versions");

  // ---- Imani can read the list and cannot change it -------------------------------------------------------------------------------
  await loginAs(page, IMANI);
  await page.goto("/risk-levels");
  await expect(page.getByTestId("levels-set")).toContainText(`Version ${original.version + 1} by Okafor on `, { timeout: 20000 });
  await expect(page.getByTestId("levels-edit")).toHaveCount(0);
  await expect(page.getByTestId("levels-cannot-manage")).toContainText("Only the AI risk owner may set the risk levels");
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "07", "imani-can-read-the-list-and-not-change-it");

  // ---- The effect on an agent held at Moderate ------------------------------------------------------------------------------------
  await agentPicture(page);

  // ---- The original list is written back, so that the other pages still read as they were captured ----------------------------------
  const back = await fetch(`${API_BASE}/risk-levels`, {
    method: "POST",
    headers: { ...(await bearerFor(OKAFOR)), "content-type": "application/json" },
    body: JSON.stringify({ levels: original.levels, enforce: original.enforce, reason: "Back to the levels the organisation held before the 120-day trial for Moderate." }),
  });
  expect(back.status).toBe(201);
  const after = await currentLevels();
  expect(after.levels).toEqual(original.levels);
  expect(after.enforce).toBe(true);
});
