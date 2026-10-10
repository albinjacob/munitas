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
  // The large tier (tests/large) takes hours and gigabytes. It is a project that exists only when MUNITAS_LARGE=1 is set, which verify/run_large.py does for
  // the one check that needs it. Without it these files cannot be selected at all, not even by naming one, so an ordinary run can never start one.
  projects: [
    { name: "ordinary", testIgnore: "**/large/**" },
    ...(process.env.MUNITAS_LARGE === "1" ? [{ name: "large", testMatch: "**/large/**/*.spec.ts", timeout: 4 * 60 * 60_000 }] : []),
  ],
});
