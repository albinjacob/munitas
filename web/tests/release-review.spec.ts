/**
 * U156 in the console: a reviewer looks at what a run made before releasing it, and cannot release blind.
 *
 * The result is made by the platform's own flow (verify/seed_release_review.py, in the verification organisation), so these tests drive the real page
 * against real files and the real checks. The claims they carry are about what the reviewer is told and offered:
 *   - the page lists the checks the platform ran, names the files that failed and says which of them stop a release;
 *   - release is not offered while one does, and holding back is;
 *   - on a result nothing measured, release is not offered until the reviewer has opened a file, and then it is;
 *   - the files open from the page, and the page counts them against the most a review may open;
 *   - the person who started the run sees the evidence and is told they cannot decide.
 */

import { execSync } from "node:child_process";
import { expect, test } from "@playwright/test";
import { loginAs } from "./auth-helpers";

// The seed writes a pipeline run and its files through the platform, which takes 20 to 25 seconds before the page is even opened, so a test that
// seeds needs more than the 30 seconds a test gets by default.
test.describe.configure({ mode: "serial", timeout: 120_000 });

const REVIEWER = "canary-reviewer";
const ENGINEER = "canary-engineer";

function seed(kind: "bad" | "good"): { decision: string; version: string; files: string[] } {
  const out = execSync(
    `wsl.exe -- bash -c "cd /mnt/c/AIProjects/ClaudeProjects/Munitas && docker compose exec -T munitas-api python /verify/seed_release_review.py ${kind}"`,
    { encoding: "utf8", timeout: 120_000 },
  );
  return JSON.parse(out.trim().split("\n").pop()!);
}

