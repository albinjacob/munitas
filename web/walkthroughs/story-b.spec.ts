/**
 * Captures the screenshots of Story B of the Health walkthroughs, "The people": patient data that must be erasable arrives locked, the same
 * three patients in three recordings and in a table of clinic appointments (docs/public/walkthroughs/people-arrive-walkthrough.html and the pages after it).
 *
 * Drives the real console against the live Health organisation as Devi (a data engineer), Sam (a researcher), Hartley (the custodian of Cardiology),
 * Imani (the de-identification reviewer), Marlow (a privacy auditor) and Nakamura (the data protection officer). Nothing here is mocked: if a step cannot be
 * reached, it fails rather than producing a picture of something that did not happen. The recordings are the three consultations in docs/audio-samples
 * (made with a text-to-speech voice, so no real person is in them) and the table's rows are invented; both are written by scripts/seed/make-story-b-files.py.
 *
 * PART picks what runs, and the parts are meant to run in order, one after another, started by scripts/walkthroughs/capture-health-story.py. What one part
 * makes (a dataset, a version) is kept in walkthroughs/shots/_story-b-state.json for the next.
 *
 *   $env:STORY_B_PART = "recordings"; npx playwright test --config=walkthroughs/playwright.config.ts story-b
 *
 * Parts: recordings, table, rows, files, remove-names, evidence.
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { loginAs } from "../tests/auth-helpers";
import { settled } from "./settled";
import { snap } from "./snap";

const PART = process.env.STORY_B_PART ?? "";
const DATA = join(process.cwd(), "walkthroughs", "shots", "_story-b-data");
const STATE = join(process.cwd(), "walkthroughs", "shots", "_story-b-state.json");
const SHOTS = join(process.cwd(), "walkthroughs", "shots", "people-arrive");

const DEVI = "eng-devi";
const SAM = "sam-researcher";
const HARTLEY = "cust-hartley";
const IMANI = "rev-imani";
const MARLOW = "aud-marlow";
const NAKAMURA = "dpo-nakamura";
const KIND = "patient number";

const RECORDINGS = "consultation-recordings";
const APPOINTMENTS = "clinic-appointments";

function state(): Record<string, string> {
  return existsSync(STATE) ? JSON.parse(readFileSync(STATE, "utf8")) : {};
}
function remember(values: Record<string, string>): void {
  writeFileSync(STATE, JSON.stringify({ ...state(), ...values }, null, 2), "utf8");
}

async function shot(page: Page, folder: string, number: string, name: string, fullPage = false): Promise<void> {
  await settled(page);
  const dir = join(process.cwd(), "walkthroughs", "shots", folder);
  mkdirSync(dir, { recursive: true });
  await snap(page, { path: join(dir, `${number}-${name}.png`), fullPage });
}

/** Puts the named part of a page at the top of the picture, so the whole of it is in view. */
async function shotAt(page: Page, folder: string, number: string, testid: string, name: string, offset = 40): Promise<void> {
  await settled(page);
  await page.getByTestId(testid).first().evaluate((e, y) => {
    const top = e.getBoundingClientRect().top + window.scrollY - y;
    window.scrollTo(0, Math.max(0, top));
  }, offset);
  await page.waitForTimeout(250);
  const dir = join(process.cwd(), "walkthroughs", "shots", folder);
  mkdirSync(dir, { recursive: true });
  await snap(page, { path: join(dir, `${number}-${name}.png`) });
}

async function headingAt(page: Page, folder: string, number: string, heading: string, name: string, offset = 60): Promise<void> {
  await settled(page);
  await page.getByRole("heading", { name: heading, exact: true }).first().evaluate((e, y) => {
    const top = e.getBoundingClientRect().top + window.scrollY - y;
    window.scrollTo(0, Math.max(0, top));
  }, offset);
  await page.waitForTimeout(250);
  const dir = join(process.cwd(), "walkthroughs", "shots", folder);
  mkdirSync(dir, { recursive: true });
  await snap(page, { path: join(dir, `${number}-${name}.png`) });
}

