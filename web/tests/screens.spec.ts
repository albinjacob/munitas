/**
 * Who may open which page comes from one place: the platform's policy (GET /screens). The menu, the sentence a page shows to somebody it is not for, and the
 * API's refusal all read that same answer, so this checks the console against what the platform says rather than against a list of its own.
 */

import { expect, test } from "@playwright/test";
import { loginAs } from "./auth-helpers";

test.describe("Pages follow the platform's policy", () => {
  test("an engineer is not offered Services, Legal holds or the audits, and each says who it is for", async ({ page }) => {
    await loginAs(page, "canary-engineer");
    await page.goto("/");
    const nav = page.getByTestId("ds-sidebar");
    await expect(nav.getByRole("link", { name: "Datasets" })).toBeVisible();
    for (const name of ["Services", "Legal holds", "Access decisions", "Locked data audit"]) {
      await expect(nav.getByRole("link", { name })).toHaveCount(0);
    }
    const expected: Record<string, string> = {
      "/services": "This page is for the platform administrator.",
      "/legal-holds": "This page is for the platform administrator.",
      "/audit": "This page is for data custodians, the data protection officer and platform administrators.",
    };
    for (const [path, sentence] of Object.entries(expected)) {
      await page.goto(path);
      await expect(page.getByTestId("role-only-notice")).toHaveText(sentence);
    }
    await page.goto("/locking-audit");
    await expect(page.getByTestId("role-only-notice")).toBeVisible();
  });

  test("the administrator is offered Services and opens it", async ({ page }) => {
    await loginAs(page, "canary-admin");
    await page.goto("/");
    await page.getByTestId("ds-sidebar").getByRole("link", { name: "Services" }).click();
    await expect(page.getByTestId("role-only-notice")).toHaveCount(0);
    await expect(page.getByTestId("component-map")).toBeVisible();
  });

  test("a custodian opens the Access decisions and is told Services is not theirs", async ({ page }) => {
    await loginAs(page, "canary-custodian");
    await page.goto("/audit");
    await expect(page.getByTestId("role-only-notice")).toHaveCount(0);
    await page.goto("/services");
    await expect(page.getByTestId("role-only-notice")).toBeVisible();
  });

  test("the privacy auditor opens the Locked data audit", async ({ page }) => {
    await loginAs(page, "canary-auditor");
    await page.goto("/locking-audit");
    await expect(page.getByTestId("role-only-notice")).toHaveCount(0);
    await expect(page.getByTestId("ds-sidebar").getByRole("link", { name: "Locked data audit" })).toBeVisible();
  });

  test("the Agents, Action runs and Egress approvals lists are for their own roles", async ({ page }) => {
    await loginAs(page, "canary-researcher");
    const nav = page.getByTestId("ds-sidebar");
    await page.goto("/");
    for (const name of ["Agents", "Action runs", "Egress approvals"]) {
      await expect(nav.getByRole("link", { name })).toHaveCount(0);
    }
    const expected: Record<string, string> = {
      "/agents": "This page is for data engineers and platform administrators.",
      "/action-runs": "This page is for data engineers and platform administrators.",
      "/egress-approvals": "This page is for network architects, data engineers and platform administrators.",
    };
    for (const [path, sentence] of Object.entries(expected)) {
      await page.goto(path);
      await expect(page.getByTestId("role-only-notice")).toHaveText(sentence);
    }
  });

  test("a data engineer is offered all three lists and opens them", async ({ page }) => {
    await loginAs(page, "canary-engineer");
    await page.goto("/");
    for (const name of ["Agents", "Action runs", "Egress approvals"]) {
      await expect(page.getByTestId("ds-sidebar").getByRole("link", { name })).toBeVisible();
    }
    for (const path of ["/agents", "/action-runs", "/egress-approvals"]) {
      await page.goto(path);
      await expect(page.getByTestId("role-only-notice")).toHaveCount(0);
    }
  });
});
