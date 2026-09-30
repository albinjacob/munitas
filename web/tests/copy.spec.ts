/**
 * U21: the console does not talk about itself.
 *
 * UI copy is for the person doing the job, not for whoever wrote the code. This
 * scans what is actually rendered on each screen for the project's own
 * vocabulary: build stages, product names, shell commands, table names.
 *
 * It exists because "stage 4" appeared in the navigation for two sessions
 * without anybody noticing, and because a failed request told the reader to run
 * `docker compose ps`, which is an instruction they cannot act on.
 *
 * Screens shown only to the people who run the platform are exempt for the
 * terms they legitimately need. A platform administrator diagnosing an incident
 * does need to know which service is called what.
 */

import { expect, test } from "@playwright/test";
import { loginAs } from "./auth-helpers";

/** Words that should never appear on a screen somebody is working on. */
const NEVER = [
  "stage 1",
  "stage 2",
  "stage 3",
  "stage 4",
  "stage 5",
  "docker compose",
  "localhost:",
  "access_decision",
  "access_lease",
  "dataset_version",
  // The class identifiers, which belong in the API and the audit log rather
  // than on a screen somebody is deciding from. Only the ones carrying an
  // underscore are listed: this match is case-insensitive, so RAW and
  // PUBLISHED would also ban their own labels and the ordinary English words.
  "visibility_class",
  "under_review",
  "open_for_annotation",
  "open_for_training",
  "foreign key",
  "check constraint",
  "not built yet",
  "python -m",
  "npm run",
];

/**
 * Jargon that is fine for the platform team and wrong for everyone else.
 * Checked only on screens a non-platform persona sees.
 */
const NOT_FOR_END_USERS = [
  "control plane",
  "policy engine",
  "storage prefix",
  "content hash",
  "OPA",
  "Temporal",
  "SeaweedFS",
  "MLflow",
  "Postgres",
];

function findAny(text: string, needles: string[]): string[] {
  const lower = text.toLowerCase();
  return needles.filter((n) => lower.includes(n.toLowerCase()));
}

test.describe("U21: no internal vocabulary on any screen", () => {
  test("nothing internal on /roles", async ({ page }) => {
    await page.goto("/roles");
    const text = (await page.textContent("body")) ?? "";
    expect(findAny(text, NEVER), "on /roles").toEqual([]);
  });

  for (const route of ["/", "/datasets"]) {
    test(`nothing internal on ${route} as a researcher`, async ({ page }) => {
      await loginAs(page, "sam-researcher");
      await page.goto(route);
      const text = (await page.textContent("body")) ?? "";

      expect(findAny(text, NEVER), `banned words on ${route}`).toEqual([]);
      expect(
        findAny(text, NOT_FOR_END_USERS),
        `platform jargon shown to a researcher on ${route}`,
      ).toEqual([]);
    });
  }

  test("a researcher is not shown the list of services", async ({ page }) => {
    await loginAs(page, "sam-researcher");
    await page.goto("/");
    // The map of components is for the people who run the platform. A
    // researcher deciding whether to ask for data has no use for it.
    expect(await page.getByTestId("component-map").count()).toBe(0);
  });

  test("the navigation names no build stage", async ({ page }) => {
    await loginAs(page, "ops-priya");
    const nav = (await page.locator("nav").textContent()) ?? "";
    expect(findAny(nav, NEVER)).toEqual([]);
  });

  test("the platform administrator still gets the detail they need", async ({
    page,
  }) => {
    await loginAs(page, "ops-priya");
    await page.goto("/");
    // The exemption is real, not a loophole: this persona exists to run the
    // system, so naming its parts is the point of their landing page.
    await expect(page.getByTestId("component-map")).toBeVisible();
  });
});
