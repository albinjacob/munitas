/**
 * U197 in the console: risk levels, the level an agent is held at, the approval of a version that widens an agent, suspending an agent, the registry and the evidence pack.
 *
 * The claims these tests carry, on the live platform and in the test organisation only:
 *   - everybody can read the risk levels, only the AI risk owner is offered a way to write them, and a change is a new version with who and when;
 *   - the person who registers an agent proposes a level and cannot confirm it; the officer confirms it and the page shows who and when;
 *   - once the organisation holds agents to its levels, a version that widens an agent waits, the approver of the owning department gives the department's approval, the officer gives theirs,
 *     and the page says at each step what it still waits for;
 *   - an approver of the department suspends an agent, the engineer is told they may not resume it, and the officer resumes it;
 *   - the registry lists the agent with its level, and anybody can read it; the evidence pack is made for the officer and refused, in words, for a reviewer.
 *
 * The agent is made over the API (it is the setup, not the claim). Its rows in the oversight tables and the levels are removed at the end; the agent and its versions are sealed and stay,
 * named U197 so the canary tidy recognises them.
 */

import { execSync } from "node:child_process";
import { expect, test } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { bearerFor, loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial", timeout: 120_000 });

const OFFICER = "canary-risk-owner";
const ENGINEER = "canary-engineer";
const CUSTODIAN = "canary-custodian";
const REVIEWER = "canary-reviewer";
const NAME = `U197 agent ${Date.now()}`;
let agentId = "";

function sql(statement: string): string {
  return execSync("wsl.exe -- docker exec -i munitas-postgres-1 psql -U munitas -d platform -At -v ON_ERROR_STOP=1", { input: statement, encoding: "utf8", timeout: 60_000 });
}

function clean(): void {
  const mine = "select id from agent where name like 'U197%'";
  sql(`delete from agent_change_decision where approval_id in (select id from agent_change_approval where agent_id in (${mine}));
       delete from agent_change_approval where agent_id in (${mine});
       delete from agent_evidence_export where agent_id in (${mine});
       delete from agent_incident where agent_id in (${mine});
       delete from agent_suspension where agent_id in (${mine});
       delete from agent_risk_record where agent_id in (${mine});
       delete from agent_risk_level_list where tenant_id = 'canary';`);
}

test.beforeAll(clean);
test.afterAll(clean);

async function call(who: string, path: string, body: unknown): Promise<{ status: number; json: any }> {
  const response = await fetch(`${API_BASE}${path}`, { method: "POST", headers: { ...(await bearerFor(who)), "content-type": "application/json" }, body: JSON.stringify(body) });
  return { status: response.status, json: await response.json().catch(() => null) };
}

test.describe("U197: agent oversight", () => {
  test("a reviewer reads the built-in levels and is offered no way to write them, and says why", async ({ page }) => {
    await loginAs(page, REVIEWER);
    await page.goto("/risk-levels");
    await expect(page.getByRole("heading", { name: "Risk levels" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("ds-sidebar").getByRole("link", { name: "Risk levels" })).toHaveCount(0);
    await expect(page.getByTestId("level-low")).toBeVisible();
    await expect(page.getByTestId("level-high")).toContainText("Yes");
    await expect(page.getByTestId("level-not_allowed")).toContainText("No");
    await expect(page.getByTestId("levels-edit")).toHaveCount(0);
    await expect(page.getByTestId("levels-cannot-manage")).toContainText("Only the AI risk owner may set the risk levels");
    await expect(page.getByTestId("levels-enforce-state")).toContainText("not yet held to these levels");
  });

  test("the AI risk owner writes the levels, names a framework for one, and sees who set them and when", async ({ page }) => {
    await loginAs(page, OFFICER);
    await page.goto("/risk-levels");
    await expect(page.getByTestId("ds-sidebar").getByRole("link", { name: "Risk levels" })).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("levels-edit").click();
    await expect(page.getByTestId("level-name-2")).toHaveValue("High");
    await page.getByTestId("level-framework-2").fill("Example framework: high impact");
    await page.getByTestId("levels-reason").fill("Written for the browser test");
    await page.getByTestId("levels-save").click();
    await expect(page.getByTestId("levels-set")).toContainText("Version 1 by Canary AI risk owner on ", { timeout: 20_000 });
    await expect(page.getByTestId("level-high")).toContainText("Example framework: high impact");
    await expect(page.getByTestId("levels-set")).toContainText("Written for the browser test");
  });

  test("the engineer who registered an agent proposes a level and cannot confirm it", async ({ page }) => {
    const org = await fetch(`${API_BASE}/organisation?tenant_id=canary`, { headers: await bearerFor(ENGINEER) }).then((r) => r.json());
    const department = org.departments.find((d: { name: string }) => d.name === "Verification").id;
    const registered = await call(ENGINEER, "/agents/register", { tenant_id: "canary", name: NAME, department_id: department, registered_by: ENGINEER, purpose: "ranks files for a person to read" });
    expect(registered.status).toBe(201);
    agentId = registered.json.id;
    const first = await call(ENGINEER, `/agents/${agentId}/versions`, { code_hash: `git:${Date.now()}`, source_path: "/checkout/agent", model_id: "qwen2.5:7b", tool_scope: [], declares_no_tools: true, registered_by: ENGINEER });
    expect(first.status).toBe(201);
    const deployed = await call(ENGINEER, `/agents/${agentId}/deploy`, { agent_version_id: first.json.id });
    expect(deployed.status).toBe(201);

    await loginAs(page, ENGINEER);
    await page.goto(`/agents/${agentId}`);
    await expect(page.getByTestId("risk-unclassified")).toContainText("No level has been confirmed", { timeout: 20_000 });
    await expect(page.getByTestId("risk-unclassified")).toContainText("treats it as High");
    await expect(page.getByTestId("tools-none-1")).toContainText("uses no tools");
    await expect(page.getByTestId("risk-confirm-open")).toHaveCount(0);
    await expect(page.getByTestId("risk-cannot-confirm")).toContainText("the AI risk owner");
    await page.getByTestId("risk-propose-open").click();
    await page.getByTestId("propose-level").selectOption({ label: "Moderate" });
    await page.getByTestId("propose-purpose").fill("Ranks files for a person to read");
    await page.getByTestId("propose-affected").fill("The people the files are about");
    await page.getByTestId("propose-reasoning").fill("A person reads every ranking before it is used");
    await page.getByTestId("propose-level-save").click();
    await expect(page.getByTestId("risk-proposal")).toContainText("Proposed: Moderate", { timeout: 20_000 });
    await expect(page.getByTestId("risk-proposal")).toContainText("waits for the AI risk owner");
  });

  test("the officer confirms a different level, and the page shows who and when", async ({ page }) => {
    await loginAs(page, OFFICER);
    await page.goto(`/agents/${agentId}`);
    await page.getByTestId("risk-confirm-open").click();
    await page.getByTestId("confirm-level").selectOption({ label: "High" });
    await page.getByTestId("confirm-reasoning").fill("It ranks files that name people");
    await page.getByTestId("confirm-level-save").click();
    await expect(page.getByTestId("risk-confirmed")).toContainText("High", { timeout: 20_000 });
    await expect(page.getByTestId("risk-confirmed")).toContainText("Example framework: high impact");
    await expect(page.getByTestId("risk-confirmed")).toContainText("confirmed by Canary AI risk owner on ");
    await expect(page.getByTestId("risk-confirmed")).toContainText("The people the files are about");
    await page.getByTestId("risk-history-toggle").click();
    await expect(page.getByTestId("risk-history").locator("li")).toHaveCount(2);
  });

  test("once agents are held to the levels, a widening waits for the department and then for the officer", async ({ page }) => {
    await loginAs(page, OFFICER);
    await page.goto("/risk-levels");
    await page.getByTestId("levels-edit").click();
    await page.getByTestId("levels-enforce").check();
    await page.getByTestId("levels-reason").fill("Hold agents to the levels");
    await page.getByTestId("levels-save").click();
    await expect(page.getByTestId("levels-enforce-state")).toContainText("Agents are held to these levels", { timeout: 20_000 });

    const widened = await call(ENGINEER, `/agents/${agentId}/versions`, { code_hash: `git:${Date.now() + 1}`, source_path: "/checkout/agent", model_id: "qwen2.5:7b", tool_scope: ["send_report"], registered_by: ENGINEER });
    expect(widened.status).toBe(201);
    expect(widened.json.widening).toBe(true);

    await loginAs(page, CUSTODIAN);
    await page.goto("/agent-changes");
    const card = page.getByTestId(`change-${NAME}-2`);
    await expect(card).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("change-state-2")).toContainText("Waiting for approval");
    await expect(page.getByTestId("change-list-2")).toContainText("Acts outside the platform, or may");
    await expect(page.getByTestId("change-list-2")).toContainText("does not know what it does");
    await expect(page.getByTestId("waiting-for-2")).toContainText("an approver of the department that owns the agent and the AI risk owner");
    await expect(page.getByTestId("decide-department-2")).toBeVisible();
    await expect(page.getByTestId("cannot-officer-2")).toContainText("the AI risk owner");
    await page.getByTestId("decide-reason-department-2").fill("Checked what the tool is for");
    await page.getByTestId("approve-department-2").click();
    await expect(page.getByTestId("waiting-for-2")).toContainText("Waiting for the AI risk owner", { timeout: 20_000 });
    await expect(page.getByTestId("decisions-2")).toContainText("Approved for the department that owns the agent by Canary custodian on ");

    await loginAs(page, OFFICER);
    await page.goto("/agent-changes");
    await page.getByTestId("decide-reason-officer-2").fill("Agreed");
    await page.getByTestId("approve-officer-2").click();
    await page.getByTestId("changes-filter-approved").click();
    await expect(page.getByTestId("change-state-2")).toContainText("Approved", { timeout: 20_000 });
    await expect(page.getByTestId("decisions-2")).toContainText("Approved for the AI risk owner by Canary AI risk owner on ");
  });

  test("an approver of the department suspends the agent, the engineer may not resume it, and the officer does", async ({ page }) => {
    await loginAs(page, CUSTODIAN);
    await page.goto(`/agents/${agentId}`);
    await expect(page.getByTestId("not-suspended")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("suspend-form-reason").fill("A defect was reported");
    await page.getByTestId("suspend-form-send").click();
    await expect(page.getByTestId("suspended")).toContainText("Suspended by Canary custodian on ", { timeout: 20_000 });
    await expect(page.getByTestId("suspended")).toContainText("A defect was reported");
    await expect(page.getByTestId("cannot-resume")).toContainText("second approver");

    await loginAs(page, ENGINEER);
    await page.goto(`/agents/${agentId}`);
    await expect(page.getByTestId("suspended")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("cannot-resume")).toBeVisible();

    await loginAs(page, OFFICER);
    await page.goto(`/agents/${agentId}`);
    await page.getByTestId("resume-form-reason").fill("The defect was fixed");
    await page.getByTestId("resume-form-send").click();
    await expect(page.getByTestId("not-suspended")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("suspension-history").locator("li")).toHaveCount(2);
  });

  test("the registry lists the agent with its level, and anybody can read it", async ({ page }) => {
    await loginAs(page, REVIEWER);
    await page.goto("/agent-registry");
    await expect(page.getByRole("heading", { name: "Agent registry" })).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("registry-search").fill(NAME);
    await expect(page.getByTestId("registry-count")).toContainText("Showing 1 of ");
    await expect(page.getByTestId(`registry-level-${NAME}`)).toContainText("High");
    await expect(page.getByTestId(`registry-level-${NAME}`)).toContainText("Example framework: high impact");
    await expect(page.getByTestId(`registry-flags-${NAME}`)).toContainText("A newer version has not been deployed");
    await expect(page.getByTestId(`registry-flags-${NAME}`)).not.toContainText("No confirmed risk level");
  });

  test("the evidence pack is made for the officer and refused, in words, for a reviewer", async ({ page }) => {
    await loginAs(page, REVIEWER);
    await page.goto(`/agents/${agentId}`);
    await page.getByTestId("pack-make").click();
    await expect(page.getByRole("alert").filter({ hasText: "evidence pack" })).toBeVisible({ timeout: 20_000 });

    await loginAs(page, OFFICER);
    await page.goto(`/agents/${agentId}`);
    await page.getByTestId("pack-make").click();
    await expect(page.getByTestId("pack-result")).toContainText("sha256:", { timeout: 20_000 });
    await expect(page.getByTestId("pack-download")).toBeVisible();
  });
});
