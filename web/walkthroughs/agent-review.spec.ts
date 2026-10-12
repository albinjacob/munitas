/**
 * Captures the screenshots of "Check an AI agent's work and show an auditor what it did"
 * (docs/public/walkthroughs/agent-review-walkthrough.html), the fourth page of AI agents in the Health walkthroughs.
 *
 * Drives the real console against the live Health organisation as Devi (a data engineer), Hartley (the custodian of Cardiology), Imani (the de-identification
 * reviewer) and Nakamura (the data protection officer). Nothing is mocked: the agent is consultation-note-drafter, which really runs in the platform's sandbox on
 * three made-up consultation transcripts and drafts a visit note from each. If a step cannot be reached, it fails rather than producing a picture of something that
 * did not happen.
 *
 * What it makes in Health: two new runs of that agent. Imani corrects the first draft she is shown and accepts the second as it was, so the agent's page shows one of
 * each. Nothing else is created. The older run from the setup stays as it was and is not shown.
 *
 * The correction is chosen from what the agent really wrote: the third draft puts two greetings from the start of the visit under "Plan", and Imani takes them out.
 * If the drafts ever stop doing that, the capture stops rather than inventing a mistake.
 *
 * When a run stops part way, carry on from a later picture, keeping the earlier ones:
 *
 *   AGENT_REVIEW_FROM=9 npx playwright test --config=walkthroughs/playwright.config.ts agent-review
 */

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "../tests/auth-helpers";
import { settled } from "./settled";
import { snap } from "./snap";

const DEVI = "eng-devi";
const HARTLEY = "cust-hartley";
const IMANI = "rev-imani";
const NAKAMURA = "dpo-nakamura";

const AGENT = "consultation-note-drafter";
const DATASET = "synthetic-consultation-transcripts v1";
const DIR = "agent-review";

const FIRST_PURPOSE = "Draft visit notes from the three made-up transcripts, first batch";
const SECOND_PURPOSE = "Draft visit notes from the three made-up transcripts, after the correction";

// The two greetings the agent put under "Plan" in the third draft. Imani takes them out.
const GREETINGS = ["Welcome back. How have the last two weeks been? (line 2)", "Are you taking the water tablet as prescribed? (line 4)"];

const FROM = Number(process.env.AGENT_REVIEW_FROM ?? "1");

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

let agentPath = "";
let agentId = "";

/** Scrolls so that the section with this heading starts just below the page header. */
async function toSection(page: Page, heading: string): Promise<void> {
  await toTop(page, page.getByRole("heading", { name: heading, exact: true }), 90);
}

/** Devi finds the agent in the Agents list; everybody else follows the link in the Agent registry, because only the people who build agents have the Agents menu. */
async function openAgentAsEngineer(page: Page): Promise<void> {
  if (!agentPath) {
    await page.goto("/agents");
    await page.getByRole("link", { name: AGENT, exact: true }).click({ timeout: 15000 });
    agentPath = new URL(page.url()).pathname;
    agentId = agentPath.split("/").pop() ?? "";
  } else {
    await page.goto(agentPath);
  }
  await expect(page.getByRole("heading", { name: AGENT, exact: true })).toBeVisible({ timeout: 15000 });
}

async function openAgentFromRegistry(page: Page): Promise<void> {
  await page.goto("/agent-registry");
  await page.getByTestId("registry-search").fill(AGENT);
  await expect(page.getByTestId("registry-count")).toContainText("Showing 1 of ", { timeout: 20000 });
  await page.getByRole("link", { name: AGENT, exact: true }).click({ timeout: 15000 });
  await expect(page.getByRole("heading", { name: AGENT, exact: true })).toBeVisible({ timeout: 15000 });
}

async function runIdFor(purpose: string): Promise<string> {
  const response = await fetch(`${API_BASE}/agents/${agentId}/runs`, { headers: await bearerFor(DEVI) });
  const runs = (await response.json()) as { id: string; purpose: string }[];
  const found = runs.find((r) => r.purpose === purpose);
  if (!found) throw new Error(`no run with the purpose "${purpose}"`);
  return found.id;
}

/** Devi fills in the Runs box, so the page can be photographed with the warning that says who will be asked. */
async function fillRun(page: Page, purpose: string): Promise<void> {
  await page.getByTestId("run-purpose").fill(purpose);
  await page.getByTestId("run-target").selectOption({ label: DATASET });
  await expect(page.getByTestId("access-notice")).toBeVisible({ timeout: 15000 });
}

