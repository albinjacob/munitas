/**
 * U171: each role's home answers what is waiting for that person and what is happening with their things.
 *
 * The homes differ by role, so each one is checked as a person who holds only that role: the reviewer sees results, the architect sees agent
 * approvals, the engineer sees their own requests, the auditor sees openings of locked files, and the administrator sees what the platform
 * holds but not the map of its parts, which is on the Services page.
 */

import { expect, test } from "@playwright/test";
import { loginAs } from "./auth-helpers";

test.describe("U171: each role has its own home", () => {
  test("a reviewer sees the results waiting for them and the ones decided lately", async ({ page }) => {
    await loginAs(page, "canary-reviewer");
    await page.goto("/");
    await expect(page.getByTestId("home-welcome")).toContainText("Welcome, Canary reviewer");
    await expect(page.getByRole("heading", { name: "Waiting for your decision" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Decided lately" })).toBeVisible();
    await expect(page.getByTestId("component-map")).toHaveCount(0);
  });

  test("an architect sees the agents waiting for their approval of hosts", async ({ page }) => {
    await loginAs(page, "canary-architect");
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Waiting for your decision" })).toBeVisible();
    await expect(page.getByText("An agent may reach only the external hosts somebody has approved for that version.")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Decided lately" })).toBeVisible();
  });

  test("an engineer sees what they asked for, what waits on somebody else, and their runs", async ({ page }) => {
    await loginAs(page, "canary-engineer");
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "What you asked for" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Waiting on somebody else" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Your recent runs" })).toBeVisible();
  });

  test("a privacy auditor sees the latest openings of locked files and a link to the whole audit", async ({ page }) => {
    await loginAs(page, "canary-auditor");
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Latest openings of locked files" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open the whole audit" }).first()).toBeVisible();
  });

  test("an administrator sees what the platform holds, and the parts of the system are on the Services page only", async ({ page }) => {
    await loginAs(page, "canary-admin");
    await page.goto("/");
    await expect(page.getByTestId("overview-stats")).toBeVisible();
    await expect(page.getByTestId("component-map")).toHaveCount(0);
    await page.getByRole("link", { name: "Open the services" }).click();
    await expect(page.getByTestId("component-map")).toBeVisible();
  });

  test("a researcher is not shown another role's queue", async ({ page }) => {
    await loginAs(page, "canary-researcher");
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Access you have been given" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Waiting for your decision" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Decided lately" })).toHaveCount(0);
  });
});
