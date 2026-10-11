/**
 * U194 in the console: release bars, and choosing one when a result is decided.
 *
 * The claims these tests carry, on the live platform and in the test organisation only:
 *   - everybody signed in can read the bars, with the platform's built-in bar beside the organisation's own, and only the data protection officer is offered a way to set one;
 *   - a new bar starts from the built-in bar, so making one never drops a limit it has;
 *   - a bar the officer makes is shown with its version, who set it, when and why, and a change adds a version and keeps the earlier one to read;
 *   - on a result waiting for a decision, the reviewer chooses a bar and the recommendation under it changes, with the figures for each kind of identifier beside what the bar asks.
 *
 * The result waiting for a decision is written here with counts that are known (150 identifiers, 146 removed, the 4 left in the clear all dates), and removed at the end with the bars.
 * Nothing is released or held back: the decision itself is proved over the API by U192.
 */

import { execSync } from "node:child_process";
import { expect, test } from "@playwright/test";
import { loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial", timeout: 90_000 });

const OFFICER = "canary-dpo";
const REVIEWER = "canary-reviewer";
const BAR = "U194 closed research team";
const DECISION = "a1940000-0000-4000-8000-000000000001";

function sql(statement: string): string {
  return execSync("wsl.exe -- docker exec -i munitas-postgres-1 psql -U munitas -d platform -At -v ON_ERROR_STOP=1", { input: statement, encoding: "utf8", timeout: 60_000 });
}

function clean(): void {
  sql(`delete from gate_decision where id = '${DECISION}';
       delete from gate_purpose_version where purpose_id in (select id from gate_purpose where name like 'U194%');
       delete from gate_purpose where name like 'U194%';`);
}

test.beforeAll(clean);
test.afterAll(clean);

test.describe("U194: release bars", () => {
  test("a reviewer reads the built-in bar, is offered no way to set one, and says why", async ({ page }) => {
    await loginAs(page, REVIEWER);
    await page.goto("/release-bars");
    await expect(page.getByRole("heading", { name: "Release bars" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("ds-sidebar").getByRole("link", { name: "Release bars" })).toBeVisible();
    await expect(page.getByTestId("release-bar-Platform default")).toBeVisible();
    await expect(page.getByTestId("release-bar-Platform default").getByTestId("bar-overall")).toContainText("98%");
    await expect(page.getByTestId("release-bar-Platform default").getByTestId("bar-kind-PERSON")).toContainText("None");
    await expect(page.getByTestId("new-bar-open")).toHaveCount(0);
    await expect(page.getByTestId("bars-cannot-set")).toContainText("Only the data protection officer may set a release bar");
  });

  test("the data protection officer makes a bar that starts from the built-in one, and sees who set it and when", async ({ page }) => {
    await loginAs(page, OFFICER);
    await page.goto("/release-bars");
    await page.getByTestId("new-bar-open").click();
    await expect(page.getByTestId("limit-overall")).toHaveValue("98");
    await expect(page.getByTestId("limit-PERSON-most")).toHaveValue("0");
    await expect(page.getByTestId("limit-EMAIL-most")).toHaveValue("0");
    await expect(page.getByTestId("limit-DATE-recall")).toHaveValue("");
    await page.getByTestId("new-bar-name").fill(BAR);
    await page.getByTestId("limit-overall").fill("95");
    await page.getByTestId("limit-DATE-recall").fill("90");
    await page.getByTestId("new-bar-reason").fill("The closed research team inside the organisation");
    await page.getByTestId("new-bar-save").click();
    const card = page.getByTestId(`release-bar-${BAR}`);
    await expect(card).toBeVisible({ timeout: 20_000 });
    await expect(card.getByTestId("bar-set")).toContainText("Version 1 by Canary oversight on ");
    await expect(card).toContainText("The closed research team inside the organisation");
    await expect(card.getByTestId("bar-overall")).toContainText("95%");
    await expect(card.getByTestId("bar-kind-DATE")).toContainText("90%");
    await expect(card.getByTestId("bar-kind-PERSON")).toContainText("None");
  });

  test("a bad figure is refused where the person is, and a change adds a version and keeps the first", async ({ page }) => {
    await loginAs(page, OFFICER);
    await page.goto("/release-bars");
    const card = page.getByTestId(`release-bar-${BAR}`);
    await card.getByTestId("change-bar-open").click();
    await page.getByTestId("limit-overall").fill("150");
    await page.getByTestId("change-bar-reason").fill("A mistake");
    await page.getByTestId("change-bar-save").click();
    await expect(page.getByRole("alert")).toContainText("from 0 to 100");
    await page.getByTestId("limit-overall").fill("97");
    await page.getByTestId("change-bar-reason").fill("The team asked for a tighter bar");
    await page.getByTestId("change-bar-save").click();
    await expect(card.getByTestId("bar-set")).toContainText("Version 2 by Canary oversight on ", { timeout: 20_000 });
    await expect(card.getByTestId("bar-overall")).toContainText("97%");
    await card.getByTestId("bar-history-toggle").click();
    await expect(card.getByTestId("bar-version-2")).toContainText("The team asked for a tighter bar");
    await expect(card.getByTestId("bar-version-1")).toContainText("The closed research team inside the organisation");
    await expect(card.getByTestId("bar-version-1").getByTestId("bar-overall")).toContainText("95%");
  });
});

test.describe("U194: choosing a bar for a waiting result", () => {
  test("the recommendation follows the bar chosen, with the figures for each kind beside what the bar asks", async ({ page }) => {
    const inserted = sql(`with v as (select id from dataset_version where tenant_id = 'canary' order by created_at desc limit 1)
      insert into gate_decision (id, tenant_id, dataset_version_id, to_class, score_card_id, metrics, recommendation, recommendation_reason, state, triggered_by, handoff)
      select '${DECISION}', 'canary', v.id, 'OPEN_FOR_ANNOTATION', 'u194',
        '{"ground_truth_spans":150,"destroyed_by_asr":0,"recall_effective":0.9733,"leaks_total":4,"leaks_direct":0,"leaks_quasi":4,"recall_threshold":0.98,"per_entity":{"PERSON":{"total":50,"destroyed":0,"effective":50,"leaked":0},"DATE":{"total":100,"destroyed":0,"effective":96,"leaked":4}}}'::jsonb,
        'fail', 'Removed 97.3% of identifying detail, below the 98% asked (4 of 150 left in the clear)', 'pending', 'canary-engineer', '{}'::jsonb from v returning id;`);
    expect(inserted).toContain(DECISION);

    await loginAs(page, REVIEWER);
    await page.goto(`/gates/${DECISION}`);
    await expect(page.getByTestId("gate-bar")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("gate-bar-select")).toHaveValue("");
    await expect(page.getByTestId("gate-recommendation")).toContainText("does not clear the Platform default bar");
    await expect(page.getByTestId("gate-recommendation")).toContainText("Removed 97.3% of identifying detail, below the 98% asked");
    await expect(page.getByTestId("gate-kind-PERSON")).toContainText("none left in the clear");
    await expect(page.getByTestId("gate-kind-DATE")).toContainText("96.0%");

    await page.getByTestId("gate-bar-select").selectOption({ label: `${BAR} (version 2)` });
    // Version 2 asks 97 percent overall and 90 percent of dates: 146 of 150 is 97.3 percent and 96 of 100 dates is 96 percent, so the same counts now pass.
    await expect(page.getByTestId("gate-recommendation")).toContainText(`clears the ${BAR} bar`, { timeout: 20_000 });
    await expect(page.getByTestId("gate-recommendation")).not.toContainText("does not clear");
    await expect(page.getByTestId("gate-kind-DATE")).toContainText("at least 90% removed");

    await page.getByTestId("gate-bar-select").selectOption("");
    await expect(page.getByTestId("gate-recommendation")).toContainText("does not clear the Platform default bar", { timeout: 20_000 });
  });
});
