/**
 * U203 in the console: an engineer can give an uploaded version a copy of the data a run is started on, and the page says what each version was given.
 *
 * Uploaded code runs with no network and no storage key, so a copy is the only way it can read a dataset. The upload form used to have no way to ask for one, so a version
 * uploaded from the console was always given no data, and its runs found an empty folder with nothing to say why. The claims, on the live platform and in the test organisation only:
 *   - the form offers the choice, under Advanced, off by default;
 *   - a version uploaded with the choice ticked is listed as given a copy of the data, and the platform sends it to the upload as `copy`;
 *   - a version uploaded without it is listed as given no data.
 *
 * The agent is made over the API (setup, not the claim); it and its versions are sealed and stay, named U203 so the canary tidy recognises them.
 */

import { expect, test } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial", timeout: 120_000 });

const ENGINEER = "canary-engineer";
const NAME = `U203 agent ${Date.now()}`;
let agentId = "";

const ZIP = {
  // A zip is needed to upload, and its contents are not run in this test: the smallest valid project, a stored (uncompressed) zip with main.py and munitas.json.
  name: "probe.zip",
  mimeType: "application/zip",
};

function tinyZip(): Buffer {
  const files: Record<string, string> = { "main.py": "print('{}')\n", "munitas.json": '{"entrypoint": "main.py"}\n' };
  const parts: Buffer[] = [];
  const central: Buffer[] = [];
  let offset = 0;
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  });
  const crc32 = (buf: Buffer): number => {
    let c = 0xffffffff;
    for (const b of buf) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  };
  for (const [name, text] of Object.entries(files)) {
    const data = Buffer.from(text);
    const nameBuf = Buffer.from(name);
    const crc = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBuf.length, 26);
    parts.push(local, nameBuf, data);
    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4);
    entry.writeUInt16LE(20, 6);
    entry.writeUInt32LE(crc, 16);
    entry.writeUInt32LE(data.length, 20);
    entry.writeUInt32LE(data.length, 24);
    entry.writeUInt16LE(nameBuf.length, 28);
    entry.writeUInt32LE(offset, 42);
    central.push(entry, nameBuf);
    offset += local.length + nameBuf.length + data.length;
  }
  const centralBuf = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(Object.keys(files).length, 8);
  end.writeUInt16LE(Object.keys(files).length, 10);
  end.writeUInt32LE(centralBuf.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, centralBuf, end]);
}

test.beforeAll(async () => {
  const headers = { ...(await bearerFor(ENGINEER)), "content-type": "application/json" };
  const org = await fetch(`${API_BASE}/organisation?tenant_id=canary`, { headers }).then((r) => r.json());
  const department = org.departments.find((d: { name: string }) => d.name === "Verification").id;
  const registered = await fetch(`${API_BASE}/agents/register`, {
    method: "POST",
    headers,
    body: JSON.stringify({ tenant_id: "canary", name: NAME, department_id: department, registered_by: ENGINEER, purpose: "Reads the data it is run on" }),
  });
  expect(registered.status).toBe(201);
  agentId = (await registered.json()).id;
});

test.describe("U203: the upload form can give a version a copy of its data", () => {
  test("the choice is under Advanced and off by default", async ({ page }) => {
    await loginAs(page, ENGINEER);
    await page.goto(`/agents/${agentId}`);
    await expect(page.getByTestId("upload-agent-version-form")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("agent-version-data-copy")).toHaveCount(0);
    await page.getByTestId("agent-version-advanced-toggle").click();
    await expect(page.getByTestId("agent-version-data-copy")).not.toBeChecked();
    await expect(page.getByText("This code runs with no network and no storage key")).toBeVisible();
  });

  test("a version uploaded with the choice ticked is listed as given a copy of the data", async ({ page }) => {
    await loginAs(page, ENGINEER);
    await page.goto(`/agents/${agentId}`);
    await page.getByTestId("agent-version-zip").setInputFiles({ ...ZIP, buffer: tinyZip() });
    await page.getByTestId("agent-version-model").fill("none");
    await page.getByTestId("agent-version-advanced-toggle").click();
    await page.getByTestId("agent-version-no-tools").check();
    await page.getByTestId("agent-version-data-copy").check();
    await page.getByTestId("upload-agent-version-submit").click();
    await expect(page.getByTestId("data-access-1")).toContainText("Given a copy of the data a run is started on", { timeout: 20_000 });
    // What the platform recorded, not only what the page says.
    const detail = await fetch(`${API_BASE}/agents/${agentId}`, { headers: await bearerFor(ENGINEER) }).then((r) => r.json());
    expect(detail.versions.find((v: { version: number }) => v.version === 1).data_access).toBe("copy");
    await expect(page.getByTestId("agent-version-data-copy")).toHaveCount(0);
  });

  test("a version uploaded without it is listed as given no data", async ({ page }) => {
    await loginAs(page, ENGINEER);
    await page.goto(`/agents/${agentId}`);
    await expect(page.getByTestId("data-access-1")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("agent-version-zip").setInputFiles({ ...ZIP, buffer: tinyZip() });
    await page.getByTestId("agent-version-model").fill("none");
    await page.getByTestId("agent-version-advanced-toggle").click();
    await page.getByTestId("agent-version-no-tools").check();
    await page.getByTestId("upload-agent-version-submit").click();
    await expect(page.getByTestId("data-access-2")).toContainText("Given no data", { timeout: 20_000 });
    await expect(page.getByTestId("data-access-1")).toContainText("Given a copy of the data a run is started on");
    const detail = await fetch(`${API_BASE}/agents/${agentId}`, { headers: await bearerFor(ENGINEER) }).then((r) => r.json());
    expect(detail.versions.find((v: { version: number }) => v.version === 2).data_access).toBe("none");
  });
});
