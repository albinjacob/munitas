/**
 * U16, U17 and U19: the roles page.
 *
 * U15, U18 and U20 used to live here too, testing the persona chooser's own
 * cards directly: whether it listed workloads, whether every offered id was
 * registered, whether a card's claims about itself held together. All three
 * stopped being expressible once the chooser was retired for a real Kratos
 * login: there is no "offered" list once nobody is offered
 * anything, only people who can or cannot actually log in.
 */

import { expect, test } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { bearerFor } from "./auth-helpers";

const API = API_BASE;

interface DirectoryEntry {
  id: string;
  label: string;
  roles: string[];
  department_name: string | null;
}

interface PolicyRoles {
  class_order: Record<string, number>;
  role_floor: Record<string, number>;
  approver_roles: string[];
}

const get = <T,>(path: string, headers?: Record<string, string>): Promise<T> =>
  fetch(API + path, { headers }).then((r) => r.json() as Promise<T>);

test.describe("U16: roles are readable before signing in", () => {
  test("the roles page renders with nobody signed in", async ({ page }) => {
    await page.goto("/roles");
    await expect(page).toHaveURL(/\/roles$/);
    await expect(page.getByTestId("roles-page")).toBeVisible();
    // Reference material still carries the banner, because the banner is about
    // the platform rather than about the page.
    await expect(page.getByTestId("unauthenticated-banner")).toBeVisible();
  });
});

test.describe("U17: the roles page matches the policy", () => {
  test("every role and floor shown equals what the policy returned", async ({
    page,
  }) => {
    const policy = await get<PolicyRoles>("/policy/roles");
    await page.goto("/roles");
    await expect(page.getByTestId("roles-page")).toBeVisible();

    const shown = await page
      .locator("[data-role]")
      .evaluateAll((nodes) =>
        nodes.map((n) => ({
          role: n.getAttribute("data-role"),
          floor: n.getAttribute("data-floor"),
        })),
      );

    // Every role the policy defines is on the page. A role missing here is a
    // role somebody could hold without the console being able to explain it.
    const shownRoles = shown.map((s) => s.role).sort();
    expect(shownRoles).toEqual(Object.keys(policy.role_floor).sort());

    // And each floor matches. The page reads these from the policy, so a
    // mismatch means the rendering is wrong rather than the copy being stale.
    const byOrdinal = Object.fromEntries(
      Object.entries(policy.class_order).map(([k, v]) => [v, k]),
    );
    for (const { role, floor } of shown) {
      expect(floor, `floor shown for ${role}`).toBe(
        byOrdinal[policy.role_floor[role!]],
      );
    }
  });

  test("the approving role is marked, and only that one", async ({ page }) => {
    const policy = await get<PolicyRoles>("/policy/roles");
    await page.goto("/roles");
    await expect(page.getByTestId("roles-page")).toBeVisible();
    const marked = page.getByText("yes, for their own department");
    expect(await marked.count()).toBe(policy.approver_roles.length);
  });
});

test.describe("U19: custodian scope is visible", () => {
  test("two custodians exist in different departments", async () => {
    // /directory requires a real session (platform/api/app/read_models.py's
    // list_directory), but is not scoped to the caller's own tenant, so any
    // seeded identity can read it -- canary-engineer is just a valid login,
    // not a claim about who this data belongs to.
    const directory = await get<DirectoryEntry[]>(
      "/directory?kind=human",
      await bearerFor("canary-engineer"),
    );
    const departments = directory
      .filter((d) => d.roles.includes("data_custodian") && d.department_name)
      .map((d) => d.department_name);

    // Without two, the rule that a custodian is scoped to their own department
    // cannot be shown to anybody, only asserted.
    expect(new Set(departments).size).toBeGreaterThan(1);
  });
});
