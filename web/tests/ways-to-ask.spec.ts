/**
 * U165 in the console: the version page offers only the ways of asking that can work for what the version holds.
 *
 * The claims these tests carry are about what reaches the person, on the live platform:
 *   - on a version of locked files the page offers one ask, to open a few files, and no form for the whole version; it says in one sentence what is locked,
 *     and the list of files gives one reason, which is not about the person's role;
 *   - on a table with locked columns the page offers both asks in one place, headed by what each one is for;
 *   - on a version with nothing locked the page offers the whole-version ask, as it always did.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial" });

const FIXTURES = join(process.cwd(), "walkthroughs", "fixtures", "locked-appointments");
const CONTRACT = readFileSync(join(FIXTURES, "contract.yaml"));
const ROWS = readFileSync(join(FIXTURES, "rows.csv"));

const ENGINEER = "canary-engineer";
const CUSTODIAN = "canary-custodian";
const RESEARCHER = "canary-researcher";

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

async function register(name: string): Promise<string> {
  const org = await fetch(`${API_BASE}/organisation?tenant_id=canary`, { headers: await bearerFor(CUSTODIAN) }).then((r) => r.json());
  const department = org.departments.find((d: { name: string }) => d.name === "Verification").id;
  const registered = await fetch(`${API_BASE}/datasets/register`, {
    method: "POST",
    headers: { ...(await bearerFor(ENGINEER)), "content-type": "application/json" },
    body: JSON.stringify({ tenant_id: "canary", name, department_id: department, registered_by: ENGINEER, provenance: "internal_regulated", declared_class: "RAW", source_kind: "upload" }),
  });
  expect(registered.status).toBe(201);
  return (await registered.json()).id as string;
}

async function lockedFiles(chart: string): Promise<string> {
  const dataset = await register(`ways-files-${Date.now()}`);
  const headers = await bearerFor(ENGINEER);
  for (let i = 0; i < 3; i++) {
    const body = wav(40_000 + i * 2_000);
    const opened = await fetch(`${API_BASE}/datasets/${dataset}/file-uploads`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ file_name: `r-${i + 1}.wav`, size_bytes: body.length, person_kind: "hospital chart number", person_value: `${chart}-${i}` }),
    });
    expect(opened.status).toBe(201);
    const upload = (await opened.json()).id as string;
    expect((await fetch(`${API_BASE}/file-uploads/${upload}/parts/1`, { method: "PUT", headers: { ...headers, "content-type": "application/octet-stream" }, body })).status).toBe(200);
    expect((await fetch(`${API_BASE}/file-uploads/${upload}/complete`, { method: "POST", headers })).status).toBe(200);
  }
  const sealed = await fetch(`${API_BASE}/datasets/${dataset}/seal-files`, { method: "POST", headers });
  expect(sealed.status).toBe(201);
  return (await sealed.json()).id as string;
}

async function lockedTable(): Promise<string> {
  const dataset = await register(`ways-table-${Date.now()}`);
  const form = new FormData();
  form.append("contract", new Blob([CONTRACT]), "contract.yaml");
  form.append("rows", new Blob([ROWS]), "rows.csv");
  const sealed = await fetch(`${API_BASE}/datasets/${dataset}/seal-table-files`, { method: "POST", headers: await bearerFor(ENGINEER), body: form });
  expect(sealed.status).toBe(201);
  return (await sealed.json()).id as string;
}

test.describe("U165: the ways of asking follow what the version holds", () => {
  let files = "";
  let table = "";

  test("on locked files there is one ask, to open a few files, and the page says what is locked once", async ({ page }) => {
    files = await lockedFiles(`U165-${Date.now()}`);
    await loginAs(page, RESEARCHER);
    await page.goto(`/versions/${files}`);
    const panel = page.getByTestId("access-panel");
    await expect(panel).toBeVisible({ timeout: 15_000 });
    await expect(panel).toHaveAttribute("data-shape", "files");
    await expect(panel.getByTestId("file-lease-form")).toBeVisible();
    await expect(panel).toContainText("These files are locked");
    await expect(page.getByTestId("request-access")).toHaveCount(0);
    await expect(page.getByTestId("request-submit")).toHaveCount(0);
    await expect(page.getByTestId("access-mark")).toHaveCount(0);

    const note = page.getByTestId("record-files-note");
    await expect(note).toContainText("has allowed it");
    await expect(note).not.toContainText("holds no role");
  });

  test("on a table with locked columns both asks are in one place, each said for what it is", async ({ page }) => {
    table = await lockedTable();
    await loginAs(page, ENGINEER);
    await page.goto(`/versions/${table}`);
    const panel = page.getByTestId("access-panel");
    await expect(panel).toBeVisible({ timeout: 15_000 });
    await expect(panel).toHaveAttribute("data-shape", "columns");
    await expect(panel).toContainText("This table has locked columns");
    await expect(panel.getByRole("heading", { name: "The readable columns" })).toBeVisible();
    await expect(panel.getByRole("heading", { name: "The locked values" })).toBeVisible();
    await expect(panel.getByTestId("request-access")).toBeVisible();
    await expect(panel.getByTestId("ask-lease")).toBeVisible();
    // The old heading is gone from the section that lists the columns: asking happens in one place.
    await expect(page.getByRole("heading", { name: "Your access to the locked values" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Locked columns" })).toBeVisible();
  });

  test("the approver is told what approving a request on a table with locked columns gives", async ({ page }) => {
    const asked = await fetch(`${API_BASE}/leases/requests`, {
      method: "POST",
      headers: { ...(await bearerFor(RESEARCHER)), "content-type": "application/json" },
      body: JSON.stringify({ tenant_id: "canary", principal: RESEARCHER, dataset_version_id: table, purpose: "see the readable columns", justification: "needs them", ttl_hours: 4 }),
    });
    expect(asked.status).toBe(201);
    const id = (await asked.json()).id as string;
    await loginAs(page, CUSTODIAN);
    await page.goto("/");
    await expect(page.getByTestId(`request-gives-${id}`)).toContainText("readable columns only", { timeout: 15_000 });
  });

  test("an agent pointed at locked files is told asking would not help, before start is pressed", async ({ page }) => {
    const headers = { ...(await bearerFor(ENGINEER)), "content-type": "application/json" };
    const org = await fetch(`${API_BASE}/organisation?tenant_id=canary`, { headers }).then((r) => r.json());
    const department = org.departments.find((d: { name: string }) => d.name === "Verification").id;
    const registered = await fetch(`${API_BASE}/agents/register`, {
      method: "POST",
      headers,
      body: JSON.stringify({ tenant_id: "canary", name: `ways-agent-${Date.now()}`, department_id: department, registered_by: ENGINEER, purpose: "rank the files" }),
    });
    expect(registered.status).toBe(201);
    const agent = (await registered.json()).id as string;
    const version = await fetch(`${API_BASE}/agents/${agent}/versions`, {
      method: "POST",
      headers,
      body: JSON.stringify({ code_hash: `git:${Date.now()}`, source_path: "/checkout/agent", model_id: "qwen2.5:7b", tool_scope: ["list_review_queue"], registered_by: ENGINEER }),
    });
    expect(version.status).toBe(201);
    const deployed = await fetch(`${API_BASE}/agents/${agent}/deploy`, { method: "POST", headers, body: JSON.stringify({ agent_version_id: (await version.json()).id }) });
    expect(deployed.ok).toBe(true);

    await loginAs(page, ENGINEER);
    await page.goto(`/agents/${agent}`);
    await page.getByTestId("run-purpose").fill("rank the files");
    await page.getByTestId("run-target").selectOption(files);
    await expect(page.getByTestId("access-notice")).toContainText("asking for access would not help", { timeout: 15_000 });
    await expect(page.getByTestId("access-notice")).toContainText("every file in this version is locked");
    await expect(page.getByTestId("start-run")).toBeDisabled();
  });

  test("the whole-version ask for locked files is refused by the platform too, in words, for anyone calling it directly", async () => {
    const refused = await fetch(`${API_BASE}/leases/requests`, {
      method: "POST",
      headers: { ...(await bearerFor(RESEARCHER)), "content-type": "application/json" },
      body: JSON.stringify({ tenant_id: "canary", principal: RESEARCHER, dataset_version_id: files, purpose: "x", justification: "x", ttl_hours: 4 }),
    });
    expect(refused.status).toBe(400);
    const body = await refused.json();
    expect(JSON.stringify(body)).toContain("every file in this version is locked");
  });
});
