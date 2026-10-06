/**
 * U143 in the console: a pipeline version whose custom step is given the recordings waits for a second person's approval.
 *
 * The claims these tests carry are about what reaches the person, on the live platform:
 *   - the version says which step is given the recordings, who uploaded it, and that it cannot run until an approver of the owning department approves;
 *   - the person who uploaded it is not offered the approval, and a custodian of another department who tries is told why;
 *   - the owning department's approver approves it, the page then says by whom, and taking the approval back puts it back to waiting.
 */

import { expect, test } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial" });

const ENGINEER = "canary-engineer";
const CUSTODIAN = "canary-custodian";
const ELSEWHERE = "canary-elsewhere";

const YAML = `name: custom-audio
steps:
  - name: inspect
    kind: script
    script: steps/inspect.py
    recordings: true
  - name: decide
    kind: gate
    depends_on: [inspect]
    to_class: OPEN_FOR_ANNOTATION
    inputs:
      recommendation: "\${steps.inspect.recommendation}"
      recommendation_reason: "\${steps.inspect.reason}"
`;

/** A zip with one file in it, built by hand so the test needs no library: stored, not compressed. */
function zipOf(name: string, text: string): Buffer {
  const body = Buffer.from(text);
  const file = Buffer.from(name);
  const crc = (() => {
    let c = ~0;
    for (const byte of body) {
      c ^= byte;
      for (let k = 0; k < 8; k += 1) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
    }
    return ~c >>> 0;
  })();
  const local = Buffer.alloc(30);
  local.writeUInt32LE(0x04034b50, 0);
  local.writeUInt16LE(20, 4);
  local.writeUInt32LE(crc, 14);
  local.writeUInt32LE(body.length, 18);
  local.writeUInt32LE(body.length, 22);
  local.writeUInt16LE(file.length, 26);
  const central = Buffer.alloc(46);
  central.writeUInt32LE(0x02014b50, 0);
  central.writeUInt16LE(20, 4);
  central.writeUInt16LE(20, 6);
  central.writeUInt32LE(crc, 16);
  central.writeUInt32LE(body.length, 20);
  central.writeUInt32LE(body.length, 24);
  central.writeUInt16LE(file.length, 28);
  const localPart = Buffer.concat([local, file, body]);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(1, 8);
  end.writeUInt16LE(1, 10);
  end.writeUInt32LE(46 + file.length, 12);
  end.writeUInt32LE(localPart.length, 16);
  return Buffer.concat([localPart, central, file, end]);
}

async function pipelineWithVersion(): Promise<string> {
  const org = await fetch(`${API_BASE}/organisation?tenant_id=canary`, { headers: await bearerFor(CUSTODIAN) }).then((r) => r.json());
  const department = org.departments.find((d: { name: string }) => d.name === "Verification").id;
  const headers = await bearerFor(ENGINEER);
  const made = await fetch(`${API_BASE}/pipelines/register`, {
    method: "POST",
    headers: { ...headers, "content-type": "application/json" },
    body: JSON.stringify({ tenant_id: "canary", name: `console-approval-${Date.now()}`, department_id: department, registered_by: ENGINEER }),
  });
  expect(made.status).toBe(201);
  const id = (await made.json()).id;
  const form = new FormData();
  form.append("config_file", new Blob([YAML]), "pipeline.yaml");
  form.append("scripts", new Blob([zipOf("steps/inspect.py", "print('{}')\n")]), "scripts.zip");
  form.append("registered_by", ENGINEER);
  const uploaded = await fetch(`${API_BASE}/pipelines/${id}/versions/upload`, { method: "POST", headers, body: form });
  expect(uploaded.status).toBe(201);
  return id;
}

test.describe("U143: a version that is given the recordings waits for an approver", () => {
  test("the uploader sees it waits and is not offered the approval, and the approver approves and takes it back", async ({ page }) => {
    const id = await pipelineWithVersion();

    await loginAs(page, ENGINEER);
    await page.goto(`/pipelines/${id}`);
    const panel = page.getByTestId("pipeline-approval-1");
    await expect(panel).toContainText("The step inspect is given the recordings");
    await expect(panel).toContainText("cannot run until an approver of Verification");
    await expect(page.getByTestId("pipeline-version-1")).toContainText("given the recordings");
    await expect(panel.getByRole("button", { name: "Approve this version" })).toHaveCount(0);

    await loginAs(page, ELSEWHERE);
    await page.goto(`/pipelines/${id}`);
    await page.getByRole("button", { name: "Approve this version" }).click();
    await expect(page.getByRole("alert")).toContainText("department that owns this pipeline");
    await expect(page.getByTestId("pipeline-approval-1")).toContainText("cannot run until an approver");

    await loginAs(page, CUSTODIAN);
    await page.goto(`/pipelines/${id}`);
    await page.getByRole("button", { name: "Approve this version" }).click();
    await expect(page.getByTestId("pipeline-approval-1")).toContainText("Approved by");
    await expect(page.getByTestId("pipeline-approval-1")).toContainText("may be given the recordings");

    await page.getByRole("button", { name: "Take the approval back" }).click();
    await expect(page.getByTestId("pipeline-approval-1")).toContainText("cannot run until an approver");
    await expect(page.getByRole("button", { name: "Approve this version" })).toBeVisible();
  });
});
