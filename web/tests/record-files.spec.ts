/**
 * U150 in the console: the files of a version are played through the platform, only for a reason the platform holds, and stop when it ends.
 *
 * The claims these tests carry are about what reaches the person, on the live platform:
 *   - on a sample drawn for them, an engineer is told why they may open it, plays a recording, and what the page's player receives is exactly the recording that
 *     was uploaded, in whole and by range, though it is stored locked;
 *   - on the real recordings nobody is offered to play anything, and the page says why, in words;
 *   - when the sample ends, the page says so and the address the player was given stops working;
 *   - nothing on the page names a patient or shows a key or a storage address.
 */

import { expect, test } from "@playwright/test";
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

async function lockedRecordings(chart: string): Promise<{ version: string; plain: Record<string, Buffer> }> {
  const org = await fetch(`${API_BASE}/organisation?tenant_id=canary`, { headers: await bearerFor(CUSTODIAN) }).then((r) => r.json());
  const department = org.departments.find((d: { name: string }) => d.name === "Verification").id;
  const headers = await bearerFor(ENGINEER);
  const registered = await fetch(`${API_BASE}/datasets/register`, {
    method: "POST",
    headers: { ...headers, "content-type": "application/json" },
    body: JSON.stringify({ tenant_id: "canary", name: `console-files-${Date.now()}`, department_id: department, registered_by: ENGINEER, provenance: "internal_regulated", declared_class: "RAW", source_kind: "upload" }),
  });
  expect(registered.status).toBe(201);
  const dataset = (await registered.json()).id as string;
  const plain: Record<string, Buffer> = {};
  for (const [n, p] of [0, 0, 1, 2].entries()) {
    const record = `v-000${n + 1}`;
    plain[record] = wav(50_000 + n * 30_000);
    const opened = await fetch(`${API_BASE}/datasets/${dataset}/recording-uploads`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ file_name: `${record}.wav`, size_bytes: plain[record].length, person_kind: "hospital chart number", person_value: `${chart}-${p}` }),
    });
    expect(opened.status).toBe(201);
    const upload = (await opened.json()).id as string;
    expect((await fetch(`${API_BASE}/recording-uploads/${upload}/parts/1`, { method: "PUT", headers: { ...headers, "content-type": "application/octet-stream" }, body: plain[record] })).status).toBe(200);
    expect((await fetch(`${API_BASE}/recording-uploads/${upload}/complete`, { method: "POST", headers })).status).toBe(200);
  }
  const sealed = await fetch(`${API_BASE}/datasets/${dataset}/seal-recordings`, { method: "POST", headers });
  expect(sealed.status).toBe(201);
  return { version: (await sealed.json()).id as string, plain };
}

