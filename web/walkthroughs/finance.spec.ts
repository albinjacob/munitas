/**
 * Captures the screenshots behind docs/finance-walkthrough.html.
 *
 * Drives the real console against the live stack as the finance
 * organisation's own people. Nothing here is mocked: if a step cannot be
 * reached, this fails rather than producing a picture of something that did
 * not happen.
 *
 * Covers the whole worked example scripts/seed/seed-finance-example.py seeds:
 * a transaction batch coming in, a promoted summary, a fraud-scoring agent
 * asking for access the same way a person does, two custodians each scoped to
 * their own department's queue, and the tenant boundary holding against a
 * deep-linked dataset from outside it. Run the seed script first if the
 * finance organisation is empty:
 *
 *   .venv\Scripts\python.exe scripts/seed/seed-finance-example.py
 *
 * Writes into walkthroughs/shots/finance/, which is gitignored with the rest
 * of shots/. The HTML page carries the images inline, so the page is the
 * committed artefact and these files are scratch.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts finance
 */

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { EMAIL_BY_DIRECTORY_ID, PASSWORD, actingHeaders, bearerFor, loginAs } from "../tests/auth-helpers";
import { API_BASE } from "../config/ports";
import { settled } from "./settled";
import { closeTestRequests, revokeEarlierRecordings } from "./tidy";

const SHOTS = join(process.cwd(), "walkthroughs", "shots", "finance");
const API = API_BASE;
const AGENT_ZIP = join(process.cwd(), "walkthroughs", "fixtures", "fraud-scorer.zip");

const ENGINEER = "eng-lena";
const CUSTODIAN_FRAUD = "cust-marcus";
const CUSTODIAN_RISK = "cust-naomi";
const ANALYST = "ana-omar";

// A phrase distinctive enough to pick Omar's own request out of Marcus's
// queue, and different again from the purpose he reads under once granted --
// the whole point of the scene that uses it.
const DIGEST_REQUEST_PURPOSE = "October merchant digest, live walkthrough capture";

// A phrase distinctive enough to pick this run's own request out of a queue
// that also holds the analyst's unrelated, pre-seeded one.
const RUN_PURPOSE = "score the day's batch for fraud, live walkthrough capture";

// Three rows, each naming a cardholder and a full card number: the kind of
// data a de-identification pass exists to remove. The card numbers are the
// networks' published test numbers, not anybody's card.
const TRANSACTIONS = [
  "transaction_id,cardholder_name,card_number,amount,merchant,timestamp",
  "tx-100231,Priya Raman,4111111111111111,249.99,Northside Electronics,2026-09-01T10:14:03Z",
  "tx-100232,Tomas Varga,5555555555554444,18.40,Harbour Cafe,2026-09-01T10:16:47Z",
  "tx-100233,Grace Okafor,378282246310005,1320.00,Summit Travel,2026-09-01T10:21:12Z",
].join("\n");

let step = 0;

// Puts an element at the top of the picture, so the part of a long page that a step is about is whole.
async function toTop(page: Page, locator: ReturnType<Page["locator"]>): Promise<void> {
  await locator.evaluate((e) => e.scrollIntoView({ block: "start" }));
}

async function shot(page: Page, name: string): Promise<void> {
  await settled(page);
  step += 1;
  const n = String(step).padStart(2, "0");
  await page.screenshot({ path: join(SHOTS, `${n}-${name}.png`) });
}

/**
 * A dataset version id from outside the finance tenant, for the deep-link
 * refusal at the end. Fetched from the live API rather than hard-coded, so
 * the capture does not silently start passing a stale id once that health
 * organisation dataset is reclaimed or renamed.
 */
async function aForeignVersionId(): Promise<string> {
  const headers = await bearerFor("cust-hartley");
  const r = await fetch(`${API}/dataset-versions?tenant_id=health`, { headers });
  if (!r.ok) throw new Error(`could not list health's own versions: HTTP ${r.status}`);
  const versions = (await r.json()) as { dataset_version_id: string }[];
  if (!versions.length) throw new Error("health has no dataset versions to deep-link to");
  return versions[0].dataset_version_id;
}

/** A setup call that must have worked: a failure here is a failed capture, not a page that quietly shows the wrong thing. */
async function checked(response: Response, what: string): Promise<any> {
  if (!response.ok) throw new Error(`${what} failed: ${response.status} ${await response.text()}`);
  return response.json().catch(() => ({}));
}

