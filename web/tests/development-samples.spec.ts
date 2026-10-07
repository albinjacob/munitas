/**
 * U146 in the console: a sample of locked recordings, for building a pipeline version that is still in development.
 *
 * The claims these tests carry are about what reaches the person, on the live platform:
 *   - an engineer asks for a sample, is refused in plain words when the number is too large, and sees the request wait;
 *   - the approver of the owning department sees the request, may lower the number, and cannot choose the recordings; another department's custodian does not see it;
 *   - once approved, the engineer opens the sample, which says it is a sample, whose, and that it names the same recordings and copies none;
 *   - the sample can be ended, and says so;
 *   - an approver may declare recordings synthetic, with a reason, and the page says by whom and why;
 *   - nothing on the page names a patient.
 */

import { expect, test, type Page } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial" });

const ENGINEER = "canary-engineer";
const CUSTODIAN = "canary-custodian";
const ELSEWHERE = "canary-elsewhere";
const RESEARCHER = "canary-researcher";

function wav(dataBytes: number): Buffer {
  const h = Buffer.alloc(44);
  h.write("RIFF", 0);
  h.writeUInt32LE(36 + dataBytes, 4);
  h.write("WAVE", 8);
  h.write("fmt ", 12);
  h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20);
  h.writeUInt16LE(1, 22);
  h.writeUInt32LE(16000, 24);
  h.writeUInt32LE(32000, 28);
  h.writeUInt16LE(2, 32);
  h.writeUInt16LE(16, 34);
  h.write("data", 36);
  h.writeUInt32LE(dataBytes, 40);
  return Buffer.concat([h, randomBytes(dataBytes)]);
}

/** Recordings sealed locked, made through the platform's own routes: six recordings, five patients. */
async function lockedVersion(stamp: number, chart: string): Promise<string> {
  const org = await fetch(`${API_BASE}/organisation?tenant_id=canary`, { headers: await bearerFor(CUSTODIAN) }).then((r) => r.json());
  const department = org.departments.find((d: { name: string }) => d.name === "Verification").id;
  const headers = await bearerFor(ENGINEER);
  const registered = await fetch(`${API_BASE}/datasets/register`, {
    method: "POST",
    headers: { ...headers, "content-type": "application/json" },
    body: JSON.stringify({ tenant_id: "canary", name: `console-samples-${stamp}`, department_id: department, registered_by: ENGINEER, provenance: "internal_regulated", declared_class: "RAW", source_kind: "upload" }),
  });
  expect(registered.status).toBe(201);
  const dataset = (await registered.json()).id as string;
  const plan = [0, 0, 1, 2, 3, 4];
  for (const [n, p] of plan.entries()) {
    const data = wav(40_000 + n * 11_000);
    const opened = await fetch(`${API_BASE}/datasets/${dataset}/recording-uploads`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ file_name: `v-000${n + 1}.wav`, size_bytes: data.length, patient_kind: "hospital chart number", patient_value: `${chart}-${p}` }),
    });
    expect(opened.status).toBe(201);
    const upload = (await opened.json()).id as string;
    const sent = await fetch(`${API_BASE}/recording-uploads/${upload}/parts/1`, { method: "PUT", headers: { ...headers, "content-type": "application/octet-stream" }, body: data });
    expect(sent.status).toBe(200);
    const done = await fetch(`${API_BASE}/recording-uploads/${upload}/complete`, { method: "POST", headers });
    expect(done.status).toBe(200);
  }
  const sealed = await fetch(`${API_BASE}/datasets/${dataset}/seal-recordings`, { method: "POST", headers });
  expect(sealed.status).toBe(201);
  return (await sealed.json()).id as string;
}

async function section(page: Page, versionId: string) {
  await page.goto(`/versions/${versionId}`);
  return page.getByTestId("development-samples");
}

