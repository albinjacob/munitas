/**
 * Captures the screenshots behind docs/public/walkthroughs/locked-recordings-walkthrough.html.
 *
 * Drives the real console against the live stack as Devi, a data engineer in the Cardiology department of the Health organisation. Nothing here is
 * mocked: if a step cannot be reached, this fails rather than producing a picture of something that did not happen. The recordings are real wav
 * files made here (noise, so that nothing of a real person is in them), and the hospital chart numbers in the manifest are invented.
 *
 * Writes into walkthroughs/shots/locked-recordings/, which is gitignored. The HTML page carries the images base64-encoded, so the page is the
 * committed artefact and these files are scratch.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts locked-recordings
 *   ..\.venv\Scripts\python.exe ..\docs\tools\build_locked_recordings_walkthrough.py
 */

import { randomBytes } from "node:crypto";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { bearerFor, loginAs } from "../tests/auth-helpers";
import { API_BASE } from "../config/ports";
import { settled } from "./settled";

const SHOTS = join(process.cwd(), "walkthroughs", "shots", "locked-recordings");
const DEVI = "eng-devi";
const HARTLEY = "cust-hartley";

let step = 0;

async function shotAt(page: Page, testid: string, name: string, offset = 90): Promise<void> {
  await settled(page);
  await page.getByTestId(testid).first().evaluate((e, y) => {
    const top = e.getBoundingClientRect().top + window.scrollY - y;
    window.scrollTo(0, Math.max(0, top));
  }, offset);
  await page.waitForTimeout(250);
  step += 1;
  await page.screenshot({ path: join(SHOTS, `${String(step).padStart(2, "0")}-${name}.png`) });
}

async function headingAt(page: Page, heading: string, name: string, offset = 90): Promise<void> {
  await settled(page);
  await page.getByRole("heading", { name: heading, exact: true }).first().evaluate((e, y) => {
    const top = e.getBoundingClientRect().top + window.scrollY - y;
    window.scrollTo(0, Math.max(0, top));
  }, offset);
  await page.waitForTimeout(250);
  step += 1;
  await page.screenshot({ path: join(SHOTS, `${String(step).padStart(2, "0")}-${name}.png`) });
}

/** A real wav file: a header that says so, and noise for the sound. */
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

const csv = (name: string, text: string) => ({ name, mimeType: "text/csv", buffer: Buffer.from(text) });
const audio = (name: string, bytes: number) => ({ name, mimeType: "audio/wav", buffer: wav(bytes) });
const answerKey = (name: string, words: string) => ({ name, mimeType: "application/json", buffer: Buffer.from(JSON.stringify({ reference_transcript: words, spans: [] })) });

test("capture: bring in recordings so that one patient can be erased later", async ({ page }) => {
  mkdirSync(SHOTS, { recursive: true });

  const hartleyAuth = await bearerFor(HARTLEY);
  const organisation = await fetch(`${API_BASE}/organisation?tenant_id=health`, { headers: hartleyAuth }).then((r) => r.json());
  const cardiology = organisation.departments.find((d: { name: string }) => d.name === "Cardiology").id;

  // Registered through the API, as the story starts after it: this page is about what happens when the recordings arrive.
  const registered = await fetch(`${API_BASE}/datasets/register`, {
    method: "POST",
    headers: { ...(await bearerFor(DEVI)), "content-type": "application/json" },
    body: JSON.stringify({
      tenant_id: "health",
      name: `cardiology-consultations-${Date.now()}`,
      department_id: cardiology,
      registered_by: DEVI,
      provenance: "internal_regulated",
      declared_class: "RAW",
      source_kind: "upload",
    }),
  });
  expect(registered.status).toBe(201);
  const datasetId = (await registered.json()).id as string;
  const stamp = Date.now();
  const [chartA, chartB] = [`HC-${stamp}-1`, `HC-${stamp}-2`];

  await loginAs(page, DEVI);
  await page.goto(`/datasets/${datasetId}/ingest`);
  await expect(page.getByTestId("erase-choice")).toBeVisible();
  await expect(page.getByTestId("erase-no")).toBeChecked();
  await shotAt(page, "erase-choice", "devi-is-asked-whether-anyone-may-need-erasing", 40);

  await page.getByTestId("erase-yes").check();
  await page.getByTestId("bringing-recordings").check();
  await expect(page.getByTestId("recordings-panel")).toBeVisible();
  await shotAt(page, "erase-choice", "devi-chooses-recordings", 40);

  // A manifest and a set of files with several mistakes: all of them are listed before a single byte is sent.
  await page.getByTestId("recordings-kind").fill("hospital chart number");
  await page.getByTestId("recordings-manifest").setInputFiles(csv("manifest.csv", `file,patient\nvisit-1.wav,${chartA}\nvisit-2.wav,\nvisit-3.wav,${chartB}\n`));
  await page.getByTestId("recordings-files").setInputFiles([audio("visit-1.wav", 30_000), audio("visit-4.wav", 20_000), answerKey("visit-9.truth.json", "invented words")]);
  await expect(page.getByTestId("recordings-problems")).toContainText("visit-2.wav names no patient");
  await shotAt(page, "recordings-panel", "every-mistake-is-listed-before-anything-is-sent", 40);

  // The corrected manifest and the right files.
  await page.getByTestId("recordings-manifest").setInputFiles(csv("manifest.csv", `file,patient\nvisit-1.wav,${chartA}\nvisit-2.wav,${chartB}\n`));
  await page.getByTestId("recordings-files").setInputFiles([
    audio("visit-1.wav", 30_000),
    audio("visit-2.wav", 60_000),
    answerKey("visit-1.truth.json", "A doctor and a patient talk about a check up, using invented names."),
  ]);
  await expect(page.getByTestId("recordings-problems")).toHaveCount(0);
  await expect(page.getByTestId("recordings-start")).toBeEnabled();
  await shotAt(page, "recordings-panel", "the-files-are-ready", 40);

  await page.getByTestId("recordings-start").click();
  await expect(page.getByTestId("recording-visit-1.wav")).toContainText("Locked", { timeout: 90_000 });
  await expect(page.getByTestId("recording-visit-2.wav")).toContainText("Locked", { timeout: 90_000 });
  await expect(page.getByTestId("recording-visit-1.truth.json")).toContainText("Locked", { timeout: 90_000 });
  await shotAt(page, "recordings-panel", "every-file-is-locked-under-its-patients-key", 40);

  await page.getByTestId("recordings-seal").click();
  await expect(page.getByTestId("recordings-sealed")).toContainText("2 recordings about 2 patients", { timeout: 60_000 });
  await shotAt(page, "recordings-sealed", "the-recordings-are-sealed", 200);

  await page.getByTestId("open-sealed-recordings").click();
  await expect(page.getByTestId("locked-recordings")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(chartA);
  // A shorter window, so that the page can scroll far enough to put this panel at the top of the picture.
  await page.setViewportSize({ width: 1920, height: 640 });
  await headingAt(page, "Locked recordings", "the-version-says-what-it-holds", 40);
});
