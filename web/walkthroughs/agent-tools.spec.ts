/**
 * Captures the screenshots of the walkthrough about approving tools in advance
 * (docs/public/walkthroughs/agent-tools-walkthrough.html), the fourth page of AI agents in the Health walkthroughs.
 *
 * Drives the real console against the live Health organisation as Okafor (the AI risk owner), Devi (a data engineer), Hartley (an approver of the Cardiology
 * department) and Okonjo (an approver of the Radiology department). Nothing is mocked: if a step cannot be reached, it fails rather than producing a picture of something
 * that did not happen.
 *
 * What it makes in Health: a second version of the approved tools list, and new versions of two agents:
 *   - followup-appointment-planner (made by the approval walkthrough) adds a tool that is on the list, so nobody is asked; then a tool that needs the department only; then a tool
 *     approved only up to a lower level, so both people are asked; then a tool that is not on the list, which the AI risk owner refuses;
 *   - radiology-intake-triage adds a tool that pages a person, which the Radiology approver refuses.
 *
 * When a run stops part way, carry on from a later picture, keeping the earlier ones. The numbers are the picture numbers:
 *
 *   AGENT_TOOLS_FROM=8 npx playwright test --config=walkthroughs/playwright.config.ts agent-tools
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts agent-tools
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
const OKONJO = "cust-okonjo";

const PLANNER = "followup-appointment-planner";   // made by the approval walkthrough, so the two pages follow one agent
const RADIOLOGY = "radiology-intake-triage";
const AGENT_ZIP = join(process.cwd(), "walkthroughs", "fixtures", "triage-agent.zip");
const DIR = "agent-tools";

// The number of the picture to carry on from, and the number to stop after. 1 and 99 are a whole run; a bigger first number skips every step before it and keeps its pictures.
const FROM = Number(process.env.AGENT_TOOLS_FROM ?? "1");
const TO = Number(process.env.AGENT_TOOLS_TO ?? "99");

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

async function toSection(page: Page, heading: string): Promise<void> {
  await toTop(page, page.getByRole("heading", { name: heading, exact: true }), 90);
}

/** Opens an agent's page from the list of agents, the way a data engineer does. */
async function openAgent(page: Page, name: string): Promise<void> {
  await page.goto("/agents");
  await page.getByRole("link", { name, exact: true }).click({ timeout: 15000 });
  await expect(page.getByRole("heading", { name })).toBeVisible({ timeout: 15000 });
}

/** The number of the newest version, counted from the agent's page. */
async function newestVersion(page: Page): Promise<number> {
  return page.getByTestId("agent-versions").locator("tbody tr").count();
}

/** Fills the new-version form, uploads nothing yet, and returns the number the version will have. */
async function fillVersion(page: Page, tools: string, hosts?: string): Promise<number> {
  const existing = await newestVersion(page);
  await page.getByTestId("agent-version-zip").setInputFiles(AGENT_ZIP);
  await page.getByTestId("agent-version-model").fill("none");
  await page.getByTestId("agent-version-advanced-toggle").click();
  await page.getByTestId("agent-version-tools").fill(tools);
  if (hosts) await page.getByTestId("agent-version-hosts").fill(hosts);
  return existing + 1;
}

/** Devi uploads a version that declares exactly these tools; returns its number. */
async function upload(page: Page, name: string, tools: string, picture?: [string, string], hosts?: string): Promise<number> {
  await loginAs(page, DEVI);
  await openAgent(page, name);
  const version = await fillVersion(page, tools, hosts);
  if (picture) {
    await toTop(page, page.getByTestId("upload-agent-version-form"));
    await shot(page, picture[0], picture[1]);
  }
  await page.getByTestId("upload-agent-version-submit").click();
  await expect(page.getByTestId(`agent-version-${version}`)).toBeVisible({ timeout: 20000 });
  return version;
}

/** A version must widen exactly what the story says: a changed model or host would be a widening that no list covers. */
async function expectWidens(found: Locator, version: number, count: number): Promise<void> {
  await expect(found.getByTestId(`change-list-${version}`).getByText("Widens", { exact: true })).toHaveCount(count);
}

async function card(page: Page, name: string, version: number): Promise<Locator> {
  await page.goto("/agent-changes");
  await page.getByTestId("changes-filter-all").click();
  const found = page.getByTestId(`change-${name}-${version}`);
  await expect(found).toBeVisible({ timeout: 20000 });
  return found;
}

