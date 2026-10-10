/**
 * The Datasets page in the enterprise layout: fixed dark sidebar, header with search, breadcrumb, four cards, light and dark themes.
 *
 * (The list's own behaviour, its sorting, its kind, format and structure cells and the widths it fits, is in datasets-list.spec.ts, and what a person can read
 * in access-preview.spec.ts. This file is about what the layout adds.)
 *
 * The claims these tests carry, on the live platform:
 *   - the cards show the platform's totals and agree with the list under them, and a card sets the same filter the list has, and pressing it again lets go;
 *   - every filter narrows the list, alone and together, and Clear lets go of all of them;
 *   - Show versions opens each version's class, what it holds and the person's access, with a way in;
 *   - the colours keep the console's rule: red is Raw and nothing else;
 *   - the theme and the sidebar can be changed and are remembered;
 *   - what the old shell showed on every screen is still shown: who is signed in and their role, and the organisation.
 */

import { expect, test, type Page } from "@playwright/test";
import { loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial" });

async function open(page: Page, who = "canary-custodian") {
  await loginAs(page, who);
  await page.goto("/datasets");
  await expect(page.getByTestId("ds-stat-all-value")).not.toHaveText("…", { timeout: 20_000 });
}

// Each card is its own query, so wait for the one being read rather than reading "…" as nothing.
const number = async (page: Page, id: string) => {
  await expect(page.getByTestId(id)).not.toHaveText("…", { timeout: 20_000 });
  return Number((await page.getByTestId(id).innerText()).replace(/\D/g, ""));
};

test.describe("Datasets, in the new layout", () => {
  test("the cards show the platform's totals and agree with the list", async ({ page }) => {
    await open(page);
    const all = await number(page, "ds-stat-all-value");
    expect(all).toBeGreaterThan(0);
    await expect(page.getByTestId("dataset-count")).toContainText(`of ${all}`, { timeout: 20_000 });
    await expect(page.getByTestId("ds-crumbs")).toContainText("Home");
    await expect(page.getByTestId("ds-crumbs")).toContainText("Datasets");
    await expect(page.getByTestId("ds-sidebar")).toContainText("Datasets");
    await expect(page.getByTestId("ds-sidebar")).not.toContainText("trial");
  });

  test("a card sets the same filter the list has, and pressing it again lets go", async ({ page }) => {
    await open(page);
    const raw = await number(page, "ds-stat-raw-value");
    await page.getByTestId("ds-stat-raw").click();
    await expect(page.getByTestId("dataset-class")).toHaveValue("RAW");
    await expect(page.getByTestId("dataset-count")).toContainText(`of ${raw}`, { timeout: 20_000 });
    for (const pill of await page.getByTestId("ds-row").locator(".ds-pill").allInnerTexts()) expect(pill).toBe("Raw");
    await page.getByTestId("ds-stat-raw").click();
    await expect(page.getByTestId("dataset-class")).toHaveValue("");
    await expect(page.getByTestId("ds-stat-all")).toHaveAttribute("aria-pressed", "true");
  });

  test("each filter narrows the list, together they all hold, and Clear lets go of every one", async ({ page }) => {
    await open(page);
    const all = await number(page, "ds-stat-all-value");
    await page.getByTestId("dataset-kind-filter").selectOption("tabular");
    await expect(page.getByTestId("dataset-count")).toContainText("matching", { timeout: 20_000 });
    // A dataset that holds more than one kind is listed under each of them, so a row may read "Audio + Tabular".
    for (const cell of await page.getByTestId("dataset-kind").allInnerTexts()) expect(cell).toContain("Tabular");
    const tabular = await page.getByTestId("ds-row").count();
    expect(tabular).toBeGreaterThan(0);
    await page.getByTestId("dataset-class").selectOption("RAW");
    await expect(async () => expect(await page.getByTestId("ds-row").count()).toBeLessThanOrEqual(tabular)).toPass({ timeout: 20_000 });
    for (const pill of await page.getByTestId("ds-row").locator(".ds-pill").allInnerTexts()) expect(pill).toBe("Raw");
    await page.getByTestId("dataset-state").selectOption("registered");
    await expect(page.getByTestId("ds-table")).toContainText("Nothing matches those filters", { timeout: 20_000 });
    await page.getByTestId("ds-clear").click();
    for (const id of ["dataset-class", "dataset-kind-filter", "dataset-format-filter", "dataset-structure-filter", "dataset-department"]) await expect(page.getByTestId(id)).toHaveValue("");
    await expect(page.getByTestId("dataset-state")).toHaveValue("");
    await expect(page.getByTestId("dataset-show-deleted")).not.toBeChecked();
    await expect(page.getByTestId("dataset-count")).toContainText(`of ${all}`, { timeout: 20_000 });
    await expect(page.getByTestId("ds-clear")).toHaveCount(0);
  });

  test("a department narrows the list to what it owns", async ({ page }) => {
    await open(page);
    // The departments arrive after the list does, so wait for them rather than counting at once.
    await expect(async () => expect((await page.getByTestId("dataset-department").locator("option").allInnerTexts()).length).toBeGreaterThan(1)).toPass({ timeout: 20_000 });
    await page.getByTestId("dataset-department").selectOption({ index: 1 });
    await expect(page.getByTestId("dataset-count")).toContainText("Showing", { timeout: 20_000 });
    for (const owner of await page.getByTestId("dataset-owner").allInnerTexts()) expect(owner).toContain("Owned by");
    await page.getByTestId("ds-clear").click();
    await expect(page.getByTestId("dataset-department")).toHaveValue("");
  });

  test("the search narrows by name and says so when nothing matches", async ({ page }) => {
    await open(page);
    await page.getByTestId("dataset-search").fill("zzz-no-such-dataset");
    await expect(page.getByTestId("ds-table")).toContainText("Nothing matches those filters", { timeout: 20_000 });
    await page.getByTestId("dataset-search").fill("");
    await expect(page.getByTestId("ds-row").first()).toBeVisible({ timeout: 20_000 });
    await page.keyboard.press("Control+k");
    await expect(page.getByTestId("dataset-search")).toBeFocused();
  });

  test("Show versions opens each version's class, what it holds and your access, with a way in", async ({ page }) => {
    await open(page);
    // A dataset that holds something: other tests register empty ones, and the newest rows are often those.
    const row = page.getByTestId("ds-row").filter({ has: page.getByTestId("ds-show-versions") }).first();
    await row.getByTestId("ds-show-versions").click();
    const versions = page.getByTestId("ds-versions");
    await expect(versions).toBeVisible({ timeout: 20_000 });
    await expect(versions).toContainText("Access level");
    await expect(versions).toContainText("Your access");
    await expect(versions.getByTestId("version-holds-count").first()).toContainText(/\d+ (rows?|files?)/);
    await expect(versions.getByTestId("access-mark").first()).toBeVisible({ timeout: 20_000 });
    await expect(versions.getByRole("link", { name: "Open" }).first()).toHaveAttribute("href", /^\/versions\//);
    await row.getByTestId("ds-show-versions").click();
    await expect(versions).toHaveCount(0);
  });

  test("the colours follow the console's rule: red is Raw and nothing else", async ({ page }) => {
    // Makes up which class each row carries, so every class is on the page whatever the organisation happens to hold: the real list, with the first
    // rows given each of the five classes in turn.
    const CLASSES = ["RAW", "UNDER_REVIEW", "OPEN_FOR_ANNOTATION", "OPEN_FOR_TRAINING", "PUBLISHED"];
    await page.route("**/datasets?*", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.datasets = (body.datasets ?? []).map((d: { widest_class: string | null }, i: number) => (i < CLASSES.length ? { ...d, widest_class: CLASSES[i] } : d));
      await route.fulfill({ response, json: body });
    });
    await open(page);
    for (const cls of CLASSES) await expect(page.locator(`.ds-pill.${cls}`).first()).toBeVisible({ timeout: 20_000 });
    const colour = (cls: string) => page.locator(`.ds-pill.${cls}`).first().evaluate((el) => getComputedStyle(el).color);
    expect(await colour("RAW")).toBe("rgb(220, 38, 38)");
    for (const cls of CLASSES.filter((c) => c !== "RAW")) expect(await colour(cls), `${cls} is not red`).not.toBe("rgb(220, 38, 38)");
  });

  test("the theme and the sidebar can be changed, and are remembered", async ({ page }) => {
    await open(page);
    await page.getByTestId("ds-theme-toggle").click();
    const theme = await page.getByTestId("ds-page").getAttribute("data-theme");
    await page.getByTestId("ds-collapse").click();
    await expect(page.getByTestId("ds-page")).toHaveAttribute("data-collapsed", "true");
    await expect(page.getByTestId("ds-sidebar")).toHaveCSS("width", "76px");
    await page.reload();
    await expect(page.getByTestId("ds-page")).toHaveAttribute("data-theme", theme!, { timeout: 20_000 });
    await expect(page.getByTestId("ds-page")).toHaveAttribute("data-collapsed", "true");
    await page.getByTestId("ds-collapse").click();
    await expect(page.getByTestId("ds-sidebar")).toHaveCSS("width", "260px");
  });

  test("who is signed in, their role and the organisation are shown, and the organisation says when it is for testing", async ({ page }) => {
    await open(page, "canary-custodian");
    await expect(page.getByTestId("current-persona")).toHaveText("Canary custodian");
    await expect(page.getByTestId("current-roles")).toContainText("custodian");
    await expect(page.getByTestId("current-tenant")).toHaveText("canary");
    await expect(page.getByTestId("tenant-purpose")).toHaveText("for testing");
    await page.getByTestId("ds-profile").click();
    await expect(page.getByTestId("switch-persona")).toBeVisible();
  });
});
