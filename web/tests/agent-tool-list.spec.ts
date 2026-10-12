/**
 * U206 in the console: an organisation approves tools in advance, and a version that adds only those tools is shown as approved in advance.
 *
 * The claims these tests carry, on the live platform and in the test organisation only:
 *   - anybody may read the Approved tools page; a person who may not write it is told so in words and has no button to;
 *   - the AI risk owner writes the list through the page, and it shows the tool, how far it is approved, the level it is approved up to, who wrote it and when;
 *   - a new version of an agent that adds only that tool is shown on the Agent changes page as approved in advance, with a line saying which entry covered it;
 *   - a person who may not write the list is refused by the platform when they try through the API as well.
 *
 * The levels, the list and the agent's rows in the oversight tables are removed at the end; the agent and its versions are sealed and stay, named U206 so the canary tidy recognises them.
 */

import { execSync } from "node:child_process";
import { expect, test } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial", timeout: 120_000 });

const OFFICER = "canary-risk-owner";   // writes the risk levels; the same person as OWNER below, kept apart so that each use reads as what it is
const OWNER = "canary-risk-owner";
const ENGINEER = "canary-engineer";
const REVIEWER = "canary-reviewer";
const NAME = `U206 agent ${Date.now()}`;
const TOOL = "u206_read_notes";

function sql(statement: string): string {
  return execSync("wsl.exe -- docker exec -i munitas-postgres-1 psql -U munitas -d platform -At -v ON_ERROR_STOP=1", { input: statement, encoding: "utf8", timeout: 60_000 });
}

function clean(): void {
  const mine = "select id from agent where name like 'U206%'";
  sql(`delete from agent_record_chain where agent_id in (${mine});
       delete from agent_change_decision where approval_id in (select id from agent_change_approval where agent_id in (${mine}));
       delete from agent_change_approval where agent_id in (${mine});
       delete from agent_risk_record where agent_id in (${mine});
       delete from agent_tool_list where tenant_id = 'canary';
       delete from agent_risk_level_list where tenant_id = 'canary';`);
}

test.beforeAll(clean);
test.afterAll(clean);

async function call(who: string, path: string, body: unknown): Promise<{ status: number; json: any }> {
  const response = await fetch(`${API_BASE}${path}`, { method: "POST", headers: { ...(await bearerFor(who)), "content-type": "application/json" }, body: JSON.stringify(body) });
  return { status: response.status, json: await response.json().catch(() => null) };
}

test("a person who may not write the list is told so, and the platform refuses them too", async ({ page }) => {
  const levels = [
    { name: "Low", description: "An agent checked by whoever reads its output.", framework_label: null, second_approver: false, blocks: false, reconfirm_days: null },
    { name: "High", description: "An agent whose output can change what happens to a person.", framework_label: null, second_approver: true, blocks: false, reconfirm_days: null },
    { name: "Prohibited", description: "An agent the organisation will not run.", framework_label: null, second_approver: false, blocks: true, reconfirm_days: null },
  ];
  expect((await call(OFFICER, "/risk-levels", { levels, enforce: false, reason: "U206 fixture" })).status).toBe(201);
  await loginAs(page, REVIEWER);
  await page.goto("/approved-tools");
  await expect(page.getByRole("heading", { name: "Approved tools" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("tools-cannot-manage")).toContainText("Only the AI risk owner may write the approved tools list");
  await expect(page.getByTestId("tools-edit")).toHaveCount(0);
  const refused = await call(REVIEWER, "/agent-tool-list", { entries: [], reason: "x" });
  expect(refused.status).toBe(403);
});

test("the AI risk owner writes the list through the page", async ({ page }) => {
  await loginAs(page, OWNER);
  await page.goto("/approved-tools");
  await page.getByTestId("tools-edit").click({ timeout: 20_000 });
  await page.getByTestId("tool-add").click();
  await page.getByTestId("tool-name-0").fill(TOOL);
  await page.getByTestId("tool-description-0").fill("Reads the notes already filed for a person.");
  await page.getByTestId("tool-reason-0").fill("It changes nothing and the data it reads is already reachable.");
  const later = new Date(Date.now() + 200 * 86_400_000).toISOString().slice(0, 10);
  await page.getByTestId("tool-review-0").fill(later);
  await page.getByTestId("tools-reason").fill("U206 first list");
  await page.getByTestId("tools-save").click();
  const row = page.getByTestId(`tool-${TOOL}`);
  await expect(row).toBeVisible({ timeout: 20_000 });
  await expect(row).toContainText("Pre-approved");
  await expect(row).toContainText("High");
  await expect(row).toContainText(later);
  await expect(page.getByTestId("tools-set")).toContainText("Version 1");
  await expect(page.getByTestId("tools-set")).toContainText("Canary AI risk owner");
});

test("a version that adds only that tool is shown as approved in advance, with the entry that covered it", async ({ page }) => {
  const department = sql("select id from department where tenant_id = 'canary' and name = 'Verification';").trim();
  const made = await call(ENGINEER, "/agents/register", { tenant_id: "canary", name: NAME, department_id: department, registered_by: ENGINEER, purpose: "verification fixture" });
  expect(made.status).toBe(201);
  const agent = made.json.id as string;
  const version = (tools: string[]) =>
    call(ENGINEER, `/agents/${agent}/versions`, { code_hash: `git:${Math.random().toString(16).slice(2)}${Date.now()}`, source_path: "/checkout/agent", model_id: "qwen2.5:7b", registered_by: ENGINEER, tool_scope: tools, declares_no_tools: tools.length === 0 });
  expect((await version([])).status).toBe(201);
  expect((await version([TOOL])).status).toBe(201);
  await loginAs(page, REVIEWER);
  await page.goto("/agent-changes");
  await page.getByTestId("changes-filter-approved_in_advance").click({ timeout: 20_000 });
  const card = page.getByTestId(`change-${NAME}-2`);
  await expect(card).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("change-state-2")).toHaveText("Approved in advance");
  await expect(page.getByTestId(`covered-${TOOL}`)).toContainText("approved up to High, so nobody is asked");
  await expect(page.getByTestId(`covered-${TOOL}`)).toContainText("Canary AI risk owner");
});
