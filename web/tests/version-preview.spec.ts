/**
 * U181: the first rows of a table can be looked at from its version's page, by somebody who may read it, and not by somebody who may not.
 *
 * Looking at rows is a read, so the page fetches nothing until the person presses the button. A table whose owner claimed it public is held at the most
 * restricted level until an approver of the owning department agrees, so the first look is refused, with what to do next; once the approver has agreed,
 * the same button shows the first rows with their columns and types. The platform's side (who may, what is recorded, the limit of 100 rows, the types
 * in two other tools) is checked by verify/v171_read_back.py.
 *
 * Set MUNITAS_SHOTS to a folder to keep a screenshot of each screen, so the screens can be read.
 */

import { expect, test, type Page } from "@playwright/test";
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
      - {id: c, name: occupancy, logicalType: number, physicalType: "decimal(5,2)", classification: none, customProperties: [{property: treatment, vendor: munitas, value: readable}]}
      - {id: d, name: counted_on, logicalType: date, classification: none, customProperties: [{property: treatment, vendor: munitas, value: readable}]}
`;

async function shot(page: Page, name: string) {
  const folder = process.env.MUNITAS_SHOTS;
  if (folder) await page.screenshot({ path: `${folder}/${name}.png`, fullPage: true });
}

test.describe("U181: the first rows of a table, on its version's page", () => {
  test.setTimeout(90_000);

  test("refused until the owner's approver agrees with the claim, then shown with every digit", async ({ page }) => {
    await loginAs(page, "canary-engineer");
    await page.goto("/datasets/register");
    await page.getByTestId("register-name").fill(`preview-${Date.now()}`);
    await page.getByTestId("register-department").selectOption({ label: "Verification" });
    await page.getByTestId("register-class").selectOption("PUBLISHED");
    await page.getByTestId("register-submit").click();
    await expect(page.getByTestId("upload-step")).toBeVisible();
    await page.getByTestId("plain-table").check();
    await page.getByTestId("seal-table-contract").setInputFiles({ name: "contract.yaml", mimeType: "text/yaml", buffer: Buffer.from(CONTRACT) });
    await page.getByTestId("seal-table-rows").setInputFiles({
      name: "wards.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("ward,admissions,occupancy,counted_on\nA,12,0.85,2026-03-31\nB,30,0.92,2026-03-31\nC,7,0.40,2026-04-01\n"),
    });
    await page.getByTestId("seal-table-submit").click();
    await expect(page.getByTestId("table-sealed")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("open-sealed-table").click();
    await expect(page).toHaveURL(/\/versions\/[0-9a-f-]{36}$/);

    await expect(page.getByTestId("current-class")).toContainText("Raw");
    await expect(page.getByTestId("preview-rows")).toHaveCount(0);
    await page.getByTestId("preview-show").click();
    const refused = page.getByTestId("preview-refused");
    await expect(refused).toBeVisible();
    await expect(refused).toContainText("The rows are not shown.");
    await expect(refused).toContainText("You do not hold access to read this version's rows yet");
    await shot(page, "preview-1-refused");

    // The approver of the owning department agrees with the claim, and the version opens to the level that was claimed.
    const version = page.url().split("/").pop();
    const mine = await bearerFor("canary-engineer");
    const detail = await fetch(`${API_BASE}/dataset-versions/${version}`, { headers: mine }).then((r) => r.json());
    const agreed = await fetch(`${API_BASE}/datasets/${detail.dataset_id}/confirm-classification`, {
      method: "POST",
      headers: { "content-type": "application/json", ...(await bearerFor("canary-custodian")) },
      body: JSON.stringify({ confirmed_by: "canary-custodian" }),
    });
    expect(agreed.status).toBe(200);

    await page.reload();
    await expect(page.getByTestId("current-class")).toContainText("Published");
    await page.getByTestId("preview-show").click();
    const rows = page.getByTestId("preview-rows");
    await expect(rows).toBeVisible();
    await expect(rows).toContainText("The first 3 of 3 rows, as they are stored. Looking at them has been recorded.");
    await expect(rows.locator("thead")).toContainText("occupancy");
    await expect(rows.locator("thead")).toContainText("decimal(5, 2)");
    await expect(rows.locator("thead")).toContainText("whole number");
    await expect(rows.locator("thead")).toContainText("date");
    await expect(rows.locator("tbody tr")).toHaveCount(3);
    await expect(rows.locator("tbody")).toContainText("0.40");
    await expect(rows.locator("tbody")).toContainText("2026-04-01");
    await shot(page, "preview-2-shown");
  });
});
