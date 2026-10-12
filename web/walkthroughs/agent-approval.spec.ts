/**
 * Captures the screenshots of "Approve an AI agent before it can work with patient records"
 * (docs/public/walkthroughs/agent-approval-walkthrough.html), the third page of AI agents in the Health walkthroughs.
 *
 * Drives the real console against the live Health organisation as Okafor (the AI risk owner), Devi (a data engineer), and Hartley (an approver of the
 * Cardiology department). Nothing is mocked: if a step cannot be reached, it fails rather than producing a picture of something that did not happen.
 *
 * What it makes in Health: one new agent, followup-appointment-planner, owned by Cardiology, with two versions. The first is decided together with the agent's level, the
 * second adds a tool that is not on the approved tools list and is approved by both people. A second whole run stops at registration because the agent's name is taken.
 *
 * When a run stops part way, the agent is left as it was. Carry on from a later picture, keeping the earlier ones:
 *
 *   AGENT_APPROVAL_FROM=19 npx playwright test --config=walkthroughs/playwright.config.ts agent-approval
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts agent-approval
 */

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { loginAs } from "../tests/auth-helpers";
import { settled } from "./settled";
import { snap } from "./snap";

const OKAFOR = "risk-okafor";
const DEVI = "eng-devi";
const HARTLEY = "cust-hartley";

const AGENT = "followup-appointment-planner";
const AGENT_ZIP = join(process.cwd(), "walkthroughs", "fixtures", "triage-agent.zip");
const DIR = "agent-approval";

// The number of the picture to carry on from. 1 is a whole run; a bigger one skips every step before it and keeps its pictures.
const FROM = Number(process.env.AGENT_APPROVAL_FROM ?? "1");

async function shot(page: Page, number: string, name: string): Promise<void> {
  await settled(page);
  const dir = join(process.cwd(), "walkthroughs", "shots", DIR);
  mkdirSync(dir, { recursive: true });
  await snap(page, { path: join(dir, `${number}-${name}.png`) });
}

/** Puts a part of the page near the top of the picture, so that the whole of it is in view. */
async function toTop(page: Page, part: Locator, offset = 110): Promise<void> {
  await part.first().evaluate((e, y) => window.scrollTo(0, Math.max(0, e.getBoundingClientRect().top + window.scrollY - y)), offset);
  await page.waitForTimeout(250);
}

let agentPath = "";

/** Opens the agent's page the way somebody without the Agents menu does: from the Agent registry, which lists every agent. */
async function openAgentFromRegistry(page: Page): Promise<void> {
  await page.goto("/agent-registry");
  await page.getByTestId("registry-search").fill(AGENT);
  await expect(page.getByTestId("registry-count")).toContainText("Showing 1 of ", { timeout: 20000 });
  await page.getByRole("link", { name: AGENT }).click({ timeout: 15000 });
  await expect(page.getByRole("heading", { name: AGENT })).toBeVisible({ timeout: 15000 });
}

/** Scrolls so that the section with this heading starts just below the page header. */
async function toSection(page: Page, heading: string): Promise<void> {
  await toTop(page, page.getByRole("heading", { name: heading, exact: true }), 90);
}

/** Opens the agent's page. The first time, Devi finds it in the list; after that its address is used, because not everybody has the Agents menu. */
async function openAgent(page: Page): Promise<void> {
  if (!agentPath) {
    await page.goto("/agents");
    await page.getByRole("link", { name: AGENT }).click({ timeout: 15000 });
    agentPath = new URL(page.url()).pathname;
  } else {
    await page.goto(agentPath);
  }
  await expect(page.getByRole("heading", { name: AGENT })).toBeVisible({ timeout: 15000 });
}

/** Fills the new-version form; returns the number the version will have. */
async function fillVersion(page: Page, tools: string): Promise<number> {
  const existing = await page.getByTestId("agent-versions").locator("tbody tr").count().catch(() => 0);
  await page.getByTestId("agent-version-zip").setInputFiles(AGENT_ZIP);
  await page.getByTestId("agent-version-model").fill("none");
  await page.getByTestId("agent-version-advanced-toggle").click();
  await page.getByTestId("agent-version-tools").fill(tools);
  return existing + 1;
}

