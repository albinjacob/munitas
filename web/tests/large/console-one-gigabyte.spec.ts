/**
 * L5 (large tier): the console sends a file of about a gigabyte in parts, and the page itself stays small.
 *
 * Never run with the ordinary tests: this project exists only when MUNITAS_LARGE=1 is set, which verify/run_large.py does after it has made the file and
 * said what it will cost. The file and its contract are named by MUNITAS_LARGE_FILE and MUNITAS_LARGE_CONTRACT.
 *
 * A browser that read the whole file to send it would hold a gigabyte, so the page's JavaScript memory is read every few seconds while the file goes, and
 * the peak while the last half was sent is set against the peak while the first third was. The console then has to show the table sealed.
 */

import { expect, test } from "@playwright/test";
import { loginAs } from "../auth-helpers";

const FILE = process.env.MUNITAS_LARGE_FILE ?? "";
const CONTRACT = process.env.MUNITAS_LARGE_CONTRACT ?? "";

test("the console sends a gigabyte in parts, the page stays level, and the table is sealed", async ({ page }) => {
  test.skip(!FILE || !CONTRACT, "run_large.py names the file and its contract");
  await loginAs(page, "canary-engineer");
  await page.goto("/datasets/register");
  await page.getByTestId("register-name").fill(`large-console-${Date.now()}`);
  await page.getByTestId("register-department").selectOption({ label: "Verification" });
  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("upload-step")).toBeVisible();
  await page.getByTestId("plain-table").check();
  await page.getByTestId("seal-table-contract").setInputFiles(CONTRACT);
  await page.getByTestId("seal-table-rows").setInputFiles(FILE);
  await expect(page.getByTestId("detected-format")).toContainText("sent in parts");
  await page.getByTestId("seal-table-submit").click();
  await expect(page.getByTestId("large-upload")).toBeVisible();

  const samples: { at: number; share: number; heap: number }[] = [];
  const started = Date.now();
  // How long to wait for the table to be sealed. run_large.py gives a short wait when it is only trying the check out at a small size.
  const waitMinutes = Number(process.env.MUNITAS_LARGE_WAIT_MINUTES ?? 180);
  let noted = 0;
  for (;;) {
    const sealed = await page.getByTestId("table-sealed").isVisible().catch(() => false);
    const refused = await page.getByTestId("table-refused").isVisible().catch(() => false);
    if (sealed || refused) break;
    const text = (await page.getByTestId("upload-progress").textContent().catch(() => "")) ?? "";
    const share = /(\d+)\s*%/.exec(text)?.[1];
    const heap = await page.evaluate(() => (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory?.usedJSHeapSize ?? 0);
    if (heap > 0) samples.push({ at: Date.now() - started, share: share ? Number(share) : -1, heap });
    if (Date.now() - started > waitMinutes * 60_000) break;
    if (Date.now() - started > noted + 60_000) {
      // Said once a minute, with a picture, so a wait that does not end can be seen for what it is.
      noted = Date.now() - started;
      console.log(`LARGE-WAITING ${Math.round(noted / 1000)}s: ${((await page.locator("main").innerText().catch(() => "")) ?? "").replace(/\s+/g, " ").slice(0, 300)}`);
      if (process.env.MUNITAS_SHOTS) await page.screenshot({ path: `${process.env.MUNITAS_SHOTS}/waiting.png` }).catch(() => undefined);
    }
    await page.waitForTimeout(5_000);
  }

  await expect(page.getByTestId("table-sealed")).toBeVisible();
  const peak = (from: number, to: number) => Math.max(0, ...samples.filter((s) => s.share >= from && s.share <= to).map((s) => s.heap));
  const early = peak(5, 35);
  const late = peak(50, 100);
  const mib = (n: number) => Math.round(n / 1048576);
  console.log(`LARGE-MEASURE ${JSON.stringify({ samples: samples.length, early_mib: mib(early), late_mib: mib(late), highest_mib: mib(peak(0, 100)) })}`);
  expect(samples.length).toBeGreaterThan(6);
  expect(late).toBeLessThanOrEqual(early * 1.5 + 100 * 1048576);
  expect(peak(0, 100)).toBeLessThan(400 * 1048576);
});
