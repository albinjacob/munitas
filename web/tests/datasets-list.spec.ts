/**
 * U158 in the console: the datasets list can be ordered by its column headings, shows when each dataset was registered, and says what each holds.
 *
 * Reads the organisation's real datasets, so it asserts on the order the page shows, not on any particular dataset.
 */
import { expect, test } from "@playwright/test";
import { loginAs } from "./auth-helpers";

const CUSTODIAN = "canary-custodian";

async function names(page: import("@playwright/test").Page): Promise<string[]> {
  return page.locator("tr[data-dataset]").evaluateAll((rows) => rows.map((r) => (r as HTMLElement).dataset.dataset ?? ""));
}

async function registered(page: import("@playwright/test").Page): Promise<number[]> {
  const texts = await page.getByTestId("dataset-registered").allTextContents();
  return texts.map((t) => new Date(t).getTime());
}

test.describe("U158: the datasets list", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, CUSTODIAN);
    await page.goto("/datasets");
    await expect(page.locator("tr[data-dataset]").first()).toBeVisible({ timeout: 20_000 });
  });

  test("newest first until a heading is pressed, with the time each was registered", async ({ page }) => {
    await expect(page.getByTestId("sort-registered").locator("..")).toHaveAttribute("aria-sort", "descending");
    const when = await registered(page);
    expect(when.length).toBeGreaterThan(1);
    expect(when.every((t) => !Number.isNaN(t))).toBe(true);
    expect([...when].sort((a, b) => b - a)).toEqual(when);
    await expect(page.getByTestId("dataset-registered").first()).toHaveText(/[A-Z][a-z]{2} \d{1,2}, \d{4}.*\d{1,2}:\d{2}/);
  });

  test("pressing Dataset orders by name, and pressing it again turns the order round", async ({ page }) => {
    await page.getByTestId("sort-name").click();
    await expect(page.getByTestId("sort-name").locator("..")).toHaveAttribute("aria-sort", "ascending");
    await expect.poll(async () => (await names(page)).join("|")).toBe([...(await names(page))].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase())).join("|"));
    const first = (await names(page))[0];
    await page.getByTestId("sort-name").click();
    await expect(page.getByTestId("sort-name").locator("..")).toHaveAttribute("aria-sort", "descending");
    await expect.poll(async () => (await names(page))[0]).not.toBe(first);
    const desc = await names(page);
    expect(desc).toEqual([...desc].sort((a, b) => b.toLowerCase().localeCompare(a.toLowerCase())));
  });

  test("pressing Registered twice shows the oldest first", async ({ page }) => {
    await page.getByTestId("sort-registered").click();
    await expect(page.getByTestId("sort-registered").locator("..")).toHaveAttribute("aria-sort", "ascending");
    await expect.poll(async () => {
      const when = await registered(page);
      return [...when].sort((a, b) => a - b).join() === when.join();
    }).toBe(true);
  });

  test("a row says what it holds: kind, format and structure, and a single kind is not labelled", async ({ page }) => {
    // A dataset that holds something: other tests register empty ones, and the newest rows are often those.
    const row = page.locator("tr[data-dataset]").filter({ has: page.getByTestId("dataset-kind") }).first();
    await expect(row.getByTestId("dataset-kind")).toBeVisible({ timeout: 20_000 });
    await expect(row.getByTestId("dataset-structure")).toBeVisible();
    await expect(page.getByText("Unimodal")).toHaveCount(0);
  });

  test("a filter keeps the order that was chosen", async ({ page }) => {
    await page.getByTestId("sort-name").click();
    await page.getByTestId("dataset-structure-filter").selectOption("structured");
    await expect(page.getByText(/Showing \d+ of \d+ matching\./)).toBeVisible();
    await expect(page.getByTestId("sort-name").locator("..")).toHaveAttribute("aria-sort", "ascending");
    const shown = await names(page);
    expect(shown).toEqual([...shown].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase())));
  });
});

test.describe("U158: what a version holds, and the layout at every width", () => {
  test("an opened dataset says rows for a table and files for anything else, never a bare number", async ({ page }) => {
    await loginAs(page, CUSTODIAN);
    await page.goto("/datasets");
    // A dataset that holds a table, and one that holds files, found with the list's own format filter, so the test does not depend on what the newest
    // fifteen datasets happen to be (most are empty, and a table is not among them every day).
    const words = new Set<string>();
    for (const format of ["table", "jsonl"]) {
      await page.getByTestId("dataset-format-filter").selectOption(format);
      const rows = page.locator("tr[data-dataset]").filter({ has: page.getByTestId("ds-show-versions") });
      await expect(rows.first(), `a dataset held as ${format} to open`).toBeVisible({ timeout: 20_000 });
      const n = Math.min(await rows.count(), 5);
      for (let i = 0; i < n; i++) {
        await rows.nth(i).getByTestId("ds-show-versions").click();
        const cells = page.getByTestId("version-holds-count");
        await expect(cells.first()).toBeVisible();
        for (const text of await cells.allTextContents()) {
          expect(text, "a count with its word").toMatch(/^[\d,]+ (rows?|files?)$/);
          words.add(text.replace(/^[\d,]+ /, "").replace(/s$/, ""));
        }
        await rows.nth(i).getByText("Hide versions").click();
      }
    }
    expect([...words].sort()).toEqual(["file", "row"]);
  });

  test("the table fits without scrolling sideways at every laptop and desktop width", async ({ page }) => {
    await loginAs(page, CUSTODIAN);
    for (const [width, layout] of [[1920, "table"], [1536, "table"], [1440, "table"], [1366, "table"], [1280, "table"]] as const) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/datasets");
      await expect(page.locator("[data-dataset]").first()).toBeVisible({ timeout: 20_000 });
      const found = await page.evaluate(() => {
        const t = document.querySelector("table");
        const box = t?.parentElement as HTMLElement | undefined;
        return { layout: t ? "table" : "cards", sideways: box ? box.scrollWidth > box.clientWidth + 1 : false, page: document.documentElement.scrollWidth > window.innerWidth };
      });
      expect(found.layout, `at ${width}px`).toBe(layout);
      expect(found.sideways, `the table scrolls sideways at ${width}px`).toBe(false);
      expect(found.page, `the page scrolls sideways at ${width}px`).toBe(false);
    }
  });
});
