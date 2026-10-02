/**
 * Captures the screenshots behind docs/custom-pipeline-walkthrough.html.
 *
 * Drives the real console against the live stack. Nothing here is mocked:
 * if a step cannot be reached, this fails rather than producing a picture
 * of something that did not happen.
 *
 * Covers registering and running an operator-authored DAG pipeline,
 * start to finish: Devi, a data engineer in Cardiology, registers a
 * pipeline the platform never shipped (checking that a cardiology note
 * carries the structured fields Cardiology needs for its own quality
 * registry reporting), uploads its config and scripts, registers and
 * seals a dataset, is refused until the department's own custodian grants
 * access, runs her pipeline, and hits the same gate decision every other
 * pipeline on the platform ends in.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts custom-pipeline
 *   ..\.venv\Scripts\python.exe ..\docs\tools\build_custom_pipeline_walkthrough.py
 */

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { EMAIL_BY_DIRECTORY_ID, PASSWORD, bearerFor, loginAs } from "../tests/auth-helpers";
import { API_BASE } from "../config/ports";
import { settled } from "./settled";

const SHOTS = join(process.cwd(), "walkthroughs", "shots", "custom-pipeline");
const PIPELINE_YAML = join(process.cwd(), "walkthroughs", "fixtures", "cardiac-note-qc.yaml");
const PIPELINE_SCRIPTS_ZIP = join(process.cwd(), "walkthroughs", "fixtures", "cardiac-note-qc-scripts.zip");

const API = API_BASE;

const ENGINEER = "eng-devi";
const CUSTODIAN_CARDIOLOGY = "cust-hartley";
const REVIEWER = "rev-imani";

const REQUEST_PURPOSE = "run cardiac-note-qc against this version to check note quality before wider release";

let step = 0;

async function shot(page: Page, name: string): Promise<void> {
  await settled(page);
  step += 1;
  const n = String(step).padStart(2, "0");
  await page.screenshot({ path: join(SHOTS, `${n}-${name}.png`) });
}

/**
 * Waits for a pipeline run to reach a terminal status, or fails the test
 * rather than hanging forever. Polls the API directly from Node, not the
 * browser DOM: healthcare.spec.ts's own waitForPipelineRun found this
 * stack's client-side polling cannot be trusted to keep running in a
 * headless, unfocused browser, so this reuses the same approach rather
 * than rediscovering the same failure.
 */
async function waitForPipelineRun(page: Page, timeoutMs = 120_000): Promise<void> {
  const match = page.url().match(/\/pipeline-runs\/([0-9a-f-]{36})/);
  if (!match) throw new Error(`not on a pipeline run page: ${page.url()}`);
  const runId = match[1];

  const deadline = Date.now() + timeoutMs;
  let status = "running";
  const headers = await bearerFor(ENGINEER);
  while (Date.now() < deadline) {
    const res = await fetch(`${API}/pipeline-runs/${runId}`, { headers });
    if (res.ok) {
      const run = (await res.json()) as { status: string };
      status = run.status;
      if (status !== "running") break;
    }
    await new Promise((resolve) => setTimeout(resolve, 2_000));
  }
  if (status === "running") {
    throw new Error(`the pipeline run did not finish within ${timeoutMs}ms`);
  }
  if (status !== "succeeded") {
    throw new Error(`the pipeline run did not finish cleanly: status was ${status}`);
  }

  await page.goto(`/pipeline-runs/${runId}`);
  await expect(page.getByTestId("pipeline-run-status")).toContainText("Finished", { timeout: 15_000 });
}