test("a tool is approved in advance, and the tools that are not on the list are decided one case at a time", async ({ page }) => {
  test.setTimeout(900_000);
  const later = new Date(Date.now() + 330 * 86_400_000).toISOString().slice(0, 10);

  // ---- Okafor reads the list and adds a tool ------------------------------------------------------------------------------------
  if (FROM <= 4) {
    await loginAs(page, OKAFOR);
    await page.goto("/approved-tools");
    await expect(page.getByTestId("tools-set")).toContainText(FROM <= 1 ? "Version 1" : "Version 2", { timeout: 20000 });
    if (FROM <= 1) {
      await toTop(page, page.getByTestId("tools-table"), 150);
      await shot(page, "01", "okafor-reads-the-approved-tools");
    }

    if (FROM <= 2) {
      await page.getByTestId("tools-edit").click();
      const index = await page.getByTestId("tools-table").locator("tbody tr").count();
      await page.getByTestId("tool-add").click();
      await page.getByTestId(`tool-name-${index}`).fill("read_clinic_schedule");
      await page.getByTestId(`tool-description-${index}`).fill("Reads the clinic's appointment schedule: the dates and times that are free, and nothing about a person.");
      await page.getByTestId(`tool-reason-${index}`).fill("It reads dates and times only and changes nothing, so a version that adds it needs no request.");
      await page.getByTestId(`tool-review-${index}`).fill(later);
      await page.getByTestId("tools-reason").fill("Agents that plan follow-up visits need the free times in the clinic's schedule, and reading them is as safe as the other reading tools.");
      await toTop(page, page.getByTestId(`tool-row-${index}`), 80);
      await shot(page, "02", "okafor-adds-a-tool");
      await page.getByTestId("tools-save").click();
      await expect(page.getByTestId("tools-set")).toContainText("Version 2", { timeout: 20000 });
    }
    await page.goto("/approved-tools");   // a fresh page, so that the "saved" notice is not in the picture
    await expect(page.getByTestId("tool-read_clinic_schedule")).toBeVisible({ timeout: 20000 });
    await toTop(page, page.getByTestId("tools-set"), 150);
    await shot(page, "03", "the-new-tool-is-on-the-list");

    await page.getByTestId("tools-history-toggle").click();
    await expect(page.getByTestId("tools-version-1")).toBeVisible();
    await expect(page.getByTestId("tools-version-2")).toHaveCount(0);
    await toTop(page, page.getByTestId("tools-history-toggle"), 150);
    await shot(page, "04", "both-versions-are-kept");
  }

  // ---- A tool that is on the list: nobody is asked ------------------------------------------------------------------------------
  if (FROM <= 7) {
    let version = 0;
    if (FROM <= 5) version = await upload(page, PLANNER, "read_dataset_version, send_confirmation, read_clinic_schedule", ["05", "devi-uploads-a-version-with-a-listed-tool"]);
    await loginAs(page, DEVI);
    if (!version) {
      await openAgent(page, PLANNER);
      version = await newestVersion(page);
    }
    const found = await card(page, PLANNER, version);
    await expectWidens(found, version, 1);
    await expect(found.getByTestId(`change-state-${version}`)).toHaveText("Approved in advance");
    await expect(found.getByTestId("covered-read_clinic_schedule")).toBeVisible();
    await toTop(page, found);
    await shot(page, "06", "the-version-is-approved-in-advance");

    await openAgent(page, PLANNER);
    await expect(page.getByTestId(`deploy-version-${version}`)).toBeEnabled({ timeout: 20000 });
    await page.getByTestId(`deploy-version-${version}`).click();
    await expect(page.getByTestId(`agent-version-${version}`)).toContainText("active", { timeout: 20000 });
    await toSection(page, "Versions");
    await shot(page, "07", "the-version-is-deployed");
  }

  // ---- A tool that needs the department only ------------------------------------------------------------------------------------
  if (FROM <= 10 && TO >= 8) {
    let version = 0;
    if (FROM <= 8) version = await upload(page, PLANNER, "read_dataset_version, send_confirmation, read_clinic_schedule, draft_appointment_letter", ["08", "devi-uploads-a-version-with-a-draft-tool"]);
    if (!version) {
      await loginAs(page, DEVI);
      await openAgent(page, PLANNER);
      version = await newestVersion(page);
    }
    if (FROM <= 9) {
      await loginAs(page, HARTLEY);
      const found = await card(page, PLANNER, version);
      await expectWidens(found, version, 1);
      await expect(found.getByTestId(`waiting-for-${version}`)).toContainText("Waiting for an approver of the department that owns the agent.");
      await expect(found.getByTestId(`waiting-for-${version}`)).not.toContainText("officer");
      await found.getByTestId(`decide-reason-department-${version}`).fill("Cardiology owns this agent. A member of staff checks and sends every letter, so the department approves it.");
      await toTop(page, found);
      await shot(page, "09", "hartley-is-the-only-person-asked");
      await found.getByTestId(`approve-department-${version}`).click();
    }
    await loginAs(page, HARTLEY);
    const done = await card(page, PLANNER, version);
    await expect(done.getByTestId(`change-state-${version}`)).toHaveText("Approved", { timeout: 20000 });
    await toTop(page, done);
    await shot(page, "10", "approved-by-the-department-alone");
  }

  // ---- A tool approved only up to a lower level: both people are asked ----------------------------------------------------------
  if (FROM <= 13 && TO >= 11) {
    let version = 0;
    if (FROM <= 11) version = await upload(page, PLANNER, "read_dataset_version, send_confirmation, read_clinic_schedule, draft_appointment_letter, queue_reminder_from_template", ["11", "devi-uploads-a-version-with-a-lower-level-tool"]);
    if (!version) {
      await loginAs(page, DEVI);
      await openAgent(page, PLANNER);
      version = await newestVersion(page);
    }
    if (FROM <= 12) {
      await loginAs(page, HARTLEY);
      const asked = await card(page, PLANNER, version);
      await expectWidens(asked, version, 1);
      if (await asked.getByTestId(`decide-reason-department-${version}`).count()) {   // a resumed run finds it already decided
        await asked.getByTestId(`decide-reason-department-${version}`).fill("Cardiology owns this agent, and the reminders use fixed neutral wording, so the department approves it.");
        await asked.getByTestId(`approve-department-${version}`).click();
        await expect(asked.getByTestId(`waiting-for-${version}`)).toContainText("Waiting for", { timeout: 20000 });
      }
      await loginAs(page, OKAFOR);
      const found = await card(page, PLANNER, version);
      await expect(found.getByTestId(`waiting-for-${version}`)).toContainText("Waiting for the AI risk owner");
      await found.getByTestId(`decide-reason-officer-${version}`).fill("The list approves this tool only up to Moderate, and this agent is held at High. The wording is fixed, so the AI risk owner approves it.");
      await toTop(page, found);
      await shot(page, "12", "the-list-does-not-cover-this-agents-level");
      await found.getByTestId(`approve-officer-${version}`).click();
    }
    await loginAs(page, OKAFOR);
    const done = await card(page, PLANNER, version);
    await expect(done.getByTestId(`change-state-${version}`)).toHaveText("Approved", { timeout: 20000 });
    await toTop(page, done);
    await shot(page, "13", "approved-by-the-department-and-the-risk-owner");
  }

  // ---- A tool that is not on the list, refused by the risk owner -----------------------------------------------------------------
  if (FROM <= 16 && TO >= 14) {
    let version = 0;
    if (FROM <= 14) version = await upload(page, PLANNER, "read_dataset_version, send_confirmation, read_clinic_schedule, draft_appointment_letter, queue_reminder_from_template, send_free_text_message", ["14", "devi-uploads-a-version-with-an-unlisted-tool"]);
    if (!version) {
      await loginAs(page, DEVI);
      await openAgent(page, PLANNER);
      version = await newestVersion(page);
    }
    if (FROM <= 15) {
      await loginAs(page, HARTLEY);
      const asked = await card(page, PLANNER, version);
      await expectWidens(asked, version, 1);
      if (await asked.getByTestId(`decide-reason-department-${version}`).count()) {
        await asked.getByTestId(`decide-reason-department-${version}`).fill("Cardiology wants patients reminded. Whether the agent may write the text itself is for the AI risk owner to decide.");
        await asked.getByTestId(`approve-department-${version}`).click();
        await expect(asked.getByTestId(`waiting-for-${version}`)).toContainText("Waiting for", { timeout: 20000 });
      }
      await loginAs(page, OKAFOR);
      const found = await card(page, PLANNER, version);
      await expect(found.getByTestId(`change-list-${version}`)).toContainText("send_free_text_message");
      await found.getByTestId(`decide-reason-officer-${version}`).fill("The agent would write the text itself, and a text can show health information on a lock screen. The fixed-wording tool is enough.");
      await toTop(page, found);
      await shot(page, "15", "the-risk-owner-refuses");
      await found.getByTestId(`refuse-officer-${version}`).click();
    }
    await loginAs(page, DEVI);
    await openAgent(page, PLANNER);
    await expect(page.getByTestId(`deploy-version-${version}`)).toBeDisabled({ timeout: 20000 });
    await toSection(page, "Versions");
    await shot(page, "16", "the-refused-version-cannot-be-deployed");
  }

  // ---- A tool that pages a person, refused by the department's approver ------------------------------------------------------
  if (FROM <= 18 && TO >= 17) {
    let version = 0;
    if (FROM <= 17) version = await upload(page, RADIOLOGY, "page_oncall_radiologist", ["17", "devi-uploads-a-version-that-pages-a-person"]);
    if (!version) {
      await loginAs(page, DEVI);
      await openAgent(page, RADIOLOGY);
      version = await newestVersion(page);
    }
    await loginAs(page, OKONJO);
    const found = await card(page, RADIOLOGY, version);
    await expectWidens(found, version, 1);   // the paging tool; the host the earlier version asked for is no longer asked for, which narrows
    await expect(found.getByTestId(`waiting-for-${version}`)).toContainText("Waiting for an approver of the department that owns the agent.");
    await found.getByTestId(`decide-reason-department-${version}`).fill("A rule that pages the on-call radiologist can fire on studies that need no action, and people stop answering pages that are often false. Radiology will decide again when the rate of false alerts has been measured and the paging service limits how often it fires.");
    await toTop(page, found);
    await shot(page, "18", "radiology-refuses-the-paging-tool");
    await found.getByTestId(`refuse-department-${version}`).click();
    await page.getByTestId("changes-filter-refused").click();
    await expect(page.getByTestId(`change-${RADIOLOGY}-${version}`).getByTestId(`change-state-${version}`)).toHaveText("Refused", { timeout: 20000 });
    await toTop(page, page.getByTestId(`change-${RADIOLOGY}-${version}`));
    await shot(page, "19", "the-paging-version-is-refused");
  }
});
