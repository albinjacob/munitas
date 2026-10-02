import { expect, type Page } from "@playwright/test";

/**
 * Waits until the console has finished drawing, so a screenshot shows the screen and not a loading
 * state. Two things mean "not yet": the grey placeholder blocks (the Loading component), and the
 * plain "Loading the directory" line shown before the signed-in person is known. The short pause
 * comes first because right after a page opens the placeholders have not been drawn yet, so looking
 * for them alone finds nothing and passes too early.
 */
export async function settled(page: Page): Promise<void> {
  await page.waitForTimeout(400);
  await expect(page.locator(".animate-pulse")).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByText("Loading the directory")).toHaveCount(0, { timeout: 15_000 });
  await page.waitForTimeout(150);
  await expect(page.locator(".animate-pulse")).toHaveCount(0, { timeout: 15_000 });
}
