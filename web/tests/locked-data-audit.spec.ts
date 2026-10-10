/**
 * U164 in the console: the Locked data audit covers locked files as well as locked columns.
 *
 * The claims these tests carry, on the live platform, with files and a lease made up here:
 *   - the page opens with a contents line, and its sections sit under Locked columns, Locked files and Keys and erasure;
 *   - a file opened under a lease is listed with who opened it, in which table, where, and why the platform allowed it;
 *   - the refusal of a file the lease does not name is listed with its reason;
 *   - the lease for locked files is listed with what for, how many files for how long, and who decided it;
 *   - no file name or file identifier appears anywhere on the page.
 */

import { execSync } from "node:child_process";
import { expect, test } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial", timeout: 90_000 });

const ENGINEER = "canary-engineer";
const CUSTODIAN = "canary-custodian";
const RESEARCHER = "canary-researcher";
const OFFICER = "canary-dpo";

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

const stamp = Date.now();
const tableName = `audit-files-${stamp}`;
const purpose = `check the audit of locked files ${stamp}`;
let lease = "";

async function json(response: Response): Promise<any> {
  return response.json();
}

test.afterAll(async () => {
  if (lease) await fetch(`${API_BASE}/file-leases/${lease}/end`, { method: "POST", headers: await bearerFor(CUSTODIAN) });
});

