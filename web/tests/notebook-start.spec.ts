/**
 * U208 in the console: "Your notebooks" tells a person how to use Munitas from a notebook, from what the platform itself offers.
 *
 * The claims these tests carry, on the live platform and in the test organisation only:
 *   - the page shows the three steps, and the install line is the address this console talks to followed by the file the platform announces, so it is always the
 *     version the platform was built with;
 *   - the version, the checksum and the sign-in address on the page are the platform's own answers, not typed text;
 *   - the Copy button puts exactly the line shown on the clipboard;
 *   - the file the line points at downloads, and is the file whose checksum the page quotes;
 *   - the panel can be hidden and shown again;
 *   - a platform that was built without the library says so and shows no install line (the answer is made up here; nothing is changed on the platform);
 *   - the words are generic: no industry word and no dash used as punctuation.
 */

import { createHash } from "node:crypto";
import { expect, test } from "@playwright/test";
import { API_BASE } from "../config/ports";
import { loginAs } from "./auth-helpers";

test.describe.configure({ mode: "serial" });
test.use({ permissions: ["clipboard-read", "clipboard-write"] });

const PERSON = "canary-reviewer";

interface Offered {
  version: string;
  path: string;
  sha256: string;
  size: number;
}

async function offered(): Promise<Offered> {
  const r = await fetch(`${API_BASE}/client`);
  expect(r.status).toBe(200);
  return (await r.json()) as Offered;
}

test.describe("U208: the notebooks page says how to use a notebook", () => {
  test("the three steps are there, and the install line is the platform's own file", async ({ page }) => {
    const lib = await offered();
    await loginAs(page, PERSON);
    await page.goto("/notebook-sign-ins");
    const panel = page.getByTestId("notebook-start");
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("heading", { name: "Use Munitas from a notebook" })).toBeVisible();
    for (const [n, title] of [[1, "Install the library"], [2, "Sign in as yourself"], [3, "Find data and ask for it"]] as const) {
      await expect(page.getByTestId(`notebook-start-step-${n}`)).toContainText(`${n}. ${title}`);
    }
    const url = `${API_BASE}${lib.path}`;
    await expect(page.getByTestId("notebook-start-install")).toHaveText(`pip install ${url}`);
    await expect(page.getByTestId("notebook-start-install-tables")).toHaveText(`pip install "munitas[tables] @ ${url}"`);
    await expect(page.getByTestId("notebook-start-step-1")).toContainText(`version ${lib.version}`);
    await expect(page.getByTestId("notebook-start-sha")).toHaveText(lib.sha256);
    await expect(page.getByTestId("notebook-start-signin")).toContainText(`Munitas.sign_in("${API_BASE}")`);
  });

  test("Copy puts exactly the line shown on the clipboard", async ({ page }) => {
    const lib = await offered();
    await loginAs(page, PERSON);
    await page.goto("/notebook-sign-ins");
    await page.getByTestId("notebook-start-install-copy").click();
    await expect(page.getByTestId("notebook-start-install-copy")).toHaveText("Copied");
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(`pip install ${API_BASE}${lib.path}`);
  });

  test("the file the line points at downloads, and is the file whose checksum the page quotes", async ({ page }) => {
    await loginAs(page, PERSON);
    await page.goto("/notebook-sign-ins");
    const quoted = (await page.getByTestId("notebook-start-sha").textContent()) ?? "";
    const line = (await page.getByTestId("notebook-start-install").textContent()) ?? "";
    const url = line.replace(/^pip install /, "");
    // No cookie and no header: this is what pip does.
    const r = await fetch(url);
    expect(r.status).toBe(200);
    expect(createHash("sha256").update(Buffer.from(await r.arrayBuffer())).digest("hex")).toBe(quoted);
  });

  test("the panel can be hidden and shown again", async ({ page }) => {
    await loginAs(page, PERSON);
    await page.goto("/notebook-sign-ins");
    await expect(page.getByTestId("notebook-start-install")).toBeVisible();
    await page.getByTestId("notebook-start-toggle").click();
    await expect(page.getByTestId("notebook-start-install")).toHaveCount(0);
    await expect(page.getByTestId("notebook-start-toggle")).toHaveText("Show");
    await page.getByTestId("notebook-start-toggle").click();
    await expect(page.getByTestId("notebook-start-install")).toBeVisible();
  });

  test("a platform built without the library says so and shows no install line", async ({ page }) => {
    await loginAs(page, PERSON);
    await page.route(`${API_BASE}/client`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ available: false }) }));
    await page.goto("/notebook-sign-ins");
    await expect(page.getByTestId("notebook-start-unavailable")).toContainText("This platform does not offer the library for download. Ask the person who runs it.");
    await expect(page.getByTestId("notebook-start-install")).toHaveCount(0);
    await expect(page.getByTestId("notebook-start-step-1")).toHaveCount(0);
  });

  test("the words are generic: no industry word, no dash used as punctuation", async ({ page }) => {
    await loginAs(page, PERSON);
    await page.goto("/notebook-sign-ins");
    const text = (await page.getByTestId("notebook-start").innerText()) ?? "";
    expect(text).not.toMatch(/patient|clinic|hospital|doctor|nurse/i);
    expect(text).not.toMatch(/[–—]/);
    expect(text.length).toBeGreaterThan(300);
  });
});