const file = (name: string, folder: string, type: string) => ({ name, mimeType: type, buffer: readFileSync(join(DATA, folder, name)) });
const csv = (name: string, text: string) => ({ name, mimeType: "text/csv", buffer: Buffer.from(text) });
const noise = (name: string) => ({ name, mimeType: "audio/wav", buffer: Buffer.concat([Buffer.from("RIFF"), Buffer.alloc(40, 1)]) });

async function register(page: Page, name: string, folder: string, number: string): Promise<void> {
  await page.goto("/datasets/register");
  await page.getByTestId("register-name").fill(name);
  await page.getByTestId("register-department").selectOption({ label: "Cardiology" });
  await shot(page, folder, number, "devi-registers-the-dataset");
  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("upload-step")).toBeVisible();
}

test("part recordings: three recordings arrive locked, one key for each patient", async ({ page }) => {
  test.skip(PART !== "recordings");
  test.setTimeout(420_000);
  const dir = "people-arrive";
  await loginAs(page, DEVI);
  await register(page, RECORDINGS, dir, "01");

  await expect(page.getByTestId("erase-choice")).toBeVisible();
  await expect(page.getByTestId("erase-no")).toBeChecked();
  await shotAt(page, dir, "02", "erase-choice", "devi-is-asked-whether-anyone-may-need-erasing");

  await page.getByTestId("erase-yes").check();
  await page.getByTestId("bringing-files").check();
  await expect(page.getByTestId("files-panel")).toBeVisible();
  await shotAt(page, dir, "03", "erase-choice", "devi-chooses-recordings");

  // A manifest and a set of files with several mistakes: all of them are listed before a single byte is sent.
  await page.getByTestId("files-kind").fill(KIND);
  await page.getByTestId("files-manifest").setInputFiles(csv("manifest.csv", "file,person\nvisit-1.wav,HC-48213\nvisit-2.wav,\nvisit-3.wav,HC-33912\n"));
  await page.getByTestId("files-files").setInputFiles([noise("visit-1.wav"), noise("visit-4.wav"), csv("visit-9.truth.json", "{}")]);
  await expect(page.getByTestId("files-problems")).toContainText("visit-2.wav names no person");
  await shotAt(page, dir, "04", "files-panel", "every-mistake-is-listed-before-anything-is-sent");

  // The right manifest and the right files: three recordings and each one's answer key.
  await page.getByTestId("files-manifest").setInputFiles(file("manifest.csv", "recordings", "text/csv"));
  await page.getByTestId("files-files").setInputFiles([
    file("visit-1.wav", "recordings", "audio/wav"), file("visit-2.wav", "recordings", "audio/wav"), file("visit-3.wav", "recordings", "audio/wav"),
    file("visit-1.truth.json", "recordings", "application/json"), file("visit-2.truth.json", "recordings", "application/json"),
    file("visit-3.truth.json", "recordings", "application/json"),
  ]);
  await expect(page.getByTestId("files-problems")).toHaveCount(0);
  await expect(page.getByTestId("files-start")).toBeEnabled();
  await shotAt(page, dir, "05", "files-panel", "the-files-are-ready");

  await page.getByTestId("files-start").click();
  for (const name of ["visit-1.wav", "visit-2.wav", "visit-3.wav", "visit-1.truth.json", "visit-2.truth.json", "visit-3.truth.json"]) {
    await expect(page.getByTestId(`file-${name}`)).toContainText("Locked", { timeout: 180_000 });
  }
  await shotAt(page, dir, "06", "files-panel", "every-file-is-locked-under-its-persons-key");

  await page.getByTestId("files-seal").click();
  await expect(page.getByTestId("files-sealed")).toContainText("about 3 people", { timeout: 90_000 });
  await shotAt(page, dir, "07", "files-sealed", "the-recordings-are-sealed", 200);

  await page.getByTestId("open-sealed-files").click();
  await expect(page.getByTestId("locked-files")).toBeVisible();
  const version = page.url().split("/versions/")[1];
  remember({ recordingsVersion: version });
  await expect(page.locator("body")).not.toContainText("HC-48213");
  await page.setViewportSize({ width: 1920, height: 305 });
  await headingAt(page, dir, "08", "Locked files", "the-version-says-what-it-holds", 40);
});

