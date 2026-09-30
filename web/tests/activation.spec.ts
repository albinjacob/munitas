/**
 * U75 on screen: approved storage access that has not taken effect yet.
 *
 * The real states need storage permissions failing to print, which means
 * stopping storage (verify/v76_activation_live.py does that, by hand). These
 * tests load the real pages with real data and change only the fields that
 * say whether access is in effect, as the responses arrive in the browser.
 * So what they prove is that each screen shows those states correctly when
 * the API reports them, not that the API reports them; U75 and U76 prove
 * that half.
 */

import { expect, test, type Page } from "@playwright/test";
import { loginAs } from "./auth-helpers";
import { API_BASE } from "../config/ports";

const API = API_BASE;

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
    await loginAs(page, "canary-engineer");
    const { agents } = await page.request
      .get(`${API}/agents?tenant_id=canary`)
      .then((r) => r.json());
    let agentId: string | null = null;
    for (const agent of agents.slice(0, 40)) {
      const runs = await page.request
        .get(`${API}/agents/${agent.id}/runs?tenant_id=canary`)
        .then((r) => r.json());
      if (Array.isArray(runs) && runs.length) {
        agentId = agent.id;
        break;
      }
    }
    test.skip(!agentId, "no canary agent has a run to show");

    let runId = "";
    await rewrite(page, `**/agents/${agentId}/runs**`, (runs) => {
      runId = runs[0].id;
      return [{ ...runs[0], status: "awaiting_activation", ended_at: null }, ...runs.slice(1)];
    });
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

test.describe("U75: the audit log says when given access is not in effect yet", () => {
  test("one allowed grant not yet active reads 'yes, taking effect', the rest 'yes'", async ({ page }) => {
    await loginAs(page, "canary-engineer");
    let targetId: number | null = null;
    await rewrite(page, "**/access-decisions**", (body) => {
      const target = body.decisions.find((d: any) => d.phase === "grant" && d.allowed);
      targetId = target ? target.id : null;
      return {
        ...body,
        decisions: body.decisions.map((d: any) =>
          d.id === targetId ? { ...d, active: false } : d.active === false ? { ...d, active: true } : d),
      };
    });
    await page.goto("/audit");
    await expect(page.getByTestId("audit-rows")).toBeVisible();
    test.skip(targetId === null, "no allowed grant in the audit log to mark");
    await expect(page.getByTestId(`audit-result-${targetId}`)).toHaveText("yes, taking effect");
    await expect(page.getByText("yes, taking effect", { exact: true })).toHaveCount(1);
  });

  test("with everything in effect nothing reads 'taking effect'", async ({ page }) => {
    await loginAs(page, "canary-engineer");
    await rewrite(page, "**/access-decisions**", (body) => ({
      ...body,
      decisions: body.decisions.map((d: any) => (d.active === false ? { ...d, active: true } : d)),
    }));
    await page.goto("/audit");
    await expect(page.getByTestId("audit-rows")).toBeVisible();
    await expect(page.getByText("yes, taking effect", { exact: true })).toHaveCount(0);
  });
});
