/**
 * U164 in the console: asking to open a few real files, deciding it, listening, and ending it.
 *
 * The claims these tests carry are about what reaches the person, on the live platform:
 *   - a researcher is offered a form on the files of a version, asks in words, and then sees that the request is waiting;
 *   - an approver sees it on the page of locked data requests, allows fewer files than were asked for, and the researcher then sees exactly those files
 *     and no others, and can play one;
 *   - the researcher who asked cannot decide their own request, and is not shown the buttons for it;
 *   - ending it closes the files again, on the next look at the page.
 */

import { expect, test } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial" });

const ENGINEER = "canary-engineer";
const CUSTODIAN = "canary-custodian";
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

async function sealedFiles(chart: string, count: number): Promise<string> {
  const org = await fetch(`${API_BASE}/organisation?tenant_id=canary`, { headers: await bearerFor(CUSTODIAN) }).then((r) => r.json());
  const department = org.departments.find((d: { name: string }) => d.name === "Verification").id;
  const headers = await bearerFor(ENGINEER);
  const registered = await fetch(`${API_BASE}/datasets/register`, {
    method: "POST",
    headers: { ...headers, "content-type": "application/json" },
    body: JSON.stringify({ tenant_id: "canary", name: `console-lease-${Date.now()}`, department_id: department, registered_by: ENGINEER, provenance: "internal_regulated", declared_class: "RAW", source_kind: "upload" }),
  });
  expect(registered.status).toBe(201);
  const dataset = (await registered.json()).id as string;
  for (let i = 0; i < count; i++) {
    const body = wav(40_000 + i * 2_000);
    const opened = await fetch(`${API_BASE}/datasets/${dataset}/file-uploads`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ file_name: `r-${i + 1}.wav`, size_bytes: body.length, person_kind: "hospital chart number", person_value: `${chart}-${i}` }),
    });
    expect(opened.status).toBe(201);
    const upload = (await opened.json()).id as string;
    expect((await fetch(`${API_BASE}/file-uploads/${upload}/parts/1`, { method: "PUT", headers: { ...headers, "content-type": "application/octet-stream" }, body })).status).toBe(200);
    expect((await fetch(`${API_BASE}/file-uploads/${upload}/complete`, { method: "POST", headers })).status).toBe(200);
  }
  const sealed = await fetch(`${API_BASE}/datasets/${dataset}/seal-files`, { method: "POST", headers });
  expect(sealed.status).toBe(201);
  return (await sealed.json()).id as string;
}

