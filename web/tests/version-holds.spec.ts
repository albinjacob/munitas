/**
 * U161 in the console: a version page says when the version was sealed and what it holds.
 *
 * The version is a real one in the organisation. What the platform found in it is stubbed for the cases a live organisation cannot be relied on to hold at
 * the moment (several kinds together, a table, files read only by name, an empty version), and read live for the case it can: the figures on the page are
 * compared with the answer the platform gives for the same version.
 */
import { expect, test } from "@playwright/test";
import { bearerFor, loginAs } from "./auth-helpers";
import { API_BASE } from "../config/ports";

const CUSTODIAN = "canary-custodian";

interface Holds {
  kinds: string[];
  formats: string[];
  structures: string[];
  kind: string | null;
  structure: string | null;
  files: number;
  bytes: number;
  by_format: { format: string; kind: string; structure: string; files: number; bytes: number }[];
  read_from: string | null;
  table: { present: boolean; rows: number | null } | null;
}

async function aVersionWithFiles(): Promise<{ id: string; holds: Holds }> {
  const headers = await bearerFor(CUSTODIAN);
  const versions = (await (await fetch(`${API_BASE}/dataset-versions?limit=60`, { headers })).json()) as { dataset_version_id: string }[];
  for (const v of versions) {
    const r = await fetch(`${API_BASE}/dataset-versions/${v.dataset_version_id}/holds`, { headers });
    if (!r.ok) continue;
    const holds = (await r.json()) as Holds;
    if (holds.files > 0 && !holds.table) return { id: v.dataset_version_id, holds };
  }
  throw new Error("no version with files was found in the organisation to read");
}

async function stubHolds(page: import("@playwright/test").Page, body: Holds) {
  await page.route("**/dataset-versions/*/holds", (route) => route.fulfill({ json: body }));
}

const base: Holds = { kinds: [], formats: [], structures: [], kind: null, structure: null, files: 0, bytes: 0, by_format: [], read_from: null, table: null };

