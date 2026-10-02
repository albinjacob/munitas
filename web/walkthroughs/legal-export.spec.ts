/**
 * Captures the screenshots and the recipient's terminal behind
 * docs/public/walkthroughs/legal-export-walkthrough.html.
 *
 * Drives the real console against the live stack, signing in as four people: two platform administrators, the
 * custodian a legal hold names, and (in a terminal) the person who receives the package. Nothing is mocked. The
 * clinic is closed down and a legal hold put on it through the API first, because that is the previous
 * walkthrough's story and this one starts after it. The days between are skipped by
 * scripts/admin/advance-closing-clock.py, which changes nothing else.
 *
 * The terminal part is real too: the package is downloaded through the link the console made and opened with
 * scripts/client/open_legal_package.py, and what that printed is what the page shows. The link and the
 * passphrase are replaced by placeholders in the commands shown, because they are secrets, and they are
 * the real values in the commands that ran.
 *
 * Needs Harbour Clinic open and holding data. Rebuild it first:
 *   .\scripts\seed\reseed-tenant.ps1 -Tenant harbour -Force
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts legal-export
 *   ..\.venv\Scripts\python.exe ..\docs\tools\build_legal_export_walkthrough.py
 */

import { execFileSync } from "node:child_process";
import { mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "../tests/auth-helpers";
import { settled } from "./settled";

const SHOTS = join(process.cwd(), "walkthroughs", "shots", "legal-export");
const PYTHON = join(process.cwd(), "..", ".venv", "Scripts", "python.exe");
const CLOCK = join(process.cwd(), "..", "scripts", "admin", "advance-closing-clock.py");
const TOOL = join(process.cwd(), "..", "scripts", "client", "open_legal_package.py");

const TENANT = "harbour";
const CUSTODIAN = "cust-dunmore";
const RECORDS_OFFICER = "dpo-adeyemi";
const ADMIN_A = "ops-priya";
const ADMIN_B = "ops-ravi";

let step = 0;

async function shot(page: Page, name: string): Promise<void> {
  await settled(page);
  // A notice from the step before is gone after about 2.6 seconds, and a picture with the last step's notice in it
  // would show something that did not just happen.
  await page.waitForTimeout(2800);
  step += 1;
  await page.screenshot({ path: join(SHOTS, `${String(step).padStart(2, "0")}-${name}.png`) });
}

async function call(who: string, path: string, body?: unknown): Promise<any> {
  const headers = { ...(await bearerFor(who)), "content-type": "application/json" };
  const r = await fetch(`${API_BASE}${path}`, { method: body === undefined ? "GET" : "POST", headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) throw new Error(`${path} as ${who}: HTTP ${r.status} ${await r.text()}`);
  return r.json();
}

function tree(dir: string): string {
  const out: string[] = [];
  const walk = (d: string) => {
    for (const name of readdirSync(d).sort()) {
      const p = join(d, name);
      if (statSync(p).isDirectory()) walk(p);
      else out.push(`${relative(dir, p).replaceAll("\\", "/")}   ${statSync(p).size} bytes`);
    }
  };
  walk(dir);
  return out.join("\n");
}

test("capture: records are produced for a legal matter, then opened by the recipient", async ({ page }) => {
  test.setTimeout(300_000);
  mkdirSync(SHOTS, { recursive: true });

  // ---- The story so far, through the API: the clinic is closed down and a legal hold is in force -------------
  const phase = (await call(ADMIN_A, "/lifecycle/organisations")).organisations.find((o: any) => o.tenant_id === TENANT);
  if (!phase || phase.phase !== "active") {
    throw new Error("Harbour Clinic is not open. Rebuild it: .\\scripts\\seed\\reseed-tenant.ps1 -Tenant harbour -Force");
  }
  await call(CUSTODIAN, "/lifecycle/organisation/retire", { reason: "Harbour Clinic is winding down. The last patient was seen on 30 September." });
  execFileSync(PYTHON, [CLOCK, "--tenant", TENANT, "--end-retiring"], { stdio: "pipe" });
  const hold = await call(ADMIN_A, "/lifecycle/holds", {
    tenant_id: TENANT, matter_name: "Alder v Harbour Clinic", matter_number: "HC-2026-0417",
    description: "A patient claim about a procedure carried out in 2024.",
    triggering_event: "Letter before claim received on 1 October 2026",
    issuing_authority: "Aldous and Brennan LLP, for the claimant", authority_reference: "AB/2026/17",
    attorney_name: "Ruth Aldous", attorney_email: "ruth.aldous@aldousbrennan.example", notice_received_on: "2026-10-01",
    preserve: "Every record of the claimant, and the audit trail of who read them.", custodian_id: RECORDS_OFFICER,
  });
  await call(ADMIN_B, `/lifecycle/holds/${hold.id}/decide`, { approve: true, note: "Notice checked against the issuing firm's reference." });
  await call(RECORDS_OFFICER, `/lifecycle/holds/${hold.id}/acknowledge`, {});

  // ---- Part one: a platform administrator asks for the records -------------------------------------------
  await loginAs(page, ADMIN_A);
  await page.goto("/legal-holds");
  const card = page.locator('[data-hold="HC-2026-0417"]');
  await expect(card.getByTestId("ask-for-records")).toBeVisible();
  await card.evaluate((e) => e.scrollIntoView({ block: "start" }));
  await shot(page, "priya-finds-the-hold-in-force");

  await card.getByTestId("ask-for-records").click();
  await card.getByTestId("export-authority").fill("High Court, King's Bench Division");
  await card.getByTestId("export-reference").fill("KB-2026-004411");
  await card.getByTestId("export-demanded-on").fill("2026-10-05");
  await card.getByTestId("export-text").fill("Disclosure of the appointment records of the claimant, Ms Alder, and the log of who read them.");
  await card.getByTestId("export-recipient").fill("Ruth Aldous");
  await card.getByTestId("export-recipient-org").fill("Aldous and Brennan LLP");
  await card.getByTestId("export-recipient-email").fill("ruth.aldous@aldousbrennan.example");
  await expect(card.getByTestId("export-datasets")).toContainText("appointments");
  await card.getByTestId("export-datasets").getByText("appointments").click();
  await card.getByTestId("export-filter-appointments").selectOption("patient_id");
  await card.getByTestId("export-form").evaluate((e) => e.scrollIntoView({ block: "start" }));
  await shot(page, "priya-fills-in-the-demand");

  await card.getByTestId("export-submit").click();
  const exportCard = page.locator('[data-export="KB-2026-004411"]');
  await expect(exportCard).toContainText("Waiting for a second administrator");
  await exportCard.evaluate((e) => e.scrollIntoView({ block: "center" }));
  await shot(page, "the-request-waits");

  await exportCard.getByTestId("export-approve").click();
  await expect(exportCard.getByTestId("failure")).toContainText("different platform administrator");
  await shot(page, "priya-cannot-approve-her-own");

  // ---- Part two: a second administrator approves ---------------------------------------------------------
  await loginAs(page, ADMIN_B);
  await page.goto("/legal-holds");
  const ravisCard = page.locator('[data-export="KB-2026-004411"]');
  await expect(ravisCard).toContainText("Waiting for a second administrator");
  await ravisCard.getByTestId("export-note").fill("Demand checked against the court's reference.");
  await ravisCard.evaluate((e) => e.scrollIntoView({ block: "center" }));
  await shot(page, "ravi-reads-the-demand");

  await ravisCard.getByTestId("export-approve").click();
  await expect(ravisCard).toContainText("Waiting for the custodian to confirm the scope");
  await shot(page, "ravi-approves");

  // ---- Part three: the custodian confirms the scope, and is given the passphrase -------------------------
  await loginAs(page, RECORDS_OFFICER);
  const custodianCard = page.locator('[data-testid="custodian-exports"] [data-export="KB-2026-004411"]');
  await expect(custodianCard).toContainText("Waiting for the custodian to confirm the scope");
  await expect(custodianCard).toContainText("appointments");
  await page.getByTestId("custodian-exports").evaluate((e) => e.scrollIntoView({ block: "start" }));
  await shot(page, "adeyemi-is-asked-to-confirm-the-scope");

  await custodianCard.getByTestId("filter-values-appointments").fill("P-4471");
  await custodianCard.getByTestId("check-filter").click();
  await expect(custodianCard.getByTestId("filter-preview")).toContainText("2 of 10 rows match");
  await custodianCard.getByTestId("confirm-note").fill("Ms Alder's patient id, and nothing more.");
  await page.getByTestId("custodian-exports").evaluate((e) => e.scrollIntoView({ block: "start" }));
  await shot(page, "adeyemi-names-the-claimant");

  await custodianCard.getByTestId("confirm-scope").click();
  await expect(custodianCard.getByTestId("read-passphrase")).toBeVisible({ timeout: 90_000 });
  await page.getByTestId("custodian-exports").evaluate((e) => e.scrollIntoView({ block: "start" }));
  await shot(page, "the-package-is-ready");

  await custodianCard.getByTestId("read-passphrase").click();
  await expect(custodianCard.getByTestId("export-passphrase")).toBeVisible();
  const passphrase = (await custodianCard.getByTestId("export-passphrase").locator("div").first().innerText()).trim();
  await page.getByTestId("custodian-exports").evaluate((e) => e.scrollIntoView({ block: "start" }));
  await shot(page, "adeyemi-reads-the-passphrase");

  // ---- Part four: a platform administrator makes the download link ---------------------------------------
  await loginAs(page, ADMIN_A);
  await page.goto("/legal-holds");
  const readyCard = page.locator('[data-export="KB-2026-004411"]');
  await expect(readyCard).toContainText("Ready");
  await readyCard.getByRole("button", { name: "Show the list of files" }).click();
  await expect(readyCard.getByTestId("export-manifest")).toBeVisible();
  await readyCard.evaluate((e) => e.scrollIntoView({ block: "center" }));
  await shot(page, "priya-sees-what-the-package-holds");

  await readyCard.getByTestId("export-make-link").click();
  await expect(readyCard.getByTestId("export-link")).toBeVisible();
  const url = (await readyCard.getByTestId("export-link").locator("div").first().innerText()).trim();
  await readyCard.evaluate((e) => e.scrollIntoView({ block: "center" }));
  await shot(page, "priya-makes-the-link");

  // ---- Part five: the recipient, in a terminal ------------------------------------------------------------
  const work = join(process.cwd(), "walkthroughs", "shots", "legal-export", "recipient");
  mkdirSync(work, { recursive: true });
  const key = await fetch(`${API_BASE}/legal-exports/signing-key`).then((r) => r.json());
  const download = await fetch(url);
  expect(download.status).toBe(200);
  const packagePath = join(work, "KB-2026-004411.mlep");
  writeFileSync(packagePath, Buffer.from(await download.arrayBuffer()));
  const opened = join(work, "opened");
  const printed = execFileSync(PYTHON, [TOOL, packagePath, "--out", opened, "--public-key", key.public_key, "--passphrase", passphrase], { encoding: "utf8" });
  const aFile = "v1.filtered.csv";
  const contents = readFileSync(join(opened, "data", "appointments", "v1", aFile), "utf8");
  const size = statSync(packagePath).size;
  writeFileSync(join(SHOTS, "terminal.json"), JSON.stringify({
    blocks: [
      { who: "the recipient", where: "a terminal on the recipient's computer",
        command: "curl <the platform's address>/legal-exports/signing-key", output: JSON.stringify(key) + "\n" },
      { who: "the recipient", where: "a terminal on the recipient's computer",
        command: "curl -o KB-2026-004411.mlep \"<the download link>\"", output: `(a file of ${size} bytes is saved, and nothing is printed)\n` },
      { who: "the recipient", where: "a terminal on the recipient's computer",
        command: "python open_legal_package.py KB-2026-004411.mlep --out opened --public-key <the platform's public key> --passphrase <the passphrase>",
        output: printed },
      { kind: "shown", what: "What the folder opened now holds", output: tree(opened) + "\n" },
      { kind: "shown", what: `What the file opened/data/appointments/v1/${aFile} holds`, output: contents },
    ],
  }, null, 2));
});