test.describe("U164: opening a few real files with an approver's permission", () => {
  const purpose = `listen to a few calls ${Date.now()}`;
  let version = "";

  test("a researcher asks in the form on the files of a version, and the request is shown as waiting", async ({ page }) => {
    version = await sealedFiles(`U164-${Date.now()}`, 6);
    await loginAs(page, RESEARCHER);
    await page.goto(`/versions/${version}`);
    const panel = page.getByTestId("file-lease-panel");
    await expect(panel).toBeVisible({ timeout: 15_000 });
    await expect(panel.getByTestId("file-lease-form")).toBeVisible();
    await expect(panel.getByTestId("file-lease-submit")).toBeDisabled();
    await panel.getByTestId("file-lease-purpose").fill(purpose);
    await panel.getByTestId("file-lease-records").fill("4");
    await panel.getByTestId("file-lease-submit").click();
    await expect(panel.getByTestId("file-lease-waiting-console")).toContainText("waiting for a decision", { timeout: 15_000 });
    await expect(panel.getByTestId("file-lease-waiting-console")).toContainText(purpose);
    await expect(page.locator('[data-testid^="record-play-"]')).toHaveCount(0);
  });

  test("the person who asked is not given the buttons to decide it", async ({ page }) => {
    await loginAs(page, RESEARCHER);
    await page.goto("/locked-column-access");
    const mine = page.getByTestId("file-leases-mine");
    await expect(mine).toContainText(purpose, { timeout: 15_000 });
    await expect(mine).toContainText("Waiting for an approver");
    await expect(page.locator('[data-testid^="file-approve-"]')).toHaveCount(0);
  });

  test("an approver allows fewer files, and the researcher then sees exactly those and can play one", async ({ page }) => {
    await loginAs(page, CUSTODIAN);
    await page.goto("/locked-column-access");
    const waiting = page.getByTestId("file-leases-to-decide");
    const row = waiting.locator("tr", { hasText: purpose });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.locator('input[data-testid^="file-allow-records-"]').fill("2");
    await row.locator('input[data-testid^="file-note-"]').fill("two is enough to judge how long they run");
    await row.locator('[data-testid^="file-approve-"]').click();
    await expect(page.getByTestId("file-leases-granted").locator("tr", { hasText: purpose })).toContainText("2 files", { timeout: 15_000 });

    await loginAs(page, RESEARCHER);
    await page.goto(`/versions/${version}`);
    const active = page.getByTestId("file-lease-active-console");
    await expect(active).toBeVisible({ timeout: 15_000 });
    await expect(active).toContainText("You may open 2 files");
    const files = page.getByTestId("record-files");
    await expect(files.locator('[data-testid^="record-row-"]')).toHaveCount(2);
    const first = files.locator('[data-testid^="record-play-"]').first();
    await first.click();
    const player = files.locator('[data-testid^="record-player-"]').first();
    await expect(player).toBeVisible();
    await expect.poll(() => player.evaluate((a: HTMLAudioElement) => a.duration), { timeout: 10_000 }).toBeGreaterThan(0);
  });

  test("a notebook is asked for in the same form, decided in the same place, and says how to use it", async ({ page }) => {
    const notebookPurpose = `read a few calls in a notebook ${Date.now()}`;
    await loginAs(page, RESEARCHER);
    await page.goto(`/versions/${version}`);
    const panel = page.getByTestId("file-lease-panel");
    // The page is already held open for listening, so the only way left to ask is a notebook, and the form says so.
    await expect(panel).toContainText("You open them in a notebook, with the Munitas client library", { timeout: 15_000 });
    await expect(panel).toContainText("up to 200");
    await panel.getByTestId("file-lease-purpose").fill(notebookPurpose);
    await panel.getByTestId("file-lease-records").fill("3");
    await panel.getByTestId("file-lease-hours").fill("2");
    await panel.getByTestId("file-lease-submit").click();
    await expect(panel.getByTestId("file-lease-waiting-notebook")).toContainText("in a notebook", { timeout: 15_000 });

    await loginAs(page, CUSTODIAN);
    await page.goto("/locked-column-access");
    const row = page.getByTestId("file-leases-to-decide").locator("tr", { hasText: notebookPurpose });
    await expect(row).toContainText("In a notebook", { timeout: 15_000 });
    await row.locator('[data-testid^="file-approve-"]').click();
    await expect(page.getByTestId("file-leases-granted").locator("tr", { hasText: notebookPurpose })).toContainText("3 files", { timeout: 15_000 });

    await loginAs(page, RESEARCHER);
    await page.goto(`/versions/${version}`);
    const active = page.getByTestId("file-lease-active-notebook");
    await expect(active).toBeVisible({ timeout: 15_000 });
    await expect(active).toContainText("You may open 3 files of this version in a notebook");
    await expect(active).toContainText("client library");
    // The page plays only what the lease for the page names (2 files). The notebook's 3 files are not added to it.
    await expect(page.locator('[data-testid^="record-play-"]')).toHaveCount(2);
    await active.getByTestId("file-lease-end-notebook").click();
    await expect(page.getByTestId("file-lease-active-notebook")).toHaveCount(0, { timeout: 15_000 });
  });

  test("ending it closes the files again", async ({ page }) => {
    await loginAs(page, RESEARCHER);
    await page.goto(`/versions/${version}`);
    await page.getByTestId("file-lease-end-console").click();
    await expect(page.getByTestId("file-lease-form")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-testid^="record-play-"]')).toHaveCount(0);
    await page.goto("/locked-column-access");
    await expect(page.getByTestId("file-leases-mine").locator("tr", { hasText: purpose })).toContainText("Ended");
  });
});