test("part table: the same patients arrive in a table, with what identifies them locked", async ({ page }) => {
  test.skip(PART !== "table");
  test.setTimeout(240_000);
  const dir = "people-arrive";
  await loginAs(page, DEVI);
  await register(page, APPOINTMENTS, dir, "09");
  await expect(page.getByTestId("erase-choice")).toBeVisible();
  await page.getByTestId("erase-yes").check();
  await expect(page.getByTestId("locking-section")).toBeVisible();
  await shotAt(page, dir, "10", "erase-choice", "devi-opens-the-table-option");

  const rows = readFileSync(join(DATA, "appointments", "appointments.csv"), "utf8");
  const broken = rows.replace("2026-09-03", "not a date").replace("HC-77044,Priya Nair", ",Priya Nair");
  await page.getByTestId("seal-table-contract").setInputFiles(file("contract.yaml", "appointments", "application/x-yaml"));
  await page.getByTestId("seal-table-rows").setInputFiles({ name: "appointments-with-mistakes.csv", mimeType: "text/csv", buffer: Buffer.from(broken) });
  await page.getByTestId("seal-table-submit").click();
  await expect(page.getByTestId("seal-table-refused")).toBeVisible();
  await shotAt(page, dir, "11", "erase-choice", "a-bad-file-is-refused");

  await page.getByTestId("seal-table-rows").setInputFiles(file("appointments.csv", "appointments", "text/csv"));
  await page.getByTestId("seal-table-submit").click();
  await expect(page.getByTestId("table-sealed")).toBeVisible({ timeout: 60_000 });
  await shotAt(page, dir, "12", "erase-choice", "the-table-is-sealed");

  await page.getByTestId("open-sealed-table").click();
  await expect(page.getByRole("heading", { name: "Locked columns" })).toBeVisible();
  remember({ appointmentsVersion: page.url().split("/versions/")[1] });
  await headingAt(page, dir, "13", "Locked columns", "the-locked-columns", 24);
});

// ---------------------------------------------------------------------------------------------------------------------------------------------
// B2: Devi checks what was brought in. A few rows and a few files, opened only with an approver's say, for a stated purpose and a short time.
// ---------------------------------------------------------------------------------------------------------------------------------------------
const ROWS_PURPOSE = "to see what the postcode column really holds before I write the rule that locks it";
const FILES_PURPOSE = "listen to the recordings to judge how long a consultation runs";

test("part rows: Devi asks to read five locked rows, and Hartley decides", async ({ page }) => {
  test.skip(PART !== "rows");
  test.setTimeout(240_000);
  const dir = "check-what-arrived";
  const versionUrl = `/versions/${state().appointmentsVersion}`;

  await loginAs(page, DEVI);
  await page.goto(`/versions/${state().appointmentsVersion}/locked-rows`);
  await expect(page.getByTestId("failure")).toBeVisible();
  await shot(page, dir, "01", "devi-is-refused-the-rows");

  await page.goto(versionUrl);
  await expect(page.getByTestId("ask-lease")).toBeVisible();
  await page.getByTestId("lease-purpose").fill(ROWS_PURPOSE);
  await page.getByTestId("lease-days").fill("3");
  await page.getByTestId("lease-rows").fill("5");
  await headingAt(page, dir, "02", "The locked values", "devi-asks-for-five-rows");
  await page.getByTestId("lease-submit").click();
  await expect(page.getByTestId("locked-access")).toContainText("waiting for an approver");
  await headingAt(page, dir, "03", "The locked values", "the-request-waits");

  await loginAs(page, SAM);
  await page.goto(versionUrl);
  await expect(page.getByTestId("locked-access")).toContainText("cannot read the locked values and cannot ask");
  await headingAt(page, dir, "04", "The locked values", "sam-has-no-way-to-ask");

  await loginAs(page, HARTLEY);
  await page.goto("/locked-column-access");
  await expect(page.getByTestId("leases-to-decide")).toContainText(ROWS_PURPOSE);
  await shot(page, dir, "05", "hartley-sees-the-request");
  const row = page.getByTestId("leases-to-decide").locator("tr", { hasText: ROWS_PURPOSE });
  await row.locator("input").fill("Five rows is enough for that.");
  await shot(page, dir, "06", "hartley-writes-a-note");
  await row.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("leases-granted")).toContainText("In force");
  await shot(page, dir, "07", "the-lease-is-in-force");

  await page.goto(versionUrl);
  await expect(page.getByTestId("group-size-finding")).toBeVisible();
  await headingAt(page, dir, "08", "Small groups", "hartley-sees-the-small-groups");
  await page.getByTestId("group-size-note").fill("The next contract version will lock the postcode.");
  await page.getByTestId("group-size-submit").click();
  await expect(page.getByTestId("group-size-decided")).toBeVisible();
  await headingAt(page, dir, "09", "Small groups", "hartley-records-a-decision");

  await loginAs(page, DEVI);
  await page.goto(versionUrl);
  await expect(page.getByTestId("locked-access")).toContainText("You hold a lease");
  await headingAt(page, dir, "10", "The locked values", "devi-holds-a-lease");
  await page.getByTestId("read-locked-rows").click();
  await expect(page.getByTestId("locked-rows").locator("tbody tr")).toHaveCount(5);
  await shot(page, dir, "11", "devi-reads-five-rows");
});

