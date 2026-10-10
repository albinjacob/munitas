/**
 * U139 in the console: the first question when bringing data in, and files locked per patient as they upload.
 *
 * The claims these tests carry are about what reaches the person, on the live platform:
 *   - the question comes first, "No" is the ordinary upload and is unchanged, and "Yes" replaces the ordinary upload with the way in that locks;
 *   - everything wrong with the manifest and the files chosen is listed together, before a single byte is sent;
 *   - a file goes up in parts, a part that fails is sent again without the person doing anything, and a connection that stays down pauses the
 *     upload with what was sent kept, and carrying on finishes it;
 *   - the sealed version says how many files and people it holds, and the example contract offered on the page really seals a table.
 */

import { randomBytes } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import { API_BASE, CONSOLE_URL } from "../config/ports";
import { bearerFor, loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial" });

const ENGINEER = "canary-engineer";
const CUSTODIAN = "canary-custodian";
const RESEARCHER = "canary-researcher";
const PART = 8 * 1024 * 1024;

async function registerDataset(name: string): Promise<string> {
  const org = await fetch(`${API_BASE}/organisation?tenant_id=canary`, { headers: await bearerFor(CUSTODIAN) }).then((r) => r.json());
  const department = org.departments.find((d: { name: string }) => d.name === "Verification").id;
  const response = await fetch(`${API_BASE}/datasets/register`, {
    method: "POST",
    headers: { ...(await bearerFor(ENGINEER)), "content-type": "application/json" },
    body: JSON.stringify({
      tenant_id: "canary",
      name,
      department_id: department,
      registered_by: ENGINEER,
      provenance: "internal_regulated",
      declared_class: "RAW",
      source_kind: "upload",
    }),
  });
  expect(response.status).toBe(201);
  return (await response.json()).id;
}

/** A real wav file: a header that says so, and noise for the sound. */
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

const csv = (text: string) => ({ name: "manifest.csv", mimeType: "text/csv", buffer: Buffer.from(text) });
const audio = (name: string, buffer: Buffer) => ({ name, mimeType: "audio/wav", buffer });

async function openLocking(page: Page, datasetId: string, what: "table" | "files") {
  await loginAs(page, ENGINEER);
  await page.goto(`/datasets/${datasetId}/ingest`);
  await page.getByTestId("erase-yes").check();
  await page.getByTestId(`bringing-${what}`).check();
}

test.describe("U139: locking what identifies a person, from the first question", () => {
  test("the question comes first, No is the ordinary upload, and Yes replaces it", async ({ page }) => {
    const datasetId = await registerDataset(`console-choice-${Date.now()}`);
    await loginAs(page, ENGINEER);
    await page.goto(`/datasets/${datasetId}/ingest`);

    const choice = page.getByTestId("erase-choice");
    await expect(choice).toContainText("Will you ever need to erase one person from this data?");
    await expect(choice).toContainText("Decide now: locking cannot be added after the upload.");
    await expect(page.getByTestId("erase-no")).toBeChecked();
    await expect(page.getByTestId("upload-files")).toBeVisible();
    await expect(page.getByTestId("locking-section")).toHaveCount(0);
    await expect(page.getByTestId("seal-submit")).toBeVisible();

    await page.getByTestId("erase-yes").check();
    await expect(choice).toContainText("hidden, not anonymised");
    await expect(page.getByTestId("upload-files")).toHaveCount(0);
    await expect(page.getByTestId("seal-submit")).toHaveCount(0);
    await expect(page.getByTestId("seal-table-contract")).toBeVisible();
    await expect(page.getByTestId("example-contract")).toHaveAttribute("href", "/privacy-contract-example.yaml");

    await page.getByTestId("bringing-files").check();
    await expect(page.getByTestId("files-panel")).toBeVisible();
    await expect(page.getByTestId("seal-table-contract")).toHaveCount(0);

    await page.getByTestId("erase-no").check();
    await expect(page.getByTestId("upload-files")).toBeVisible();
  });

  test("a researcher, who may not bring data in, is not asked the question", async ({ page }) => {
    const datasetId = await registerDataset(`console-choice-researcher-${Date.now()}`);
    await loginAs(page, RESEARCHER);
    await page.goto(`/datasets/${datasetId}/ingest`);
    await expect(page.getByTestId("erase-choice")).toHaveCount(0);
  });

  test("the example contract offered on the page seals a table", async ({ page }) => {
    const datasetId = await registerDataset(`console-example-${Date.now()}`);
    const example = await fetch(`${CONSOLE_URL}/privacy-contract-example.yaml`);
    expect(example.status).toBe(200);
    const contract = Buffer.from(await example.arrayBuffer());
    await openLocking(page, datasetId, "table");
    await page.getByTestId("seal-table-contract").setInputFiles({ name: "privacy-contract-example.yaml", mimeType: "application/x-yaml", buffer: contract });
    await page.getByTestId("seal-table-rows").setInputFiles(
      csv("appointment_id,chart_number,patient_name,postcode\nA1,HC-1,Invented One,D01\nA2,HC-2,Invented Two,D02\nA3,HC-1,Invented One,D01\n"),
    );
    await page.getByTestId("seal-table-submit").click();
    await expect(page.getByTestId("table-sealed")).toContainText("3 rows about 2 people", { timeout: 30_000 });
    await page.getByTestId("open-sealed-table").click();
    await expect(page.getByRole("heading", { name: "Locked columns" })).toBeVisible();
    await expect(page.getByTestId("locked-files")).toHaveCount(0);
  });

  test("everything wrong with the manifest and the files is listed before anything is sent", async ({ page }) => {
    const datasetId = await registerDataset(`console-manifest-${Date.now()}`);
    const sent: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/file-uploads")) sent.push(r.url());
    });
    await openLocking(page, datasetId, "files");

    await page.getByTestId("files-kind").fill("hospital chart number");
    await page.getByTestId("files-manifest").setInputFiles(csv("file,person\nvisit-1.wav,HC-1\nvisit-2.wav,\nvisit-3.wav,HC-3\nvisit-3.wav,HC-4\n"));
    await page.getByTestId("files-files").setInputFiles([
      audio("visit-1.wav", wav(1000)),
      audio("visit-5.wav", wav(1000)),
      { name: "visit-9.truth.json", mimeType: "application/json", buffer: Buffer.from("{}") },
      { name: "notes.mp3", mimeType: "audio/mpeg", buffer: Buffer.from("x") },
    ]);

    const problems = page.getByTestId("files-problems");
    await expect(problems).toContainText("visit-2.wav names no person");
    await expect(problems).toContainText("visit-3.wav appears more than once");
    await expect(problems).toContainText("visit-5.wav is not in the manifest");
    await expect(problems).toContainText("visit-9.truth.json is an answer key, and its file visit-9.wav");
    await expect(problems).toContainText("notes.mp3 is neither a file");
    await expect(problems).toContainText("visit-3.wav is in the manifest, and the file was not chosen");
    await expect(page.getByTestId("files-start")).toBeDisabled();
    expect(sent).toEqual([]);
  });

  test("a file goes up in parts, a failed part is sent again by itself, and the version says what it holds", async ({ page }) => {
    const datasetId = await registerDataset(`console-parts-${Date.now()}`);
    await openLocking(page, datasetId, "files");
    const patientA = `A-${Date.now()}`;
    const patientB = `B-${Date.now()}`;

    // The second part fails once, as a dropped connection would, and then goes through.
    let dropped = 0;
    await page.route("**/file-uploads/*/parts/2", async (route) => {
      if (dropped === 0) {
        dropped += 1;
        await route.abort("connectionreset");
      } else await route.continue();
    });

    await page.getByTestId("files-kind").fill("hospital chart number");
    await page.getByTestId("files-manifest").setInputFiles(csv(`file,person\nbig.wav,${patientA}\nsmall.wav,${patientB}\n`));
    await page.getByTestId("files-files").setInputFiles([
      audio("big.wav", wav(PART * 2 + 12_345)),
      audio("small.wav", wav(50_000)),
      { name: "big.truth.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify({ reference_transcript: "invented words", spans: [] })) },
    ]);
    await expect(page.getByTestId("files-problems")).toHaveCount(0);
    await page.getByTestId("files-start").click();

    const big = page.getByTestId("file-big.wav");
    await expect(big).toContainText("Sending");
    await expect(big).toContainText("Locked", { timeout: 90_000 });
    await expect(page.getByTestId("file-small.wav")).toContainText("Locked");
    await expect(page.getByTestId("file-big.truth.json")).toContainText("Locked");
    expect(dropped).toBe(1);

    await page.getByTestId("files-seal").click();
    await expect(page.getByTestId("files-sealed")).toContainText("2 files about 2 people", { timeout: 60_000 });
    await page.getByTestId("open-sealed-files").click();
    const held = page.getByTestId("locked-files");
    await expect(held).toBeVisible();
    await expect(held).toContainText("Files");
    await expect(held).toContainText("2");
    await expect(page.getByTestId("locked-files-erased")).toHaveText("0");
    // Neither the patient's identifier nor anything of the file's name beyond what the platform lists is on the page.
    await expect(page.locator("body")).not.toContainText(patientA);
  });

  test("a connection that stays down pauses the upload, keeps what was sent, and carrying on finishes it", async ({ page }) => {
    test.setTimeout(120_000);
    const datasetId = await registerDataset(`console-pause-${Date.now()}`);
    await openLocking(page, datasetId, "files");

    let down = true;
    await page.route("**/file-uploads/*/parts/2", async (route) => {
      if (down) await route.abort("connectionreset");
      else await route.continue();
    });
    await page.getByTestId("files-kind").fill("hospital chart number");
    await page.getByTestId("files-manifest").setInputFiles(csv(`file,person\nlong.wav,P-${Date.now()}\n`));
    await page.getByTestId("files-files").setInputFiles([audio("long.wav", wav(PART + 20_000))]);
    await page.getByTestId("files-start").click();

    const row = page.getByTestId("file-long.wav");
    // Four waits of 1, 2, 4 and 8 seconds, then it stops trying.
    await expect(row).toContainText("Paused", { timeout: 60_000 });
    await expect(row).toContainText("1 of 2 parts held");
    await expect(row).toContainText("The parts already sent are kept");
    await expect(row).not.toContainText("Failed to fetch");
    await expect(page.getByTestId("files-seal")).toHaveCount(0);

    down = false;
    await page.getByTestId("files-carry-on").click();
    await expect(row).toContainText("Locked", { timeout: 60_000 });
    await expect(page.getByTestId("files-seal")).toBeVisible();
  });
});
