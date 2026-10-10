/**
 * U172: the Datasets page shows where each dataset stands, and leaves deleted ones out until asked.
 *
 * The list is made up for the test (the platform's own list changes with every run, and a deleted dataset cannot be made on demand), so what is checked
 * is what the page does with the three states: it says each one, it hides the deleted dataset by default, the checkbox names how many are hidden and lists
 * them, and a state chosen on its own turns the checkbox off because it no longer applies. The platform's side of this is checked against real data by
 * verify/v164_dataset_state.py.
 */

import { expect, test, type Page } from "@playwright/test";
import { loginAs } from "./auth-helpers";

type State = "registered" | "active" | "deleted";

function row(name: string, state: State) {
  return {
    id: `00000000-0000-4000-8000-${String(["registered", "active", "deleted"].indexOf(state) + 1).padStart(12, "0")}`,
    name,
    tenant_id: "canary",
    created_at: "2026-10-09T08:00:00Z",
    state,
    version_count: state === "registered" ? 0 : 1,
    latest_version: state === "registered" ? null : 1,
    widest_class: state === "registered" ? null : "RAW",
    provenance: "collected",
    department_id: null,
    kinds: [],
    formats: [],
    structures: [],
    license_tag: null,
    license_export_unmodified: null,
    license_export_modified: null,
    provenance_registered_as: null,
    provenance_overridden_at: null,
  };
}

const ALL = [row("made-up-registered", "registered"), row("made-up-active", "active"), row("made-up-deleted", "deleted")];

async function stubList(page: Page) {
  await page.route("**/access-preview", (r) => r.fulfill({ json: { versions: {}, datasets: {} } }));
  await page.route(/\/datasets\?/, (r) => {
    const q = new URL(r.request().url()).searchParams;
    const state = q.get("state");
    const withDeleted = q.get("include_deleted") === "true";
    const shown = ALL.filter((d) => (state ? d.state === state : withDeleted || d.state !== "deleted"));
    r.fulfill({ json: { datasets: shown, shown: shown.length, total: shown.length, deleted: 1, limit: 15, offset: 0 } });
  });
}

test.describe("U172: a dataset is registered, active or deleted", () => {
  test("each dataset says where it stands, and the deleted one is left out by default", async ({ page }) => {
    await loginAs(page, "canary-engineer");
    await stubList(page);
    await page.goto("/datasets");
    await expect(page.getByTestId("ds-row")).toHaveCount(2, { timeout: 20_000 });
    await expect(page.getByTestId("ds-row").filter({ hasText: "made-up-registered" }).getByTestId("dataset-state-cell")).toHaveText("Registered");
    await expect(page.getByTestId("ds-row").filter({ hasText: "made-up-active" }).getByTestId("dataset-state-cell")).toHaveText("Active");
    await expect(page.getByTestId("ds-row").filter({ hasText: "made-up-deleted" })).toHaveCount(0);
    await expect(page.getByTestId("dataset-show-deleted")).not.toBeChecked();
  });

  test("the checkbox lists the deleted datasets, and each says what became of it", async ({ page }) => {
    await loginAs(page, "canary-engineer");
    await stubList(page);
    await page.goto("/datasets");
    await expect(page.getByText("Also show deleted datasets", { exact: true })).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("dataset-show-deleted").check();
    await expect(page.getByTestId("ds-row")).toHaveCount(3, { timeout: 20_000 });
    await expect(page.getByTestId("ds-row").filter({ hasText: "made-up-deleted" }).getByTestId("dataset-state-cell")).toHaveText("Deleted, record kept");
    await page.getByTestId("ds-clear").click();
    await expect(page.getByTestId("dataset-show-deleted")).not.toBeChecked();
    await expect(page.getByTestId("ds-row")).toHaveCount(2, { timeout: 20_000 });
  });

  test("choosing a state on its own switches the checkbox off, because it no longer applies", async ({ page }) => {
    await loginAs(page, "canary-engineer");
    await stubList(page);
    await page.goto("/datasets");
    await expect(page.getByTestId("dataset-state")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("dataset-state").selectOption("registered");
    await expect(page.getByTestId("ds-row")).toHaveCount(1, { timeout: 20_000 });
    await expect(page.getByTestId("ds-row")).toContainText("made-up-registered");
    await expect(page.getByTestId("dataset-show-deleted")).toBeDisabled();
  });
});
