/**
 * People see which versions they can read, and the version page agrees.
 *
 * Built in the canary organisation, which exists for verification, through
 * the same API the console uses, so the state each test starts from is made
 * here rather than found.
 */
import { expect, test } from "@playwright/test";
import { bearerFor, loginAs } from "./auth-helpers";
import { API_BASE } from "../config/ports";

const API = API_BASE;
const CANARY = "canary";

async function post<T>(path: string, body: unknown, headers: Record<string, string> = {}): Promise<T> {
  const r = await fetch(API + path, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} returned ${r.status}: ${await r.text()}`);
  return r.json() as Promise<T>;
}

/** A sealed RAW version in a dataset the Verification department owns. */
async function ownedRawVersion(name: string): Promise<{ datasetId: string; versionId: string }> {
  const org = await fetch(`${API}/organisation?tenant_id=${CANARY}`, {
    headers: await bearerFor("canary-engineer"),
  }).then((r) => r.json());
  const department = org.departments.find((d: { name: string }) => d.name === "Verification");
  const dataset = await post<{ id: string }>("/datasets/register", {
    tenant_id: CANARY,
    name,
    department_id: department.id,
    registered_by: "canary-engineer",
    provenance: "internal_regulated",
    modality: [],
  });
  const form = new FormData();
  form.append("file", new Blob([new Uint8Array([1, 2, 3, 4, 5])]), "probe.bin");
  const uploaded = await fetch(`${API}/datasets/${dataset.id}/files`, { method: "POST", body: form });
  if (!uploaded.ok) throw new Error(`upload returned ${uploaded.status}: ${await uploaded.text()}`);
  const sealed = await post<{ id: string }>(`/datasets/${dataset.id}/seal`, {});
  return { datasetId: dataset.id, versionId: sealed.id };
}

test.describe("U79: people see what they can read", () => {
  test("ask, wait, be granted: the list and the version page follow", async ({ page }) => {
    const name = `access-preview-${Date.now()}`;
    const { versionId } = await ownedRawVersion(name);

    await loginAs(page, "canary-researcher");
    await page.goto("/datasets");
    await page.getByTestId("dataset-search").fill(name);
    const row = page.locator(`[data-dataset="${name}"]`);
    await expect(row.getByTestId("dataset-readable")).toHaveText("0 of 1");
    await row.getByRole("button", { name: "Show versions" }).click();
    await expect(page.getByTestId("access-mark")).toHaveAttribute("data-mark", "ask");
    await expect(page.getByTestId("access-mark")).toHaveText(/Needs a request/);

    await page.goto(`/versions/${versionId}`);
    await expect(page.getByTestId("request-access")).toBeVisible();
    await page.getByTestId("request-purpose").fill("U79 check");
    await page.getByTestId("request-justification").fill("automated verification");
    await page.getByTestId("request-submit").click();
    await expect(page.getByTestId("request-sent")).toBeVisible();

    await page.goto("/datasets");
    await page.getByTestId("dataset-search").fill(name);
    await row.getByRole("button", { name: "Show versions" }).click();
    await expect(page.getByTestId("access-mark")).toHaveAttribute("data-mark", "pending");
    await expect(page.getByTestId("access-mark")).toHaveText(/Asked, waiting/);

    // The custodian grants it, through the same endpoint the console uses.
    const custodian = await bearerFor("canary-custodian");
    const { lease_requests: pendingRows } = await fetch(
      `${API}/lease-requests?tenant_id=${CANARY}&state=pending&principal=canary-researcher`,
      { headers: await bearerFor("canary-custodian") },
    ).then((r) => r.json());
    const ask = pendingRows.find((r: { dataset_version_id: string }) => r.dataset_version_id === versionId);
    await post(`/leases/requests/${ask.id}/approve`, {}, custodian);

    await page.reload();
    await page.getByTestId("dataset-search").fill(name);
    await expect(row.getByTestId("dataset-readable")).toHaveText("1 of 1");
    await row.getByRole("button", { name: "Show versions" }).click();
    await expect(page.getByTestId("access-mark")).toHaveAttribute("data-mark", "lease");
    await expect(page.getByTestId("access-mark")).toHaveText(/You can read this until/);

    await page.goto(`/versions/${versionId}`);
    await expect(page.getByTestId("access-mark")).toHaveText(/You can read this until .* for U79 check/);
    await expect(page.getByTestId("request-access")).toHaveCount(0);
  });

  test("a version your role covers says so, with no form", async ({ page }) => {
    const versions = await fetch(
      `${API}/dataset-versions?tenant_id=${CANARY}&current_class=PUBLISHED&limit=1`,
    ).then((r) => r.json());
    test.skip(!versions.length, "the canary organisation has no published version");
    await loginAs(page, "canary-researcher");
    await page.goto(`/versions/${versions[0].dataset_version_id}`);
    await expect(page.getByTestId("access-mark")).toHaveAttribute("data-mark", "role");
    await expect(page.getByTestId("access-mark")).toHaveText(/You can read this/);
    await expect(page.getByTestId("request-access")).toHaveCount(0);
  });
});
