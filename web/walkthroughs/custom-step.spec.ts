/**
 * Captures the screenshots behind docs/public/walkthroughs/custom-step-walkthrough.html.
 *
 * Drives the real console against the live stack as Devi (a data engineer) and Hartley (the data custodian and approver for Cardiology), both in the
 * Health organisation. Nothing here is mocked: if a step cannot be reached, this fails rather than producing a picture of something that did not
 * happen. The pipeline's own script runs for real in the sandbox worker, so the sandbox worker and the host worker must be running. The recordings are
 * noise, made here, and the chart numbers are invented. Setting up the sealed recordings is not part of the story and takes no pictures.
 *
 * Writes into walkthroughs/shots/custom-step/, which is gitignored. The HTML page carries the images base64-encoded.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts custom-step
 *   ..\.venv\Scripts\python.exe ..\docs\tools\build_custom_step_walkthrough.py
 */

import { randomBytes } from "node:crypto";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { bearerFor, loginAs } from "../tests/auth-helpers";
import { API_BASE } from "../config/ports";
import { settled } from "./settled";
import { snap } from "./snap";

const SHOTS = join(process.cwd(), "walkthroughs", "shots", "custom-step");
const DEVI = "eng-devi";
const HARTLEY = "cust-hartley";

let step = 0;

async function shot(page: Page, name: string): Promise<void> {
  await settled(page);
  step += 1;
  await snap(page, { path: join(SHOTS, `${String(step).padStart(2, "0")}-${name}.png`) });
}

async function shotAt(page: Page, testid: string, name: string, offset = 90): Promise<void> {
  await settled(page);
  await page.getByTestId(testid).first().evaluate((e, y) => {
    const top = e.getBoundingClientRect().top + window.scrollY - y;
    window.scrollTo(0, Math.max(0, top));
  }, offset);
  await page.waitForTimeout(250);
  step += 1;
  await snap(page, { path: join(SHOTS, `${String(step).padStart(2, "0")}-${name}.png`) });
}

async function headingAt(page: Page, heading: string, name: string, offset = 90): Promise<void> {
  await settled(page);
  await page.getByRole("heading", { name: heading, exact: true }).first().evaluate((e, y) => {
    const top = e.getBoundingClientRect().top + window.scrollY - y;
    window.scrollTo(0, Math.max(0, top));
  }, offset);
  await page.waitForTimeout(250);
  step += 1;
  await snap(page, { path: join(SHOTS, `${String(step).padStart(2, "0")}-${name}.png`) });
}

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

