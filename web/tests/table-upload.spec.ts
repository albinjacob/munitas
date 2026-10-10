/**
 * U179: a large table is brought in through the console, in parts.
 *
 * A table over 5 MB is not sent in one request. The page reads the file's name for how it is laid out, offers what the layout needs to be told
 * (the separator, the text encoding, whether there is a header, the words that mean a missing value), sends the file in parts of 8 MB, and then shows
 * the table being written until it is sealed or refused. The platform's side (the parts, the limits, the worker) is checked by
 * verify/v169_table_upload.py and v170_table_upload_limits.py; this checks that the page leads a person through it, shows what is happening, survives
 * a lost connection, can be cancelled, and says what is wrong with a file in rows and lines.
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
`;

const ROWS = 800_000;

function rows(count: number, damage?: { at: number; value: string }): Buffer {
  const lines = ["ward,admissions"];
  for (let n = 1; n <= count; n += 1) lines.push(`W${String(n).padStart(7, "0")},${damage && damage.at === n ? damage.value : n % 97}`);
  return Buffer.from(lines.join("\n") + "\n");
}

async function shot(page: Page, name: string) {
  const folder = process.env.MUNITAS_SHOTS;
  if (folder) await page.screenshot({ path: `${folder}/${name}.png`, fullPage: true });
}

async function register(page: Page, name: string) {
  await loginAs(page, "canary-engineer");
  await page.goto("/datasets/register");
  await page.getByTestId("register-name").fill(name);
  await page.getByTestId("register-department").selectOption({ label: "Verification" });
  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("upload-step")).toBeVisible();
  await page.getByTestId("plain-table").check();
  await page.getByTestId("seal-table-contract").setInputFiles({ name: "contract.yaml", mimeType: "text/yaml", buffer: Buffer.from(CONTRACT) });
}

async function choose(page: Page, name: string, buffer: Buffer) {
  await page.getByTestId("seal-table-rows").setInputFiles({ name, mimeType: "text/csv", buffer });
}

test.describe("U179: a large table is brought in through the console, in parts", () => {
  test.setTimeout(240_000);

  test("the page says how the file is read, sends it in parts, shows the table being written and seals it", async ({ page }) => {
    await register(page, `large-table-${Date.now()}`);
    await choose(page, "wards.csv", rows(ROWS));
    await expect(page.getByTestId("detected-format")).toContainText("Read as a CSV file.");
    await expect(page.getByTestId("detected-format")).toContainText("over 5 MB");
    await expect(page.getByTestId("option-delimiter")).toHaveValue(",");
    await expect(page.getByTestId("option-header")).toBeChecked();
    await expect(page.getByTestId("seal-table-submit")).toHaveText("Send and write as a table");
    await shot(page, "1-file-chosen");

    await page.getByTestId("seal-table-submit").click();
    await expect(page.getByTestId("large-upload")).toBeVisible();
    await expect(page.getByTestId("upload-phase")).toContainText(/Sending wards\.csv|All of it has arrived|Waiting for a worker|Writing the table|Handing it to a worker/);
    await shot(page, "2-sending");

    const sealed = page.getByTestId("table-sealed");
    await expect(sealed).toBeVisible({ timeout: 150_000 });
    await expect(sealed).toContainText("Sealed as version 1: 800,000 rows.");
    await expect(sealed.getByTestId("brought-in")).toContainText(/wards\.csv \(\d+ MB\) was brought in by canary-engineer on [A-Z][a-z]{2} \d{1,2}, 20\d\d/);
    await expect(sealed).toContainText("Nothing in it is locked.");
    await shot(page, "3-sealed");

    await page.getByTestId("open-sealed-table").click();
    await expect(page).toHaveURL(/\/versions\/[0-9a-f-]{36}$/);
    await expect(page.getByTestId("sealed-class")).toContainText("Raw");
    await expect(page.getByTestId("brought-in-by")).toContainText(/wards\.csv \(\d+(\.\d+)? MB\) by canary-engineer on [A-Z][a-z]{2} \d{1,2}, 20\d\d/);
    await shot(page, "3b-version-page");
    const version = page.url().split("/").pop();
    const holds = await fetch(`${API_BASE}/dataset-versions/${version}/holds`, { headers: await bearerFor("canary-engineer") }).then((r) => r.json());
    expect(holds.table).toMatchObject({ present: true, rows: ROWS });
  });

  test("a lost connection pauses the upload, and carrying on sends only the part that is missing", async ({ page }) => {
    await register(page, `large-table-break-${Date.now()}`);
    await choose(page, "wards.csv", rows(ROWS));
    let down = true;
    const sent: number[] = [];
    await page.route("**/table-uploads/*/parts/*", async (route) => {
      const part = Number(route.request().url().split("/").pop());
      if (part === 2 && down) return route.abort("connectionreset");
      sent.push(part);
      return route.continue();
    });
    await page.getByTestId("seal-table-submit").click();

    const paused = page.getByTestId("upload-paused");
    await expect(paused).toBeVisible({ timeout: 90_000 });
    await expect(paused).toContainText("What has arrived is kept");
    await shot(page, "4-paused");
    expect(sent).toEqual([1]);

    down = false;
    await page.getByTestId("upload-resume").click();
    await expect(page.getByTestId("table-sealed")).toBeVisible({ timeout: 150_000 });
    expect(sent).toEqual([1, 2]);
  });

  test("a wrong value is refused with every problem named by row and line, and the list can be kept", async ({ page }) => {
    await register(page, `large-table-refused-${Date.now()}`);
    await choose(page, "wards.csv", rows(ROWS, { at: 700_000, value: "twelve" }));
    await page.getByTestId("seal-table-submit").click();

    const refused = page.getByTestId("table-refused");
    await expect(refused).toBeVisible({ timeout: 150_000 });
    await expect(refused).toContainText("Nothing was sealed. Fix these first:");
    await expect(page.getByTestId("table-problems")).toContainText("Row 700000 (line 700001): admissions is not a whole number");
    await expect(page.getByTestId("download-problems")).toHaveAttribute("download", "wards.csv.problems.txt");
    await shot(page, "5-refused");
    await expect(page.getByTestId("table-sealed")).toHaveCount(0);
  });

  test("an upload can be cancelled while it is going up, and nothing is kept", async ({ page }) => {
    await register(page, `large-table-cancel-${Date.now()}`);
    await choose(page, "wards.csv", rows(ROWS));
    await page.route("**/table-uploads/*/parts/*", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 3000));
      return route.continue();
    });
    await page.getByTestId("seal-table-submit").click();
    await expect(page.getByTestId("upload-progress")).toBeVisible({ timeout: 30_000 });
    const kept = await page.evaluate(() => Object.keys(window.localStorage));
    const id = await page.evaluate(() => Object.entries(window.localStorage).find(([k]) => k.startsWith("munitas-table-upload:"))?.[1] ?? null);
    expect(id, `the browser kept: ${JSON.stringify(kept)}`).not.toBeNull();
    await page.getByTestId("upload-cancel").click();
    await expect(page.getByTestId("upload-cancelled")).toContainText("This was cancelled. Nothing was kept.");
    await shot(page, "6-cancelled");
    await expect.poll(async () => (await fetch(`${API_BASE}/table-uploads/${id}`, { headers: await bearerFor("canary-engineer") }).then((r) => r.json())).outcome, { timeout: 15_000 }).toBe("cancelled");
  });

  test("what a file needs to be told is offered, and a file that is read as another layout is not offered a wrong one", async ({ page }) => {
    await register(page, `plain-options-${Date.now()}`);
    await choose(page, "wards.tsv", Buffer.from("ward\tadmissions\nA\t12\nB\t30\n"));
    await expect(page.getByTestId("detected-format")).toContainText("Read as a TSV file.");
    await expect(page.getByTestId("option-delimiter")).toHaveValue("\t");
    await expect(page.getByTestId("seal-table-submit")).toHaveText("Seal as a table");

    await choose(page, "wards.csv.gz", Buffer.from("not really compressed"));
    await expect(page.getByTestId("detected-format")).toContainText("Read as a CSV file, compressed with gzip.");

    await choose(page, "wards.jsonl", Buffer.from('{"ward":"A","admissions":1}\n'));
    await expect(page.getByTestId("detected-format")).toContainText("Read as a file with one JSON row per line.");
    await expect(page.getByTestId("option-delimiter")).toHaveCount(0);

    await choose(page, "wards.xml", Buffer.from("<rows/>"));
    await expect(page.getByTestId("detected-format")).toContainText("This ending is not one that is read");
    await expect(page.getByTestId("seal-table-submit")).toBeDisabled();
    await shot(page, "7-ending-not-read");
  });

  test("a small file with its own separator is sealed at once when it is told so", async ({ page }) => {
    await register(page, `plain-semicolon-${Date.now()}`);
    await choose(page, "wards.csv", Buffer.from("ward;admissions\nA;12\nB;30\nC;7\n"));
    await page.getByTestId("option-delimiter").selectOption({ label: "Semicolons" });
    await shot(page, "8-small-with-options");
    await page.getByTestId("seal-table-submit").click();
    await expect(page.getByTestId("table-sealed")).toContainText("Sealed as version 1: 3 rows.", { timeout: 30_000 });
  });
});