test.describe("U164: the audit of locked files", () => {
  test("a lease is asked for, allowed, and files are opened and refused under it", async () => {
    const org = await json(await fetch(`${API_BASE}/organisation?tenant_id=canary`, { headers: await bearerFor(CUSTODIAN) }));
    const department = org.departments.find((d: { name: string }) => d.name === "Verification").id;
    const engineer = await bearerFor(ENGINEER);
    const registered = await fetch(`${API_BASE}/datasets/register`, {
      method: "POST",
      headers: { ...engineer, "content-type": "application/json" },
      body: JSON.stringify({ tenant_id: "canary", name: tableName, department_id: department, registered_by: ENGINEER, provenance: "internal_regulated", declared_class: "RAW", source_kind: "upload" }),
    });
    expect(registered.status).toBe(201);
    const dataset = (await json(registered)).id as string;
    for (let i = 0; i < 4; i++) {
      const body = wav(40_000 + i * 2_000);
      const opened = await fetch(`${API_BASE}/datasets/${dataset}/file-uploads`, {
        method: "POST",
        headers: { ...engineer, "content-type": "application/json" },
        body: JSON.stringify({ file_name: `r-${i + 1}.wav`, size_bytes: body.length, person_kind: "chart number", person_value: `AUDIT-${stamp}-${i}` }),
      });
      expect(opened.status).toBe(201);
      const upload = (await json(opened)).id as string;
      expect((await fetch(`${API_BASE}/file-uploads/${upload}/parts/1`, { method: "PUT", headers: { ...engineer, "content-type": "application/octet-stream" }, body })).status).toBe(200);
      expect((await fetch(`${API_BASE}/file-uploads/${upload}/complete`, { method: "POST", headers: engineer })).status).toBe(200);
    }
    const sealed = await fetch(`${API_BASE}/datasets/${dataset}/seal-files`, { method: "POST", headers: engineer });
    expect(sealed.status).toBe(201);
    const version = (await json(sealed)).id as string;

    const researcher = await bearerFor(RESEARCHER);
    const asked = await fetch(`${API_BASE}/dataset-versions/${version}/file-leases`, {
      method: "POST",
      headers: { ...researcher, "content-type": "application/json" },
      body: JSON.stringify({ kind: "console", purpose, records: 2, hours: 1 }),
    });
    expect(asked.status).toBe(201);
    lease = (await json(asked)).id as string;
    const allowed = await fetch(`${API_BASE}/file-leases/${lease}/approve`, {
      method: "POST",
      headers: { ...(await bearerFor(CUSTODIAN)), "content-type": "application/json" },
      body: JSON.stringify({ note: "checked", records: 2 }),
    });
    expect(allowed.status).toBe(200);

    const results: number[] = [];
    for (const id of ["r-1", "r-2", "r-3", "r-4"]) {
      results.push((await fetch(`${API_BASE}/dataset-versions/${version}/records/${id}/open`, { method: "POST", headers: researcher })).status);
    }
    expect(results.filter((s) => s === 200)).toHaveLength(2);
    expect(results.filter((s) => s === 403)).toHaveLength(2);
  });

  test("the page opens with its contents, and its sections sit under their group", async ({ page }) => {
    await loginAs(page, OFFICER);
    await page.goto("/locking-audit");
    const contents = page.getByTestId("audit-contents");
    await expect(contents).toBeVisible({ timeout: 20_000 });
    await expect(contents).toContainText("Locked columns:");
    await expect(contents).toContainText("Locked files:");
    await expect(contents).toContainText("Keys and erasure:");
    for (const group of ["Locked columns", "Locked files", "Keys and erasure"]) {
      await expect(page.getByRole("heading", { level: 2, name: group })).toBeVisible();
    }
    await contents.getByRole("link", { name: "Opens" }).click();
    await expect(page.getByRole("heading", { name: "Who opened locked files, and who was refused" })).toBeInViewport();
  });

  test("the openings, the refusal and the lease are listed, and no file is named", async ({ page }) => {
    await loginAs(page, OFFICER);
    await page.goto("/locking-audit");
    const opens = page.getByTestId("audit-file-reads");
    await expect(opens).toBeVisible({ timeout: 20_000 });
    const newest = opens.locator("tr", { hasText: tableName });
    await expect(newest.filter({ hasText: "Opened" }).first()).toContainText("Canary researcher");
    await expect(newest.filter({ hasText: "Opened" }).first()).toContainText("In the page");
    await expect(newest.filter({ hasText: "Opened" }).first()).toContainText("With an approver's permission");
    await expect(newest.filter({ hasText: "Refused" }).first()).toContainText("not one of the files your lease opens");

    const leases = page.getByTestId("audit-file-leases");
    const mine = leases.locator("tr", { hasText: purpose });
    await expect(mine).toContainText("Canary researcher");
    await expect(mine).toContainText(tableName);
    await expect(mine).toContainText("2 files for 1 hour");
    await expect(mine).toContainText("In the page");
    await expect(mine).toContainText("Approved by Canary custodian on");

    // The files are named, for the two roles this page is for.
    await expect(opens.locator("tr", { hasText: tableName }).filter({ hasText: "Opened" }).first().getByTestId("audit-file-name")).toHaveText(/^r-[1-4]$/);
    await expect(opens.locator("tr", { hasText: tableName }).filter({ hasText: "Refused" }).first().getByTestId("audit-file-name")).toHaveText(/^r-[1-4]$/);
    await expect(mine).toContainText("In force until");
  });

  test("a key line says why it was opened and for which table, and a person code shows that key's own lines", async ({ page }) => {
    await loginAs(page, OFFICER);
    await page.goto("/locking-audit");
    const keys = page.getByTestId("audit-keys");
    await expect(keys).toBeVisible({ timeout: 20_000 });
    // Searched for by table, as a person looking for one table's lines would: the list shows the newest lines first, and a long run of other tests' lines can
    // push this table's off the first page.
    await page.getByTestId("filter-table").fill(tableName);
    await expect(page.getByTestId("audit-keys-count")).toContainText("match", { timeout: 20_000 });
    const opened = keys.locator("tr", { hasText: tableName }).first();
    await expect(opened).toContainText("A key was opened");
    await expect(opened).toContainText("To open a locked file in the page, with an approver's permission");
    await expect(opened).toContainText(`Table: ${tableName}, version 1`);
    await expect(opened).toContainText("Canary researcher");
    await expect(opened).toContainText(/File: r-[1-4]/);
    const code = (await opened.getByTestId("follow-code").innerText()).trim();
    expect(code).toMatch(/^T-[0-9a-f]+$/);
    await opened.getByTestId("follow-code").click();
    await expect(page.getByTestId("audit-code-chip")).toHaveCount(2);
    await expect(page.getByTestId("audit-code-chip").first()).toContainText(`Showing only the key ${code}`);
    // The search by table found the line; it is let go now, so that what is checked next is the person code alone.
    await page.getByTestId("filter-table").fill("");
    await expect(page.getByTestId("audit-keys-count")).toContainText("match", { timeout: 20_000 });
    // The lists that do not carry a person code are left as they were.
    await expect(page.getByTestId("audit-reads-count")).not.toContainText("match");
    await expect(page.getByTestId("filter-q")).toHaveValue("");
    const codes = await page.getByTestId("audit-keys").locator("tbody tr").evaluateAll((rows) => rows.map((r) => (r.querySelector("[data-testid=follow-code]") as HTMLElement | null)?.innerText.trim()));
    expect(codes.length).toBeGreaterThanOrEqual(2);
    expect(new Set(codes)).toEqual(new Set([code]));
    await expect(page.getByTestId("audit-keys")).toContainText("A key was made for a person");
    await page.getByTestId("clear-code").first().click();
    await expect(page.getByTestId("audit-code-chip")).toHaveCount(0);
    await expect(page.getByTestId("audit-keys-count")).not.toContainText("match", { timeout: 20_000 });
  });

  test("a lease that ran out is said to have run out, not to have been ended", async ({ page }) => {
    execSync(
      `wsl.exe -- bash -c "docker exec munitas-postgres-1 psql -U munitas -d platform -c \\"update file_lease set expires_at = now() - interval '1 minute' where id = '${lease}'\\""`,
      { encoding: "utf8", timeout: 60_000 },
    );
    await loginAs(page, OFFICER);
    await page.goto("/locking-audit");
    const mine = page.getByTestId("audit-file-leases").locator("tr", { hasText: purpose });
    await expect(mine).toContainText("Approved by Canary custodian on", { timeout: 20_000 });
    await expect(mine).toContainText("Ran out on");
    await expect(mine).not.toContainText("Ended early");
  });
});
