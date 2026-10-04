/**
 * Captures the screenshots behind docs/public/walkthroughs/department-approvers-walkthrough.html.
 *
 * Drives the real console against the live stack, signing in as Hartley, the data custodian for Cardiology, and as Mensah, the data custodian for
 * Oncology, who is named as cover. Nothing here is mocked: if a step cannot be reached, this fails rather than producing a picture of something
 * that did not happen.
 *
 * Writes into walkthroughs/shots/department-approvers/, which is gitignored. The HTML page carries the images base64-encoded, so the page is the
 * committed artefact and these files are scratch.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts department-approvers
 *   ..\.venv\Scripts\python.exe ..\docs\tools\build_department_approvers_walkthrough.py
 */

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { actingHeaders, bearerFor, loginAs } from "../tests/auth-helpers";
import { API_BASE } from "../config/ports";
import { settled } from "./settled";

const SHOTS = join(process.cwd(), "walkthroughs", "shots", "department-approvers");

const HARTLEY = "cust-hartley";
const MENSAH = "cust-mensah";
const ENGINEER = "eng-devi";
const HEALTH = "health";

let step = 0;

async function shot(page: Page, name: string): Promise<void> {
  await settled(page);
  step += 1;
  const n = String(step).padStart(2, "0");
  await page.screenshot({ path: join(SHOTS, `${n}-${name}.png`) });
}

// The top of the screen only. Health's list of access requests below it is long and carries leftovers of earlier checks, which have nothing to do
// with this flow, so the picture stops where the part being shown ends.
async function shotTop(page: Page, name: string, height: number): Promise<void> {
  await settled(page);
  step += 1;
  const n = String(step).padStart(2, "0");
  await page.screenshot({ path: join(SHOTS, `${n}-${name}.png`), clip: { x: 0, y: 0, width: 1920, height } });
}

async function call<T>(method: string, path: string, body: unknown, headers: Record<string, string>): Promise<T> {
  const response = await fetch(API_BASE + path, {
    method,
    headers: { "content-type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${path} returned ${response.status}: ${await response.text()}`);
  return response.json() as Promise<T>;
}

// Puts one department's card at the top of the picture, so the whole card is in view.
async function showCard(page: Page, id: string): Promise<void> {
  await page.getByTestId(`department-${id}`).evaluate((e) => e.scrollIntoView({ block: "start" }));
}

function inTwoWeeks(): string {
  const d = new Date(Date.now() + 14 * 24 * 3600 * 1000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

test("capture: cover for a department, used once, then ended", async ({ page }) => {
  mkdirSync(SHOTS, { recursive: true });

  const hartleyAuth = await bearerFor(HARTLEY);
  const organisation = await call<{ departments: { id: string; name: string }[] }>(
    "GET",
    `/organisation?tenant_id=${HEALTH}`,
    undefined,
    hartleyAuth,
  );
  const cardiology = organisation.departments.find((d) => d.name === "Cardiology")!.id;

  // A capture that inherited cover from an earlier run would photograph two approvers and call one of them new.
  await call("POST", `/departments/${cardiology}/approvers/${MENSAH}/remove`, { reason: "clearing before a capture" }, hartleyAuth).catch(
    () => undefined,
  );

  // ---- Hartley opens the departments --------------------------------
  await loginAs(page, HARTLEY);
  await page.goto("/departments");
  await expect(page.getByTestId(`approvers-${cardiology}`).locator(`[data-approver="${HARTLEY}"]`)).toContainText("permanent");
  await showCard(page, cardiology);
  await shot(page, "hartley-opens-the-departments");

  // ---- Hartley names cover -------------------------------------------
  await page.getByTestId(`add-person-${cardiology}`).selectOption(MENSAH);
  await page.getByTestId(`add-reason-${cardiology}`).fill("covers while Hartley is on leave");
  await page.getByTestId(`add-ends-${cardiology}`).fill(inTwoWeeks());
  // Leave the date field, so the picture does not show one part of the date highlighted.
  await page.getByRole("heading", { name: "Departments", exact: true }).click();
  await showCard(page, cardiology);
  await shot(page, "hartley-names-cover");

  await page.getByTestId(`add-submit-${cardiology}`).click();
  await expect(page.getByTestId(`approvers-${cardiology}`).locator(`[data-approver="${MENSAH}"]`)).toContainText("covering until");
  await showCard(page, cardiology);
  await shot(page, "the-cover-is-listed");

  // ---- A claim arrives, and the cover confirms it ---------------------
  const claimName = `cardiology-clinic-letters-${Date.now().toString(36)}`;
  const claim = await call<{ id: string }>(
    "POST",
    "/datasets/register",
    {
      tenant_id: HEALTH,
      name: claimName,
      department_id: cardiology,
      registered_by: ENGINEER,
      provenance: "internal_regulated",
      declared_class: "PUBLISHED",
      modality: [],
    },
    await actingHeaders("POST", "/datasets/register", { registered_by: ENGINEER }),
  );

  await loginAs(page, MENSAH);
  const arrival = page.getByTestId("awaiting-confirmation").locator(`[data-arrival="${claim.id}"]`);
  await expect(arrival).toBeVisible();
  await shotTop(page, "the-cover-sees-the-claim", 520);

  await arrival.getByTestId(`confirm-${claim.id}`).click();
  await expect(arrival).toHaveCount(0);
  await shotTop(page, "the-cover-confirms-it", 315);

  // ---- Hartley ends the cover -----------------------------------------
  await loginAs(page, HARTLEY);
  await page.goto("/departments");
  await expect(page.getByTestId(`approvers-${cardiology}`).locator(`[data-approver="${MENSAH}"]`)).toBeVisible();
  await page.getByTestId(`remove-${cardiology}-${MENSAH}`).click();
  await page.getByTestId(`remove-reason-${cardiology}`).fill("Hartley is back");
  await showCard(page, cardiology);
  await shot(page, "hartley-ends-the-cover");

  await page.getByTestId(`confirm-remove-${cardiology}`).click();
  await expect(page.getByTestId(`approvers-${cardiology}`).locator(`[data-approver="${MENSAH}"]`)).toHaveCount(0);
  await showCard(page, cardiology);
  await shot(page, "the-cover-has-ended");

  // ---- The last permanent approver stays ------------------------------
  await page.getByTestId(`remove-${cardiology}-${HARTLEY}`).click();
  await page.getByTestId(`remove-reason-${cardiology}`).fill("leaving");
  await page.getByTestId(`confirm-remove-${cardiology}`).click();
  await expect(page.getByTestId(`remove-error-${cardiology}`)).toContainText("always keeps at least one permanent approver");
  await showCard(page, cardiology);
  await shot(page, "the-last-approver-stays");

  // ---- The history ----------------------------------------------------
  await page.getByTestId(`remove-reason-${cardiology}`).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Cancel" }).first().click();
  await page.getByTestId(`history-toggle-${cardiology}`).click();
  await expect(page.getByTestId(`history-${cardiology}`)).toContainText("Hartley is back");
  await showCard(page, cardiology);
  await shot(page, "the-history");
});