test.describe("U156: releasing what a run made", () => {
  let bad: ReturnType<typeof seed>;
  let good: ReturnType<typeof seed>;

  test("the reviewer sees the checks, the files that failed, and cannot release; holding back is possible", async ({ page }) => {
    bad = seed("bad");
    await loginAs(page, REVIEWER);
    await page.goto(`/gates/${bad.decision}`);
    const checks = page.getByTestId("review-checks");
    await expect(checks).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("gate-lead")).toContainText("You are deciding whether to release this. Releasing moves it from under review to open for annotation, which cannot be undone.");
    await expect(checks).toContainText("8 files.");
    await expect(checks).toContainText("2 checks failed.");
    const failures = page.getByTestId("review-failures");
    await expect(failures).toContainText("same.wav");
    await expect(failures).toContainText("identical to the original file it was made from");
    await expect(failures).toContainText("notsound.wav");
    await expect(failures).toContainText("not one");
    await expect(failures.getByText("stops a release")).toHaveCount(2);
    await expect(page.getByTestId("gate-stoppers")).toContainText("You cannot release this yet");
    await page.getByTestId("gate-reason").fill("A copy of the original and a file that is not sound.");
    await expect(page.getByTestId("gate-promote")).toBeDisabled();
    await expect(page.getByTestId("gate-refuse")).toBeEnabled();
    await page.getByTestId("gate-refuse").click();
    await expect(page.getByTestId("gate-state")).toContainText("Held back", { timeout: 15_000 });
    await expect(page.getByTestId("gate-rested")).toContainText("It rested on 8 files checked by the platform and 0 opened by the reviewer.");
    await expect(page.getByTestId("gate-decision-reason")).toContainText("Reason: A copy of the original and a file that is not sound.");
    await expect(page.getByText("Decided as held back")).toHaveCount(0);
    await expect(page.getByTestId("gate-lead")).toContainText("This was held back and stays under review.");
    // The page says when it was started and when it was decided, as a date and a time.
    await expect(page.getByTestId("gate-detail-started")).toHaveText(/[A-Z][a-z]{2} \d{1,2}, \d{4}.*\d{1,2}:\d{2}/);
    await expect(page.getByText(/Held back by .+ on .*\d{1,2}:\d{2}/)).toBeVisible();
  });

  test("the files open from the page, and the page counts them", async ({ page }) => {
    good = seed("good");
    await loginAs(page, REVIEWER);
    await page.goto(`/gates/${good.decision}`);
    await expect(page.getByTestId("review-checks")).toContainText("Every file passed every check.", { timeout: 20_000 });
    const files = page.getByTestId("record-files");
    await expect(files).toBeVisible();
    await expect(files).toContainText("You have opened 0 of the 25 files you may open to judge this result.");
    // The page says what each part is for, and what to do, in words a first-time reader can follow.
    await expect(page.getByText("Look at the files yourself")).toBeVisible();
    await expect(page.getByText("What the platform checked")).toBeVisible();
    await expect(page.getByTestId("gate-stoppers")).toContainText("No automatic measurement was made of this result, so you must open at least one of its files before you can release it.");
    await expect(page.getByTestId("gate-practice")).toHaveCount(0);
    await expect(page.getByTestId("gate-recommendation")).toContainText("The pipeline’s note: This step changes the sound and does not look for identifying detail.");
    // A note that only says nothing was measured is not shown: the sentence above already says it.
    await expect(page.getByText("The pipeline’s note: Nothing measured it.")).toHaveCount(0);
    await expect(page.getByTestId("gate-not-measured")).toContainText("How much identifying detail this run removed was not counted. That is neither a pass nor a fail. The decision rests with you as the reviewer. Use the platform's checks and the files below to reach it.");
    await expect(page.getByText("Not measured", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Why nothing was measured")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "No automatic measurement" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "What the run measured" })).toHaveCount(0);
    await expect(page.getByText("What was left in the clear")).toHaveCount(0);
    await expect(page.getByText("The reason above says why")).toHaveCount(0);
    // The page says which run made this and links to it, to the pipeline, to what it was given and to the version being decided.
    const run = page.getByTestId("gate-run");
    await expect(run).toContainText("Pipeline");
    await expect(run.getByRole("link", { name: /^u155-.*, version 1$/ })).toHaveAttribute("href", /\/pipelines\/[0-9a-f-]{36}$/);
    await expect(run).toContainText("Uploaded by Canary engineer on");
    await expect(page.getByTestId("gate-run-link")).toHaveAttribute("href", /\/pipeline-runs\/[0-9a-f-]{36}$/);
    await expect(run).toContainText("Started by");
    await expect(run).toContainText("By hand");
    await expect(run).toContainText("Input dataset");
    await expect(run).not.toContainText("Given");
    await expect(run.getByRole("link", { name: /, version \d+$/ }).last()).toHaveAttribute("href", /\/versions\/[0-9a-f-]{36}$/);
    await expect(page.getByTestId("gate-steps")).toContainText("Inspect");
    await expect(page.getByTestId("gate-version-link")).toHaveAttribute("href", /\/versions\/[0-9a-f-]{36}$/);
    await expect(page.getByTestId("gate-stoppers")).toContainText("open at least one of its files");
    await page.getByTestId("gate-reason").fill("Opened a file and the checks passed.");
    await expect(page.getByTestId("gate-promote")).toBeDisabled();
    await files.getByTestId("record-play-a.bin").click();
    await expect(files).toContainText("You have opened 1 of the 25 files", { timeout: 15_000 });
    await expect(files.getByText("The first 256 bytes of the file.")).toBeVisible();
  });

  test("once a file is opened release is offered, and releasing keeps what it rested on", async ({ page }) => {
    await loginAs(page, REVIEWER);
    await page.goto(`/gates/${good.decision}`);
    await expect(page.getByTestId("record-files")).toContainText("You have opened 1 of the 25 files", { timeout: 20_000 });
    await expect(page.getByTestId("gate-stoppers")).toHaveCount(0);
    await expect(page.getByTestId("gate-needs-reason")).toContainText("Write your reason above");
    await page.getByTestId("gate-reason").fill("Opened a file and the checks passed.");
    await expect(page.getByTestId("gate-promote")).toBeEnabled();
    await page.getByTestId("gate-promote").click();
    await expect(page.getByTestId("gate-state")).toContainText("Released", { timeout: 15_000 });
    await expect(page.getByTestId("gate-rested")).toContainText("It rested on 3 files checked by the platform and 1 opened by the reviewer.");
    await expect(page.getByTestId("gate-lead")).toContainText("This was released and is now open for annotation.");
  });

  test("the person who started the run sees the evidence and is told they cannot decide it", async ({ page }) => {
    await loginAs(page, ENGINEER);
    await page.goto(`/gates/${bad.decision}`);
    await expect(page.getByTestId("review-checks")).toContainText("2 checks failed.", { timeout: 20_000 });
    await expect(page.getByTestId("record-files")).toHaveCount(0);
    // The runs this person started are listed with when each started and what became of it.
    await page.goto("/gates");
    const mine = page.getByTestId(`my-run-${bad.decision}`);
    await expect(mine).toBeVisible({ timeout: 20_000 });
    await expect(mine).toContainText(/Held back by .+ on .*\d{1,2}:\d{2}/);
  });

  // The two ways the page can fail to load a result, with the whole answer made up for each, since neither can be caused on demand.
  test("when the platform answers with an error the page says so, gives the reference, and does not say it is not responding", async ({ page }) => {
    await loginAs(page, REVIEWER);
    await page.route("**/gate-decisions/00000000-0000-4000-8000-000000000001", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        headers: { "access-control-allow-origin": "http://localhost:5173", "access-control-allow-credentials": "true" },
        body: JSON.stringify({ detail: { reasons: ["the platform ran into a problem it did not expect while doing this"], request_id: "ref-1234abcd" } }),
      }),
    );
    await page.goto("/gates/00000000-0000-4000-8000-000000000001");
    const failure = page.getByTestId("failure");
    await expect(failure).toBeVisible({ timeout: 15_000 });
    await expect(failure).toContainText("Could not load this result.");
    await expect(failure).toContainText("The platform ran into a problem it did not expect while doing this.");
    await expect(failure).toContainText("Try again in a moment");
    await expect(page.getByTestId("failure-reference")).toHaveText("ref-1234abcd");
    await expect(failure).not.toContainText("not responding");
    await expect(failure).not.toContainText("request failed with");
  });

  test("when nothing comes back at all the page says it could not reach the platform", async ({ page }) => {
    await loginAs(page, REVIEWER);
    await page.route("**/gate-decisions/00000000-0000-4000-8000-000000000002", (route) => route.abort("connectionrefused"));
    await page.goto("/gates/00000000-0000-4000-8000-000000000002");
    const failure = page.getByTestId("failure");
    await expect(failure).toBeVisible({ timeout: 15_000 });
    await expect(failure).toContainText("could not reach the platform");
    await expect(failure).not.toContainText("Failed to fetch");
  });
});
