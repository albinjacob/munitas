/**
 * Captures the screenshots of "Approve which internet addresses an AI agent may call"
 * (docs/public/walkthroughs/agent-egress-walkthrough.html), the second page of AI agents in the Health walkthroughs.
 *
 * Drives the real console against the live Health organisation as Devi (a data engineer), Okonjo (the custodian of Radiology, who owns the agent's data but
 * does not hold the network architect role) and Turner (the network architect). Nothing is mocked: if a step cannot be reached, it fails rather than
 * producing a picture of something that did not happen.
 *
 * What it makes in Health: two versions of the agent radiology-intake-triage. The first names one host, is approved and deployed. The second names a host
 * nobody can account for and is refused. Run it once on a Health organisation that was just rebuilt, or after the agent's other page, so that the
 * version numbers read in order; it works out the next version number itself.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts agent-egress
 */

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { loginAs } from "../tests/auth-helpers";
import { settled } from "./settled";
import { snap } from "./snap";

const DEVI = "eng-devi";
const OKONJO = "cust-okonjo";
const TURNER = "arch-turner";

const AGENT = "radiology-intake-triage";
const AGENT_ZIP = join(process.cwd(), "walkthroughs", "fixtures", "triage-agent.zip");
const DIR = "agent-egress";

const GOOD_HOST = "reports.radiology.example";
const BAD_HOST = "files.unknown-host.example";
const WHY_APPROVED = "One host, run by the hospital group's own reporting service. The agent needs it to fetch report templates.";
const WHY_REFUSED = "Nobody on the platform team can say who runs this host, so it cannot be approved until its owner is named.";

async function shot(page: Page, number: string, name: string): Promise<void> {
  await settled(page);
  const dir = join(process.cwd(), "walkthroughs", "shots", DIR);
  mkdirSync(dir, { recursive: true });
  await snap(page, { path: join(dir, `${number}-${name}.png`) });
}

/** Puts a part of the page near the top of the picture, so that the whole of it is in view. */
async function toTop(page: Page, part: Locator, offset = 40): Promise<void> {
  await part.first().evaluate((e, y) => window.scrollTo(0, Math.max(0, e.getBoundingClientRect().top + window.scrollY - y)), offset);
  await page.waitForTimeout(250);
}

async function openAgent(page: Page): Promise<void> {
  await page.goto("/agents");
  await page.getByRole("link", { name: AGENT }).click();
  await expect(page.getByRole("heading", { name: AGENT })).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId("upload-agent-version-form")).toBeVisible();
}

async function uploadVersion(page: Page, host: string): Promise<number> {
  const existing = await page.getByTestId("agent-versions").locator("tbody tr").count().catch(() => 0);
  const next = existing + 1;
  await page.getByTestId("agent-version-zip").setInputFiles(AGENT_ZIP);
  await page.getByTestId("agent-version-model").fill("none");
  await page.getByTestId("agent-version-advanced-toggle").click();
  await page.getByTestId("agent-version-tools").fill("read_dataset_version");
  await page.getByTestId("agent-version-hosts").fill(host);
  return next;
}