test("part files: Devi asks to open the recordings, Hartley allows fewer, and the access ends", async ({ page }) => {
  test.skip(PART !== "files");
  test.setTimeout(240_000);
  const dir = "check-what-arrived";
  const versionUrl = `/versions/${state().recordingsVersion}`;

  await loginAs(page, DEVI);
  await page.goto(versionUrl);
  await expect(page.getByTestId("file-lease-form")).toBeVisible();
  const kinds = page.getByTestId("file-lease-kind-console");
  if (await kinds.count()) await kinds.check();
  await page.getByTestId("file-lease-purpose").fill(FILES_PURPOSE);
  await page.getByTestId("file-lease-records").fill("3");
  await page.getByTestId("file-lease-hours").fill("1");
  await shotAt(page, dir, "12", "file-lease-panel", "devi-asks-to-open-the-recordings");
  await page.getByTestId("file-lease-submit").click();
  await expect(page.getByTestId("file-lease-waiting-console")).toBeVisible({ timeout: 15_000 });
  await shotAt(page, dir, "13", "file-lease-panel", "the-request-waits-for-an-approver");

  await loginAs(page, HARTLEY);
  await page.goto("/locked-column-access");
  const waiting = page.getByTestId("file-leases-to-decide");
  await expect(waiting).toContainText(FILES_PURPOSE);
  await shotAt(page, dir, "14", "file-leases-to-decide", "hartley-sees-the-request-to-open-files", 90);
  const row = waiting.locator("tr", { hasText: FILES_PURPOSE });
  await row.locator('[data-testid^="file-allow-records-"]').fill("2");
  await row.locator('[data-testid^="file-note-"]').fill("Two of the three are enough to judge their length.");
  await shotAt(page, dir, "15", "file-leases-to-decide", "hartley-allows-two-of-the-three", 90);
  await row.locator('[data-testid^="file-approve-"]').click();
  await expect(page.getByTestId("file-leases-granted")).toContainText(FILES_PURPOSE, { timeout: 15_000 });
  await shotAt(page, dir, "16", "file-leases-granted", "the-access-is-in-force", 90);

  await loginAs(page, DEVI);
  await page.goto(versionUrl);
  const files = page.getByTestId("record-files");
  await expect(files).toBeVisible({ timeout: 15_000 });
  await expect(files.locator('[data-testid^="record-row-"]')).toHaveCount(2);
  await shotAt(page, dir, "17", "record-files", "devi-sees-two-recordings");
  await files.locator('[data-testid^="record-play-"]').first().click();
  const player = files.locator('[data-testid^="record-player-"]').first();
  await expect(player).toBeVisible();
  await expect.poll(() => player.evaluate((a: HTMLAudioElement) => a.duration), { timeout: 15_000 }).toBeGreaterThan(0);
  await shotAt(page, dir, "18", "record-files", "devi-plays-one");

  await loginAs(page, HARTLEY);
  await page.goto("/locked-column-access");
  const granted = page.getByTestId("file-leases-granted").locator("tr", { hasText: FILES_PURPOSE });
  await granted.locator('[data-testid^="file-end-"]').click();
  await expect(page.getByTestId("file-leases-granted").locator("tr", { hasText: FILES_PURPOSE })).toHaveCount(0, { timeout: 15_000 });

  await loginAs(page, DEVI);
  await page.goto(versionUrl);
  await expect(page.getByTestId("record-files-note")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('[data-testid^="record-play-"]')).toHaveCount(0);
  await shotAt(page, dir, "19", "record-files", "when-the-access-ends-the-files-close");
});

