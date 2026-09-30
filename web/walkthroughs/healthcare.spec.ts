/**
 * Captures the screenshots behind docs/healthcare-walkthrough.html.
 *
 * Drives the real console against the live stack as the health
 * organisation's own people. Nothing here is mocked: if a step cannot be
 * reached, this fails rather than producing a picture of something that did
 * not happen.
 *
 * Covers the whole worked example: a recording coming in, a real
 * de-identification pipeline run (transcription, speaker diarisation, PHI
 * detection, redaction), a gate decision only the de-identification
 * reviewer may make, a researcher's access request, an AI agent asking for
 * access the same way a person does, and the platform administrator's own
 * view of the system's health. Run the seed script first if the health
 * organisation is empty:
 *
 *   .venv\Scripts\python.exe scripts/seed/seed-health-example.py
 *
 * The pipeline run does real GPU work (faster-whisper transcription,
 * pyannote speaker diarisation) against a real ~4-minute synthetic
 * recording, so this takes several minutes to run end to end.
 *
 * Writes into walkthroughs/shots/healthcare/, which is gitignored with the
 * rest of shots/. The HTML page carries the images inline, so the page is
 * the committed artefact and these files are scratch.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts healthcare
 */

import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { EMAIL_BY_DIRECTORY_ID, PASSWORD, bearerFor, loginAs } from "../tests/auth-helpers";
import { API_BASE } from "../config/ports";

const SHOTS = join(process.cwd(), "walkthroughs", "shots", "healthcare");
const AUDIO_SAMPLE = join(
  process.cwd(), "..", "docs", "audio-samples", "cardiology-consult", "cardiology-consult-synthetic.wav",
);
const TRUTH_JSON = join(
  process.cwd(), "..", "docs", "audio-samples", "cardiology-consult", "truth.json",
);
const AGENT_ZIP = join(process.cwd(), "walkthroughs", "fixtures", "triage-agent.zip");

const API = API_BASE;

const ENGINEER = "eng-devi";
const CUSTODIAN_CARDIOLOGY = "cust-hartley";
const CUSTODIAN_RADIOLOGY = "cust-okonjo";
const RESEARCHER = "sam-researcher";
const REVIEWER = "rev-imani";
const ADMIN = "ops-priya";

// Phrases distinctive enough to pick specific rows out of queues that also
// hold real, pre-seeded traffic from scripts/seed/seed-health-example.py.
const REQUEST_PURPOSE = "measure recall of the de-identification model before wider release";
const RUN_PURPOSE = "rank the incoming radiology batch for review, live walkthrough capture";
const DIGEST_REQUEST_PURPOSE = "quarterly outcomes digest, live walkthrough capture";

/**
 * A small, already-reviewed dataset version, owned by Cardiology -- made
 * through the same register/upload/seal path the console itself uses, then
 * promoted one step, the same way scripts/seed/seed-health-example.py
 * promotes consultation-deidentified. UNDER_REVIEW, not RAW: this is the one
 * class a custodian is actually allowed to loosen. Every other dataset this
 * walkthrough touches is raw patient data on purpose -- this is the one
 * exception, built fresh so the choice a custodian gets to make has
 * somewhere safe to be shown.
 */
async function anUnderReviewVersion(name: string): Promise<string> {
  const org = await fetch(`${API}/organisation?tenant_id=health`, {
    headers: await bearerFor(CUSTODIAN_CARDIOLOGY),
  }).then((r) => r.json());
  const department = org.departments.find((d: { name: string }) => d.name === "Cardiology");

  const dataset = await fetch(`${API}/datasets/register`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      tenant_id: "health",
      name,
      department_id: department.id,
      registered_by: ENGINEER,
      provenance: "internal_regulated",
      modality: ["tabular"],
    }),
  }).then((r) => r.json());

  const form = new FormData();
  form.append(
    "file",
    new Blob([Buffer.from("quarter,outcome_rate\nQ3,0.94\n")]),
    "digest.csv",
  );
  await fetch(`${API}/datasets/${dataset.id}/files`, { method: "POST", body: form });
  const sealed = await fetch(`${API}/datasets/${dataset.id}/seal`, { method: "POST" }).then((r) => r.json());

  await fetch(`${API}/dataset-versions/${sealed.id}/promote`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      to_class: "UNDER_REVIEW",
      decided_by: "health-pipeline",
      decided_by_kind: "workload",
      gate_evidence: { note: "reviewed for the walkthrough's own pattern-choice scene" },
      grant_roles: [],
    }),
  });

  return sealed.id;
}

let step = 0;