/** A zip with one file in it, built by hand so that no library is needed: stored, not compressed. */
function zipOf(name: string, text: string): Buffer {
  const body = Buffer.from(text);
  const file = Buffer.from(name);
  let c = ~0;
  for (const byte of body) {
    c ^= byte;
    for (let k = 0; k < 8; k += 1) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  const crc = ~c >>> 0;
  const local = Buffer.alloc(30);
  local.writeUInt32LE(0x04034b50, 0);
  local.writeUInt16LE(20, 4);
  local.writeUInt32LE(crc, 14);
  local.writeUInt32LE(body.length, 18);
  local.writeUInt32LE(body.length, 22);
  local.writeUInt16LE(file.length, 26);
  const central = Buffer.alloc(46);
  central.writeUInt32LE(0x02014b50, 0);
  central.writeUInt16LE(20, 4);
  central.writeUInt16LE(20, 6);
  central.writeUInt32LE(crc, 16);
  central.writeUInt32LE(body.length, 20);
  central.writeUInt32LE(body.length, 24);
  central.writeUInt16LE(file.length, 28);
  const localPart = Buffer.concat([local, file, body]);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(1, 8);
  end.writeUInt16LE(1, 10);
  end.writeUInt32LE(46 + file.length, 12);
  end.writeUInt32LE(localPart.length, 16);
  return Buffer.concat([localPart, central, file, end]);
}

const PIPELINE_YAML = `name: silence-the-start
steps:
  - name: silence
    kind: script
    script: steps/silence.py
    reads_files: true
    writes_files: true
  - name: decide
    kind: gate
    depends_on: [silence]
    to_class: OPEN_FOR_ANNOTATION
    inputs:
      recommendation: "\${steps.silence.recommendation}"
      recommendation_reason: "\${steps.silence.reason}"
`;

// The step's own code: it reads each recording the platform gives it, makes the first half second of the sound silent, and writes the result back
// through the platform. It holds no key and no storage address, and uses the library the platform provides.
const SCRIPT = `import io
import wave

import munitas_step as step

done = []
for item in step.list_files():
    if item["erased"]:
        continue
    record = item["record_id"]
    with wave.open(io.BytesIO(step.read_file(record)), "rb") as source:
        params, frames = source.getparams(), source.readframes(source.getnframes())
    quiet = int(params.framerate * 0.5) * params.sampwidth * params.nchannels
    out = io.BytesIO()
    with wave.open(out, "wb") as target:
        target.setparams(params)
        target.writeframes(bytes(quiet) + frames[quiet:])
    silent = out.getvalue()
    with step.write_output(record + ".silenced.wav", from_record=record, size=len(silent)) as written:
        written.write(silent)
    done.append(record)
step.result({"recommendation": "not_measured", "reason": "the step only changes the sound and measures nothing", "done": done})
`;

test("capture: let your own code read recordings and write files, with an approver", async ({ page }) => {
  test.setTimeout(240_000);
  mkdirSync(SHOTS, { recursive: true });

  const hartleyAuth = await bearerFor(HARTLEY);
  const organisation = await fetch(`${API_BASE}/organisation?tenant_id=health`, { headers: hartleyAuth }).then((r) => r.json());
  const cardiology = organisation.departments.find((d: { name: string }) => d.name === "Cardiology").id;
  const stamp = Date.now();
  const datasetName = `cardiology-consultations-${stamp}`;
  const pipelineName = `silence-the-start-${stamp}`;

  // Setup, not the story: a dataset of sealed locked recordings, brought in through the console as the first walkthrough shows.
  const registered = await fetch(`${API_BASE}/datasets/register`, {
    method: "POST",
    headers: { ...(await bearerFor(DEVI)), "content-type": "application/json" },
    body: JSON.stringify({ tenant_id: "health", name: datasetName, department_id: cardiology, registered_by: DEVI, provenance: "internal_regulated", declared_class: "RAW", source_kind: "upload" }),
  });
  expect(registered.status).toBe(201);
  const datasetId = (await registered.json()).id as string;
  await loginAs(page, DEVI);
  await page.goto(`/datasets/${datasetId}/ingest`);
  await page.getByTestId("erase-yes").check();
  await page.getByTestId("bringing-files").check();
  await page.getByTestId("files-kind").fill("hospital chart number");
  await page.getByTestId("files-manifest").setInputFiles({ name: "manifest.csv", mimeType: "text/csv", buffer: Buffer.from(`file,person\nvisit-1.wav,HC-${stamp}-1\nvisit-2.wav,HC-${stamp}-2\n`) });
  await page.getByTestId("files-files").setInputFiles([
    { name: "visit-1.wav", mimeType: "audio/wav", buffer: wav(64_000) },
    { name: "visit-2.wav", mimeType: "audio/wav", buffer: wav(48_000) },
  ]);
  await page.getByTestId("files-start").click();
  await expect(page.getByTestId("file-visit-2.wav")).toContainText("Locked", { timeout: 90_000 });
  await page.getByTestId("files-seal").click();
  await expect(page.getByTestId("files-sealed")).toContainText("2 files about 2 people", { timeout: 60_000 });
  await page.getByTestId("open-sealed-files").click();
  await expect(page).toHaveURL(/\/versions\/[0-9a-f-]{36}$/);
  const versionUrl = page.url();

  // PART ONE. Devi registers a pipeline whose step reads the recordings and writes files.
  await page.goto("/pipelines");
  await page.getByTestId("pipelines-register-link").click();
  await page.getByTestId("pipeline-name").fill(pipelineName);
  await page.getByTestId("pipeline-department").selectOption({ label: "Cardiology" });
  await page.getByTestId("register-pipeline-submit").click();
  await expect(page.getByTestId("upload-pipeline-version-form")).toBeVisible();
  await page.getByTestId("pipeline-version-config").setInputFiles({ name: "pipeline.yaml", mimeType: "application/x-yaml", buffer: Buffer.from(PIPELINE_YAML) });
  await page.getByTestId("pipeline-version-scripts").setInputFiles({ name: "scripts.zip", mimeType: "application/zip", buffer: zipOf("steps/silence.py", SCRIPT) });
  await shot(page, "devi-uploads-the-pipeline-and-its-script");

  await page.getByTestId("upload-pipeline-version-submit").click();
  await expect(page.getByTestId("pipeline-version-1")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("pipeline-lifecycle-1")).toContainText("an approver of Cardiology other than Devi");
  await expect(page.getByTestId("pipeline-lifecycle-1").getByRole("button", { name: "Move to production" })).toHaveCount(0);
  const pipelineUrl = page.url();
  await shotAt(page, "pipeline-version-1", "the-version-waits-for-an-approver", 40);

  // Devi tries to run it anyway.
  await page.goto(versionUrl);
  await expect(page.getByTestId("version-pipeline-start")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("pipeline-kind").selectOption({ label: `${pipelineName} (v1)` });
  await page.getByTestId("version-pipeline-start").click();
  await expect(page.getByTestId("version-pipeline-refused")).toContainText("is in development");
  await shotAt(page, "version-pipeline-refused", "the-run-is-refused-in-development", 300);

  // PART TWO. Hartley, an approver of the owning department, approves it.
  await loginAs(page, HARTLEY);
  await page.goto(pipelineUrl);
  await expect(page.getByTestId("pipeline-lifecycle-1").getByRole("button", { name: "Move to production" })).toBeVisible();
  await shotAt(page, "pipeline-version-1", "hartley-is-offered-the-move-to-production", 40);
  await page.getByTestId("pipeline-lifecycle-1").getByRole("button", { name: "Move to production" }).click();
  await expect(page.getByTestId("pipeline-lifecycle-1")).toContainText("Moved to production by");
  await shotAt(page, "pipeline-version-1", "the-version-is-in-production", 40);

  // PART THREE. Devi runs it, and finds what it made.
  await loginAs(page, DEVI);
  await page.goto(versionUrl);
  await expect(page.getByTestId("version-pipeline-start")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("pipeline-kind").selectOption({ label: `${pipelineName} (v1)` });
  await page.getByTestId("version-pipeline-start").click();
  await expect(page).toHaveURL(/\/pipeline-runs\/[0-9a-f-]{36}$/, { timeout: 20_000 });
  const runId = page.url().match(/\/pipeline-runs\/([0-9a-f-]{36})/)![1];
  const deadline = Date.now() + 150_000;
  const headers = await bearerFor(DEVI);
  let state = "running";
  while (Date.now() < deadline && state === "running") {
    const res = await fetch(`${API_BASE}/pipeline-runs/${runId}`, { headers });
    if (res.ok) state = ((await res.json()) as { status: string }).status;
    if (state === "running") await new Promise((resolve) => setTimeout(resolve, 2_000));
  }
  expect(state).toBe("succeeded");
  await page.goto(`/pipeline-runs/${runId}`);
  await expect(page.getByTestId("pipeline-run-status")).toContainText("Finished", { timeout: 15_000 });
  await expect(page.getByTestId("pipeline-step-made-silence")).toBeVisible();
  await shotAt(page, "pipeline-step-silence", "the-run-finishes-and-names-what-it-made", 160);

  await page.getByTestId("pipeline-step-made-silence").click();
  await expect(page).toHaveURL(/\/versions\/[0-9a-f-]{36}$/);
  await shot(page, "the-new-version-waits-for-a-review");
  await page.setViewportSize({ width: 1920, height: 640 });
  await headingAt(page, "Where this came from", "where-the-new-version-came-from", 40);

  // PART FOUR. The version is retired, and cannot be run again.
  await loginAs(page, HARTLEY);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(pipelineUrl);
  await page.getByTestId("pipeline-lifecycle-1").getByRole("button", { name: "Retire this version" }).click();
  await expect(page.getByTestId("pipeline-lifecycle-1")).toContainText("Retiring is final");
  await page.getByTestId("pipeline-lifecycle-1").getByRole("button", { name: "Retire v1" }).click();
  await expect(page.getByTestId("pipeline-stage-1")).toHaveText("Retired");
  await shotAt(page, "pipeline-version-1", "hartley-retires-the-version", 40);

  await loginAs(page, DEVI);
  await page.goto(versionUrl);
  await expect(page.getByTestId("version-pipeline-start")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("pipeline-kind").selectOption({ label: `${pipelineName} (v1)` });
  await page.getByTestId("version-pipeline-start").click();
  await expect(page.getByTestId("version-pipeline-refused")).toContainText("was retired");
  await shotAt(page, "version-pipeline-refused", "a-retired-version-cannot-be-run", 300);
});