/**
 * A small, already-reviewed dataset version, owned by Fraud Operations --
 * made through the same register/upload/seal path the console itself uses
 * (access-preview.spec.ts's `ownedRawVersion` follows the same shape), then
 * promoted one step, the way scripts/seed/seed-finance-example.py promotes
 * its own summary. UNDER_REVIEW, not RAW: this is the one class a custodian
 * is actually allowed to loosen, and access_lease's own guardrail would
 * refuse a "covers any purpose" grant against RAW regardless of who asks.
 */
async function anUnderReviewVersion(name: string): Promise<string> {
  const org = await fetch(`${API}/organisation?tenant_id=finance`, {
    headers: await bearerFor(CUSTODIAN_FRAUD),
  }).then((r) => r.json());
  const department = org.departments.find((d: { name: string }) => d.name === "Fraud Operations");

  // Registering is done by a person, so the call carries that person's session, as the console's own would.
  const registration = { tenant_id: "finance", name, department_id: department.id, registered_by: ENGINEER, provenance: "internal_regulated", modality: ["tabular"] };
  const registered = await fetch(`${API}/datasets/register`, {
    method: "POST",
    headers: { "content-type": "application/json", ...(await actingHeaders("POST", "/datasets/register", registration)) },
    body: JSON.stringify(registration),
  });
  if (!registered.ok) throw new Error(`could not register ${name}: ${registered.status} ${await registered.text()}`);
  const dataset = await registered.json();

  const form = new FormData();
  form.append("file", new Blob([Buffer.from("merchant,total\nHarbour Cafe,1042.50\n")]), "digest.csv");
  await checked(await fetch(`${API}/datasets/${dataset.id}/files`, { method: "POST", headers: await bearerFor(ENGINEER), body: form }), "uploading the file");
  const sealed = await checked(await fetch(`${API}/datasets/${dataset.id}/seal`, { method: "POST", headers: await bearerFor(ENGINEER) }), "sealing the dataset");

  await checked(await fetch(`${API}/dataset-versions/${sealed.id}/promote`, {
    method: "POST",
    headers: { "content-type": "application/json", ...(await actingHeaders("POST", `/dataset-versions/${sealed.id}/promote`, null)) },
    body: JSON.stringify({
      to_class: "UNDER_REVIEW",
      decided_by: "finance-pipeline",
      decided_by_kind: "workload",
      gate_evidence: { note: "reviewed for the walkthrough's own pattern-choice scene" },
      grant_roles: [],
    }),
  }), "releasing the version one step");

  return sealed.id;
}

/**
 * Polls the API directly for the named run rather than trusting the
 * browser's own client-side polling, which this stack cannot be relied on
 * to keep running in a headless, unfocused tab (TanStack Query pauses
 * refetchInterval while document.visibilityState is not "visible", and
 * neither leaving the page alone nor calling page.bringToFront() reliably
 * avoided that in practice, confirmed against the real API response, not
 * assumed). Only touches the page once, after the API already says it
 * finished.
 */
async function waitForRunToFinish(page: Page, timeoutMs = 120_000): Promise<void> {
  const match = page.url().match(/\/agents\/([0-9a-f-]{36})/);
  if (!match) throw new Error(`not on an agent page: ${page.url()}`);
  const agentId = match[1];

  const deadline = Date.now() + timeoutMs;
  let status = "";
  const headers = await bearerFor(ENGINEER);
  while (Date.now() < deadline) {
    const res = await fetch(`${API}/agents/${agentId}/runs?tenant_id=finance`, { headers });
    if (res.ok) {
      const runs = (await res.json()) as { purpose: string; status: string }[];
      // Newest first, matching the order the console itself renders: a
      // rerun of this capture meets its own earlier runs sharing this
      // purpose text, and only the newest one is still in flight.
      const run = runs.find((r) => r.purpose === RUN_PURPOSE);
      if (run) {
        status = run.status;
        if (status !== "running" && status !== "awaiting_access" && status !== "awaiting_activation") break;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 4_000));
  }
  if (status === "succeeded") {
    await page.reload();
    const row = page.getByTestId("agent-runs").locator("li", { hasText: RUN_PURPOSE }).first();
    await expect(row).toContainText("succeeded", { timeout: 15_000 });
    return;
  }
  throw new Error(`the run did not finish successfully within ${timeoutMs}ms (last status: ${status || "unknown"})`);
}

