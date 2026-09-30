/**
 * U72 in the console: a role is asked for on one screen and granted by
 * somebody else on the same screen, or not at all.
 *
 * The claim these tests carry is not that the section renders. It is that
 * the separation the policy engine states survives the trip through a
 * browser: the person who asked sees no buttons on their own ask, the
 * administrator who records the decision is refused in the policy's own
 * words, and a granted role arrives unconfirmed rather than quietly
 * counting as reviewed.
 *
 * Serial, and deliberately so. Each test acts on the row the one before it
 * left, which is what the flow actually is: ask, decide, confirm, withdraw.
 * Splitting them into four independent tests would need four asks and would
 * prove less, because nothing would then check that a decision is visible to
 * the next person who opens the screen.
 */

import { expect, test } from "@playwright/test";
import { loginAs } from "./auth-helpers";
import { ADMIN, ASKER, CUSTODIAN, WANTED, clearTheRole } from "./role-fixtures";
import { roleLabel } from "../src/api/roles";

// The console shows a role by its human-readable label (RolesHeld.tsx),
// never the raw slug WANTED itself carries for API/fixture matching. Text
// assertions against what a person actually sees check for this instead.
const WANTED_LABEL = roleLabel(WANTED);

test.describe.configure({ mode: "serial" });

const WHY = "covering de-identification reviews for a fortnight";

test.describe("U72: roles are asked for and granted by different people", () => {
  test.beforeAll(clearTheRole);
  test.afterAll(clearTheRole);

  test("a person asks for a role, and cannot decide their own ask", async ({
    page,
  }) => {
    await loginAs(page, ASKER);
    await page.goto("/directory");

    await page.getByTestId("ask-role").fill(WANTED);
    await page.getByTestId("ask-reason").fill(WHY);
    await page.getByTestId("ask-submit").click();

    const asks = page.getByTestId("role-asks");
    await expect(asks).toContainText(WANTED_LABEL);
    await expect(asks).toContainText(WHY);

    // Said rather than hidden. A missing button leaves somebody wondering
    // whether the screen is broken.
    await expect(asks).toContainText("your own ask");
    await expect(asks.getByTestId("decide-approve")).toHaveCount(0);
  });

  test("the administrator who records the decision cannot make it", async ({
    page,
  }) => {
    await loginAs(page, ADMIN);
    await page.goto("/directory");

    const asks = page.getByTestId("role-asks");
    await asks.getByTestId("decide-reason").first().fill("seems reasonable");
    await asks.getByTestId("decide-approve").first().click();

    // The policy's own sentence, shown as written. Not paraphrased by this
    // console into an apology that says nothing about what to do next.
    await expect(page.getByTestId("failure")).toContainText(
      "holds no role that may decide who holds a role",
    );
  });

  test("a custodian decides it, and the role arrives unconfirmed", async ({
    page,
  }) => {
    await loginAs(page, CUSTODIAN);
    await page.goto("/directory");

    const asks = page.getByTestId("role-asks");
    await asks.getByTestId("decide-reason").first().fill("Imani is away");
    await asks.getByTestId("decide-approve").first().click();

    const held = page.getByTestId("roles-held");
    await expect(held).toContainText(WANTED_LABEL);
    // Hartley, not cust-hartley. Every other table on this console shows a
    // person rather than the id stored against the row.
    await expect(held).toContainText("Hartley");
    // The part every platform skips: granted is not reviewed, and the
    // screen says so instead of leaving the column blank.
    await expect(held).toContainText("never checked");
  });

  test("confirming it records that somebody looked, and withdrawing ends it", async ({
    page,
  }) => {
    await loginAs(page, CUSTODIAN);
    await page.goto("/directory");

    const row = page
      .getByTestId("roles-held")
      .locator("tr")
      .filter({ hasText: WANTED_LABEL })
      .first();

    await row.getByTestId("attest-keep").click();
    await expect(row).toContainText("checked");
    await expect(row).not.toContainText("never checked");

    // Also the teardown. A withdrawn grant leaves the list, so a second run
    // starts where this one did.
    await row.getByTestId("attest-withdraw").click();
    await expect(
      page.getByTestId("roles-held").locator("tr").filter({ hasText: WANTED_LABEL }),
    ).toHaveCount(0);
  });
});
