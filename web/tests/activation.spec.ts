/**
 * U75 on screen: approved storage access that has not taken effect yet.
 *
 * The real states need storage permissions failing to print, which means
 * stopping storage (verify/v76_activation_live.py does that, by hand). The
 * housekeeping tests load the real screen and change only the fields that say
 * whether access is in effect; the parked-run and audit-log tests make up the
 * data the page is given, so none of them depends on what the organisation
 * holds and none can skip. So what they prove is that each screen shows those states correctly when
 * the API reports them, not that the API reports them; U75 and U76 prove
 * that half.
 */

import { expect, test, type Page } from "@playwright/test";
import { loginAs } from "./auth-helpers";

/** Rewrite one JSON response on its way to the page. */
async function rewrite(page: Page, pattern: string, change: (body: any) => any) {
  await page.route(pattern, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: change(body) });
  });
}


function minutesAgo(n: number): string {
  return new Date(Date.now() - n * 60_000).toISOString();
}

test.describe("U75: the housekeeping screen raises an outage, not individual runs", () => {
  test("an outage the platform is retrying says so, and how many runs wait", async ({ page }) => {
    await loginAs(page, "ops-priya");
    await rewrite(page, "**/housekeeping/storage", (body) =>
      body.scope !== "platform" ? body : {
        ...body,
        storage_permissions: {
          ...body.storage_permissions,
          failing: true, alert: true, retryable: true,
          failing_since: minutesAgo(12),
          reason: "cannot read identity config: the storage filer is not reachable",
          parked_runs: 3,
        },
      });
    await page.goto("/housekeeping");
    const alert = page.getByTestId("activation-alert");
    await expect(alert).toContainText("New storage access has not taken effect for 12 minutes.");
    await expect(alert).toContainText("the storage filer is not reachable");
    await expect(alert).toContainText("The platform keeps retrying");
    await expect(alert).toContainText("3 runs are waiting and will continue by themselves.");
    await expect(alert).not.toContainText("Retrying will not fix this");
  });

  test("a failure retrying cannot fix says what to do, and one run reads as one", async ({ page }) => {
    await loginAs(page, "ops-priya");
    await rewrite(page, "**/housekeeping/storage", (body) =>
      body.scope !== "platform" ? body : {
        ...body,
        storage_permissions: {
          ...body.storage_permissions,
          failing: true, alert: true, retryable: false,
          failing_since: minutesAgo(1),
          reason: "the safety guard refused a print that would remove too much access",
          parked_runs: 1,
        },
      });
    await page.goto("/housekeeping");
    const alert = page.getByTestId("activation-alert");
    await expect(alert).toContainText("for 1 minute.");
    await expect(alert).toContainText("Retrying will not fix this.");
    await expect(alert).toContainText("run reconcile-grants.py");
    await expect(alert).toContainText("1 run is waiting and will continue by itself.");
  });

  test("with nothing failing there is no banner", async ({ page }) => {
    await loginAs(page, "ops-priya");
    await rewrite(page, "**/housekeeping/storage", (body) =>
      body.scope !== "platform" ? body : {
        ...body,
        storage_permissions: { ...body.storage_permissions, failing: false, alert: false },
      });
    await page.goto("/housekeeping");
    await expect(page.getByTestId("volumes-used").or(page.getByText("containers in use")).first())
      .toBeVisible();
    await expect(page.getByTestId("activation-alert")).toHaveCount(0);
  });

  test("a failure that is failing but not yet alerting shows no banner", async ({ page }) => {
    await loginAs(page, "ops-priya");
    await rewrite(page, "**/housekeeping/storage", (body) =>
      body.scope !== "platform" ? body : {
        ...body,
        storage_permissions: {
          ...body.storage_permissions,
          failing: true, alert: false, retryable: true,
          failing_since: minutesAgo(1), reason: "brief", parked_runs: 1,
        },
      });
    await page.goto("/housekeeping");
    await expect(page.getByText("containers in use").first()).toBeVisible();
    await expect(page.getByTestId("activation-alert")).toHaveCount(0);
  });
});

