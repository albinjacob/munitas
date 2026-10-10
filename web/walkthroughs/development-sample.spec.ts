/**
 * Captures the screenshots behind docs/public/walkthroughs/development-sample-walkthrough.html.
 *
 * Drives the real console against the live stack as Devi (a data engineer) and Hartley (the data custodian and approver for Cardiology), both in the
 * Health organisation. Nothing here is mocked: if a step cannot be reached, this fails rather than producing a picture of something that did not
 * happen. The pipeline's own script runs for real in the sandbox worker, so the sandbox worker and the host worker must be running. The recordings are
 * noise, made here, and the chart numbers are invented. Setting up the sealed recordings and the pipeline version is not part of the story and takes
 * no pictures.
 *
 * Writes into walkthroughs/shots/development-sample/, which is gitignored. The HTML page carries the images base64-encoded.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts development-sample
 *   ..\.venv\Scripts\python.exe ..\docs\tools\build_development_sample_walkthrough.py
 */

import { randomBytes } from "node:crypto";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { bearerFor, loginAs } from "../tests/auth-helpers";
import { API_BASE } from "../config/ports";
import { settled } from "./settled";
import { snap } from "./snap";

const SHOTS = join(process.cwd(), "walkthroughs", "shots", "development-sample");
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

/** Setup, not the story: recordings sealed locked through the platform's own routes. Six recordings of five patients, of different lengths. */
async function lockedRecordings(department: string, name: string, chart: string): Promise<{ dataset: string; version: string }> {
  const headers = await bearerFor(DEVI);
  const registered = await fetch(`${API_BASE}/datasets/register`, {
    method: "POST",
    headers: { ...headers, "content-type": "application/json" },
    body: JSON.stringify({ tenant_id: "health", name, department_id: department, registered_by: DEVI, provenance: "internal_regulated", declared_class: "RAW", source_kind: "upload" }),
  });
  expect(registered.status).toBe(201);
  const dataset = (await registered.json()).id as string;
  for (const [n, p] of [0, 0, 1, 2, 3, 4].entries()) {
    const data = wav(60_000 + n * 40_000);
    const opened = await fetch(`${API_BASE}/datasets/${dataset}/file-uploads`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ file_name: `visit-${n + 1}.wav`, size_bytes: data.length, person_kind: "hospital chart number", person_value: `${chart}-${p}` }),
    });
    expect(opened.status).toBe(201);
    const upload = (await opened.json()).id as string;
    expect((await fetch(`${API_BASE}/file-uploads/${upload}/parts/1`, { method: "PUT", headers: { ...headers, "content-type": "application/octet-stream" }, body: data })).status).toBe(200);
    expect((await fetch(`${API_BASE}/file-uploads/${upload}/complete`, { method: "POST", headers })).status).toBe(200);
  }
  const sealed = await fetch(`${API_BASE}/datasets/${dataset}/seal-files`, { method: "POST", headers });
  expect(sealed.status).toBe(201);
  return { dataset, version: (await sealed.json()).id as string };
}

async function startAndWait(page: Page, pipelineName: string): Promise<string> {
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
  return runId;
}

