import { defineConfig } from "@playwright/test";

import { CONSOLE_URL } from "../config/ports";

/**
 * Capture runs, not tests.
 *
 * These scripts drive the live console and write screenshots to disk for the
 * HTML walkthroughs under `docs/`. They live outside `tests/` and under their
 * own config so `npx playwright test` stays what it has always been: the
 * suite. A capture that ran on every test run would rewrite a megabyte of
 * images nobody asked for.
 *
 *   npx playwright test --config=walkthroughs/playwright.config.ts
 *
 * 1920x1080 because a screenshot taken at the default viewport looks soft
 * once it is enlarged in the walkthrough's lightbox.
 */
export default defineConfig({
  testDir: ".",
  timeout: 60_000,
  fullyParallel: false,
  reporter: [["list"]],
  use: {
    baseURL: CONSOLE_URL,
    viewport: { width: 1920, height: 1080 },
    trace: "off",
  },
});
