/**
 * U199 in the console: an agent's first submission is decided once, an agent has a sponsor and is re-confirmed, the records can be checked for changes, and the evidence pack says which
 * outcomes its records support.
 *
 * The claims these tests carry, on the live platform and in the test organisation only:
 *   - the person registering an agent can propose its level in the same form, and the officer then sees the level and the first version together and decides both once;
 *   - the department approver still gives the department's own approval, and is told they may not take the officer's decision;
 *   - the agent page shows who sponsors the agent, who may assign one and who may not, and the re-confirmation clock the level sets;
 *   - checking the records says nothing was changed, and says so in words when a record was changed with the database bypassed;
 *   - the evidence pack carries the head of the chain and a table that says what its records are, including what is not provided.
 *
 * The agent is registered through the form, which is part of the claim. Its rows in the oversight tables and the levels are removed at the end; the agent and its versions are sealed and stay,
 * named U199 so the canary tidy recognises them.
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
const NAME = `U199 agent ${Date.now()}`;
let agentId = "";

function sql(statement: string): string {
  return execSync("wsl.exe -- docker exec -i munitas-postgres-1 psql -U munitas -d platform -At -v ON_ERROR_STOP=1", { input: statement, encoding: "utf8", timeout: 60_000 });
}

function clean(): void {
  const mine = "select id from agent where name like 'U199%'";
  sql(`delete from agent_output_review where agent_id in (${mine});
       delete from agent_run where agent_id in (${mine}) and purpose like 'U199%';
       delete from agent_record_chain where agent_id in (${mine});
       delete from agent_change_decision where approval_id in (select id from agent_change_approval where agent_id in (${mine}));
       delete from agent_change_approval where agent_id in (${mine});
       delete from agent_evidence_export where agent_id in (${mine});
       delete from agent_incident where agent_id in (${mine});
       delete from agent_suspension where agent_id in (${mine});
       delete from agent_sponsorship where agent_id in (${mine});
       delete from agent_reconfirmation where agent_id in (${mine});
       delete from agent_risk_record where agent_id in (${mine});
       delete from agent_risk_level_list where tenant_id = 'canary';`);
}

test.beforeAll(clean);
test.afterAll(() => {
  sql("alter table agent_incident enable trigger agent_incident_no_edit;");
  clean();
});

async function call(who: string, path: string, body: unknown): Promise<{ status: number; json: any }> {
  const response = await fetch(`${API_BASE}${path}`, { method: "POST", headers: { ...(await bearerFor(who)), "content-type": "application/json" }, body: JSON.stringify(body) });
  return { status: response.status, json: await response.json().catch(() => null) };
}

test.describe("U199: first submission, sponsor, chain and mapping", () => {
  test("the officer writes levels that ask for re-confirmation, and the page shows it", async ({ page }) => {
    const levels = [
      { name: "Low", description: "An agent checked by whoever reads its output.", framework_label: null, second_approver: false, blocks: false, reconfirm_days: null },
      { name: "Moderate", description: "An agent whose output informs a decision.", framework_label: null, second_approver: false, blocks: false, reconfirm_days: 90 },
      { name: "High", description: "An agent whose output can change what happens to a person.", framework_label: "Example framework: high impact", second_approver: true, blocks: false, reconfirm_days: 30 },
      { name: "Prohibited", description: "An agent the organisation will not run.", framework_label: null, second_approver: false, blocks: true, reconfirm_days: null },
    ];
    expect((await call(OFFICER, "/risk-levels", { levels, enforce: true, reason: "U199 fixture" })).status).toBe(201);
    await loginAs(page, OFFICER);
    await page.goto("/risk-levels");
    await expect(page.getByTestId("level-high")).toContainText("30 days", { timeout: 20_000 });
    await expect(page.getByTestId("level-low")).toContainText("Never");
    await page.getByTestId("levels-edit").click();
    await expect(page.getByTestId("level-reconfirm-2")).toHaveValue("30");
  });

  test("the engineer proposes a level in the registration form", async ({ page }) => {
    await loginAs(page, ENGINEER);
    await page.goto("/agents/register");
    await page.getByTestId("agent-name").fill(NAME);
    await page.getByTestId("agent-department").selectOption({ label: "Verification" });
    await page.getByTestId("agent-purpose").fill("Ranks files for a person to read");
    await page.getByTestId("agent-level").selectOption({ label: "Moderate" });
    await expect(page.getByTestId("register-agent-submit")).toBeDisabled();
    await page.getByTestId("agent-level-affected").fill("The people the files are about");
    await page.getByTestId("agent-level-reason").fill("A person reads every ranking");
    await page.getByTestId("register-agent-submit").click();
    await expect(page.getByTestId("risk-proposal")).toContainText("Proposed: Moderate", { timeout: 20_000 });
    agentId = page.url().split("/agents/")[1];
    expect(agentId).toMatch(/^[0-9a-f-]{36}$/);
  });

  test("the first version arrives with the level, the department approves its part, and the department approver may not take the officer's", async ({ page }) => {
    const first = await call(ENGINEER, `/agents/${agentId}/versions`, { code_hash: `git:${Date.now()}`, source_path: "/checkout/agent", model_id: "qwen2.5:7b", tool_scope: ["read_dataset_version"], registered_by: ENGINEER });
    expect(first.status).toBe(201);
    expect(first.json.widening).toBe(true);

    await loginAs(page, CUSTODIAN);
    await page.goto("/agent-changes");
    const card = page.getByTestId(`change-${NAME}-1`);
    await expect(card).toBeVisible({ timeout: 20_000 });
    await expect(card.getByTestId("cannot-officer-1")).toContainText("AI risk owner");
    await expect(card.getByTestId("first-submission-1")).toHaveCount(0);
    await card.getByTestId("decide-reason-department-1").fill("Checked the tool");
    await card.getByTestId("approve-department-1").click();
    await expect(card.getByTestId("waiting-for-1")).toContainText("Waiting for the AI risk owner", { timeout: 20_000 });
  });

  test("the officer sees the level and the version together and decides both in one step", async ({ page }) => {
    await loginAs(page, OFFICER);
    await page.goto("/agent-changes");
    const card = page.getByTestId(`change-${NAME}-1`);
    await expect(card.getByTestId("first-submission-1")).toBeVisible({ timeout: 20_000 });
    await expect(card.getByTestId("first-proposal-1")).toContainText("Level proposed: Moderate");
    await expect(card.getByTestId("first-proposal-1")).toContainText("The people the files are about");
    await expect(card.getByTestId("approve-first-1")).toBeDisabled();
    await card.getByTestId("first-level-1").selectOption({ label: "High" });
    await card.getByTestId("first-reason-1").fill("The rankings name people");
    const decided = page.waitForResponse((r) => r.url().includes("/first-submission"));
    await card.getByTestId("approve-first-1").click();
    expect((await decided).status()).toBe(201);
    await page.getByTestId("changes-filter-approved").click();
    await expect(card.getByTestId("change-state-1")).toContainText("Approved", { timeout: 20_000 });
    await expect(card.getByTestId("decisions-1")).toContainText("Approved for the AI risk owner by Canary AI risk owner on ");
    await page.goto(`/agents/${agentId}`);
    await expect(page.getByTestId("risk-confirmed")).toContainText("High", { timeout: 20_000 });
    await expect(page.getByTestId("risk-confirmed")).toContainText("The rankings name people");
    await expect(page.getByTestId("risk-confirmed")).toContainText("The people the files are about");
  });

  test("the agent page shows its sponsor and its re-confirmation clock, and who may assign and re-confirm", async ({ page }) => {
    await loginAs(page, REVIEWER);
    await page.goto(`/agents/${agentId}`);
    await expect(page.getByTestId("sponsor-none")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("reconfirm-clock")).toContainText("every 30 days");
    await expect(page.getByTestId("cannot-assign")).toContainText("may assign its sponsor");
    await expect(page.getByTestId("cannot-reconfirm")).toContainText("may re-confirm it");

    await loginAs(page, CUSTODIAN);
    await page.goto(`/agents/${agentId}`);
    await page.getByTestId("sponsor-select").selectOption({ label: "Canary custodian" });
    await page.getByTestId("sponsor-reason").fill("The department answers for it");
    await page.getByTestId("sponsor-assign").click();
    await expect(page.getByTestId("sponsor-current")).toContainText("Sponsor: Canary custodian, assigned by Canary custodian on ", { timeout: 20_000 });
    await page.getByTestId("reconfirm-note").fill("Still needed, still High");
    await page.getByTestId("reconfirm-send").click();
    await page.getByTestId("sponsor-history-toggle").click();
    await expect(page.getByTestId("sponsor-history")).toContainText("Re-confirmed by Canary custodian on ", { timeout: 20_000 });
  });

  test("the registry shows the sponsor", async ({ page }) => {
    await loginAs(page, REVIEWER);
    await page.goto("/agent-registry");
    await page.getByTestId("registry-search").fill(NAME);
    await expect(page.getByTestId(`registry-row-${NAME}`)).toContainText("Sponsor: Canary custodian", { timeout: 20_000 });
    await expect(page.getByTestId(`registry-flags-${NAME}`)).not.toContainText("No sponsor");
  });

  test("checking the records says nothing was changed, and says what changed when a record was altered underneath", async ({ page }) => {
    await call(ENGINEER, `/agents/${agentId}/incidents`, { what: "U199 the first thing that happened" });
    await loginAs(page, OFFICER);
    await page.goto(`/agents/${agentId}`);
    await page.getByTestId("chain-check").click();
    await expect(page.getByTestId("chain-result")).toContainText("None was changed, removed or added outside the platform", { timeout: 20_000 });

    sql("alter table agent_incident disable trigger agent_incident_no_edit; update agent_incident set what = 'U199 changed afterwards' where what like 'U199 the first%'; alter table agent_incident enable trigger agent_incident_no_edit;");
    await page.getByTestId("chain-check").click();
    await expect(page.getByTestId("chain-problems")).toContainText("no longer says what it said when it was written", { timeout: 20_000 });
    await expect(page.getByTestId("chain-problems")).toContainText("incident");
  });

  test("somebody other than the person who started a finished run reviews its draft in the console, and the person who started it is told somebody else has to", async ({ page }) => {
    const detail = await fetch(`${API_BASE}/agents/${agentId}`, { headers: await bearerFor(ENGINEER) }).then((r) => r.json());
    const runId = "a1990000-0000-4000-8000-000000000001";
    expect((await call(ENGINEER, `/agents/${agentId}/deploy`, { agent_version_id: detail.versions[0].id })).status).toBe(201);
    sql(`delete from agent_output_review where run_id = '${runId}'; delete from agent_run where id = '${runId}';
         insert into agent_run (id, tenant_id, agent_id, agent_version_id, status, purpose, requested_by, execution_mode, findings, ended_at)
         values ('${runId}', 'canary', '${agentId}', '${detail.versions[0].id}', 'succeeded', 'U199 draft notes', 'canary-engineer', 'sandboxed',
                 $j$[{"agent":"note-drafter","documents_read":1,"drafts":[{"document":"visit-1.txt","note":"DRAFT NOTE for visit-1.txt\\nPlan:\\n  - Book a follow up in three weeks. (line 10)"}]}]$j$::jsonb,
                 now() - interval '2 minutes');`);

    await loginAs(page, ENGINEER);
    await page.goto(`/agents/${agentId}`);
    await expect(page.getByTestId(`run-draft-${runId}-0`)).toContainText("Book a follow up in three weeks. (line 10)", { timeout: 20_000 });
    await expect(page.getByTestId(`review-someone-else-${runId}`)).toContainText("Someone else has to review this");
    await expect(page.getByTestId(`review-form-${runId}`)).toHaveCount(0);

    await loginAs(page, REVIEWER);
    await page.goto(`/agents/${agentId}`);
    await expect(page.getByTestId(`review-form-${runId}`)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId(`review-accept-${runId}`)).toBeEnabled();
    await expect(page.getByTestId(`review-change-${runId}`)).toBeDisabled();
    await page.getByTestId(`review-text-${runId}`).fill("DRAFT NOTE for visit-1.txt\nPlan:\n  - Book a follow up in two weeks.");
    await expect(page.getByTestId(`review-accept-${runId}`)).toBeDisabled();
    await page.getByTestId(`review-change-${runId}`).click();
    await expect(page.getByTestId(`run-review-${runId}`)).toContainText("Changed by the reviewer by Canary reviewer on ", { timeout: 20_000 });
    await expect(page.getByTestId(`run-review-${runId}`)).toContainText("seconds after the run ended");
    await expect(page.getByTestId(`review-form-${runId}`)).toHaveCount(0);

    await loginAs(page, OFFICER);
    await page.goto(`/agents/${agentId}`);
    await expect(page.getByTestId("review-figures")).toContainText("1 reviewed: 0 accepted as they were, 1 changed, 0 refused", { timeout: 20_000 });
  });

  test("the evidence pack carries the head of the chain and says which outcomes its records support, including what it does not provide", async ({ page }) => {
    await loginAs(page, OFFICER);
    await page.goto(`/agents/${agentId}`);
    await page.getByTestId("pack-make").click();
    await expect(page.getByTestId("pack-integrity")).toContainText("Something was changed or added outside the platform", { timeout: 20_000 });
    await expect(page.getByTestId("pack-integrity")).toContainText("Head: ");
    await page.getByTestId("pack-mapping-toggle").click();
    const table = page.getByTestId("pack-mapping");
    await expect(table).toContainText("It is not a statement that the agent or the organisation complies with either");
    await expect(table).toContainText("GOVERN 2");
    await expect(table).toContainText("Article 14");
    await expect(table.getByTestId("mapping-records").first()).toContainText("Records kept");
    await expect(table.getByTestId("mapping-not-provided").first()).toContainText("Not provided");
    await expect(table.getByTestId("mapping-declared")).toContainText("Declared, not checked");
    await expect(table).toContainText("Outputs are tested for quality");
    await expect(table).toContainText("ISO/IEC 42001 is not mapped");
  });
});