test.describe("U150: playing the files of a version", () => {
  const stamp = Date.now();
  const chart = `U150-${stamp}`;
  let real = "";
  let sample = "";
  let sampleRequest = "";
  let plain: Record<string, Buffer> = {};
  let streamAddress = "";

  test("an engineer plays a recording of their own sample, and the player receives exactly what was uploaded", async ({ page }) => {
    const made = await lockedRecordings(chart);
    real = made.version;
    plain = made.plain;
    const asked = await fetch(`${API_BASE}/dataset-versions/${real}/development-samples`, {
      method: "POST",
      headers: { ...(await bearerFor(ENGINEER)), "content-type": "application/json" },
      body: JSON.stringify({ kind: "debug", purpose: "listen to two recordings", days: 3, records: 2, named_records: ["v-0001", "v-0003"] }),
    });
    expect(asked.status).toBe(201);
    sampleRequest = (await asked.json()).id as string;
    const approved = await fetch(`${API_BASE}/development-samples/${sampleRequest}/approve`, { method: "POST", headers: { ...(await bearerFor(CUSTODIAN)), "content-type": "application/json" }, body: "{}" });
    expect(approved.status).toBe(200);
    sample = (await approved.json()).sample_version_id as string;

    await loginAs(page, ENGINEER);
    await page.goto(`/versions/${sample}`);
    const files = page.getByTestId("record-files");
    await expect(files).toBeVisible({ timeout: 15_000 });
    await expect(files).toContainText("These are in a sample drawn for you, so you may open them while the sample is in force.");
    await expect(files.locator('[data-testid^="record-row-"]')).toHaveCount(2);
    await expect(files.getByTestId("record-row-v-0001")).toContainText("Locked");
    await files.getByTestId("record-play-v-0001").click();
    const player = files.getByTestId("record-player-v-0001");
    await expect(player).toBeVisible();
    streamAddress = (await player.getAttribute("src"))!;
    expect(streamAddress).toMatch(/\/record-stream\//);
    // The browser itself loads it: it reads the header through the platform's range requests and knows the length, which is what playing needs.
    await expect.poll(() => player.evaluate((a: HTMLAudioElement) => a.duration), { timeout: 10_000 }).toBeGreaterThan(0);
    expect(await player.evaluate((a: HTMLAudioElement) => a.error === null)).toBe(true);
    await expect(page.locator("body")).not.toContainText(chart);

    const whole = await fetch(streamAddress);
    expect(whole.status).toBe(200);
    expect(Buffer.from(await whole.arrayBuffer()).equals(plain["v-0001"])).toBe(true);
    const part = await fetch(streamAddress, { headers: { Range: "bytes=100-199" } });
    expect(part.status).toBe(206);
    expect(part.headers.get("content-range")).toBe(`bytes 100-199/${plain["v-0001"].length}`);
    expect(Buffer.from(await part.arrayBuffer()).equals(plain["v-0001"].subarray(100, 200))).toBe(true);
  });

  test("on the real recordings nobody is offered a player, and the page says why", async ({ page }) => {
    for (const who of [ENGINEER, CUSTODIAN, ELSEWHERE, RESEARCHER]) {
      await loginAs(page, who);
      await page.goto(`/versions/${real}`);
      const files = page.getByTestId("record-files");
      await expect(files).toBeVisible({ timeout: 15_000 });
      await expect(files.getByTestId("record-files-note")).toContainText("You cannot open these files.");
      await expect(files.getByTestId("record-files-note")).toContainText("only under a lease");
      await expect(files.locator('[data-testid^="record-play-"]')).toHaveCount(0);
      await expect(files.locator('[data-testid^="record-row-"]')).toHaveCount(4);
    }
  });

  test("the approver, who approved the sample, is not offered it: it is for the person it was drawn for", async ({ page }) => {
    await loginAs(page, CUSTODIAN);
    await page.goto(`/versions/${sample}`);
    const files = page.getByTestId("record-files");
    await expect(files.getByTestId("record-files-note")).toContainText("You cannot open these files.");
    await expect(files.locator('[data-testid^="record-play-"]')).toHaveCount(0);
  });

  test("when the sample ends, the page says so and the address the player was given stops working", async ({ page }) => {
    const ended = await fetch(`${API_BASE}/development-samples/${sampleRequest}/end`, { method: "POST", headers: await bearerFor(CUSTODIAN) });
    expect(ended.status).toBe(200);
    const stopped = await fetch(streamAddress, { headers: { Range: "bytes=0-99" } });
    expect(stopped.status).toBe(403);
    expect(await stopped.text()).toContain("ended");
    await loginAs(page, ENGINEER);
    await page.goto(`/versions/${sample}`);
    await expect(page.getByTestId("record-files-ended")).toContainText("has ended");
    await expect(page.locator('[data-testid^="record-play-"]')).toHaveCount(0);
  });

  test("after its sample ended the platform deletes the sample's files, and the page says so, while the real recordings stay", async ({ page }) => {
    test.setTimeout(150_000);
    await loginAs(page, ENGINEER);
    // The platform's own timer ends it, within a minute of the sample ending: it is waited for, not triggered.
    await expect(async () => {
      await page.goto(`/versions/${sample}`);
      await expect(page.getByTestId("record-files-ended")).toBeVisible({ timeout: 5_000 });
    }).toPass({ timeout: 120_000, intervals: [5_000] });
    await expect(page.getByTestId("record-files-ended")).toContainText("This version was made for development and has ended");
    await expect(page.getByTestId("record-files-ended")).toContainText("Its files are deleted");
    await expect(page.locator('[data-testid^="record-row-"]')).toHaveCount(0);
    await page.goto(`/versions/${real}`);
    await expect(page.locator('[data-testid^="record-row-"]')).toHaveCount(4);
  });
});
