/**
 * U169 in the console: the Locked data audit can be searched, filtered and read a page at a time.
 *
 * The claims these tests carry, on the live platform, with a table and thirty refused reads made up here so every count is exact:
 *   - a list shows how many lines match, shows 25 at a time, and has a previous and a next that stop at the ends;
 *   - the person, table, date and search filters each narrow a list, together they all must hold, and a filter that matches nothing says so;
 *   - a list that cannot use a filter says so instead of quietly ignoring it;
 *   - each list has its own choice of result;
 *   - clearing the filters brings everything back;
 *   - the contracts list names the tables sealed under each contract, and can be found by a table.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial", timeout: 120_000 });

const FIXTURES = join(process.cwd(), "walkthroughs", "fixtures", "locked-appointments");
const CONTRACT = readFileSync(join(FIXTURES, "contract.yaml"));
const ROWS = readFileSync(join(FIXTURES, "rows.csv"));

const ENGINEER = "canary-engineer";
const CUSTODIAN = "canary-custodian";
const RESEARCHER = "canary-researcher";
const OFFICER = "canary-dpo";

const stamp = Date.now();
const tableName = `audit-filter-${stamp}`;
const REFUSALS = 30;

async function sealTable(): Promise<string> {
  const org = await fetch(`${API_BASE}/organisation?tenant_id=canary`, { headers: await bearerFor(CUSTODIAN) }).then((r) => r.json());
  const department = org.departments.find((d: { name: string }) => d.name === "Verification").id;
  const engineer = await bearerFor(ENGINEER);
  const registered = await fetch(`${API_BASE}/datasets/register`, {
    method: "POST",
    headers: { ...engineer, "content-type": "application/json" },
    body: JSON.stringify({ tenant_id: "canary", name: tableName, department_id: department, registered_by: ENGINEER, provenance: "internal_regulated", declared_class: "RAW", source_kind: "upload" }),
  });
  expect(registered.status).toBe(201);
  const form = new FormData();
  form.append("contract", new Blob([CONTRACT]), "contract.yaml");
  form.append("rows", new Blob([ROWS]), "rows.csv");
  const sealed = await fetch(`${API_BASE}/datasets/${(await registered.json()).id}/seal-table-files`, { method: "POST", headers: engineer, body: form });
  expect(sealed.status).toBe(201);
  return (await sealed.json()).id as string;
}

/** The local day, as the date field writes it. */
function day(offset = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

async function open(page: Page) {
  await loginAs(page, OFFICER);
  await page.goto("/locking-audit");
  await expect(page.getByTestId("audit-reads-count")).toBeVisible({ timeout: 30_000 });
}

async function narrowToTable(page: Page) {
  await page.getByTestId("filter-table").fill(tableName);
  await expect(page.getByTestId("audit-reads-count")).toHaveText(`${REFUSALS} lines match`, { timeout: 20_000 });
}

test.describe("U169: finding lines in the locked data audit", () => {
  test("a table is made and refused thirty times", async () => {
    const version = await sealTable();
    const researcher = await bearerFor(RESEARCHER);
    for (let i = 0; i < REFUSALS; i++) {
      const r = await fetch(`${API_BASE}/dataset-versions/${version}/person-rows`, { headers: researcher });
      expect(r.status).toBe(403);
    }
  });

  test("a list says how many lines match, shows 25, and pages stop at both ends", async ({ page }) => {
    await open(page);
    const before = await page.getByTestId("audit-reads-count").innerText();
    expect(before).toMatch(/^[\d,]+ lines$/);
    await narrowToTable(page);
    await expect(page.getByTestId("audit-reads-range")).toHaveText(`Showing 1 to 25 of ${REFUSALS}`);
    await expect(page.getByTestId("audit-reads").locator("tbody tr")).toHaveCount(25);
    await expect(page.getByTestId("audit-reads-prev")).toBeDisabled();
    await page.getByTestId("audit-reads-next").click();
    await expect(page.getByTestId("audit-reads-range")).toHaveText(`Showing 26 to ${REFUSALS} of ${REFUSALS}`);
    await expect(page.getByTestId("audit-reads").locator("tbody tr")).toHaveCount(REFUSALS - 25);
    await expect(page.getByTestId("audit-reads-next")).toBeDisabled();
    await page.getByTestId("audit-reads-prev").click();
    await expect(page.getByTestId("audit-reads-range")).toHaveText(`Showing 1 to 25 of ${REFUSALS}`);
  });

  test("lines per page can be changed, and the pages follow", async ({ page }) => {
    await open(page);
    await narrowToTable(page);
    await page.getByTestId("filter-size").selectOption("10");
    await expect(page.getByTestId("audit-reads-range")).toHaveText(`Showing 1 to 10 of ${REFUSALS}`, { timeout: 20_000 });
    await expect(page.getByTestId("audit-reads").locator("tbody tr")).toHaveCount(10);
    await page.getByTestId("audit-reads-next").click();
    await page.getByTestId("audit-reads-next").click();
    await expect(page.getByTestId("audit-reads-range")).toHaveText(`Showing 21 to ${REFUSALS} of ${REFUSALS}`);
    await expect(page.getByTestId("audit-reads-next")).toBeDisabled();
    await page.getByTestId("filter-size").selectOption("100");
    await expect(page.getByTestId("audit-reads-range")).toHaveText(`Showing 1 to ${REFUSALS} of ${REFUSALS}`, { timeout: 20_000 });
    await expect(page.getByTestId("audit-reads-next")).toBeDisabled();
  });

  test("a change of filter starts again at the first page", async ({ page }) => {
    await open(page);
    await narrowToTable(page);
    await page.getByTestId("audit-reads-next").click();
    await expect(page.getByTestId("audit-reads-range")).toContainText("Showing 26");
    await page.getByTestId("filter-q").fill("lease");
    await expect(page.getByTestId("audit-reads-range")).toHaveText(`Showing 1 to 25 of ${REFUSALS}`, { timeout: 20_000 });
  });

  test("result, person, search and dates each narrow it, and together all must hold", async ({ page }) => {
    await open(page);
    await narrowToTable(page);
    await page.getByTestId("audit-reads-result").selectOption("allowed");
    await expect(page.getByTestId("audit-reads-none")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("audit-reads-count")).toHaveText("0 lines match");
    await page.getByTestId("audit-reads-result").selectOption("refused");
    await expect(page.getByTestId("audit-reads-count")).toHaveText(`${REFUSALS} lines match`, { timeout: 20_000 });

    await page.getByTestId("filter-person").fill("researcher");
    await expect(page.getByTestId("audit-reads-count")).toHaveText(`${REFUSALS} lines match`, { timeout: 20_000 });
    await page.getByTestId("filter-person").fill("engineer");
    await expect(page.getByTestId("audit-reads-none")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("filter-person").fill("");

    await page.getByTestId("filter-q").fill("no such words " + stamp);
    await expect(page.getByTestId("audit-reads-none")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("filter-q").fill("not an approver");
    await expect(page.getByTestId("audit-reads-count")).toHaveText(`${REFUSALS} lines match`, { timeout: 20_000 });
    await page.getByTestId("filter-q").fill("");

    await page.getByTestId("filter-from").fill(day(0));
    await page.getByTestId("filter-to").fill(day(0));
    await expect(page.getByTestId("audit-reads-count")).toHaveText(`${REFUSALS} lines match`, { timeout: 20_000 });
    await page.getByTestId("filter-from").fill(day(1));
    await page.getByTestId("filter-to").fill(day(2));
    await expect(page.getByTestId("audit-reads-none")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("filter-from").fill("");
    await page.getByTestId("filter-to").fill(day(-1));
    await expect(page.getByTestId("audit-reads-none")).toBeVisible({ timeout: 20_000 });
  });

  test("a list that cannot use a filter says so, and the others carry on", async ({ page }) => {
    await open(page);
    await narrowToTable(page);
    await expect(page.getByTestId("audit-erasures-ignored")).toHaveText("This list cannot be narrowed by table.");
    await expect(page.getByTestId("audit-reads-ignored")).toHaveCount(0);
    await expect(page.getByTestId("audit-keys-ignored")).toHaveCount(0);
  });

  test("each list has its own result choices, in words", async ({ page }) => {
    await open(page);
    await expect(page.getByTestId("audit-reads-result").locator("option")).toHaveText(["All", "Read", "Refused"]);
    await expect(page.getByTestId("audit-leases-result").locator("option")).toHaveText(["All", "Waiting", "Approved", "Declined", "Ended early"]);
    await expect(page.getByTestId("audit-file-reads-result").locator("option")).toHaveText(["All", "Opened", "Refused"]);
    await expect(page.getByTestId("audit-keys-result").locator("option")).toHaveText(["All", "Made for the organisation", "Made for a person", "Opened", "Destroyed"]);
    await expect(page.getByTestId("audit-group-sizes-result").locator("option")).toHaveText(["All", "Risk accepted", "Contract will change", "Not decided"]);
    await page.getByTestId("audit-keys-result").selectOption("destroy");
    await expect(page.getByTestId("audit-keys-count")).toContainText("match", { timeout: 20_000 });
  });

  test("the contracts list names the tables under each contract, and is found by a table", async ({ page }) => {
    await open(page);
    await page.getByTestId("filter-table").fill(tableName);
    await expect(page.getByTestId("audit-contracts-count")).toHaveText("1 line match", { timeout: 20_000 });
    await expect(page.getByTestId("audit-contract-tables")).toContainText(tableName);
    await expect(page.getByTestId("audit-contract-tables")).toContainText("version 1");
  });

  test("when the sign-in has ended, the page says so once, not in every list", async ({ page }) => {
    await loginAs(page, OFFICER);
    await page.route("**/audit/locked-columns**", (route) => route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "not signed in" }) }));
    await page.goto("/locking-audit");
    const line = page.getByTestId("audit-signed-out");
    await expect(line).toBeVisible({ timeout: 20_000 });
    await expect(line).toContainText("Your sign-in has ended.");
    await expect(line.getByRole("link", { name: "Sign in again" })).toHaveAttribute("href", "/auth/login");
    await expect(page.getByTestId("failure")).toHaveCount(0);
    await expect(page.getByTestId("audit-reads-list")).toHaveCount(0);
    await expect(page.getByTestId("audit-filters")).toHaveCount(0);
  });

  test("clearing the filters brings everything back", async ({ page }) => {
    await open(page);
    const everything = await page.getByTestId("audit-reads-count").innerText();
    await narrowToTable(page);
    await page.getByTestId("audit-reads-result").selectOption("refused");
    await page.getByTestId("filter-clear").click();
    await expect(page.getByTestId("audit-reads-count")).not.toContainText("match", { timeout: 20_000 });
    await expect(page.getByTestId("filter-table")).toHaveValue("");
    await expect(page.getByTestId("audit-reads-result")).toHaveValue("");
    await expect(page.getByTestId("audit-reads-count")).toHaveText(new RegExp(`^[\\d,]+ lines$`), { timeout: 20_000 });
    expect(Number((await page.getByTestId("audit-reads-count").innerText()).replace(/\D/g, ""))).toBeGreaterThanOrEqual(Number(everything.replace(/\D/g, "")));
  });
});
