/**
 * Captures the console screenshots behind the derivation walkthroughs
 * (docs/public/walkthroughs/derive-health-walkthrough.html and
 * derive-finance-walkthrough.html), one organisation per run.
 *
 * Drives the real console against the live stack as that organisation's own
 * people. DERIVE_TENANT picks the organisation (health by default). Nothing here is mocked: if a step cannot be reached, this fails
 * rather than producing a picture of something that did not happen.
 *
 * The notebook half of the story (a person querying with DuckDB, making the new
 * dataset) has no screen, so it is shown as the real code and the real output
 * that scripts/demo/derive-demo.py printed. This file covers what the console
 * does show: the closed table, the request for access and the custodian's
 * decision, the new dataset and where it came from, the decision log, and the
 * access ending.
 *
 * Run the seed and the workers first:
 *
 *   .venv\Scripts\python.exe scripts\seed\seed-derive-demo-data.py
 *
 * Writes into walkthroughs/shots/derive-<organisation>/, which is gitignored with the rest of
 * shots/. The HTML page carries the images inline, so the page is the committed
 * artefact and these files are scratch.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts derive
 *   $env:DERIVE_TENANT = "finance"; npx playwright test --config=walkthroughs/playwright.config.ts derive
 */

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { bearerFor, loginAs } from "../tests/auth-helpers";
import { API_BASE } from "../config/ports";

const API = API_BASE;

// Each organisation's story: the same flow over its own people and datasets, matching
// the scenarios in scripts/demo/derive-demo.py.
const STORIES = {
  health: {
    shots: "derive-health",
    researcher: "sam-researcher", custodian: "cust-hartley",
    raw: "admissions", lookup: "diagnosis_codes",
    purpose: "readmission study, live walkthrough capture",
    justification: "Compare readmission rates for older patients with a chronic heart condition.",
    inputs: [{ dataset: "admissions", alias: "a" }, { dataset: "diagnosis_codes", alias: "d" }],
    sql: "SELECT a.admission_id, a.age, a.diagnosis_code, d.description, d.chronic, " +
      "a.length_of_stay_days, a.readmitted_30d FROM a JOIN d ON d.code = a.diagnosis_code " +
      "WHERE a.age > 65 AND d.chronic",
    key: "admission_id", prefix: "older-chronic-patients",
  },
  finance: {
    shots: "derive-finance",
    researcher: "ana-omar", custodian: "cust-marcus",
    raw: "transactions", lookup: "merchants",
    purpose: "cross-border fraud review, live walkthrough capture",
    justification: "Review large transactions made outside the home country at risky merchants.",
    inputs: [{ dataset: "transactions", alias: "t" }, { dataset: "merchants", alias: "m" }],
    sql: "SELECT t.txn_id, t.amount, t.country, t.occurred_at, m.category, m.high_risk, t.flagged " +
      "FROM t JOIN m ON m.merchant_id = t.merchant_id WHERE t.amount > 300 AND t.country <> 'US'",
    key: "txn_id", prefix: "large-foreign-transfers",
  },
} as const;
const STORY = STORIES[(process.env.DERIVE_TENANT ?? "health") as keyof typeof STORIES];
if (!STORY) throw new Error("DERIVE_TENANT must be health or finance");

const SHOTS = join(process.cwd(), "walkthroughs", "shots", STORY.shots);
const RESEARCHER = STORY.researcher;
const CUSTODIAN = STORY.custodian;

const PURPOSE = STORY.purpose;
const NEW_NAME = `${STORY.prefix}-${new Date().toISOString().slice(5, 16).replace(/[-:T]/g, "")}`;

let step = 0;

async function shot(page: Page, name: string): Promise<void> {
  step += 1;
  const n = String(step).padStart(2, "0");
  await page.screenshot({ path: join(SHOTS, `${n}-${name}.png`) });
}

async function api(path: string, who: string, init: RequestInit = {}): Promise<any> {
  const headers = { ...(await bearerFor(who)), "Content-Type": "application/json" };
  const r = await fetch(`${API}${path}`, { ...init, headers });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`${init.method ?? "GET"} ${path} -> ${r.status} ${JSON.stringify(body).slice(0, 300)}`);
  return body;
}

async function versionIdOf(who: string, dataset: string): Promise<string> {
  const found = await api(`/datasets?q=${encodeURIComponent(dataset)}`, who);
  const row = found.datasets.find((d: { name: string }) => d.name === dataset);
  const versions = await api(`/datasets/${row.id}/versions`, who);
  return versions[0].dataset_version_id;
}

async function searchDatasets(page: Page, text: string): Promise<void> {
  await page.goto("/datasets");
  await page.getByLabel("Search by name").fill(text);
  // The count of what the person can read is worked out after the row appears,
  // so wait for it, or the picture shows an empty cell that is not the result.
  await expect(page.getByRole("row").filter({ hasText: text }).first()).toContainText(/\d+ of \d+/);
}