async function shot(page: Page, name: string): Promise<void> {
  step += 1;
  const n = String(step).padStart(2, "0");
  await page.screenshot({ path: join(SHOTS, `${n}-${name}.png`) });
}

/**
 * Waits for a pipeline run to reach a terminal status, or fails the test
 * rather than hanging forever. A real transcription and speaker-separation
 * run takes real minutes on this machine's GPU.
 *
 * Polls the API directly from Node, not the browser DOM, and only touches
 * the page once, after the API already says the run is done. Two earlier
 * versions tried to rely on the browser instead and both failed, each only
 * diagnosed by checking real evidence rather than assumed:
 *   1. Driving page.reload() in a loop lost the session partway through
 *      (confirmed by reading the failure snapshot's actual page content),
 *      so the poll kept reading a signed-out page for the rest of its
 *      budget and never saw the real, already-finished result.
 *   2. Removing the reload loop in favour of the app's own
 *      refetchInterval (web/src/api/pipeline.ts's usePipelineRun) still did
 *      not work: the API reported "succeeded" minutes before Playwright's
 *      own poll ever saw anything but "Running now" (confirmed by querying
 *      the API directly, bypassing the browser). TanStack Query pauses
 *      refetchInterval while `document.visibilityState !== "visible"`
 *      unless `refetchIntervalInBackground` is set, which this hook does
 *      not. page.bringToFront() did not reliably fix this either: a quick
 *      isolated check proved only that a *fresh load* of an
 *      already-finished run renders correctly, not that interval polling
 *      keeps firing on a page left open for minutes, which is a different
 *      code path this stack apparently cannot be trusted to run in a
 *      headless, unfocused browser. Asking the API is simpler and correct
 *      regardless of any of that.
 */
async function waitForPipelineRun(page: Page, timeoutMs = 600_000): Promise<void> {
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
    await new Promise((resolve) => setTimeout(resolve, 4_000));
  }
  if (status === "running") {
    throw new Error(`the pipeline run did not finish within ${timeoutMs}ms`);
  }
  if (status !== "succeeded") {
    throw new Error(`the pipeline run did not finish cleanly: status was ${status}`);
  }

  // Not page.reload(): logging showed the page's own URL had already
  // drifted away from /pipeline-runs/<id> to /versions/<id> sometime during
  // the wait above, despite nothing in this function touching the page
  // itself. Navigating explicitly to the known-correct URL sidesteps
  // whatever caused that rather than trusting reload() to still be on the
  // right page.
  await page.goto(`/pipeline-runs/${runId}`);
  await expect(page.getByTestId("pipeline-run-status")).toContainText("Finished", { timeout: 15_000 });
}

/**
 * Polls the API directly for the named run, the same reasoning as
 * waitForPipelineRun above: this stack's client-side polling cannot be
 * trusted to keep running in this headless browser, so ask the API from
 * Node instead and only touch the page once, after it is already known to
 * be done.
 */
async function waitForAgentRunToFinish(
  page: Page,
  purpose: string,
  timeoutMs = 120_000,
): Promise<void> {
  const match = page.url().match(/\/agents\/([0-9a-f-]{36})/);
  if (!match) throw new Error(`not on an agent page: ${page.url()}`);
  const agentId = match[1];

  const deadline = Date.now() + timeoutMs;
  let status = "";
  const headers = await bearerFor(ENGINEER);
  while (Date.now() < deadline) {
    const res = await fetch(`${API}/agents/${agentId}/runs?tenant_id=health`, { headers });
    if (res.ok) {
      const runs = (await res.json()) as { purpose: string; status: string }[];
      const run = runs.find((r) => r.purpose === purpose);
      if (run) {
        status = run.status;
        if (status !== "running" && status !== "awaiting_access" && status !== "awaiting_activation") break;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 4_000));
  }
  if (status === "succeeded") {
    await page.reload();
    const row = page.getByTestId("agent-runs").locator("li", { hasText: purpose }).first();
    await expect(row).toContainText("succeeded", { timeout: 15_000 });
    return;
  }
  throw new Error(`the run did not finish successfully within ${timeoutMs}ms (last status: ${status || "unknown"})`);
}