test("capture: build a pipeline version on a development sample of locked recordings", async ({ page }) => {
  test.setTimeout(300_000);
  mkdirSync(SHOTS, { recursive: true });

  const organisation = await fetch(`${API_BASE}/organisation?tenant_id=health`, { headers: await bearerFor(HARTLEY) }).then((r) => r.json());
  const cardiology = organisation.departments.find((d: { name: string }) => d.name === "Cardiology").id;
  const stamp = Date.now();
  const pipelineName = `silence-the-start-${stamp}`;
  const real = await lockedRecordings(cardiology, `cardiology-consultations-${stamp}`, `HC-${stamp}`);

  // Setup: a pipeline version with a step of its own, uploaded and left in development.
  const devi = await bearerFor(DEVI);
  const made = await fetch(`${API_BASE}/pipelines/register`, {
    method: "POST",
    headers: { ...devi, "content-type": "application/json" },
    body: JSON.stringify({ tenant_id: "health", name: pipelineName, department_id: cardiology, registered_by: DEVI }),
  });
  expect(made.status).toBe(201);
  const pipelineId = (await made.json()).id as string;
  const form = new FormData();
  form.append("config_file", new Blob([PIPELINE_YAML]), "pipeline.yaml");
  form.append("scripts", new Blob([zipOf("steps/silence.py", SCRIPT)]), "scripts.zip");
  form.append("registered_by", DEVI);
  const uploaded = await fetch(`${API_BASE}/pipelines/${pipelineId}/versions/upload`, { method: "POST", headers: devi, body: form });
  expect(uploaded.status).toBe(201);
  expect((await uploaded.json()).stage).toBe("development");

  // PART ONE. Devi asks for a sample, and Hartley decides.
  await loginAs(page, DEVI);
  await page.goto(`/versions/${real.version}`);
  const panel = page.getByTestId("development-samples");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("sample-records").fill("4");
  await panel.getByTestId("sample-days").fill("5");
  await panel.getByTestId("sample-purpose").fill("Building a step that makes the first half second of each recording silent");
  await shotAt(page, "development-samples", "devi-asks-for-a-sample", 40);

  await panel.getByTestId("sample-ask").click();
  await expect(panel.locator('[data-testid^="sample-row-"]').first()).toContainText("Waiting for an approver");
  await shotAt(page, "development-samples", "the-request-waits-for-an-approver", 40);

  await loginAs(page, HARTLEY);
  await page.goto(`/versions/${real.version}`);
  const hartleyPanel = page.getByTestId("development-samples");
  const waiting = hartleyPanel.locator('[data-testid^="sample-row-"]').first();
  await expect(waiting).toContainText("asks for 4 files for 5 days");
  const id = (await waiting.getAttribute("data-testid"))!.replace("sample-row-", "");
  await shotAt(page, "development-samples", "hartley-sees-the-request", 40);

  await hartleyPanel.getByTestId(`sample-grant-records-${id}`).fill("3");
  await hartleyPanel.getByTestId(`sample-grant-days-${id}`).fill("3");
  await hartleyPanel.getByTestId(`sample-note-${id}`).fill("Three is enough to find the first faults");
  await hartleyPanel.getByTestId(`sample-approve-${id}`).click();
  await expect(hartleyPanel.getByTestId(`sample-granted-${id}`)).toContainText("3 files");
  await shotAt(page, "development-samples", "hartley-approves-fewer-and-the-platform-draws", 40);

  // PART TWO. Devi uses it.
  await loginAs(page, DEVI);
  await page.goto(`/versions/${real.version}`);
  const mine = page.getByTestId("development-samples").locator('[data-testid^="sample-row-"]').first();
  await expect(mine).toContainText("In force");
  await mine.locator('[data-testid^="sample-open-"]').click();
  await expect(page.getByTestId("sample-banner")).toBeVisible();
  const sampleUrl = page.url();
  await shotAt(page, "sample-banner", "devi-opens-the-sample", 260);

  await page.goto(`/versions/${real.version}`);
  await expect(page.getByTestId("version-pipeline-start")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("pipeline-kind").selectOption({ label: `${pipelineName} (v1)` });
  await page.getByTestId("version-pipeline-start").click();
  await expect(page.getByTestId("version-pipeline-refused")).toContainText("development data");
  await shotAt(page, "version-pipeline-refused", "on-the-real-recordings-it-is-refused", 300);

  await page.goto(sampleUrl);
  const runId = await startAndWait(page, pipelineName);
  await expect(page.getByTestId("pipeline-step-made-silence")).toBeVisible();
  await shotAt(page, "pipeline-step-silence", "on-the-sample-it-runs", 160);

  // The files the step wrote are Devi's to open, because Devi's run made them on Devi's sample.
  await page.getByTestId("pipeline-step-made-silence").click();
  await expect(page).toHaveURL(/\/versions\/[0-9a-f-]{36}$/);
  const madeUrl = page.url();
  const madeFiles = page.getByTestId("record-files");
  await expect(madeFiles).toBeVisible({ timeout: 15_000 });
  await expect(madeFiles.locator('[data-testid^="record-row-"]')).toHaveCount(3);
  await shotAt(page, "record-files", "devi-sees-the-files-the-step-made", 40);
  await madeFiles.locator('[data-testid^="record-play-"]').first().click();
  const player = madeFiles.locator('[data-testid^="record-player-"]').first();
  await expect(player).toBeVisible();
  await expect.poll(() => player.evaluate((a: HTMLAudioElement) => a.duration), { timeout: 10_000 }).toBeGreaterThan(0);
  await shotAt(page, "record-files", "devi-plays-one", 40);
  await loginAs(page, HARTLEY);
  await page.goto(madeUrl);
  await expect(page.getByTestId("record-files-note")).toBeVisible({ timeout: 15_000 });
  await shotAt(page, "record-files", "hartley-cannot-open-them", 40);
  await loginAs(page, DEVI);

  // PART THREE. The sample ends.
  await page.goto(`/versions/${real.version}`);
  await page.getByTestId("development-samples").locator('[data-testid^="sample-end-"]').first().click();
  await expect(page.getByTestId("development-samples").locator('[data-testid^="sample-row-"]').first()).toContainText("Ended");
  await shotAt(page, "development-samples", "devi-ends-the-sample", 40);

  await page.goto(sampleUrl);
  await expect(page.getByTestId("version-pipeline-start")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("pipeline-kind").selectOption({ label: `${pipelineName} (v1)` });
  await page.getByTestId("version-pipeline-start").click();
  await expect(page.getByTestId("version-pipeline-refused")).toContainText("development data");
  await shotAt(page, "version-pipeline-refused", "an-ended-sample-cannot-be-used", 300);
  expect(runId).toBeTruthy();

  await page.goto(madeUrl);
  await expect(page.getByTestId("record-files-ended")).toBeVisible({ timeout: 15_000 });
  await shotAt(page, "record-files", "when-the-sample-ends-the-files-close", 40);

  // PART FOUR. Recordings with no real person in them need no sample.
  const synthetic = await lockedRecordings(cardiology, `cardiology-read-aloud-${stamp}`, `SYN-${stamp}`);
  await loginAs(page, HARTLEY);
  await page.goto(`/versions/${synthetic.version}`);
  const synPanel = page.getByTestId("development-samples");
  await synPanel.getByTestId("sample-synthetic-note").fill("Read aloud by volunteers from invented scripts, so no patient is in them");
  await shotAt(page, "development-samples", "hartley-declares-recordings-synthetic", 40);
  await synPanel.getByTestId("sample-synthetic-declare").click();
  await expect(synPanel.getByTestId("sample-synthetic")).toContainText("Declared synthetic by");
  await shotAt(page, "development-samples", "the-declaration-is-recorded", 40);
});