/** Hartley grants the access a run is waiting for. A grant covers one run, so each run asks again. */
async function grant(page: Page, purpose: string, picture?: [string, string]): Promise<void> {
  await loginAs(page, HARTLEY);
  await page.goto("/");
  const row = page.getByTestId("pending-requests").locator("li", { hasText: purpose });
  await expect(async () => {
    await page.reload();
    await expect(row).toBeVisible({ timeout: 4000 });
  }).toPass({ timeout: 60_000 });
  if (picture) {
    await toTop(page, page.getByTestId("pending-requests"));
    await shot(page, picture[0], picture[1]);
  }
  await row.getByRole("button", { name: "Grant access" }).click();
  await expect(row).toHaveCount(0);
}

async function waitForRun(page: Page, purpose: string): Promise<string> {
  const row = page.getByTestId("agent-run").filter({ hasText: purpose });
  await expect(row).toContainText("succeeded", { timeout: 240_000 });
  const id = await runIdFor(purpose);
  await expect(page.getByTestId(`run-draft-${id}-0`)).toBeVisible({ timeout: 30000 });
  return id;
}

test("a run is started, somebody other than the person who started it reviews its drafts, and the agent's page and evidence pack count what was decided", async ({ page }) => {
  test.setTimeout(900_000);
  let first = "";
  let second = "";

  // ---- Devi starts the first run ---------------------------------------------------------------------------------------------
  if (FROM <= 2) {
    await loginAs(page, DEVI);
    await openAgentAsEngineer(page);
    await fillRun(page, FIRST_PURPOSE);
    await toTop(page, page.getByRole("heading", { name: "Runs", exact: true }), 90);
    await shot(page, "01", "devi-starts-a-run");

    await page.getByTestId("start-run").click();
    await expect(page.getByTestId("agent-runs")).toContainText(FIRST_PURPOSE, { timeout: 20000 });
    await toTop(page, page.getByRole("heading", { name: "Runs", exact: true }), 90);
    await shot(page, "02", "the-run-waits-for-permission");
  } else {
    await loginAs(page, DEVI);
    await openAgentAsEngineer(page);
  }

  // ---- Hartley grants the access ----------------------------------------------------------------------------------------------
  if (FROM <= 3) await grant(page, FIRST_PURPOSE, ["03", "hartley-sees-the-request"]);

  // ---- The run finishes, and Devi is told somebody else has to review it -----------------------------------------------------
  if (FROM <= 4) {
    await loginAs(page, DEVI);
    await openAgentAsEngineer(page);
    first = await waitForRun(page, FIRST_PURPOSE);
    await expect(page.getByTestId(`review-someone-else-${first}`)).toContainText("Someone else has to review this");
    await toTop(page, page.getByTestId(`review-someone-else-${first}`), 700);
    await shot(page, "04", "the-run-finishes-and-needs-somebody-else");
  }
  first = first || (await runIdFor(FIRST_PURPOSE));

  // ---- Imani reads the drafts and corrects one --------------------------------------------------------------------------------
  if (FROM <= 8) {
    await loginAs(page, IMANI);
    await page.goto("/agent-registry");
    await page.getByTestId("registry-search").fill(AGENT);
    await expect(page.getByTestId("registry-count")).toContainText("Showing 1 of ", { timeout: 20000 });
    await toTop(page, page.getByTestId("registry-table"));
    await shot(page, "05", "imani-finds-the-agent-in-the-registry");

    await page.getByRole("link", { name: AGENT, exact: true }).click({ timeout: 15000 });
    await expect(page.getByTestId(`run-draft-${first}-2`)).toBeVisible({ timeout: 20000 });
    // The capture only goes on if the agent really did what the page says it did.
    const third = await page.getByTestId(`run-draft-${first}-2`).innerText();
    for (const line of GREETINGS) expect(third, `the third draft no longer holds "${line}"`).toContain(line);
    await toTop(page, page.getByTestId(`run-draft-${first}-2`), 150);
    await shot(page, "06", "imani-reads-the-third-draft");

    const box = page.getByTestId(`review-text-${first}`);
    const original = await box.inputValue();
    const corrected = original.split("\n").filter((l) => !GREETINGS.some((g) => l.includes(g))).join("\n");
    expect(corrected.length).toBeLessThan(original.length);
    await box.fill(corrected);
    await box.evaluate((el) => { (el as HTMLTextAreaElement).scrollTop = (el as HTMLTextAreaElement).scrollHeight; });
    await toTop(page, page.getByTestId(`review-form-${first}`), 110);
    await expect(page.getByTestId(`review-change-${first}`)).toBeEnabled();
    await shot(page, "07", "imani-removes-the-two-greetings");

    await page.getByTestId(`review-change-${first}`).click();
    await expect(page.getByTestId(`run-review-${first}`)).toContainText("Changed by the reviewer by Imani on ", { timeout: 20000 });
    await toTop(page, page.getByTestId(`run-review-${first}`), 300);
    await shot(page, "08", "the-review-is-recorded");
  }

  // ---- The drafter is corrected, a clean second run is made, and Imani accepts it -------------------------------------------------
  const FIXED_VERSION = 3;
  if (FROM <= 10) {
    await loginAs(page, DEVI);
    await openAgentAsEngineer(page);
    await expect(page.getByTestId(`agent-version-${FIXED_VERSION}`)).toBeVisible({ timeout: 20000 });
    await expect(page.getByTestId(`deploy-version-${FIXED_VERSION}`)).toBeEnabled();
    await toSection(page, "Versions");
    await shot(page, "09", "the-corrected-version-is-listed");

    await page.getByTestId(`deploy-version-${FIXED_VERSION}`).click();
    await expect(page.getByTestId(`agent-version-${FIXED_VERSION}`)).toContainText("active", { timeout: 20000 });
    await toSection(page, "Versions");
    await shot(page, "10", "the-corrected-version-is-deployed");
  }

  if (FROM <= 13) {
    await loginAs(page, DEVI);
    await openAgentAsEngineer(page);
    await fillRun(page, SECOND_PURPOSE);
    await toTop(page, page.getByRole("heading", { name: "Runs", exact: true }), 90);
    await shot(page, "11", "devi-starts-a-run-on-the-corrected-version");
    await page.getByTestId("start-run").click();
    await expect(page.getByTestId("agent-runs")).toContainText(SECOND_PURPOSE, { timeout: 20000 });
    await grant(page, SECOND_PURPOSE);

    await loginAs(page, IMANI);
    await openAgentFromRegistry(page);
    second = await waitForRun(page, SECOND_PURPOSE);
    // The capture only goes on if the corrected drafter really did what the page says it did.
    const clean = await page.getByTestId(`run-draft-${second}-2`).innerText();
    for (const line of GREETINGS) expect(clean, `the corrected draft still holds "${line}"`).not.toContain(line);
    await expect(page.getByTestId(`review-accept-${second}`)).toBeEnabled();
    await toTop(page, page.getByTestId(`run-draft-${second}-2`), 150);
    await shot(page, "12", "imani-reads-the-clean-third-draft");

    await page.getByTestId(`review-accept-${second}`).click();
    await expect(page.getByTestId(`run-review-${second}`)).toContainText("Accepted", { timeout: 20000 });
    await toTop(page, page.getByTestId(`run-review-${second}`), 300);
    await shot(page, "13", "the-acceptance-is-recorded");
  }

  // ---- Nakamura reads the figures and makes the evidence pack -----------------------------------------------------------------
  await loginAs(page, NAKAMURA);
  await openAgentFromRegistry(page);
  await expect(page.getByTestId("review-figures")).toContainText("2 reviewed: 1 accepted as they were, 1 changed, 0 refused", { timeout: 20000 });
  await toTop(page, page.getByTestId("review-figures"), 200);
  await shot(page, "14", "nakamura-reads-the-figures");

  await page.getByTestId("pack-make").click();
  await expect(page.getByTestId("pack-result")).toContainText("sha256:", { timeout: 30000 });
  await toTop(page, page.getByTestId("pack-result"), 200);
  await shot(page, "15", "nakamura-makes-the-evidence-pack");

  await page.getByTestId("chain-check").click();
  await expect(page.getByTestId("chain-result")).toContainText("None was changed, removed or added outside the platform", { timeout: 30000 });
  await toTop(page, page.getByTestId("chain-result"), 300);
  await shot(page, "16", "nakamura-checks-the-records");
});
