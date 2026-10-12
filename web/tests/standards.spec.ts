/**
 * U194 in the console: de-identification standards, and choosing one when a result is decided.
 *
 * The claims these tests carry, on the live platform and in the test organisation only:
 *   - everybody signed in can read the standards, with the platform's built-in standard beside the organisation's own, and only the data protection officer is offered a way to set one;
 *   - a new bar starts from the built-in bar, so making one never drops a limit it has;
 *   - a standard the officer makes is shown with its version, who set it, when and why, and a change adds a version and keeps the earlier one to read;
 *   - on a result waiting for a decision, the reviewer chooses a standard and the recommendation under it changes, with the figures for each kind of identifier beside what the standard asks.
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
       delete from deidentification_standard_version where standard_id in (select id from deidentification_standard where name like 'U194%');
       delete from deidentification_standard where name like 'U194%';`);
}

test.beforeAll(clean);
test.afterAll(clean);

test.describe("U194: de-identification standards", () => {
  test("a reviewer reads the built-in bar, is offered no way to set one, and says why", async ({ page }) => {
    await loginAs(page, REVIEWER);
    await page.goto("/deidentification-standards");
    await expect(page.getByRole("heading", { name: "De-identification standards" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("ds-sidebar").getByRole("link", { name: "De-identification standards" })).toBeVisible();
    await expect(page.getByTestId("standard-card-Platform default")).toBeVisible();
    await expect(page.getByTestId("standard-card-Platform default").getByTestId("standard-overall")).toContainText("98%");
    await expect(page.getByTestId("standard-card-Platform default").getByTestId("standard-kind-PERSON")).toContainText("None");
    await expect(page.getByTestId("new-standard-open")).toHaveCount(0);
    await expect(page.getByTestId("standards-cannot-set")).toContainText("Only the data protection officer may set a de-identification standard");
  });

  test("the page says what a standard is for and explains it with a worked example whose figures add up", async ({ page }) => {
    await loginAs(page, REVIEWER);
    await page.goto("/deidentification-standards");
    await expect(page.getByRole("heading", { name: "De-identification standards" })).toBeVisible({ timeout: 20_000 });
    await expect(page.locator("main")).toContainText("A de-identification standard is what a de-identified result has to meet before it can be released to a particular audience");
    const how = page.getByTestId("standards-how-to-read");
    for (const word of ["Removed, at least", "Left in the clear, at most", "Over all kinds together", "Kind of identifier", "No minimum", "None may remain", "No maximum"]) {
      await expect(how).toContainText(word);
    }
    // The old words that did not say which side they were about are gone from the table.
    await expect(page.getByTestId("standard-card-Platform default")).not.toContainText("No limit");
    await expect(page.getByTestId("standard-card-Platform default")).not.toContainText("None allowed");
    // The example: 100 names and 100 addresses in the key, all names and 94 addresses removed, so 6 left and 194 of 200, which is 97%.
    const result = page.getByTestId("standards-example-result");
    await expect(result).toContainText("100 names and 100 addresses");
    await expect(result).toContainText("6 addresses are left in the clear");
    await expect(result).toContainText("it removes 194 of the 200 details, which is 97%");
    expect(Math.round(((100 + 94) / 200) * 100)).toBe(97);
    await expect(page.getByTestId("standards-example-internal")).toContainText("Clears it.");
    await expect(page.getByTestId("standards-example-public")).toContainText("Does not clear it.");
    // No dash used as punctuation, no industry word.
    const text = await how.innerText();
    expect(text).not.toMatch(/[–—]/);
    expect(text).not.toMatch(/patient|clinic|hospital|doctor|nurse/i);
  });

  test("the data protection officer makes a standard that starts from the built-in one, and sees who set it and when", async ({ page }) => {
    await loginAs(page, OFFICER);
    await page.goto("/deidentification-standards");
    await page.getByTestId("new-standard-open").click();
    await expect(page.getByTestId("limit-overall")).toHaveValue("98");
    await expect(page.getByTestId("limit-PERSON-most")).toHaveValue("0");
    await expect(page.getByTestId("limit-EMAIL-most")).toHaveValue("0");
    await expect(page.getByTestId("limit-DATE-recall")).toHaveValue("");
    await page.getByTestId("new-standard-name").fill(BAR);
    await page.getByTestId("limit-overall").fill("95");
    await page.getByTestId("limit-DATE-recall").fill("90");
    await page.getByTestId("new-standard-reason").fill("The closed research team inside the organisation");
    await page.getByTestId("new-standard-save").click();
    const card = page.getByTestId(`standard-card-${BAR}`);
    await expect(card).toBeVisible({ timeout: 20_000 });
    await expect(card.getByTestId("standard-set")).toContainText("Version 1 by Canary oversight on ");
    await expect(card).toContainText("The closed research team inside the organisation");
    await expect(card.getByTestId("standard-overall")).toContainText("95%");
    await expect(card.getByTestId("standard-kind-DATE")).toContainText("90%");
    await expect(card.getByTestId("standard-kind-PERSON")).toContainText("None");
  });

  test("a bad figure is refused where the person is, and a change adds a version and keeps the first", async ({ page }) => {
    await loginAs(page, OFFICER);
    await page.goto("/deidentification-standards");
    const card = page.getByTestId(`standard-card-${BAR}`);
    await card.getByTestId("change-standard-open").click();
    await page.getByTestId("limit-overall").fill("150");
    await page.getByTestId("change-standard-reason").fill("A mistake");
    await page.getByTestId("change-standard-save").click();
    await expect(page.getByRole("alert")).toContainText("from 0 to 100");
    await page.getByTestId("limit-overall").fill("97");
    await page.getByTestId("change-standard-reason").fill("The team asked for a tighter standard");
    await page.getByTestId("change-standard-save").click();
    await expect(card.getByTestId("standard-set")).toContainText("Version 2 by Canary oversight on ", { timeout: 20_000 });
    await expect(card.getByTestId("standard-overall")).toContainText("97%");
    await card.getByTestId("standard-history-toggle").click();
    await expect(card.getByTestId("standard-version-2")).toContainText("The team asked for a tighter standard");
    await expect(card.getByTestId("standard-version-1")).toContainText("The closed research team inside the organisation");
    await expect(card.getByTestId("standard-version-1").getByTestId("standard-overall")).toContainText("95%");
  });
});

test.describe("U194: choosing a standard for a waiting result", () => {
  test("the recommendation follows the standard chosen, with the figures for each kind beside what the standard asks", async ({ page }) => {
    const inserted = sql(`with v as (select id from dataset_version where tenant_id = 'canary' order by created_at desc limit 1)
      insert into gate_decision (id, tenant_id, dataset_version_id, to_class, score_card_id, metrics, recommendation, recommendation_reason, state, triggered_by, handoff)
      select '${DECISION}', 'canary', v.id, 'OPEN_FOR_ANNOTATION', 'u194',
        '{"ground_truth_spans":150,"destroyed_by_asr":0,"recall_effective":0.9733,"leaks_total":4,"leaks_direct":0,"leaks_quasi":4,"recall_threshold":0.98,"per_entity":{"PERSON":{"total":50,"destroyed":0,"effective":50,"leaked":0},"DATE":{"total":100,"destroyed":0,"effective":96,"leaked":4}}}'::jsonb,
        'fail', 'Removed 97.3% of identifying detail, below the 98% asked (4 of 150 left in the clear)', 'pending', 'canary-engineer', '{}'::jsonb from v returning id;`);
    expect(inserted).toContain(DECISION);

    await loginAs(page, REVIEWER);
    await page.goto(`/gates/${DECISION}`);
    await expect(page.getByTestId("gate-standard")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("gate-standard-select")).toHaveValue("");
    await expect(page.getByTestId("gate-recommendation")).toContainText("does not clear the Platform default standard");
    await expect(page.getByTestId("gate-recommendation")).toContainText("Removed 97.3% of identifying detail, below the 98% asked");
    await expect(page.getByTestId("gate-kind-PERSON")).toContainText("none left in the clear");
    await expect(page.getByTestId("gate-kind-DATE")).toContainText("96.0%");

    await page.getByTestId("gate-standard-select").selectOption({ label: `${BAR} (version 2)` });
    // Version 2 asks 97 percent overall and 90 percent of dates: 146 of 150 is 97.3 percent and 96 of 100 dates is 96 percent, so the same counts now pass.
    await expect(page.getByTestId("gate-recommendation")).toContainText(`clears the ${BAR} standard`, { timeout: 20_000 });
    await expect(page.getByTestId("gate-recommendation")).not.toContainText("does not clear");
    await expect(page.getByTestId("gate-kind-DATE")).toContainText("at least 90% removed");

    await page.getByTestId("gate-standard-select").selectOption("");
    await expect(page.getByTestId("gate-recommendation")).toContainText("does not clear the Platform default standard", { timeout: 20_000 });
  });
});
