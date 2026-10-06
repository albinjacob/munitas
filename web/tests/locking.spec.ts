/**
 * U135 in the console: a table is sealed from two files, its locked columns are read only by those the platform lets, and the evidence
 * of how they were used is readable by the auditor and holds no value.
 *
 * Serial, because the flow is one story told by several people: an engineer seals a table, asks to read what is locked, a custodian
 * decides, the engineer reads their few rows, and an auditor reads the evidence. Each test acts on what the one before it left.
 *
 * The claim these tests carry is not that the screens render. It is that what the platform decides reaches the person: the engineer is
 * offered a form and not the rows, the custodian is offered the rows and the decision, the researcher is offered neither, a refusal says why,
 * and no page the auditor can open shows a name from the table.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial" });

const FIXTURES = join(process.cwd(), "walkthroughs", "fixtures", "locked-appointments");
const CONTRACT = readFileSync(join(FIXTURES, "contract.yaml"));
const ROWS = readFileSync(join(FIXTURES, "rows.csv"));
// A name that is in the rows, and so must never appear on a page that holds no values.
const A_NAME = "Maeve Doyle";

const ENGINEER = "canary-engineer";
const CUSTODIAN = "canary-custodian";
const RESEARCHER = "canary-researcher";
const AUDITOR = "canary-auditor";

async function departmentId(): Promise<string> {
  const headers = await bearerFor(CUSTODIAN);
  const org = await fetch(`${API_BASE}/organisation?tenant_id=canary`, { headers }).then((r) => r.json());
  return org.departments.find((d: { name: string }) => d.name === "Verification").id;
}

async function registerDataset(name: string): Promise<string> {
  const response = await fetch(`${API_BASE}/datasets/register`, {
    method: "POST",
    headers: { ...(await bearerFor(ENGINEER)), "content-type": "application/json" },
    body: JSON.stringify({
      tenant_id: "canary",
      name,
      department_id: await departmentId(),
      registered_by: ENGINEER,
      provenance: "internal_regulated",
      declared_class: "RAW",
      source_kind: "upload",
    }),
  });
  expect(response.status).toBe(201);
  return (await response.json()).id;
}

async function sealThroughApi(datasetId: string): Promise<string> {
  const form = new FormData();
  form.append("contract", new Blob([CONTRACT]), "contract.yaml");
  form.append("rows", new Blob([ROWS]), "rows.csv");
  const response = await fetch(`${API_BASE}/datasets/${datasetId}/seal-table-files`, {
    method: "POST",
    headers: await bearerFor(ENGINEER),
    body: form,
  });
  expect(response.status).toBe(201);
  return (await response.json()).id;
}

let versionId = "";
const purpose = `to see what the postcode column really holds before I write the rule that locks it (${Date.now()})`;

async function openVersion(page: Page) {
  await page.goto(`/versions/${versionId}`);
  await expect(page.getByRole("heading", { name: "Locked columns" })).toBeVisible();
}

test.describe("U135: locked columns in the console", () => {
  test("an engineer seals a table from two files and is told about small groups", async ({ page }) => {
    const datasetId = await registerDataset(`console-seal-${Date.now()}`);
    await loginAs(page, ENGINEER);
    await page.goto(`/datasets/${datasetId}/ingest`);

    await page.getByTestId("seal-table-contract").setInputFiles({ name: "contract.yaml", mimeType: "application/x-yaml", buffer: CONTRACT });
    await page.getByTestId("seal-table-rows").setInputFiles({ name: "rows.csv", mimeType: "text/csv", buffer: ROWS });
    await page.getByTestId("seal-table-submit").click();

    const done = page.getByTestId("table-sealed");
    // Sealing makes the keys and writes the first file, which can take several seconds.
    await expect(done).toContainText("23 rows about 20 people", { timeout: 30_000 });
    await expect(page.getByTestId("sealed-small-groups")).toContainText("12 people sit in a group smaller than 5");
    await expect(done).toContainText("did not stop the seal");
    await page.getByTestId("open-sealed-table").click();
    await expect(page.getByRole("heading", { name: "Locked columns" })).toBeVisible();
  });

  test("a bad file is refused with every problem listed, and nothing is sealed", async ({ page }) => {
    const datasetId = await registerDataset(`console-refused-${Date.now()}`);
    await loginAs(page, ENGINEER);
    await page.goto(`/datasets/${datasetId}/ingest`);

    const broken = ROWS.toString("utf8")
      .replace("2026-09-03", "not a date")
      .replace("HC-4101,Tomas Byrne", ",Tomas Byrne");
    await page.getByTestId("seal-table-contract").setInputFiles({ name: "contract.yaml", mimeType: "application/x-yaml", buffer: CONTRACT });
    await page.getByTestId("seal-table-rows").setInputFiles({ name: "rows.csv", mimeType: "text/csv", buffer: Buffer.from(broken) });
    await page.getByTestId("seal-table-submit").click();

    const refused = page.getByTestId("seal-table-refused");
    await expect(refused).toContainText("Nothing was sealed");
    await expect(refused).toContainText("not a date");
    await expect(refused).toContainText("names nobody");
    await expect(page.getByTestId("table-sealed")).toHaveCount(0);
  });

  test("the engineer sees the locked columns and is offered a form, not the rows", async ({ page }) => {
    const datasetId = await registerDataset(`console-locked-${Date.now()}`);
    versionId = await sealThroughApi(datasetId);
    await loginAs(page, ENGINEER);
    await openVersion(page);

    const columns = page.getByTestId("locked-columns");
    await expect(columns).toContainText("patient_name");
    await expect(columns).toContainText("Locked under the person's key");
    await expect(columns).toContainText("Replaced by a person code");
    await expect(page.getByTestId("ask-lease")).toBeVisible();
    await expect(page.getByTestId("read-locked-rows")).toHaveCount(0);
    // Not a value from the table anywhere on the page, and the small-group report is not for this role.
    await expect(page.locator("body")).not.toContainText(A_NAME);
    await expect(page.getByTestId("group-sizes")).toHaveCount(0);
  });

  test("the limits are the policy's, and a request needs a reason", async ({ page }) => {
    await loginAs(page, ENGINEER);
    await openVersion(page);
    await expect(page.getByTestId("ask-lease")).toContainText("up to 7");
    await expect(page.getByTestId("ask-lease")).toContainText("up to 20");
    await expect(page.getByTestId("lease-submit")).toBeDisabled();

    await page.getByTestId("lease-purpose").fill(purpose);
    await page.getByTestId("lease-days").fill("9");
    await page.getByTestId("lease-submit").click();
    await expect(page.getByTestId("lease-refused")).toContainText("between 1 and 7 days");
  });

  test("the engineer asks, and the request waits", async ({ page }) => {
    await loginAs(page, ENGINEER);
    await openVersion(page);
    await page.getByTestId("lease-purpose").fill(purpose);
    await page.getByTestId("lease-days").fill("3");
    await page.getByTestId("lease-rows").fill("5");
    await page.getByTestId("lease-submit").click();
    await expect(page.getByTestId("locked-access")).toContainText("waiting for an approver");
    await expect(page.getByTestId("locked-access")).toContainText(purpose);
  });

  test("a researcher can neither read nor ask", async ({ page }) => {
    await loginAs(page, RESEARCHER);
    await openVersion(page);
    await expect(page.getByTestId("locked-access")).toContainText("cannot read the locked values and cannot ask");
    await expect(page.getByTestId("ask-lease")).toHaveCount(0);
  });

  test("the owning department's custodian sees the request, and the person who asked cannot decide it", async ({ page }) => {
    await loginAs(page, ENGINEER);
    await page.goto("/locked-column-access");
    await expect(page.getByTestId("leases-mine")).toContainText(purpose);
    await expect(page.getByTestId("leases-to-decide")).toHaveCount(0);

    await loginAs(page, CUSTODIAN);
    await page.goto("/locked-column-access");
    const waiting = page.getByTestId("leases-to-decide");
    await expect(waiting).toContainText("Canary engineer");
    await expect(waiting).toContainText(purpose);
    await expect(waiting).toContainText("5 rows, 3 days");
  });

  test("the custodian approves it, and it moves to the leases in force", async ({ page }) => {
    await loginAs(page, CUSTODIAN);
    await page.goto("/locked-column-access");
    const row = page.getByTestId("leases-to-decide").locator("tr", { hasText: purpose });
    await row.locator("input").fill("Checked with the team lead");
    await row.getByRole("button", { name: "Approve" }).click();

    await expect(page.getByTestId("leases-to-decide")).toHaveCount(0);
    await expect(page.getByTestId("leases-granted")).toContainText("Canary engineer");
    await expect(page.getByTestId("leases-granted")).toContainText("In force");
  });

  test("the engineer reads exactly the rows the lease opens, with the locked values opened", async ({ page }) => {
    await loginAs(page, ENGINEER);
    await openVersion(page);
    await expect(page.getByTestId("locked-access")).toContainText("5 rows of 23");
    await expect(page.getByTestId("locked-access")).toContainText("Approved by Canary custodian");
    await page.getByTestId("read-locked-rows").click();

    await expect(page.getByTestId("locked-rows-banner")).toContainText("Your lease opens 5 of 23 rows");
    const table = page.getByTestId("locked-rows");
    await expect(table.locator("tbody tr")).toHaveCount(5);
    await expect(table).not.toContainText("locked:v1");
    // A real name, which is what the lease is for, and a person code where the identifier was.
    await expect(table.locator("tbody tr").first().locator("td").nth(2)).not.toHaveText("");
    await expect(table).toContainText("T-");
    const first = await page.getByTestId("locked-rows").textContent();
    await page.reload();
    await expect(page.getByTestId("locked-rows")).toHaveText(first ?? "");
  });

  test("the custodian reads every row, sees the small groups, and records a decision", async ({ page }) => {
    await loginAs(page, CUSTODIAN);
    await openVersion(page);
    await expect(page.getByTestId("locked-access")).toContainText("every locked value");
    await expect(page.getByTestId("group-size-finding")).toContainText("12 of 20 people sit in a group smaller than 5");
    await expect(page.getByTestId("group-size-advice")).toContainText("Locking postcode would leave 4 people");
    await expect(page.getByTestId("group-size-advice")).toContainText("Locking age_band would leave 6 people");

    await page.getByTestId("group-size-note").fill("The next contract version will lock the postcode.");
    await page.getByTestId("group-size-submit").click();
    await expect(page.getByTestId("group-size-decided")).toContainText("the contract will change before the next table");

    await page.getByTestId("read-locked-rows").click();
    await expect(page.getByTestId("locked-rows-banner")).toContainText("all 23 rows");
    await expect(page.getByTestId("locked-rows").locator("tbody tr")).toHaveCount(23);
    await expect(page.getByTestId("locked-rows")).toContainText(A_NAME);
  });

  test("the engineer ends their own lease, and is back to asking", async ({ page }) => {
    await loginAs(page, ENGINEER);
    await openVersion(page);
    await page.getByTestId("end-own-lease").click();
    await expect(page.getByTestId("ask-lease")).toBeVisible();
    await page.goto(`/versions/${versionId}/locked-rows`);
    await expect(page.getByTestId("failure")).toContainText("refused");
  });

  test("the auditor reads the evidence, which holds no value from the table", async ({ page }) => {
    await loginAs(page, AUDITOR);
    await page.goto("/locking-audit");
    const audit = page.getByTestId("locking-audit");
    await expect(audit).toContainText("Who read locked columns, and who was refused");
    await expect(page.getByTestId("audit-reads")).toContainText("Refused");
    await expect(page.getByTestId("audit-reads")).toContainText("Read");
    await expect(page.getByTestId("audit-leases")).toContainText(purpose);
    await expect(page.getByTestId("audit-treatments").first()).toContainText("Locked under the person's key");
    await expect(page.getByTestId("audit-group-sizes")).toContainText("Contract will change");
    await expect(page.getByTestId("audit-keys")).toContainText("A key was opened");
    await expect(page.locator("body")).not.toContainText(A_NAME);
    await expect(page.locator("body")).not.toContainText("Zzexample");
  });

  test("the engineer has no audit page", async ({ page }) => {
    await loginAs(page, ENGINEER);
    await page.goto("/locking-audit");
    await expect(page.getByText("This page is for the privacy auditor")).toBeVisible();
  });
});