test("Devi asks for a host, Turner decides, and the version can then be deployed or not", async ({ page }) => {
  test.setTimeout(300_000);

  // ---- Devi names the hosts a new version may call ----------------------------------------------------------------------
  await loginAs(page, DEVI);
  await openAgent(page);
  await toTop(page, page.getByRole("heading", { name: AGENT }), 80);
  await shot(page, "01", "devi-opens-the-agent");

  const first = await uploadVersion(page, GOOD_HOST);
  await toTop(page, page.getByTestId("upload-agent-version-form"));
  await shot(page, "02", "devi-names-the-host");

  await page.getByTestId("upload-agent-version-submit").click();
  await expect(page.getByTestId(`agent-version-${first}`)).toBeVisible({ timeout: 20000 });
  await expect(page.getByTestId(`egress-state-${first}`)).toHaveText("egress pending");
  await expect(page.getByTestId(`deploy-version-${first}`)).toBeDisabled();
  await toTop(page, page.getByTestId("agent-versions"));
  await shot(page, "03", "the-version-waits-for-a-decision");

  // ---- Devi cannot decide it, and neither can the custodian who owns the agent ------------------------------------------------
  await page.getByTestId(`agent-version-${first}`).getByRole("link", { name: "view request" }).click();
  await expect(page.getByTestId("egress-blocked")).toContainText("You submitted this version, so somebody else has to decide");
  const requestPath = new URL(page.url()).pathname;
  await shot(page, "04", "devi-cannot-decide-her-own-request");

  await loginAs(page, OKONJO);
  await page.goto(requestPath);
  await expect(page.getByTestId("egress-blocked")).toContainText("is not part of your role");
  await shot(page, "05", "okonjo-is-not-the-one-who-decides");

  // ---- Turner decides ----------------------------------------------------------------------------------------------------------
  await loginAs(page, TURNER);
  await page.goto("/");
  await expect(page.getByTestId("architect-waiting-count")).toBeVisible();
  await expect(page.getByTestId("architect-waiting")).toContainText(GOOD_HOST);
  await shot(page, "06", "turner-sees-it-waiting");

  await page.goto("/egress-approvals");
  await expect(page.getByTestId("egress-queue")).toContainText(GOOD_HOST);
  await shot(page, "07", "the-queue-of-requests");

  await page.getByTestId("egress-queue").getByRole("link", { name: `${AGENT} v${first}` }).click();
  await expect(page.getByTestId("egress-reason")).toBeVisible();
  await page.getByTestId("egress-reason").fill(WHY_APPROVED);
  await expect(page.getByTestId("egress-approve")).toBeEnabled();
  await shot(page, "08", "turner-writes-why-and-both-buttons-work");

  await page.getByTestId("egress-approve").click();
  await expect(page.getByText(/^Approved\b/).first()).toBeVisible({ timeout: 15000 });
  await shot(page, "09", "turner-approves-the-host");

  // ---- Devi can now deploy that version ---------------------------------------------------------------------------------------
  await loginAs(page, DEVI);
  await openAgent(page);
  await expect(page.getByTestId(`egress-state-${first}`)).toHaveText("egress approved");
  await expect(page.getByTestId(`deploy-version-${first}`)).toBeEnabled();
  await toTop(page, page.getByTestId("agent-versions"));
  await shot(page, "10", "the-approved-version-can-be-deployed");

  await page.getByTestId(`deploy-version-${first}`).click();
  await expect(page.getByTestId(`agent-version-${first}`)).toContainText("active", { timeout: 15000 });
  await toTop(page, page.getByTestId("agent-versions"));
  await shot(page, "11", "the-version-is-deployed");

  // ---- A host that is refused ---------------------------------------------------------------------------------------------------
  const second = await uploadVersion(page, BAD_HOST);
  await page.getByTestId("upload-agent-version-submit").click();
  await expect(page.getByTestId(`agent-version-${second}`)).toBeVisible({ timeout: 20000 });
  await expect(page.getByTestId(`egress-state-${second}`)).toHaveText("egress pending");

  await loginAs(page, TURNER);
  await page.goto("/egress-approvals");
  await page.getByTestId("egress-queue").getByRole("link", { name: `${AGENT} v${second}` }).click();
  await expect(page.getByTestId("egress-reason")).toBeVisible();
  await page.getByTestId("egress-reason").fill(WHY_REFUSED);
  await page.getByTestId("egress-refuse").click();
  await expect(page.getByText(/^Refused\b/).first()).toBeVisible({ timeout: 15000 });
  await shot(page, "12", "turner-refuses-the-second-host");

  await loginAs(page, DEVI);
  await openAgent(page);
  await expect(page.getByTestId(`egress-state-${second}`)).toHaveText("egress refused");
  await expect(page.getByTestId(`deploy-version-${second}`)).toBeDisabled();
  await toTop(page, page.getByTestId("agent-versions"));
  await shot(page, "13", "the-refused-version-cannot-be-deployed");
});
