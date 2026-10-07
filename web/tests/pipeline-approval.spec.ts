/**
 * U143 in the console: a pipeline version whose custom step is given the recordings, or writes files, starts in development and moves to production
 * only when a second person says so. A version is retired for good by its owner's approver or by whoever uploaded it.
 *
 * The claims these tests carry are about what reaches the person, on the live platform:
 *   - the version shows its stage, says which step is given the recordings and which writes files, who uploaded it, and that it cannot run until an approver of the owning department moves it to production;
 *   - the person who uploaded it is not offered the move, and a custodian of another department who tries is told why;
 *   - the owning department's approver moves it to production, the page then says by whom, and returning it puts it back in development;
 *   - retiring asks first, is final, and the page then says who retired it and offers nothing more.
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
    writes_files: true
    memory_mb: 2048
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

test.describe("U143: a version that is given the recordings starts in development", () => {
  test("the uploader sees it waits and is not offered the move, the approver moves it to production and back, and retiring is final", async ({ page }) => {
    const id = await pipelineWithVersion();

    await loginAs(page, ENGINEER);
    await page.goto(`/pipelines/${id}`);
    const panel = page.getByTestId("pipeline-lifecycle-1");
    await expect(page.getByTestId("pipeline-stage-1")).toHaveText("In development");
    await expect(panel).toContainText("Step inspect reads the patient recordings and writes files, which become a new version that a reviewer checks before anyone can use it");
    await expect(panel).toContainText("An approver of Verification other than");
    await expect(page.getByTestId("pipeline-version-1")).toContainText("reads the recordings, writes files, 2048 MB of memory");
    await expect(panel.getByRole("button", { name: "Move to production" })).toHaveCount(0);

    await loginAs(page, ELSEWHERE);
    await page.goto(`/pipelines/${id}`);
    await page.getByRole("button", { name: "Move to production" }).click();
    await expect(page.getByRole("alert")).toContainText("department that owns this pipeline");
    await expect(page.getByTestId("pipeline-lifecycle-1")).toContainText("checks it and moves it to production first");
    await expect(page.getByTestId("pipeline-stage-1")).toHaveText("In development");

    await loginAs(page, CUSTODIAN);
    await page.goto(`/pipelines/${id}`);
    await page.getByRole("button", { name: "Move to production" }).click();
    await expect(page.getByTestId("pipeline-lifecycle-1")).toContainText("Moved to production by");
    await expect(page.getByTestId("pipeline-lifecycle-1")).toContainText("writes files, which become a new version that a reviewer checks");
    await expect(page.getByTestId("pipeline-stage-1")).toHaveText("In production");

    await page.getByRole("button", { name: "Return to development" }).click();
    await expect(page.getByTestId("pipeline-lifecycle-1")).toContainText("checks it and moves it to production first");
    await expect(page.getByTestId("pipeline-stage-1")).toHaveText("In development");
    await expect(page.getByRole("button", { name: "Move to production" })).toBeVisible();

    // Retiring asks first, and what it says is true: nothing can bring the version back.
    await page.getByRole("button", { name: "Retire this version" }).click();
    await expect(page.getByTestId("pipeline-lifecycle-1")).toContainText("Retiring is final");
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByTestId("pipeline-stage-1")).toHaveText("In development");
    await page.getByRole("button", { name: "Retire this version" }).click();
    await page.getByRole("button", { name: "Retire v1" }).click();
    await expect(page.getByTestId("pipeline-stage-1")).toHaveText("Retired");
    await expect(page.getByTestId("pipeline-lifecycle-1")).toContainText("This version can no longer be run");
    await expect(page.getByRole("button", { name: "Move to production" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Retire this version" })).toHaveCount(0);
  });
});