test("a person makes a new dataset from a query, and the custodian stays in charge", async ({ page }) => {
  test.setTimeout(240_000);
  mkdirSync(SHOTS, { recursive: true });

  // Clear any access left by an earlier run, so the closed table is really closed.
  const raw = await versionIdOf(CUSTODIAN, STORY.raw);
  const leases = await api(`/leases?principal=${RESEARCHER}&active_only=true`, CUSTODIAN);
  for (const l of leases.leases ?? []) {
    if (l.dataset_version_id === raw) await api(`/leases/${l.id}/revoke`, CUSTODIAN, { method: "POST" });
  }

  // Likewise a request left pending by an earlier run, which would replace the
  // request form with "already asked".
  const pending = await api(`/lease-requests?state=pending&principal=${RESEARCHER}`, CUSTODIAN);
  for (const r of pending.lease_requests ?? []) {
    if (r.dataset_version_id === raw) {
      await api(`/leases/requests/${r.id}/reject`, CUSTODIAN, {
        method: "POST", body: JSON.stringify({ reason: "cleared before a fresh capture" }),
      });
    }
  }

  // The custodian's home page lists every request waiting on that person. Leftovers from
  // automated test runs would bury the one this walkthrough is about, so close them first.
  const waiting = await api(`/lease-requests?state=pending&custodian=${CUSTODIAN}&limit=500`, CUSTODIAN);
  for (const r of waiting.lease_requests ?? []) {
    if (r.principal === "svc-trainer" && String(r.purpose).startsWith("queue rendering check")) {
      await api(`/leases/requests/${r.id}/reject`, CUSTODIAN, {
        method: "POST", body: JSON.stringify({ reason: "automated test request, closed" }),
      });
    }
  }

  // ---- Act one: a closed table, and a request for access ----------------
  await loginAs(page, RESEARCHER);
  // First the table that is open to every signed-in person, to contrast with the closed one.
  await searchDatasets(page, STORY.lookup);
  await shot(page, "sam-finds-the-open-lookup");
  await searchDatasets(page, STORY.raw);
  await shot(page, "sam-finds-admissions-closed");

  await page.goto(`/versions/${raw}`);
  await expect(page.getByTestId("request-access")).toBeVisible();
  await page.getByTestId("request-purpose").fill(PURPOSE);
  await page.getByTestId("request-justification").fill(
    STORY.justification,
  );
  await shot(page, "sam-fills-in-the-request");
  await page.getByTestId("request-submit").click();
  await expect(page.getByTestId("request-sent")).toBeVisible();
  await shot(page, "request-sent");

  await loginAs(page, CUSTODIAN);
  await page.goto("/");
  await expect(page.getByTestId("pending-requests")).toContainText(PURPOSE);
  await shot(page, "hartley-sees-the-request");
  const row = page.getByTestId("pending-requests").locator("li", { hasText: PURPOSE });
  await row.getByRole("button", { name: "Grant access" }).click();
  await expect(row).toHaveCount(0);
  await shot(page, "after-granting");

  // ---- Act two: the new dataset (the query itself is in the notebook) ----
  // The same calls the notebook makes (scripts/client/munitas_client.py).
  const draft = await api("/derivations", RESEARCHER, {
    method: "POST",
    body: JSON.stringify({
      inputs: STORY.inputs, sql: STORY.sql,
      target_name: NEW_NAME, primary_key: [STORY.key], purpose: PURPOSE,
    }),
  });
  const confirmed = await api(`/derivations/${draft.id}/confirm`, RESEARCHER, { method: "POST", body: "{}" });
  let result = confirmed;
  for (let i = 0; i < 90 && !["succeeded", "failed"].includes(result.status); i++) {
    await new Promise((r) => setTimeout(r, 2000));
    result = await api(`/derivations/${confirmed.id}`, RESEARCHER);
  }
  expect(result.status, `the derivation ended ${result.status}: ${result.error}`).toBe("succeeded");

  await loginAs(page, RESEARCHER);
  await searchDatasets(page, NEW_NAME);
  await shot(page, "sam-finds-the-new-dataset");

  const made = await versionIdOf(RESEARCHER, NEW_NAME);
  await page.goto(`/versions/${made}`);
  await expect(page.getByText("Where this came from")).toBeVisible();
  await page.getByText("Where this came from").scrollIntoViewIfNeeded();
  await shot(page, "where-it-came-from");

  await loginAs(page, CUSTODIAN);
  await page.goto("/audit");
  await expect(page.getByText("Who accessed what").first()).toBeVisible();
  // The newest rows are the platform's own reads of the inputs. The refusals are
  // what this step is about, so show the log filtered to them.
  await page.getByRole("button", { name: /^Refused/ }).click();
  await page.waitForTimeout(1500);
  await shot(page, "the-decision-log");

  // ---- Act three: the access is withdrawn --------------------------------
  const active = await api(`/leases?principal=${RESEARCHER}&active_only=true`, CUSTODIAN);
  for (const l of active.leases ?? []) {
    if (l.dataset_version_id === raw) await api(`/leases/${l.id}/revoke`, CUSTODIAN, { method: "POST" });
  }
  await loginAs(page, RESEARCHER);
  await searchDatasets(page, NEW_NAME);
  await shot(page, "sam-after-the-lease-is-withdrawn");
});