test.describe("U75: a parked run says it is waiting for access, not stuck", () => {
  test("the run reads 'access taking effect' and explains itself", async ({ page }) => {
    // The agent, its deployed version and its run are all made up here, so the test does not depend on which
    // agents the organisation happens to hold today. The page shows runs only for an agent with a deployed version.
    const agentId = "stub-agent";
    const versionId = "stub-agent-version";
    const runId = "stub-run";
    const agent = {
      id: agentId,
      tenant_id: "canary",
      name: "stub-agent",
      purpose: "an agent made up for this test",
      principal_id: "canary-agent",
      created_at: minutesAgo(60),
      department_name: null,
      registered_by_label: "Canary engineer",
      version_count: 1,
      latest_version: 1,
      latest_code_hash: "git:stub",
      versions: [
        {
          id: versionId, version: 1, code_hash: "git:stub", source_path: "/stub", image_digest: "native:stub",
          model_id: "none", tool_scope: [], content_hash: "sha256:stub", sealed: true, created_at: minutesAgo(60),
          registered_by_label: "Canary engineer", sandboxed: false, entrypoint: "", requested_hosts: [],
          egress_approval_id: null, egress_state: null,
        },
      ],
      active_version: { agent_version_id: versionId, deployed_at: minutesAgo(50) },
    };
    const run = {
      id: runId, agent_version_id: versionId, agent_version: 1, status: "awaiting_activation",
      purpose: "a run made up for this test", requested_by: "canary-engineer", requested_by_label: "Canary engineer",
      tool_calls: 0, halted_reason: null, error: null, started_at: minutesAgo(5), ended_at: null,
      approved_by: "canary-custodian", approved_at: minutesAgo(2), lease_request_id: null, findings: [],
      execution_mode: "native",
    };

    await loginAs(page, "canary-engineer");
    await page.route(`**/agents/${agentId}/runs**`, (route) => route.fulfill({ json: [run] }));
    await page.route(`**/agents/${agentId}?**`, (route) => route.fulfill({ json: agent }));
    await page.goto(`/agents/${agentId}`);

    const row = page.getByTestId("agent-runs").locator("li").first();
    await expect(row).toContainText("access taking effect");
    await expect(page.getByTestId(`run-activation-${runId}`)).toHaveText(
      "Approved; waiting for storage access to take effect. The run continues on its own once it has.",
    );
    // Nobody acts on a run waiting for activation: no approve button on it.
    await expect(row.getByRole("button", { name: "Approve" })).toHaveCount(0);
  });
});

/** A page of the audit log made up for the test: three allowed grants, of which `waiting` are not in effect yet. */
function auditPage(waiting: number[]) {
  const decisions = [1, 2, 3].map((id) => ({
    id,
    at: minutesAgo(id),
    principal: "canary-engineer",
    principal_kind: "human",
    principal_roles: ["data_engineer"],
    tenant_id: "canary",
    dataset_version_id: null,
    requested_class: null,
    purpose: "a request made up for this test",
    allowed: true,
    reasons: [],
    lease_id: null,
    phase: "grant",
    agent_run_id: null,
    active: !waiting.includes(id),
  }));
  return { decisions, shown: decisions.length, total: decisions.length, limit: 15, offset: 0 };
}

test.describe("U75: the audit log says when given access is not in effect yet", () => {
  // The rows are made up, so these tests never depend on the organisation having a grant to mark, and never skip.
  test("one allowed grant not yet active reads 'yes, taking effect', the rest 'yes'", async ({ page }) => {
    await loginAs(page, "canary-engineer");
    await page.route("**/access-decisions**", (route) => route.fulfill({ json: auditPage([2]) }));
    await page.goto("/audit");
    await expect(page.getByTestId("audit-rows")).toBeVisible();
    await expect(page.getByTestId("audit-result-2")).toHaveText("yes, taking effect");
    await expect(page.getByTestId("audit-result-1")).toHaveText("yes");
    await expect(page.getByTestId("audit-result-3")).toHaveText("yes");
    await expect(page.getByText("yes, taking effect", { exact: true })).toHaveCount(1);
  });

  test("with everything in effect nothing reads 'taking effect'", async ({ page }) => {
    await loginAs(page, "canary-engineer");
    await page.route("**/access-decisions**", (route) => route.fulfill({ json: auditPage([]) }));
    await page.goto("/audit");
    await expect(page.getByTestId("audit-rows")).toBeVisible();
    await expect(page.getByTestId("audit-result-1")).toHaveText("yes");
    await expect(page.getByText("yes, taking effect", { exact: true })).toHaveCount(0);
  });
});