test("capture: the healthcare worked example, start to finish", async ({ page }) => {
  mkdirSync(SHOTS, { recursive: true });
  test.setTimeout(900_000);

  const suffix = `${new Date().toISOString().slice(0, 10)}-${Date.now() % 10000}`;
  const demoName = `consultation-audio-intake-${suffix}`;
  const pipelineName = `consultation-recording-${suffix}`;

  // ---- Act one: signing in -------------------------------------------
  await page.goto("/auth/login");
  await page.getByTestId("login-identifier").fill(EMAIL_BY_DIRECTORY_ID[ENGINEER]);
  await page.getByTestId("login-password").fill(PASSWORD);
  await shot(page, "devi-signs-in");

  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("current-persona")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Devi" })).toBeVisible();
  await shot(page, "devi-home");

  // ---- Act two: Devi brings a recording in ------------------------------
  await page.goto("/datasets/register");
  await shot(page, "register-blank");

  await page.getByTestId("register-name").fill(demoName);
  await page.getByTestId("register-department").selectOption({ label: "Cardiology" });
  await page.getByTestId("modality-audio").check();
  await shot(page, "register-filled");

  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("upload-step")).toBeVisible();

  await page.getByTestId("upload-files").setInputFiles({
    name: "probe.wav",
    mimeType: "audio/wav",
    buffer: Buffer.from("this is not really a WAV file"),
  });
  await expect(page.getByText(/could not be read as a wav file/i)).toBeVisible({ timeout: 10000 });
  await shot(page, "fake-wav-rejected");

  await page.getByTestId("upload-files").setInputFiles(AUDIO_SAMPLE);
  await expect(page.getByTestId("uploaded-files")).toContainText("cardiology-consult-synthetic.wav", {
    timeout: 30000,
  });
  await page.getByTestId("seal-submit").click();
  await expect(page.getByTestId("seal-done")).toBeVisible({ timeout: 15000 });
  await shot(page, "sealed-plain-version");

  // ---- Act three: the pipeline runs, and a reviewer decides -------------
  await page.goto("/datasets/register");
  await page.getByTestId("register-name").fill(pipelineName);
  await page.getByTestId("register-department").selectOption({ label: "Cardiology" });
  await page.getByTestId("modality-audio").check();
  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("upload-step")).toBeVisible();

  // One at a time, not as a single multi-file selection: two uploads
  // resolving close together hits a state-update race in the console's
  // dev build (React StrictMode remounts the upload form once), which
  // drops whichever entry applied first. Real people rarely select two
  // files in the same instant either.
  await page.getByTestId("upload-files").setInputFiles(AUDIO_SAMPLE);
  await expect(page.getByTestId("uploaded-files")).toContainText("cardiology-consult-synthetic.wav", {
    timeout: 30000,
  });
  // The answer key's filename must share the recording's own basename (the
  // pairing convention is "<name>.wav" with "<name>.truth.json"), not the
  // literal filename on disk.
  await page.getByTestId("upload-files").setInputFiles({
    name: "cardiology-consult-synthetic.truth.json",
    mimeType: "application/json",
    buffer: readFileSync(TRUTH_JSON),
  });
  await expect(page.getByTestId("uploaded-files")).toContainText("cardiology-consult-synthetic.truth.json", {
    timeout: 15000,
  });
  await shot(page, "recording-and-answer-key-uploaded");

  await page.getByTestId("seal-audio-submit").click();
  await expect(page.getByTestId("seal-done")).toContainText("recording", { timeout: 15000 });
  await shot(page, "sealed-as-recordings");

  await page.getByRole("link", { name: "Run a pipeline against this version" }).click();
  await expect(page.getByTestId("version-pipeline-start")).toBeVisible();
  await page.getByTestId("version-pipeline-start").click();
  await expect(page).toHaveURL(/\/pipeline-runs\/[0-9a-f-]{36}$/, { timeout: 15000 });
  await shot(page, "pipeline-started");

  await waitForPipelineRun(page);
  await shot(page, "pipeline-finished");

  await page.getByRole("link", { name: "Open the decision" }).click();
  await expect(page).toHaveURL(/\/gates\/[0-9a-f-]{36}$/);
  await expect(page.getByTestId("gate-blocked")).toContainText("You started this run");
  await shot(page, "devi-blocked-from-own-decision");

  const gateUrl = page.url();

  await loginAs(page, CUSTODIAN_CARDIOLOGY);
  await page.goto(gateUrl);
  await expect(page.getByTestId("gate-blocked")).toContainText("not part of your role");
  await shot(page, "hartley-blocked-too");

  await loginAs(page, REVIEWER);
  await page.goto(gateUrl);
  await shot(page, "imani-opens-the-decision");

  await page.getByTestId("gate-reason").fill(
    "Recall could not be measured on this synthetic recording; holding back until a real sample is scored.",
  );
  await shot(page, "reason-written-buttons-enabled");

  await page.getByTestId("gate-refuse").click();
  await expect(page.getByTestId("gate-state")).toContainText("Held back");
  await shot(page, "held-back");

  // ---- Act four: a researcher asks, a custodian grants -------------------
  await loginAs(page, ENGINEER);
  await page.goto("/datasets");
  await page.getByTestId("dataset-search").fill(pipelineName);
  const pipelineRow = page.locator(`tr[data-dataset="${pipelineName}"]`);
  await expect(pipelineRow).toBeVisible();
  await pipelineRow.getByRole("button", { name: "Show versions" }).click();
  await pipelineRow.locator("+ tr").getByRole("link", { name: "Open" }).click();
  await expect(page).toHaveURL(/\/versions\/[0-9a-f-]{36}$/);
  const rawVersionUrl = page.url();

  await loginAs(page, RESEARCHER);
  await page.goto(rawVersionUrl);
  await expect(page.getByTestId("request-access")).toBeVisible();
  await shot(page, "sam-finds-a-request-form");

  await page.getByTestId("request-purpose").fill(REQUEST_PURPOSE);
  await page.getByTestId("request-justification").fill(
    "for arrhythmia detection study, comparing detected spans against the original audio",
  );
  await shot(page, "sam-fills-in-the-ask");

  await page.getByTestId("request-submit").click();
  await expect(page.getByTestId("request-sent")).toBeVisible();
  await shot(page, "request-sent");

  await loginAs(page, CUSTODIAN_CARDIOLOGY);
  await expect(page.getByTestId("pending-requests")).toBeVisible();
  await expect(page.getByTestId("pending-requests")).toContainText(REQUEST_PURPOSE);
  await shot(page, "hartley-sees-sams-request");

  const samRow = page.getByTestId("pending-requests").locator("li", { hasText: REQUEST_PURPOSE });
  await samRow.getByRole("button", { name: "Grant access" }).click();
  await expect(samRow).toHaveCount(0);
  await shot(page, "queue-after-granting");

  // ---- Act five: an AI agent asks for access the same way a person does --
  await loginAs(page, ENGINEER);
  await page.goto("/agents");
  await expect(page.getByRole("link", { name: "radiology-intake-triage" })).toBeVisible();
  await shot(page, "agent-before-upload");

  await page.getByRole("link", { name: "radiology-intake-triage" }).click();
  await expect(page.getByTestId("upload-agent-version-form")).toBeVisible();
  await shot(page, "agent-empty-upload-form");

  const existingVersions = await page.getByTestId("agent-versions").locator("li").count();
  const nextVersion = existingVersions + 1;

  await page.getByTestId("agent-version-zip").setInputFiles(AGENT_ZIP);
  await page.getByTestId("agent-version-model").fill("none");
  // Tools/hosts live behind a progressive-disclosure toggle now, collapsed by
  // default -- the field does not exist in the DOM until it is opened.
  await page.getByTestId("agent-version-advanced-toggle").click();
  await page.getByTestId("agent-version-tools").fill("read_dataset_version");
  await shot(page, "agent-upload-filled");

  await page.getByTestId("upload-agent-version-submit").click();
  await expect(page.getByTestId(`agent-version-${nextVersion}`)).toBeVisible({ timeout: 15000 });
  await shot(page, "agent-version-sealed");

  await page.getByTestId(`deploy-version-${nextVersion}`).click();
  await expect(page.getByText("active")).toBeVisible({ timeout: 10000 });

  await page.getByTestId("run-purpose").fill(RUN_PURPOSE);
  await page.getByTestId("run-target").selectOption({ label: "radiology-reports v1" });
  await expect(page.getByTestId("access-notice")).toBeVisible();
  await shot(page, "run-warned");

  await page.getByTestId("start-run").click();
  await expect(page.getByTestId("agent-runs")).toContainText(RUN_PURPOSE);
  await shot(page, "run-parked-waiting");

  await loginAs(page, CUSTODIAN_RADIOLOGY);
  await expect(page.getByTestId("pending-requests")).toBeVisible();
  await expect(page.getByTestId("pending-requests")).toContainText(RUN_PURPOSE);
  await shot(page, "okonjo-sees-the-request");

  const firstAgentRow = page.getByTestId("pending-requests").locator("li", { hasText: RUN_PURPOSE });
  await firstAgentRow.getByRole("button", { name: "Grant access" }).click();
  await expect(firstAgentRow).toHaveCount(0);

  // A grant covers one run, not a standing credential: starting a second run
  // against the same dataset asks again.
  await loginAs(page, ENGINEER);
  await page.goto("/agents");
  await page.getByRole("link", { name: "radiology-intake-triage" }).click();
  const secondRunPurpose = `${RUN_PURPOSE}, second batch`;
  await page.getByTestId("run-purpose").fill(secondRunPurpose);
  await page.getByTestId("run-target").selectOption({ label: "radiology-reports v1" });
  await page.getByTestId("start-run").click();
  await expect(page.getByTestId("agent-runs")).toContainText(secondRunPurpose);
  await shot(page, "second-run-needs-second-grant");

  await loginAs(page, CUSTODIAN_RADIOLOGY);
  const secondAgentRow = page.getByTestId("pending-requests").locator("li", { hasText: secondRunPurpose });
  await expect(secondAgentRow).toBeVisible();
  await secondAgentRow.getByRole("button", { name: "Grant access" }).click();
  await expect(secondAgentRow).toHaveCount(0);

  await loginAs(page, ENGINEER);
  await page.goto("/agents");
  await page.getByRole("link", { name: "radiology-intake-triage" }).click();
  await waitForAgentRunToFinish(page, secondRunPurpose);
  await page.getByTestId("agent-runs").scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await shot(page, "run-finished");

  // ---- Act six: beyond the built-in pipeline, and who is watching it all -
  await page.goto("/pipelines");
  await shot(page, "pipeline-registry-empty");

  await loginAs(page, ADMIN);
  await expect(page.getByTestId("component-map")).toBeVisible();
  await shot(page, "priya-sees-platform-health");

  // ---- Act seven: the same decision, made the other way -----------------
  //
  // Every grant in this walkthrough covered one purpose, because every
  // dataset it touched was raw, identifiable patient data: access_lease's
  // own guardrail refuses anything looser against raw data, no matter how
  // long an agent has run or how much a custodian trusts it. Below that
  // floor, the shape of the grant is the custodian's own call, not the
  // platform's. This one dataset is not raw -- reviewed already, the one
  // class Hartley is actually allowed to loosen.
  const digestVersionId = await anUnderReviewVersion(`cardiology-outcomes-digest-${Date.now()}`);

  await loginAs(page, RESEARCHER);
  await page.goto(`/versions/${digestVersionId}`);
  await expect(page.getByTestId("request-access")).toBeVisible();
  await shot(page, "sam-finds-a-second-request-form");

  await page.getByTestId("request-purpose").fill(DIGEST_REQUEST_PURPOSE);
  await page.getByTestId("request-justification").fill(
    "quarterly outcomes summary, already reviewed, for the cardiology programme report",
  );
  await page.getByTestId("request-submit").click();
  await expect(page.getByTestId("request-sent")).toBeVisible();

  await loginAs(page, CUSTODIAN_CARDIOLOGY);
  await expect(page.getByTestId("pending-requests")).toBeVisible();
  await expect(page.getByTestId("pending-requests")).toContainText(DIGEST_REQUEST_PURPOSE);
  await shot(page, "hartley-sees-sams-second-request");

  const digestRow = page.getByTestId("pending-requests").locator("li", { hasText: DIGEST_REQUEST_PURPOSE });
  // Not offered at all against raw data -- every other row Hartley or Okonjo
  // saw in this walkthrough had no such choice. Offered here because
  // UNDER_REVIEW is exactly the class the database allows a custodian to
  // loosen.
  await digestRow.getByLabel("Any purpose, while this lasts").check();
  await shot(page, "hartley-chooses-any-purpose");

  await digestRow.getByRole("button", { name: "Grant access" }).click();
  await expect(digestRow).toHaveCount(0);
  await shot(page, "any-purpose-granted");

  // The payoff: a purpose Hartley never saw at approval time still reads,
  // because she covered any purpose, not just the one Sam asked with.
  // `allowed` is the fact this proves, checked directly rather than through
  // the HTTP status: a human role reads through a workspace that holds its
  // own credential, so this endpoint can answer with no data-plane key even
  // on a policy allow -- a fact about how humans reach storage, nothing to
  // do with what the lease itself covers.
  const laterCredential = await fetch(`${API}/credentials`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      principal: RESEARCHER,
      principal_kind: "human",
      roles: ["notebook_explore"],
      tenant_id: "health",
      dataset_version_id: digestVersionId,
      purpose: "an unplanned spot-check, never mentioned when Hartley approved this",
    }),
  });
  const laterOutcome = await laterCredential.json().catch(() => null);
  const laterAllowed = laterCredential.ok || laterOutcome?.detail?.allowed === true;
  if (!laterAllowed) {
    throw new Error(
      `a simple lease should have covered a later purpose too: HTTP ${laterCredential.status}: ${JSON.stringify(laterOutcome)}`,
    );
  }

  await page.reload();
  await expect(page.getByText("covers any purpose").first()).toBeVisible();
  await shot(page, "any-purpose-lease-marked-in-history");
});
