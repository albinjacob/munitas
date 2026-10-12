/**
 * U174: bringing in an ordinary table through the console.
 *
 * The first question when bringing data in is whether one person will ever need to be erased from it. Answered no, the person chooses between files
 * and a table; a table is a contract and the rows, sealed as a table that nobody can read until the person who answers for it allows it. The platform's
 * side, in every layout and every refusal, is checked by verify/v166_plain_tables.py; this checks that the page leads a person through it.
 */

import { expect, test } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "./auth-helpers";

const CONTRACT = `apiVersion: v3.2.0
kind: DataContract
id: ward-counts
name: ward-counts
version: 1.0.0
status: active
customProperties:
  - {property: erasable, vendor: munitas, value: false}
  - {property: notErasableReason, vendor: munitas, value: "Counts about wards, not about people."}
schema:
  - id: obj
    name: ward_counts
    logicalType: object
    properties:
      - {id: a, name: ward, logicalType: string, primaryKey: true, required: true, classification: none, customProperties: [{property: treatment, vendor: munitas, value: readable}]}
      - {id: b, name: admissions, logicalType: integer, classification: none, customProperties: [{property: treatment, vendor: munitas, value: readable}]}
`;

async function register(page: import("@playwright/test").Page, name: string) {
  await loginAs(page, "canary-engineer");
  await page.goto("/datasets/register");
  await page.getByTestId("register-name").fill(name);
  await page.getByTestId("register-department").selectOption({ label: "Verification" });
  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("upload-step")).toBeVisible();
}

test.describe("U174: an ordinary table is brought in through the console", () => {
  test("answering no offers files or a table, and a table is sealed with nothing locked", async ({ page }) => {
    const name = `plain-table-check-${Date.now()}`;
    await register(page, name);

    await expect(page.getByTestId("erase-no")).toBeChecked();
    await expect(page.getByTestId("plain-files")).toBeChecked();
    await page.getByTestId("plain-table").check();
    await expect(page.getByTestId("plain-table-section")).toBeVisible();
    await expect(page.getByTestId("locking-section")).toHaveCount(0);

    await page.getByTestId("seal-table-contract").setInputFiles({ name: "contract.yaml", mimeType: "text/yaml", buffer: Buffer.from(CONTRACT) });
    await page.getByTestId("seal-table-rows").setInputFiles({ name: "wards.jsonl", mimeType: "application/json", buffer: Buffer.from('{"ward":"A","admissions":12}\n{"ward":"B","admissions":30}\n') });
    await page.getByTestId("seal-table-submit").click();

    const done = page.getByTestId("table-sealed");
    await expect(done).toBeVisible({ timeout: 30_000 });
    await expect(done).toContainText("Sealed as version 1: 2 rows.");
    await expect(done).toContainText("Nothing in it is locked.");
    await expect(done).toContainText("nobody can read it");

    await page.getByTestId("open-sealed-table").click();
    await expect(page).toHaveURL(/\/versions\/[0-9a-f-]{36}$/);
    await expect(page.getByTestId("sealed-class")).toContainText("Raw");
    await expect(page.getByTestId("brought-in-by")).toContainText(/wards\.jsonl .* by Canary engineer on [A-Z][a-z]{2} \d{1,2}, 20\d\d/);

    // It is a table, with its two rows, and not a file that looks like one.
    const version = page.url().split("/").pop();
    const holds = await fetch(`${API_BASE}/dataset-versions/${version}/holds`, { headers: await bearerFor("canary-engineer") }).then((r) => r.json());
    expect(holds.table).toMatchObject({ present: true, rows: 2 });
  });

  test("a wrong value is refused with the row and column named, and nothing is sealed", async ({ page }) => {
    await register(page, `plain-table-refused-${Date.now()}`);
    await page.getByTestId("plain-table").check();
    await page.getByTestId("seal-table-contract").setInputFiles({ name: "contract.yaml", mimeType: "text/yaml", buffer: Buffer.from(CONTRACT) });
    await page.getByTestId("seal-table-rows").setInputFiles({ name: "wards.csv", mimeType: "text/csv", buffer: Buffer.from("ward,admissions\nA,twelve\nB,30\n") });
    await page.getByTestId("seal-table-submit").click();

    const refused = page.getByTestId("seal-table-refused");
    await expect(refused).toBeVisible({ timeout: 20_000 });
    await expect(refused).toContainText("Nothing was sealed");
    await expect(refused).toContainText("Row 1 (line 2): admissions is not a whole number");
    await expect(page.getByTestId("table-sealed")).toHaveCount(0);
  });

  test("answering yes still offers the locked route, and a table of people is not offered here", async ({ page }) => {
    await register(page, `plain-table-locked-${Date.now()}`);
    await page.getByTestId("erase-yes").check();
    await expect(page.getByTestId("locking-section")).toBeVisible();
    await expect(page.getByTestId("plain-table-section")).toHaveCount(0);
    await expect(page.getByTestId("plain-table")).toHaveCount(0);
  });
});