test.describe("U161: the version page", () => {
  test("says when the version was sealed, as a date and a time", async ({ page }) => {
    const { id } = await aVersionWithFiles();
    await loginAs(page, CUSTODIAN);
    await page.goto(`/versions/${id}`);
    await expect(page.getByTestId("sealed-at")).toHaveText(/[A-Z][a-z]{2} \d{1,2}, \d{4}.*\d{1,2}:\d{2}/, { timeout: 20_000 });
    await expect(page.getByText("Records", { exact: true })).toHaveCount(0);
  });

  test("shows what a real version holds, the same as the platform says", async ({ page }) => {
    const { id, holds } = await aVersionWithFiles();
    await loginAs(page, CUSTODIAN);
    await page.goto(`/versions/${id}`);
    const section = page.getByTestId("version-holds");
    await expect(section).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("holds-files")).toHaveText(holds.files.toLocaleString());
    await expect(page.getByTestId("holds-kind")).toHaveText(holds.kinds.map((k) => k[0].toUpperCase() + k.slice(1)).join(" + "));
    await expect(page.getByTestId("holds-format-list")).toHaveText(holds.formats.map((f) => f.toUpperCase()).join(", "));
    // One format is said once, in the summary; a table of formats appears only when there is more than one to tell apart.
    await expect(page.getByTestId("holds-formats")).toHaveCount(holds.by_format.length > 1 ? 1 : 0);
    if (holds.by_format.length > 1) await expect(page.getByTestId("holds-formats").locator("tbody tr")).toHaveCount(holds.by_format.length);
  });

  test("several kinds together: multimodal, structure mixed with each named, a row per format", async ({ page }) => {
    const { id } = await aVersionWithFiles();
    await stubHolds(page, {
      ...base, kinds: ["audio", "text"], formats: ["json", "wav"], structures: ["semi-structured", "unstructured"], kind: "multimodal", structure: "mixed",
      files: 7, bytes: 2_500_000, read_from: "content",
      by_format: [
        { format: "wav", kind: "audio", structure: "unstructured", files: 4, bytes: 2_400_000 },
        { format: "json", kind: "text", structure: "semi-structured", files: 3, bytes: 100_000 },
      ],
    });
    await loginAs(page, CUSTODIAN);
    await page.goto(`/versions/${id}`);
    await expect(page.getByTestId("holds-kind")).toHaveText("Audio + Text", { timeout: 20_000 });
    await expect(page.getByTestId("version-holds")).toContainText("Multimodal");
    await expect(page.getByTestId("holds-structure")).toHaveText("Mixed");
    await expect(page.getByTestId("version-holds")).toContainText("Semi-structured, Unstructured");
    await expect(page.getByTestId("holds-files")).toHaveText("7");
    await expect(page.getByTestId("holds-format-list")).toHaveText("JSON, WAV");
    await expect(page.getByTestId("holds-formats").locator("tbody tr")).toHaveCount(2);
    await expect(page.getByTestId("holds-basis")).toHaveCount(0);
  });

  test("a table is shown by its rows, with no file counts", async ({ page }) => {
    const { id } = await aVersionWithFiles();
    await stubHolds(page, {
      ...base, kinds: ["tabular"], formats: ["table"], structures: ["structured"], kind: "tabular", structure: "structured", read_from: "table",
      by_format: [{ format: "table", kind: "tabular", structure: "structured", files: 0, bytes: 0 }], table: { present: true, rows: 12_345 },
    });
    await loginAs(page, CUSTODIAN);
    await page.goto(`/versions/${id}`);
    await expect(page.getByTestId("holds-rows")).toHaveText("12,345", { timeout: 20_000 });
    await expect(page.getByTestId("holds-kind")).toHaveText("Tabular");
    await expect(page.getByTestId("holds-format-list")).toHaveText("Table");
    await expect(page.getByTestId("holds-formats")).toHaveCount(0);
    await expect(page.getByTestId("holds-files")).toHaveCount(0);
    await expect(page.getByTestId("holds-size")).toHaveCount(0);
  });

  test("a table that could not be written says so", async ({ page }) => {
    const { id } = await aVersionWithFiles();
    await stubHolds(page, {
      ...base, kinds: ["tabular"], formats: ["table"], structures: ["structured"], kind: "tabular", structure: "structured", read_from: "table",
      by_format: [{ format: "table", kind: "tabular", structure: "structured", files: 0, bytes: 0 }], table: { present: false, rows: null },
    });
    await loginAs(page, CUSTODIAN);
    await page.goto(`/versions/${id}`);
    await expect(page.getByTestId("holds-rows")).toHaveText("No table was written", { timeout: 20_000 });
  });

  test("files told only by their names say why", async ({ page }) => {
    const { id } = await aVersionWithFiles();
    await stubHolds(page, {
      ...base, kinds: ["audio"], formats: ["wav"], structures: ["unstructured"], kind: "audio", structure: "unstructured", files: 2, bytes: 90, read_from: "name",
      by_format: [{ format: "wav", kind: "audio", structure: "unstructured", files: 2, bytes: 90 }],
    });
    await loginAs(page, CUSTODIAN);
    await page.goto(`/versions/${id}`);
    await expect(page.getByTestId("holds-basis")).toContainText("worked out from the file names", { timeout: 20_000 });
  });

  test("a version with nothing in it says so and shows no empty table", async ({ page }) => {
    const { id } = await aVersionWithFiles();
    await stubHolds(page, base);
    await loginAs(page, CUSTODIAN);
    await page.goto(`/versions/${id}`);
    await expect(page.getByText("Nothing is stored in this version.")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("holds-formats")).toHaveCount(0);
  });

  test("a failure to read it is shown, not an empty section", async ({ page }) => {
    const { id } = await aVersionWithFiles();
    await page.route("**/dataset-versions/*/holds", (route) => route.fulfill({ status: 500, json: { detail: "broken" } }));
    await loginAs(page, CUSTODIAN);
    await page.goto(`/versions/${id}`);
    await expect(page.getByTestId("version-holds")).toHaveCount(0);
    await expect(page.getByText("Nothing is stored in this version.")).toHaveCount(0);
    await expect(page.getByRole("alert").first()).toBeVisible({ timeout: 20_000 });
  });
});