// ---------------------------------------------------------------------------------------------------------------------------------------------
// B3: names are removed from the recordings by the built-in pipeline, and the de-identification reviewer decides. Real speech work on the graphics card.
// ---------------------------------------------------------------------------------------------------------------------------------------------
test("part remove-names: the pipeline runs over the locked recordings, and Imani decides", async ({ page }) => {
  test.skip(PART !== "remove-names");
  test.setTimeout(1_500_000);
  const dir = "remove-names-story";
  const versionUrl = `/versions/${state().recordingsVersion}`;

  await loginAs(page, DEVI);
  await page.goto(versionUrl);
  await expect(page.getByTestId("version-pipeline-start")).toBeVisible({ timeout: 20_000 });
  await shotAt(page, dir, "01", "version-pipeline-start", "devi-chooses-to-remove-names", 160);
  await expect(async () => {
    await page.getByTestId("version-pipeline-start").click();
    await expect(page).toHaveURL(/\/pipeline-runs\/[0-9a-f-]{36}$/, { timeout: 6000 });
  }).toPass({ timeout: 60_000, intervals: [1000] });
  const runUrl = page.url();
  const runId = runUrl.split("/pipeline-runs/")[1];
  remember({ pipelineRun: runId });
  await shot(page, dir, "02", "the-run-has-started");

  // The run does real work on the graphics card: wait for it to finish by asking the platform, not by watching the page.
  const { bearerFor } = await import("../tests/auth-helpers");
  const { API_BASE } = await import("../config/ports");
  const headers = await bearerFor(DEVI);
  const began = Date.now();
  let status = "running";
  while (Date.now() - began < 1_200_000) {
    status = (await fetch(`${API_BASE}/pipeline-runs/${runId}`, { headers }).then((r) => r.json())).status;
    if (status !== "running") break;
    await new Promise((r) => setTimeout(r, 15_000));
  }
  expect(status, "the pipeline run did not succeed").toBe("succeeded");
  await page.goto(runUrl);
  await expect(page.getByTestId("pipeline-run-status")).toContainText("Finished", { timeout: 20_000 });
  await shot(page, dir, "03", "the-run-has-finished");

  await page.getByRole("link", { name: "Open the decision" }).click();
  await expect(page).toHaveURL(/\/gates\/[0-9a-f-]{36}$/);
  await expect(page.getByTestId("gate-blocked")).toContainText("You started this run");
  const gateUrl = page.url();
  remember({ gate: gateUrl });
  await shot(page, dir, "04", "devi-cannot-decide-their-own-run", true);

  await loginAs(page, HARTLEY);
  await page.goto(gateUrl);
  await expect(page.getByTestId("gate-blocked")).toContainText("not part of your role");
  await shot(page, dir, "05", "hartley-cannot-decide-either", true);

  await loginAs(page, IMANI);
  await page.goto(gateUrl);
  await expect(page.getByTestId("gate-reason")).toBeVisible();
  await shot(page, dir, "06", "imani-opens-the-decision", true);
  await page.getByTestId("gate-reason").fill("The names, dates and addresses that the answer keys mark were all removed from the transcripts. Releasing the de-identified recordings and transcripts.");
  await shot(page, dir, "07", "imani-writes-a-reason");
  await page.getByTestId("gate-promote").click();
  await expect(page.getByTestId("gate-state")).toContainText(/Released|Promoted|Cleared/i, { timeout: 30_000 });
  await afterRelease(page, dir);
});

const REDACTED = "consultation-recordings-redacted";

