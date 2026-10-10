/**
 * Captures the screenshots behind docs/public/walkthroughs/a-table-arrives-walkthrough.html (Story A, step one of the Health story).
 *
 * Drives the real console against the live stack as Devi (a data engineer in Cardiology) and Hartley (the custodian of Cardiology). Devi brings in two
 * ordinary tables, not about people: a table of hospital admissions and a small public lookup of diagnosis codes. The lookup is registered with a claim
 * that it is Published, and Hartley agrees with the claim. Nothing here is mocked: if a step cannot be reached, this fails rather than producing a
 * picture of something that did not happen.
 *
 * Needs the files from scripts/seed/make-story-a-files.py. Run it against a clean Health organisation (the datasets must not exist yet). WALKTHROUGH_ORG=canary
 * does a dry run in the test organisation, with its own people, and writes its pictures elsewhere, so the real story stays clean.
 *
 *   .venv\Scripts\python.exe scripts\seed\make-story-a-files.py
 *   npx playwright test --config=walkthroughs/playwright.config.ts story-a-1
 *   ..\.venv\Scripts\python.exe ..\docs\tools\build_a_table_arrives_walkthrough.py
 */

import { mkdirSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "../tests/auth-helpers";
import { settled } from "./settled";
import { snap } from "./snap";

const DRY = process.env.WALKTHROUGH_ORG === "canary";
const WHO = DRY
  ? { engineer: "canary-engineer", custodian: "canary-custodian", researcher: "canary-researcher", department: "Verification" }
  : { engineer: "eng-devi", custodian: "cust-hartley", researcher: "sam-researcher", department: "Cardiology" };
const SUFFIX = DRY ? `-${Date.now()}` : "";
const ADMISSIONS = `admissions${SUFFIX}`;
const LOOKUP = `diagnosis_codes${SUFFIX}`;

const DATA = join(process.cwd(), "walkthroughs", "shots", "_story-a-data");
const SHOTS = join(process.cwd(), "walkthroughs", "shots", DRY ? "_dry-a-table-arrives" : "a-table-arrives");

let step = 0;

async function shot(page: Page, name: string, fullPage = false): Promise<void> {
  await settled(page);
  step += 1;
  await snap(page, { path: join(SHOTS, `${String(step).padStart(2, "0")}-${name}.png`), fullPage });
}

async function bringIn(page: Page, name: string, file: string, claim: string | null): Promise<void> {
  await page.goto("/datasets/register");
  await page.getByTestId("register-name").fill(name);
  await page.getByTestId("register-department").selectOption({ label: WHO.department });
  if (claim) await page.getByTestId("register-class").selectOption({ label: claim });
  await shot(page, `${file}-the-registration`);
  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("upload-step")).toBeVisible();
  await expect(page.getByTestId("erase-no")).toBeChecked();
  await page.getByTestId("plain-table").check();
  await expect(page.getByTestId("plain-table-section")).toBeVisible();
  await page.getByTestId("seal-table-contract").setInputFiles(join(DATA, `${file}.contract.yaml`));
  await page.getByTestId("seal-table-rows").setInputFiles(join(DATA, `${file}.csv`));
  await shot(page, `${file}-the-table-and-its-contract`, true);
  await page.getByTestId("seal-table-submit").click();
  await expect(page.getByTestId("table-sealed")).toBeVisible({ timeout: 60_000 });
  await shot(page, `${file}-sealed`);
}

test("capture: a table arrives", async ({ page }) => {
  test.setTimeout(240_000);
  rmSync(SHOTS, { recursive: true, force: true });
  mkdirSync(SHOTS, { recursive: true });
  readFileSync(join(DATA, "admissions.csv"));

  const bearer = await bearerFor(WHO.engineer);
  const existing = await fetch(`${API_BASE}/datasets?q=${encodeURIComponent(DRY ? SUFFIX : "admissions")}&limit=200`, { headers: bearer }).then((r) => r.json());
  const clash = (existing.datasets ?? []).filter((d: { name: string }) => d.name === ADMISSIONS || d.name === LOOKUP);
  expect(clash, "the datasets already exist: this capture needs a clean organisation").toHaveLength(0);

  // ---- Devi brings in the admissions table ------------------------------
  await loginAs(page, WHO.engineer);
  await bringIn(page, ADMISSIONS, "admissions", null);
  await page.getByTestId("open-sealed-table").click();
  await expect(page).toHaveURL(/\/versions\/[0-9a-f-]{36}$/);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "admissions-the-version", true);

  // ---- and a public lookup, with a claim --------------------------------
  await bringIn(page, LOOKUP, "diagnosis_codes", "Published");

  // What a researcher can read before the custodian agrees: the claim changes nothing until it is agreed (asserted on the researcher's own screen).
  const sam = await bearerFor(WHO.researcher);
  const lookup = (await fetch(`${API_BASE}/datasets?q=${encodeURIComponent(LOOKUP)}`, { headers: sam }).then((r) => r.json())).datasets.find((d: { name: string }) => d.name === LOOKUP);
  const versions = await fetch(`${API_BASE}/datasets/${lookup.id}/versions`, { headers: sam }).then((r) => r.json());
  const lookupVersion: string = versions[0].dataset_version_id;
  await loginAs(page, WHO.researcher);
  await page.goto(`/versions/${lookupVersion}`);
  await expect(page.getByTestId("request-access")).toBeVisible();
  const before = await fetch(`${API_BASE}/datasets?q=${encodeURIComponent(LOOKUP)}`, { headers: sam }).then((r) => r.json());
  const view = (r: any) => JSON.stringify((r.datasets ?? []).filter((d: { name: string }) => d.name === LOOKUP).map((d: any) => Object.fromEntries(Object.entries(d).filter(([k]) => /read|level|class|visible|open|access/i.test(k)))));
  console.log("BEFORE AGREEMENT, as the researcher:", view(before));

  // ---- Hartley agrees with the claim ------------------------------------
  await loginAs(page, WHO.custodian);
  await page.goto("/");
  const waiting = page.getByTestId("awaiting-confirmation");
  await expect(waiting).toContainText(LOOKUP);
  await shot(page, "hartley-sees-the-claim");
  await waiting.locator("li", { hasText: LOOKUP }).getByRole("button", { name: "Agree with this" }).click();
  await expect(page.getByText(`Agreed with ${LOOKUP}'s claim.`)).toBeVisible();
  await shot(page, "hartley-has-agreed");

  await loginAs(page, WHO.researcher);
  await page.goto(`/versions/${lookupVersion}`);
  await settled(page);
  await expect(page.getByTestId("request-access")).toHaveCount(0);
  const after = await fetch(`${API_BASE}/datasets?q=${encodeURIComponent(LOOKUP)}`, { headers: sam }).then((r) => r.json());
  console.log("AFTER AGREEMENT, as the researcher:", view(after));
});
