/**
 * U185: a table about people too large for one request is brought in through the console in parts, locked as it is written, and read a page at a time.
 *
 * The platform's side (the keys, the batches, the group-size report, erasing one person) is checked by verify/v174_people_tables.py; this checks that the page
 * leads a person through it: the file is sent in parts, the table is checked whole and then locked, the result says how many people it holds and that the
 * file was not kept, a file whose last row names nobody is refused with that row, and the department's approver reads the locked rows a page at a time.
 *
 * Set MUNITAS_SHOTS to a folder to keep a screenshot of each screen, so the screens can be read.
 */

import { expect, test, type Page } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial" });

const ENGINEER = "canary-engineer";
const CUSTODIAN = "canary-custodian";
const KIND = `visit card number ${Date.now()}`;
const REPLACED = "replaced by the person code";

const CONTRACT = `apiVersion: v3.2.0
kind: DataContract
id: visit-log
name: visit_log
version: 1.0.0
status: active
customProperties:
  - {property: subject, vendor: munitas, value: {kind: "${KIND}", foundBy: column, column: card_number}}
schema:
  - id: obj
    name: visit_log
    logicalType: object
    properties:
      - {id: a, name: visit_id, logicalType: string, primaryKey: true, required: true, classification: none, customProperties: [{property: treatment, vendor: munitas, value: readable}]}
      - {id: b, name: card_number, logicalType: string, classification: direct, customProperties: [{property: personIdentifier, vendor: munitas, value: true}, {property: treatment, vendor: munitas, value: "${REPLACED}"}]}
      - {id: c, name: full_name, logicalType: string, classification: direct, customProperties: [{property: treatment, vendor: munitas, value: lock}]}
      - {id: d, name: town, logicalType: string, classification: quasi, customProperties: [{property: treatment, vendor: munitas, value: readable}]}
`;

const ROWS = 25_000;
const PEOPLE = 12_000;
const TOWNS = ["Ashford", "Brinley", "Corwen", "Dunmore", "Elstow", "Fenwick", "Garland", "Hexham"];

function visits(blankLast = false): Buffer {
  const lines = ["visit_id,card_number,full_name,town"];
  for (let n = 1; n <= ROWS; n += 1) {
    const person = n % PEOPLE;
    lines.push(`V${String(n).padStart(7, "0")},${blankLast && n === ROWS ? "" : `C${String(person).padStart(6, "0")}`},Visitor ${person} of the large visit log,${TOWNS[person % TOWNS.length]}`);
  }
  return Buffer.from(lines.join("\n") + "\n");
}

async function shot(page: Page, name: string) {
  const folder = process.env.MUNITAS_SHOTS;
  if (folder) await page.screenshot({ path: `${folder}/${name}.png`, fullPage: true });
}

async function registered(name: string): Promise<string> {
  const org = await fetch(`${API_BASE}/organisation?tenant_id=canary`, { headers: await bearerFor(CUSTODIAN) }).then((r) => r.json());
  const department = org.departments.find((d: { name: string }) => d.name === "Verification").id;
  const response = await fetch(`${API_BASE}/datasets/register`, {
    method: "POST",
    headers: { ...(await bearerFor(ENGINEER)), "content-type": "application/json" },
    body: JSON.stringify({ tenant_id: "canary", name, department_id: department, registered_by: ENGINEER, provenance: "internal_regulated", declared_class: "RAW", source_kind: "upload" }),
  });
  expect(response.status).toBe(201);
  return (await response.json()).id;
}

async function choose(page: Page, datasetId: string, rows: Buffer) {
  await loginAs(page, ENGINEER);
  await page.goto(`/datasets/${datasetId}/ingest`);
  await page.getByTestId("erase-yes").check();
  await page.getByTestId("seal-table-contract").setInputFiles({ name: "contract.yaml", mimeType: "application/x-yaml", buffer: Buffer.from(CONTRACT) });
  await page.getByTestId("seal-table-rows").setInputFiles({ name: "visits.csv", mimeType: "text/csv", buffer: rows });
}

let versionId = "";

test.describe("U185: a large table about people in the console", () => {
  test.setTimeout(240_000);

  test("the page says it is sent in parts, checks the table whole, locks it, and says how many people it holds", async ({ page }) => {
    await choose(page, await registered(`people-large-${Date.now()}`), visits());
    await expect(page.getByTestId("detected-format")).toContainText("Read as a CSV file.");
    await expect(page.getByTestId("detected-format")).toContainText("over 1 MB");
    await expect(page.getByTestId("seal-table-submit")).toHaveText("Send and write as a table");
    await shot(page, "1-people-file-chosen");
    await page.getByTestId("seal-table-submit").click();
    await expect(page.getByTestId("large-upload")).toBeVisible();
    await shot(page, "2-people-sending");
    const done = page.getByTestId("table-sealed");
    await expect(done).toContainText(`25,000 rows about 12,000 people`, { timeout: 180_000 });
    await expect(done).toContainText("Their identifying values are locked, each under a key made for that person");
    await expect(done).toContainText("The file itself was not kept");
    await expect(done).not.toContainText("Nothing in it is locked");
    await shot(page, "3-people-sealed");
    await page.getByTestId("open-sealed-table").click();
    await expect(page).toHaveURL(/\/versions\/[0-9a-f-]{36}$/);
    versionId = page.url().split("/").pop() ?? "";
  });

  test("a file whose last row names nobody is refused with that row, and nothing is sealed", async ({ page }) => {
    await choose(page, await registered(`people-refused-${Date.now()}`), visits(true));
    await page.getByTestId("seal-table-submit").click();
    const refused = page.getByTestId("table-refused");
    await expect(refused).toContainText("Nothing was sealed", { timeout: 120_000 });
    await expect(page.getByTestId("table-problems")).toContainText("Row 25000");
    await expect(page.getByTestId("table-problems")).toContainText("names nobody");
    await expect(page.getByTestId("table-sealed")).toHaveCount(0);
    await shot(page, "4-people-refused");
  });

  test("the department's approver reads the locked rows a page at a time", async ({ page }) => {
    expect(versionId).not.toBe("");
    await loginAs(page, CUSTODIAN);
    await page.goto(`/versions/${versionId}/locked-rows`);
    const banner = page.getByTestId("locked-rows-banner");
    await expect(banner).toContainText("you can read all 25,000 rows, 200 at a time. Page 1.");
    await expect(page.getByTestId("locked-rows").locator("tbody tr")).toHaveCount(200);
    await expect(page.getByText("Visitor 1 of the large visit log", { exact: true }).first()).toBeVisible();
    await expect(page.getByTestId("locked-rows-previous")).toBeDisabled();
    await shot(page, "5-people-page-one");
    await page.getByTestId("locked-rows-next").click();
    await expect(banner).toContainText("Page 2.");
    await expect(page.getByTestId("locked-rows-pager")).toContainText("Rows 201 to 400 of 25,000");
    await page.getByTestId("locked-rows-previous").click();
    await expect(banner).toContainText("Page 1.");
  });
});
