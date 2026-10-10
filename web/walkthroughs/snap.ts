import { writeFileSync } from "node:fs";
import type { Page } from "@playwright/test";

/**
 * Takes the picture, and saves the words on the page beside it (same name, `.txt`).
 *
 * The note under each step quotes what is on screen. The builder checks every quoted phrase against these words, so a note that names something the
 * picture does not show is found when the page is built, not by somebody reading a picture. The words are what the browser draws, so text the page
 * capitalises with a style reads capitalised here; the builder compares without regard to case. What is typed or chosen in a box is added at the end,
 * one line each (an empty box gives its hint), because it is on the screen but is not part of the page's text.
 */
export async function snap(page: Page, options: NonNullable<Parameters<Page["screenshot"]>[0]> & { path: string }): Promise<void> {
  await page.screenshot(options);
  const words = await page
    .evaluate(() => {
      const boxes = [...document.querySelectorAll("input, textarea, select")]
        .map((el) => {
          if (el instanceof HTMLSelectElement) return el.selectedOptions[0]?.text ?? "";
          if (el instanceof HTMLInputElement && (el.type === "file" || el.type === "checkbox" || el.type === "radio" || el.type === "password")) {
            return el.type === "file" ? [...(el.files ?? [])].map((f) => f.name).join(", ") : "";
          }
          const box = el as HTMLInputElement | HTMLTextAreaElement;
          return box.value || box.placeholder || "";
        })
        .filter(Boolean);
      return document.body.innerText + "\n" + boxes.join("\n");
    })
    .catch(() => "");
  writeFileSync(options.path.replace(/\.png$/, ".txt"), words, "utf8");
}
