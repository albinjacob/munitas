/**
 * U168 in the console: the Limits page shows what people may ask for, and says what would happen to a request.
 *
 * The claims these tests carry, on the live platform:
 *   - with nothing set, every limit is shown with its default as the value in force, and "Not changed";
 *   - a limit an operator sets is shown with the new value, who set it, when and why, and the default beside it;
 *   - the "what would happen" answer accepts a request within the limits and refuses one outside them, naming the limit's own words;
 *   - the list of roles that may ask reads as words.
 */

import { execSync } from "node:child_process";
import { expect, test } from "@playwright/test";
import { loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial", timeout: 90_000 });

const RESEARCHER = "canary-researcher";
const BY = "U168 console test";

function inPlatform(command: string): string {
  return execSync(`wsl.exe -- bash -c "cd /mnt/c/AIProjects/ClaudeProjects/Munitas && ${command}"`, { encoding: "utf8", timeout: 60_000 });
}

function setLimit(limit: string, value: string, reason: string): void {
  inPlatform(`docker compose exec -T munitas-api python /scripts-admin/set-limit.py --tenant canary --limit ${limit} --value '${value}' --by '${BY}' --reason '${reason}'`);
}

function clean(): void {
  inPlatform(`docker exec munitas-postgres-1 psql -U munitas -d platform -c \\"delete from limit_version where set_by = '${BY}'\\"`);
}

test.beforeAll(clean);
test.afterAll(clean);

test.describe("U168: limits", () => {
  test("with nothing set, every limit shows its default and says it was not changed", async ({ page }) => {
    await loginAs(page, RESEARCHER);
    await page.goto("/limits");
    await expect(page.getByRole("heading", { name: "Limits" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("limit-locked_rows.most_rows-value")).toHaveText("20 rows");
    await expect(page.getByTestId("limit-locked_rows.most_days-value")).toHaveText("7 days");
    await expect(page.getByTestId("limit-files_in_page.most_files-value")).toHaveText("10 files");
    await expect(page.getByTestId("limit-files_in_page.most_hours-value")).toHaveText("1 hour");
    await expect(page.getByTestId("limit-files_in_notebook.most_hours-value")).toHaveText("8 hours");
    await expect(page.getByTestId("limit-locked_rows.who_may_ask-value")).toHaveText("a data engineer, a de-identification reviewer, a privacy auditor, the data protection officer");
    for (const key of ["locked_rows.who_may_ask", "locked_rows.most_rows", "files_in_page.most_files", "files_in_notebook.most_files"]) {
      await expect(page.getByTestId(`limit-${key}-who`)).toHaveText("Not changed");
    }
  });

  test("the two pages say which question each answers, and link to each other", async ({ page }) => {
    await loginAs(page, RESEARCHER);
    await page.goto("/limits");
    await page.getByTestId("limits-to-roles").getByRole("link", { name: "What each role can do" }).click();
    await expect(page.getByRole("heading", { name: "What each role can do" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("roles-to-limits")).toContainText("is set for each organisation on the Limits page");
    await page.getByTestId("roles-to-limits").getByRole("link", { name: "Limits" }).click();
    await expect(page.getByRole("heading", { name: "Limits" })).toBeVisible({ timeout: 20_000 });
  });

  test("a limit an operator sets shows the value, who set it, when and why", async ({ page }) => {
    setLimit("locked_rows.most_rows", "30", "the audit asked for thirty");
    await loginAs(page, RESEARCHER);
    await page.goto("/limits");
    const row = page.getByTestId("limit-locked_rows.most_rows");
    await expect(row).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("limit-locked_rows.most_rows-value")).toHaveText("30 rows");
    const who = page.getByTestId("limit-locked_rows.most_rows-who");
    await expect(who).toContainText(`by ${BY} on `);
    await expect(who).toContainText("the audit asked for thirty");
    await expect(row).toContainText("20 rows");
    await expect(row).toContainText("100 rows");
    await expect(page.getByTestId("limit-locked_rows.most_days-who")).toHaveText("Not changed");
  });

  test("the test accepts a request within the limits and refuses one outside them, in the limit's own words", async ({ page }) => {
    await loginAs(page, RESEARCHER);
    await page.goto("/limits");
    await expect(page.getByTestId("test-run")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("test-capability").selectOption("locked_rows");
    await page.getByTestId("test-role").selectOption("pipeline_operator");
    await page.getByTestId("test-size").fill("30");
    await page.getByTestId("test-length").fill("3");
    await page.getByTestId("test-run").click();
    await expect(page.getByTestId("test-answer")).toContainText("It would be accepted.");
    await page.getByTestId("test-size").fill("31");
    await page.getByTestId("test-run").click();
    await expect(page.getByTestId("test-answer")).toContainText("It would be refused:");
    await expect(page.getByTestId("test-answer")).toContainText("a lease opens between 1 and 30 rows");
    await page.getByTestId("test-size").fill("5");
    await page.getByTestId("test-role").selectOption("analyst");
    await page.getByTestId("test-run").click();
    await expect(page.getByTestId("test-answer")).toContainText("this person holds no role that may ask for a lease on locked columns");
    await page.getByTestId("test-capability").selectOption("files_in_notebook");
    await page.getByTestId("test-size").fill("200");
    await page.getByTestId("test-length").fill("8");
    await page.getByTestId("test-run").click();
    await expect(page.getByTestId("test-answer")).toContainText("It would be accepted.");
  });
});