test.describe("U146: development samples of locked recordings", () => {
  let version = "";
  const stamp = Date.now();
  const chart = `U146-${stamp}`;

  test("an engineer asks, is refused in plain words when asking for too much, and sees the request wait", async ({ page }) => {
    version = await lockedVersion(stamp, chart);
    await loginAs(page, ENGINEER);
    const panel = await section(page, version);
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId("sample-ask-form")).toContainText("An approver of the department that owns these recordings, other than you, decides");

    await panel.getByTestId("sample-purpose").fill("Building a step that silences the patient's name");
    await panel.getByTestId("sample-records").fill("99");
    await panel.getByTestId("sample-ask").click();
    await expect(page.getByRole("alert")).toContainText("a sample holds between 1 and 50 recordings");

    await panel.getByTestId("sample-records").fill("4");
    await panel.getByTestId("sample-days").fill("5");
    await panel.getByTestId("sample-ask").click();
    const row = panel.locator('[data-testid^="sample-row-"]').first();
    await expect(row).toContainText("Waiting for an approver");
    await expect(row).toContainText("Asked for 4 recordings for 5 days");
    await expect(panel.getByTestId("sample-kind-develop")).toHaveCount(0);
    await expect(panel.getByTestId("sample-kind-debug")).toBeVisible();
    await expect(panel.locator('[data-testid^="sample-approve-"]')).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText(chart);
  });

  test("another department's custodian does not see the request, and a researcher sees no section at all", async ({ page }) => {
    await loginAs(page, ELSEWHERE);
    const panel = await section(page, version);
    await expect(page.getByTestId("locked-recordings")).toBeVisible();
    await expect(panel.locator('[data-testid^="sample-approve-"]')).toHaveCount(0);
    await loginAs(page, RESEARCHER);
    await page.goto(`/versions/${version}`);
    await expect(page.getByTestId("locked-recordings")).toBeVisible();
    await expect(page.getByTestId("development-samples")).toHaveCount(0);
  });

  test("the owning department's approver lowers the number and approves, and the platform draws", async ({ page }) => {
    await loginAs(page, CUSTODIAN);
    const panel = await section(page, version);
    const waiting = panel.locator('[data-testid^="sample-row-"]').first();
    await expect(waiting).toContainText("asks for 4 recordings for 5 days");
    await expect(waiting).toContainText("You cannot choose the recordings: the platform draws them.");
    const id = (await waiting.getAttribute("data-testid"))!.replace("sample-row-", "");
    await panel.getByTestId(`sample-grant-records-${id}`).fill("5");
    await panel.getByTestId(`sample-approve-${id}`).click();
    await expect(page.getByRole("alert")).toContainText("never raise");
    await panel.getByTestId(`sample-grant-records-${id}`).fill("2");
    await panel.getByTestId(`sample-grant-days-${id}`).fill("3");
    await panel.getByTestId(`sample-approve-${id}`).click();
    const granted = panel.getByTestId(`sample-granted-${id}`);
    await expect(granted).toContainText("2 recordings");
    await expect(granted).toContainText("short");
    await expect(page.locator("body")).not.toContainText(chart);
  });

  test("the engineer opens the sample, which says whose it is and that it copies nothing, and ends it", async ({ page }) => {
    await loginAs(page, ENGINEER);
    const panel = await section(page, version);
    const row = panel.locator('[data-testid^="sample-row-"]').first();
    await expect(row).toContainText("In force");
    await expect(row).toContainText("Approved by");
    await row.locator('[data-testid^="sample-open-"]').click();
    const banner = page.getByTestId("sample-banner");
    await expect(banner).toContainText("This is a development sample of 2 recordings, drawn for you.");
    await expect(banner).toContainText("copies none of them");
    await expect(page.getByTestId("sample-ask-form")).toHaveCount(0);
    await banner.getByRole("link", { name: "the recordings it was drawn from" }).click();
    await expect(page).toHaveURL(new RegExp(`/versions/${version}$`));

    await page.getByTestId("development-samples").locator('[data-testid^="sample-end-"]').first().click();
    await expect(page.getByTestId("development-samples").locator('[data-testid^="sample-row-"]').first()).toContainText("Ended");
    await expect(page.getByTestId("sample-ask-form")).toBeVisible();
  });

  test("an approver declares recordings synthetic with a reason, and the page says by whom", async ({ page }) => {
    const other = await lockedVersion(Date.now(), `U146S-${stamp}`);
    await loginAs(page, CUSTODIAN);
    const panel = await section(page, other);
    await expect(panel.getByTestId("sample-synthetic-declare")).toBeDisabled();
    await panel.getByTestId("sample-synthetic-note").fill("Read aloud by volunteers from invented scripts");
    await panel.getByTestId("sample-synthetic-declare").click();
    await expect(panel.getByTestId("sample-synthetic")).toContainText("Declared synthetic by");
    await expect(panel.getByTestId("sample-synthetic")).toContainText("Read aloud by volunteers from invented scripts");
    await loginAs(page, ENGINEER);
    const seen = await section(page, other);
    await expect(seen.getByTestId("sample-synthetic")).toContainText("a pipeline version in development may run on them");
  });
});