test("capture: an operator registers and runs their own pipeline", async ({ page }) => {
  mkdirSync(SHOTS, { recursive: true });
  test.setTimeout(180_000);

  const suffix = `${new Date().toISOString().slice(0, 10)}-${Date.now() % 10000}`;
  const pipelineName = `cardiac-note-qc-${suffix}`;
  const datasetName = `cardiology-intake-notes-${suffix}`;

  // ---- Act one: Devi registers her own pipeline --------------------------
  await loginAs(page, ENGINEER);
  await page.goto("/pipelines");
  // The page draws before the person is resolved, and a picture taken then shows only "Nobody is
  // signed in" and a loading line. Wait for the person and for the list.
  await expect(page.getByTestId("current-persona")).toBeVisible();
  await expect(page.getByTestId("pipelines-register-link")).toBeVisible();
  await expect(page.getByText("Loading")).toHaveCount(0);
  await shot(page, "pipelines-list");

  await page.getByTestId("pipelines-register-link").click();
  await expect(page.getByTestId("register-pipeline-form")).toBeVisible();
  await page.getByTestId("pipeline-name").fill(pipelineName);
  await page.getByTestId("pipeline-department").selectOption({ label: "Cardiology" });
  await shot(page, "names-it-and-its-department");

  await page.getByTestId("register-pipeline-submit").click();
  await expect(page.getByTestId("upload-pipeline-version-form")).toBeVisible();
  await shot(page, "registered-no-version-yet");

  await page.getByTestId("pipeline-version-config").setInputFiles(PIPELINE_YAML);
  await page.getByTestId("pipeline-version-scripts").setInputFiles(PIPELINE_SCRIPTS_ZIP);
  await shot(page, "uploads-the-dag-config-and-scripts");

  await page.getByTestId("upload-pipeline-version-submit").click();
  await expect(page.getByTestId("pipeline-version-1")).toBeVisible({ timeout: 15000 });
  await shot(page, "version-1-is-sealed");

  // ---- Act two: a dataset is brought in, and Devi has to ask -------------
  await page.goto("/datasets/register");
  await page.getByTestId("register-name").fill(datasetName);
  await page.getByTestId("register-department").selectOption({ label: "Cardiology" });
  await page.getByTestId("modality-text").check();
  await shot(page, "registers-the-dataset");

  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("upload-step")).toBeVisible();
  await page.getByTestId("upload-files").setInputFiles({
    name: "consult-note.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Cardiology consult note, awaiting structured-field check.\n"),
  });
  await expect(page.getByTestId("uploaded-files")).toContainText("consult-note.txt", { timeout: 15000 });
  await page.getByTestId("seal-submit").click();
  await expect(page.getByTestId("seal-done")).toBeVisible({ timeout: 15000 });
  await shot(page, "uploads-a-file-and-seals-version-1");

  await page.getByRole("link", { name: "Run a pipeline against this version" }).click();
  await expect(page).toHaveURL(/\/versions\/[0-9a-f-]{36}$/);
  await expect(page.getByTestId("request-access")).toBeVisible();
  await page.getByTestId("request-purpose").fill(REQUEST_PURPOSE);
  await page.getByTestId("request-justification").fill(
    "checking required registry fields before this note is used more widely",
  );
  await shot(page, "tries-to-run-and-has-to-ask-first");

  await page.getByTestId("request-submit").click();
  await expect(page.getByTestId("request-sent")).toBeVisible();
  const versionUrl = page.url();
  await shot(page, "the-request-goes-to-cardiologys-custodian");

  // ---- Act three: Cardiology's custodian grants it -----------------------
  await loginAs(page, CUSTODIAN_CARDIOLOGY);
  await expect(page.getByTestId("pending-requests")).toBeVisible();
  await expect(page.getByTestId("pending-requests")).toContainText(REQUEST_PURPOSE);
  await shot(page, "sees-devis-request-waiting");

  const requestRow = page.getByTestId("pending-requests").locator("li", { hasText: REQUEST_PURPOSE });
  await requestRow.getByRole("button", { name: "Grant access" }).click();
  await expect(requestRow).toHaveCount(0);
  await shot(page, "grants-access");

  // ---- Act four: Devi runs her own pipeline against it -------------------
  await loginAs(page, ENGINEER);
  await page.goto(versionUrl);
  await expect(page.getByTestId("version-pipeline-start")).toBeVisible({ timeout: 15000 });
  await shot(page, "access-shows-up-on-the-version-page");

  await page.getByTestId("pipeline-kind").selectOption({ label: `${pipelineName} (v1)` });
  await shot(page, "picks-her-own-pipeline-from-the-run-picker");

  await page.getByTestId("version-pipeline-start").click();
  await expect(page).toHaveURL(/\/pipeline-runs\/[0-9a-f-]{36}$/, { timeout: 15000 });
  await shot(page, "starts-the-run");

  await waitForPipelineRun(page);
  await expect(page.getByTestId("pipeline-step-check_notes")).toBeVisible();
  await expect(page.getByTestId("pipeline-step-decide")).toBeVisible();
  await shot(page, "both-steps-finish");

  // ---- Act five: the same gate decision every pipeline ends in -----------
  await page.getByRole("link", { name: "Open the decision" }).click();
  await expect(page).toHaveURL(/\/gates\/[0-9a-f-]{36}$/);
  await expect(page.getByTestId("gate-blocked")).toContainText("You started this run");
  await shot(page, "opens-the-gate-decision-and-is-blocked");

  const gateUrl = page.url();

  await loginAs(page, REVIEWER);
  await page.goto(gateUrl);
  await expect(page.getByText(/moves it from raw to open for annotation/i)).toBeVisible();
  await page.getByTestId("gate-reason").fill(
    "Required registry fields are present and well-formed; safe to open for annotation.",
  );
  await shot(page, "the-reviewer-writes-why");

  await page.getByTestId("gate-promote").click();
  await expect(page.getByTestId("gate-state")).toContainText("Released");
  await shot(page, "cleared-and-promoted");
});