test("capture: the finance worked example, start to finish", async ({ page }) => {
  mkdirSync(SHOTS, { recursive: true });

  // Marcus's queue and the count of granted access must start where the story starts, so what test runs and earlier recordings of this
  // walkthrough left behind is closed first.
  await closeTestRequests(CUSTODIAN_FRAUD, [DIGEST_REQUEST_PURPOSE, RUN_PURPOSE]);
  await revokeEarlierRecordings(CUSTODIAN_FRAUD, ANALYST, DIGEST_REQUEST_PURPOSE);
  test.setTimeout(240_000);

  // A unique name, so the capture can run again without meeting its own
  // earlier dataset. The page shows it as registered, which is the point.
  const name = `card-transaction-intake-${new Date().toISOString().slice(0, 10)}-${Date.now() % 10000}`;
  const foreignVersionId = await aForeignVersionId();

  // ---- Act one: signing in -------------------------------------------
  await page.goto("/auth/login");
  await page.getByTestId("login-identifier").fill(EMAIL_BY_DIRECTORY_ID[ENGINEER]);
  await page.getByTestId("login-password").fill(PASSWORD);
  await shot(page, "lena-signs-in");

  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("current-persona")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Lena" })).toBeVisible();
  await shot(page, "lena-home");

  // ---- Act two: Lena brings a transaction batch in ---------------------
  await page.goto("/datasets/register");
  await page.getByTestId("register-name").fill(name);
  await page.getByTestId("register-department").selectOption({ label: "Fraud Operations" });
  await shot(page, "register-filled");

  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("upload-step")).toBeVisible();
  await page.getByTestId("upload-files").setInputFiles({
    name: "transactions.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(TRANSACTIONS + "\n"),
  });
  await expect(page.getByTestId("uploaded-files")).toContainText("transactions.csv");
  await shot(page, "csv-uploaded");

  await page.getByTestId("seal-submit").click();
  await expect(page).toHaveURL(/\/versions\/[0-9a-f-]{36}$/, { timeout: 15000 });
  await expect(page.getByTestId("sealed-class")).toBeVisible({ timeout: 30000 });
  // The access box and the release history fill in after the page does. A picture taken before they
  // arrive shows "Checking" and grey placeholders, which proves nothing.
  await expect(page.getByText("Checking", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Nobody has widened access to this.")).toBeVisible();
  await shot(page, "sealed-v1");

  // ---- Act three: a mask, not a move -----------------------------------
  await page.goto("/datasets");
  await page.getByTestId("dataset-search").fill("card-transaction-summary");
  const summaryRow = page.locator('tr[data-dataset="card-transaction-summary"]');
  await expect(summaryRow).toBeVisible();
  await summaryRow.getByRole("button", { name: "Show versions" }).click();
  await summaryRow.locator("+ tr").getByRole("link", { name: "Open" }).click();
  await expect(page.getByTestId("sealed-class")).toBeVisible();
  await expect(page.getByTestId("current-class")).toBeVisible();
  await shot(page, "promoted-summary");

  // ---- Act four: a fraud-scoring agent asks for access -----------------
  // A version, once sealed, is immutable and stays forever, so a second run
  // of this capture meets an agent that already has one. Whatever state that
  // is, real or freshly empty, is what gets shown: the next version number is
  // read off the page rather than assumed to be 1.
  await page.goto("/agents");
  await expect(page.getByRole("link", { name: "fraud-transaction-scoring" })).toBeVisible();
  await shot(page, "agent-before-upload");

  await page.getByRole("link", { name: "fraud-transaction-scoring" }).click();
  // The upload form is in the same render as the version list, so waiting
  // for it rules out counting before the agent has finished loading (count()
  // does not itself wait the way an assertion does).
  await expect(page.getByTestId("upload-agent-version-form")).toBeVisible();
  const existingVersions = await page.getByTestId("agent-versions").locator("li").count();
  const nextVersion = existingVersions + 1;

  await page.getByTestId("agent-version-zip").setInputFiles(AGENT_ZIP);
  await page.getByTestId("agent-version-model").fill("none");
  // Tools/hosts live behind a progressive-disclosure toggle now, collapsed by
  // default -- the field does not exist in the DOM until it is opened.
  await page.getByTestId("agent-version-advanced-toggle").click();
  await page.getByTestId("agent-version-tools").fill("read_dataset_version");
  await page.getByTestId("upload-agent-version-submit").click();
  await expect(page.getByTestId(`agent-version-${nextVersion}`)).toBeVisible({ timeout: 15000 });
  await toTop(page, page.getByTestId("agent-versions"));
  await shot(page, "agent-version-sealed");

  await page.getByTestId(`deploy-version-${nextVersion}`).click();
  await expect(page.getByText("active")).toBeVisible({ timeout: 10000 });

  await page.getByTestId("run-purpose").fill(RUN_PURPOSE);
  await page
    .getByTestId("run-target")
    .selectOption({ label: "card-transaction-log v1" });
  await expect(page.getByTestId("access-notice")).toBeVisible();
  await toTop(page, page.getByRole("heading", { name: "Runs" }));
  await shot(page, "run-warned");

  await page.getByTestId("start-run").click();
  await expect(page.getByTestId("agent-runs")).toContainText(RUN_PURPOSE);

  // ---- Act five: two departments, two queues, and a dataset that was
  // never here -----------------------------------------------------------
  await loginAs(page, CUSTODIAN_FRAUD);
  await expect(page.getByTestId("pending-requests")).toBeVisible();
  await expect(page.getByTestId("pending-requests")).toContainText(RUN_PURPOSE);
  await shot(page, "marcus-queue");

  const agentRow = page
    .getByTestId("pending-requests")
    .locator("li", { hasText: RUN_PURPOSE });
  await agentRow.getByRole("button", { name: "Grant access" }).click();
  await expect(agentRow).toHaveCount(0);

  await loginAs(page, ENGINEER);
  await page.goto("/agents");
  await page.getByRole("link", { name: "fraud-transaction-scoring" }).click();
  await waitForRunToFinish(page);
  await toTop(page, page.getByRole("heading", { name: "Runs" }));
  await shot(page, "run-finished");

  await loginAs(page, CUSTODIAN_RISK);
  await expect(page.getByTestId("pending-requests")).toBeVisible();
  await expect(page.getByTestId("pending-requests")).toContainText("kyc-identity-documents");
  await expect(page.getByTestId("pending-requests")).not.toContainText(RUN_PURPOSE);
  await shot(page, "naomi-queue");

  await page.goto(`/versions/${foreignVersionId}`);
  await expect(page.getByTestId("failure")).toBeVisible();
  await shot(page, "deep-link-refused");

  // ---- Act six: the same decision, made the other way -------------------
  //
  // Every grant above covered one purpose, because card-transaction-log and
  // kyc-identity-documents are both raw: access_lease's own guardrail
  // refuses anything looser against raw data, no matter how much a
  // custodian trusts who is asking. Below that floor, the shape of the
  // grant is Marcus's own call, not the platform's. Omar runs the same
  // merchant digest every month; asking Marcus to re-approve it each time,
  // solely because he reworded his own reason, is not a safer platform, it
  // is a queue Marcus stops reading closely.
  const digestVersionId = await anUnderReviewVersion(`fraud-analytics-digest-${Date.now()}`);

  await loginAs(page, ANALYST);
  await page.goto(`/versions/${digestVersionId}`);
  await expect(page.getByTestId("request-access")).toBeVisible();
  await shot(page, "omar-finds-a-request-form");

  await page.getByTestId("request-purpose").fill(DIGEST_REQUEST_PURPOSE);
  await page.getByTestId("request-justification").fill(
    "monthly merchant-level summary, already reviewed, for the ops digest",
  );
  await page.getByTestId("request-submit").click();
  await expect(page.getByTestId("request-sent")).toBeVisible();

  await loginAs(page, CUSTODIAN_FRAUD);
  await expect(page.getByTestId("pending-requests")).toBeVisible();
  await expect(page.getByTestId("pending-requests")).toContainText(DIGEST_REQUEST_PURPOSE);
  await shot(page, "marcus-sees-omars-request");

  const digestRow = page.getByTestId("pending-requests").locator("li", { hasText: DIGEST_REQUEST_PURPOSE });
  // Not offered at all against raw data (see card-transaction-log's own
  // queue row above, which has no such choice) -- offered here because
  // UNDER_REVIEW is exactly the class the database allows a custodian to
  // loosen.
  await digestRow.getByLabel("Any purpose, while this lasts").check();
  await shot(page, "marcus-chooses-any-purpose");

  await digestRow.getByRole("button", { name: "Grant access" }).click();
  await expect(digestRow).toHaveCount(0);
  await shot(page, "any-purpose-granted");

  // The payoff: a purpose Marcus never saw at approval time still reads,
  // because he covered any purpose, not just the one Omar asked with.
  //
  // `allowed` is the fact this scene is proving, checked directly rather
  // than through the HTTP status: a human role like "analyst" reads through
  // a workspace that holds its own credential, so this endpoint answers 503
  // even on a policy allow ("policy permitted this read, but no credential
  // exists for the role") -- a fact about how humans reach storage, nothing
  // to do with what the lease itself covers.
  const laterCredential = await fetch(`${API}/credentials`, {
    method: "POST",
    headers: { "content-type": "application/json", ...(await actingHeaders("POST", "/credentials", null)) },
    body: JSON.stringify({
      principal: ANALYST,
      principal_kind: "human",
      roles: ["analyst"],
      tenant_id: "finance",
      dataset_version_id: digestVersionId,
      purpose: "an unplanned spot-check, never mentioned when Marcus approved this",
    }),
  });
  const laterOutcome = await laterCredential.json();
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