test("a new agent is registered, its level and first version decided together, and a later version that adds a tool approved by two people", async ({ page }) => {
  test.setTimeout(420_000);
  const first = 1;

  // ---- Devi registers the agent and proposes a level ---------------------------------------------------------------------------
  if (FROM <= 1) {
    await loginAs(page, OKAFOR);
    await page.goto("/risk-levels");
    await expect(page.getByTestId("levels-set")).toBeVisible({ timeout: 20000 });
    await expect(page.getByTestId("levels-enforce-state")).toContainText("Agents are held to these levels");
    await shot(page, "01", "okafor-reads-the-risk-levels");

    await loginAs(page, DEVI);
    await page.goto("/agents/register");
    await page.getByTestId("agent-name").fill(AGENT);
    await page.getByTestId("agent-department").selectOption({ label: "Cardiology" });
    await page.getByTestId("agent-purpose").fill("Plans follow-up appointments from a consultation note, for scheduling staff to confirm");
    await page.getByTestId("agent-level").selectOption({ label: "Moderate" });
    await page.getByTestId("agent-level-affected").fill("Patients whose consultations ask for a follow-up appointment");
    await page.getByTestId("agent-level-reason").fill("Scheduling staff confirm every appointment, and the agent cannot book or send anything itself");
    await shot(page, "02", "devi-registers-the-agent");

    await page.getByTestId("register-agent-submit").click();
    await expect(page.getByTestId("risk-proposal")).toContainText("Proposed: Moderate", { timeout: 20000 });
    await expect(page.getByTestId("risk-cannot-confirm")).toBeVisible();
    agentPath = new URL(page.url()).pathname;
    await toSection(page, "Risk level");
    await shot(page, "03", "the-level-waits-for-the-officer");

    await fillVersion(page, "read_dataset_version");
    await toTop(page, page.getByTestId("upload-agent-version-form"));
    await shot(page, "04", "devi-uploads-the-first-version");
    await page.getByTestId("upload-agent-version-submit").click();
    await expect(page.getByTestId(`agent-version-${first}`)).toBeVisible({ timeout: 20000 });
  } else {
    await loginAs(page, DEVI);
    await openAgent(page);
  }

  if (FROM <= 5) {
    await expect(page.getByTestId(`deploy-version-${first}`)).toBeDisabled();
    await expect(page.getByTestId(`held-${first}`)).toContainText("waits for");
    await toSection(page, "Versions");
    await shot(page, "05", "the-first-version-waits-for-approval");
  }

  // ---- Hartley gives the department's part -------------------------------------------------------------------------------------
  if (FROM <= 7) {
    await loginAs(page, HARTLEY);
    await page.goto("/agent-changes");
    const card = page.getByTestId(`change-${AGENT}-${first}`);
    await expect(card).toBeVisible({ timeout: 20000 });
    await card.getByTestId(`decide-reason-department-${first}`).fill("Cardiology owns this agent. It reads one dataset and cannot book or send anything, which is what the department asked for.");
    await toTop(page, card);
    await shot(page, "06", "hartley-gives-the-departments-part");

    await card.getByTestId(`approve-department-${first}`).click();
    await expect(card.getByTestId(`waiting-for-${first}`)).toContainText("Waiting for the AI risk owner", { timeout: 20000 });
    await toTop(page, card);
    await shot(page, "07", "hartley-cannot-give-the-officers-part");
  }

  // ---- Okafor decides the level and the version together ---------------------------------------------------------------------
  if (FROM <= 9) {
    await loginAs(page, OKAFOR);
    await page.goto("/agent-changes");
    const card = page.getByTestId(`change-${AGENT}-${first}`);
    await expect(card.getByTestId(`first-submission-${first}`)).toBeVisible({ timeout: 20000 });
    await card.getByTestId(`first-level-${first}`).selectOption({ label: "High" });
    await card.getByTestId(`first-reason-${first}`).fill("The agent works with named appointment requests, and a later version is expected to message people, so it is held at High even though scheduling staff confirm every booking.");
    await toTop(page, card);
    await shot(page, "08", "okafor-decides-the-level-and-the-version");

    const decided = page.waitForResponse((r) => r.url().includes("/first-submission"));
    await card.getByTestId(`approve-first-${first}`).click();
    expect((await decided).status()).toBe(201);
    await page.getByTestId("changes-filter-approved").click();
    await expect(card.getByTestId(`change-state-${first}`)).toContainText("Approved", { timeout: 20000 });
    await toTop(page, card);
    await shot(page, "09", "both-parts-are-recorded");
  }

  if (FROM <= 10) {
    await loginAs(page, OKAFOR);
    await page.goto("/agent-registry");
    await page.getByTestId("registry-search").fill(AGENT);
    await expect(page.getByTestId("registry-count")).toContainText("Showing 1 of ", { timeout: 20000 });
    await toTop(page, page.getByTestId("registry-table"));
    await shot(page, "10", "the-registry-lists-the-agent-at-its-level");

    await page.getByRole("link", { name: AGENT }).click({ timeout: 15000 });
    agentPath = new URL(page.url()).pathname;
    await expect(page.getByTestId("risk-confirmed")).toContainText("High", { timeout: 20000 });
    await toSection(page, "Risk level");
    await shot(page, "11", "the-agent-page-shows-the-confirmed-level");
  }

  // ---- Devi can now deploy it --------------------------------------------------------------------------------------------------
  if (FROM <= 12) {
    await loginAs(page, DEVI);
    await openAgent(page);
    await expect(page.getByTestId(`deploy-version-${first}`)).toBeEnabled({ timeout: 20000 });
    await page.getByTestId(`deploy-version-${first}`).click();
    await expect(page.getByTestId(`agent-version-${first}`)).toContainText("active", { timeout: 20000 });
    await toSection(page, "Versions");
    await shot(page, "12", "the-first-version-is-deployed");
  }

  // ---- A later version adds a tool the platform does not know ------------------------------------------------------------------
  const second = 2;
  if (FROM <= 14) {
    await loginAs(page, DEVI);
    await openAgent(page);
    expect(await fillVersion(page, "read_dataset_version, send_confirmation")).toBe(second);
    await toTop(page, page.getByTestId("upload-agent-version-form"));
    await shot(page, "13", "devi-uploads-a-version-that-adds-a-tool");

    await page.getByTestId("upload-agent-version-submit").click();
    await expect(page.getByTestId(`agent-version-${second}`)).toBeVisible({ timeout: 20000 });
    await expect(page.getByTestId(`deploy-version-${second}`)).toBeDisabled();
    await expect(page.getByTestId(`held-${second}`)).toContainText("waits for");
    await toSection(page, "Versions");
    await shot(page, "14", "the-second-version-waits-for-two-people");
  }

  if (FROM <= 15) {
    await loginAs(page, HARTLEY);
    await page.goto("/agent-changes");
    const card = page.getByTestId(`change-${AGENT}-${second}`);
    await expect(card).toBeVisible({ timeout: 20000 });
    await expect(card.getByTestId(`change-list-${second}`)).toContainText("send_confirmation");
    await card.getByTestId(`decide-reason-department-${second}`).fill("Cardiology wants confirmations sent by the clinic's own system, never by the agent. This version has to be checked before it goes live.");
    await toTop(page, card);
    await shot(page, "15", "hartley-reads-what-the-version-adds");
    await card.getByTestId(`approve-department-${second}`).click();
    await expect(card.getByTestId(`waiting-for-${second}`)).toContainText("Waiting for the AI risk owner", { timeout: 20000 });
  }

  if (FROM <= 16) {
    await loginAs(page, OKAFOR);
    await page.goto("/agent-changes");
    const card = page.getByTestId(`change-${AGENT}-${second}`);
    await expect(card).toBeVisible({ timeout: 20000 });
    await card.getByTestId(`decide-reason-officer-${second}`).fill("The platform cannot see what send_confirmation does, so it is treated as an action outside the platform. Approved for a trial on made-up requests only.");
    await toTop(page, card);
    await shot(page, "16", "okafor-reads-it-and-gives-her-part");

    const decided = page.waitForResponse((r) => r.url().includes("/decisions") && r.request().method() === "POST");
    await card.getByTestId(`approve-officer-${second}`).click();
    expect((await decided).status()).toBe(201);
  }

  if (FROM <= 17) {
    // A fresh page, so that the list is read after the decision and not from what the page held before it.
    await loginAs(page, OKAFOR);
    await page.goto("/agent-changes");
    await page.getByTestId("changes-filter-approved").click();
    const card = page.getByTestId(`change-${AGENT}-${second}`);
    await expect(card.getByTestId(`change-state-${second}`)).toContainText("Approved", { timeout: 20000 });
    await toTop(page, card);
    await shot(page, "17", "both-people-have-approved");
  }

  if (FROM <= 18) {
    await loginAs(page, DEVI);
    await openAgent(page);
    await expect(page.getByTestId(`deploy-version-${second}`)).toBeEnabled({ timeout: 20000 });
    await toSection(page, "Versions");
    await shot(page, "18", "the-second-version-can-now-be-deployed");
  }

  // ---- The registry flagged the agent as having no sponsor: Okafor names one ---------------------------------------------------
  await loginAs(page, OKAFOR);
  await openAgentFromRegistry(page);
  await expect(page.getByTestId("sponsor-none")).toBeVisible({ timeout: 20000 });
  await page.getByTestId("sponsor-select").selectOption({ label: "Hartley" });
  await page.getByTestId("sponsor-reason").fill("Hartley is the approver for Cardiology, which owns this agent, and answers for what it does.");
  await toSection(page, "Sponsor and re-confirmation");
  await shot(page, "19", "okafor-names-a-sponsor");

  await page.getByTestId("sponsor-assign").click();
  await expect(page.getByTestId("sponsor-current")).toContainText("Sponsor: Hartley, assigned by Okafor on ", { timeout: 20000 });
  await toSection(page, "Sponsor and re-confirmation");
  await shot(page, "20", "the-sponsor-is-recorded");

  await page.goto("/agent-registry");
  await page.getByTestId("registry-search").fill(AGENT);
  await expect(page.getByTestId("registry-count")).toContainText("Showing 1 of ", { timeout: 20000 });
  await expect(page.getByTestId(`registry-row-${AGENT}`)).toContainText("Sponsor: Hartley");
  await toTop(page, page.getByTestId("registry-table"));
  await shot(page, "21", "the-registry-now-shows-the-sponsor");
});