/** Imani's page after the decision, then what a researcher sees of the released result. */
async function afterRelease(page: Page, dir: string): Promise<void> {
  await page.getByRole("heading", { name: "The decision", exact: true }).scrollIntoViewIfNeeded();
  await shot(page, dir, "08", "released");

  await loginAs(page, SAM);
  await page.goto("/datasets");
  await page.getByTestId("dataset-search").fill(REDACTED);
  await expect(page.getByRole("row").filter({ hasText: REDACTED }).first()).toContainText(/\d+ of \d+/);
  await shot(page, dir, "09", "sam-finds-the-released-result");
}

test("part after-release: Imani's page after the decision, and what Sam sees of the result", async ({ page }) => {
  test.skip(PART !== "after-release");
  await loginAs(page, IMANI);
  await page.goto(state().gate);
  await expect(page.getByTestId("gate-state")).toContainText(/Released/i);
  await afterRelease(page, "remove-names-story");
});

// ---------------------------------------------------------------------------------------------------------------------------------------------
// B4: the privacy auditor and the data protection officer read the evidence of everything above, and see no name.
// ---------------------------------------------------------------------------------------------------------------------------------------------
test("part evidence: Marlow and Nakamura read how locked data was used", async ({ page }) => {
  test.skip(PART !== "evidence");
  test.setTimeout(240_000);
  const dir = "evidence-without-names";
  // The four pictures belong together: they show one moment of the audit. They are taken into a holding folder and moved into place only when every check has
  // passed, so a run that fails (and this one fails by design when other activity has pushed the story's key lines off the first page) leaves the earlier set whole.
  const holding = `${dir}/.pending`;
  rmSync(join(process.cwd(), "walkthroughs", "shots", holding), { recursive: true, force: true });

  await loginAs(page, MARLOW);
  await page.goto("/locking-audit");
  await expect(page.getByTestId("audit-reads")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Keys", exact: true }).first()).toBeVisible();

  // One person, one key: a person code that the table's rows were read under is also the code the pipeline opened a recording under. Checked first, before any
  // picture is taken.
  const lines: { code: string; text: string }[] = await page.evaluate(() =>
    [...document.querySelectorAll("a, button, span, code")]
      .filter((a) => a.children.length === 0 && /^T-[0-9a-f]+$/.test((a.textContent ?? "").trim()))
      .map((a) => {
        let el: HTMLElement | null = a;
        while (el && !(el.innerText ?? "").includes("A key was opened")) el = el.parentElement;
        return { code: a.textContent ?? "", text: el?.innerText ?? "" };
      }));
  console.log("KEYLINES", lines.length, JSON.stringify(lines.map((l) => [l.code.slice(0, 8), /clinic-appointments/.test(l.text), /pipeline_run|action_run|consultation-recordings/.test(l.text)])));
  const inTable = new Set(lines.filter((l) => l.text.includes("clinic-appointments")).map((l) => l.code));
  const inRecordings = new Set(lines.filter((l) => /pipeline_run|action_run|consultation-recordings/.test(l.text)).map((l) => l.code));
  expect([...inTable].filter((c) => inRecordings.has(c)).length, "no person code is shared by the table and the recordings: run this part straight after the rest of the story, before other captures add activity").toBeGreaterThan(0);

  await shot(page, holding, "01", "marlow-reads-the-audit");
  await headingAt(page, holding, "02", "How each table's columns were treated", "marlow-sees-how-columns-were-treated");
  await headingAt(page, holding, "03", "Keys", "marlow-sees-the-keys");

  await loginAs(page, NAKAMURA);
  await page.goto("/locking-audit");
  await expect(page.getByTestId("audit-reads")).toBeVisible();
  await shot(page, holding, "04", "nakamura-reads-the-same-audit");
  await expect(page.locator("body")).not.toContainText("Arjun");
  await expect(page.locator("body")).not.toContainText("Priya");
  await expect(page.locator("body")).not.toContainText("Rahul");

  // Everything passed: the set replaces the earlier one in one go.
  const from = join(process.cwd(), "walkthroughs", "shots", holding);
  const to = join(process.cwd(), "walkthroughs", "shots", dir);
  for (const file of readdirSync(from)) renameSync(join(from, file), join(to, file));
  rmSync(from, { recursive: true, force: true });
});
