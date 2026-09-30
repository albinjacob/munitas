/**
 * The Datasets list says where each dataset came from, what its licence
 * allows, and when a fetched licence corrected what was registered.
 *
 * The corrected dataset is made by U44 (verify/v44_license_provenance.py),
 * which sets the same columns a HuggingFace fetch writes, in the canary
 * organisation. Found through the API here rather than assumed.
 */
import { expect, test } from "@playwright/test";
import { bearerFor, loginAs } from "./auth-helpers";
import { API_BASE } from "../config/ports";

const API = API_BASE;

interface Row {
  name: string;
  provenance: string;
  provenance_registered_as: string | null;
  license_tag: string | null;
}

test.describe("U80: where a dataset came from, on the list", () => {
  test("a licence correction stays visible after the fetch", async ({ page }) => {
    const page1 = await fetch(`${API}/datasets?tenant_id=canary&q=license-corrected-&limit=1`, {
      headers: await bearerFor("canary-researcher"),
    }).then((r) => r.json());
    const corrected: Row | undefined = page1.datasets[0];
    test.skip(!corrected, "no corrected dataset in canary yet; run verify/v44_license_provenance.py");

    await loginAs(page, "canary-researcher");
    await page.goto("/datasets");
    await page.getByTestId("dataset-search").fill(corrected!.name);
    const row = page.locator(`[data-dataset="${corrected!.name}"]`);

    await expect(row.getByTestId("dataset-provenance")).toHaveText(
      "Collected here, regulated. Licence cc-by-nc-sa-3.0: may not be shared outside.",
    );
    await expect(row.getByTestId("dataset-provenance-corrected")).toHaveText(
      /^Registered as "Already public"\. Changed on .+, when its licence was read\.$/,
    );
  });

  test("a dataset nobody corrected says only where it came from", async ({ page }) => {
    const page1 = await fetch(`${API}/datasets?tenant_id=canary&q=license-plain-&limit=1`, {
      headers: await bearerFor("canary-researcher"),
    }).then((r) => r.json());
    const plain: Row | undefined = page1.datasets[0];
    test.skip(!plain, "no plain dataset in canary yet; run verify/v44_license_provenance.py");

    await loginAs(page, "canary-researcher");
    await page.goto("/datasets");
    await page.getByTestId("dataset-search").fill(plain!.name);
    const row = page.locator(`[data-dataset="${plain!.name}"]`);

    await expect(row.getByTestId("dataset-provenance")).toHaveText("Collected here, regulated.");
    await expect(row.getByTestId("dataset-provenance-corrected")).toHaveCount(0);
  });
});
