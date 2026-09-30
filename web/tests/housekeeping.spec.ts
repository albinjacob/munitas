/**
 * U71: the housekeeping screen shows each person their own half, and refuses
 * the rest in the policy engine's own words.
 *
 * The claim is not "the page renders". It is that the same URL gives three
 * different people three different answers, decided by `may_see_housekeeping`
 * in access.rego rather than by this console, and that a refusal arrives as a
 * reason somebody can act on instead of a blank screen.
 *
 * The navigation is deliberately not the subject. AppShell hides the link
 * from a researcher, but hiding a link enforces nothing, so these tests go
 * straight to the URL the way somebody typing it would.
 */

import { expect, test } from "@playwright/test";
import { loginAs } from "./auth-helpers";

test.describe("U71: storage housekeeping is scoped by role", () => {
  test("the administrator sees every organisation and the volume pool", async ({
    page,
  }) => {
    await loginAs(page, "ops-priya");
    await page.goto("/housekeeping");

    // The number that was invisible until this screen existed, and whose
    // running out stopped every write with an error naming neither volumes
    // nor the pool.
    await expect(page.getByTestId("volumes-used")).toBeVisible();

    const tenants = page.getByTestId("tenant-storage");
    await expect(tenants).toBeVisible();
    // Two organisations that are not this person's own, which is the whole
    // point of the platform-wide scope.
    await expect(tenants).toContainText("canary");
    await expect(tenants).toContainText("finance");
  });

  test("real organisations are grouped above the test ones", async ({ page }) => {
    await loginAs(page, "ops-priya");
    await page.goto("/housekeeping");

    // Lowercased before comparing: the group headings are uppercased by
    // CSS, and innerText returns what is rendered, not what the markup says.
    const text = (
      (await page.getByTestId("tenant-storage").innerText()) ?? ""
    ).toLowerCase();
    const real = text.indexOf("real organisations");
    const testGroup = text.indexOf("test organisations");
    const finance = text.indexOf("finance");
    const canary = text.indexOf("canary");

    expect(real).toBeGreaterThan(-1);
    expect(testGroup).toBeGreaterThan(real);
    // A real organisation above the divider, a test one below it. Somebody
    // scanning for a customer should never have to work out which they are
    // looking at.
    expect(finance).toBeGreaterThan(real);
    expect(finance).toBeLessThan(testGroup);
    expect(canary).toBeGreaterThan(testGroup);

    // Said once, by the heading, not again on every row.
    expect(text.match(/test organisations/g)?.length ?? 0).toBe(1);
  });

  test("each organisation says why it exists", async ({ page }) => {
    await loginAs(page, "ops-priya");
    await page.goto("/housekeeping");

    const tenants = page.getByTestId("tenant-storage");
    // The note is recorded against the organisation, so a reader is never
    // left working out what an organisation is from its name alone. Checked
    // on the two every install has: probe organisations come and go with
    // each verification run.
    await expect(tenants).toContainText("A hospital");
    await expect(tenants).toContainText("Where the verification suite writes");
    // And where its files actually are, by backend rather than bucket alone.
    await expect(tenants).toContainText("Cloudflare R2");
    await expect(tenants).toContainText("SeaweedFS");
  });

  test("a custodian sees their own organisation's deletions and nothing wider", async ({
    page,
  }) => {
    await loginAs(page, "cust-hartley");
    await page.goto("/housekeeping");

    await expect(page.getByRole("heading", { name: /freed from health/i })).toBeVisible();

    // Not merely absent from the navigation: absent from the page.
    await expect(page.getByTestId("tenant-storage")).toHaveCount(0);
    await expect(page.getByTestId("volumes-used")).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("finance");
  });

  test("a researcher is refused, and told why rather than shown nothing", async ({
    page,
  }) => {
    await loginAs(page, "sam-researcher");
    await page.goto("/housekeeping");

    const refusal = page.getByTestId("failure");
    await expect(refusal).toBeVisible();
    // The wording comes from access.rego. If the policy changes its mind
    // about who may look, this fails rather than drifting quietly.
    await expect(refusal).toContainText(/no role that may see/i);
    await expect(page.getByTestId("tenant-storage")).toHaveCount(0);
  });

  test("freeing storage cannot be asked for without a reason", async ({ page }) => {
    await loginAs(page, "ops-priya");
    await page.goto("/housekeeping");

    const rehearse = page.getByTestId("reclaim-rehearse");
    await expect(rehearse).toBeDisabled();

    await page.getByTestId("reclaim-reason").fill("old verification leftovers");
    await expect(rehearse).toBeEnabled();
  });

  test("the screen rehearses before it offers to destroy anything", async ({
    page,
  }) => {
    await loginAs(page, "ops-priya");
    await page.goto("/housekeeping");

    // No confirm button exists until the platform has said what would go.
    await expect(page.getByTestId("reclaim-confirm")).toHaveCount(0);

    await page.getByTestId("reclaim-reason").fill("old verification leftovers");
    await page.getByTestId("reclaim-rehearse").click();

    await expect(page.getByTestId("reclaim-rehearsal")).toBeVisible({
      timeout: 30_000,
    });
  });
});
