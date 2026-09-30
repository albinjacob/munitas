import { defineConfig } from "@playwright/test";

import { CONSOLE_URL } from "./config/ports";

/**
 * Run against the live stack, not against mocks.
 *
 * A console tested only against fixtures proves the fixtures render. The claims
 * in the U series are about whether the screens tell the truth about the
 * platform, and that can only be checked by asking the platform the same
 * question and comparing.
 */
export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  fullyParallel: false,
  reporter: [["list"]],
  use: {
    baseURL: CONSOLE_URL,
    trace: "off",
  },
});
