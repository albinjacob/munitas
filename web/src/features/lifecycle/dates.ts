/** A moment as a person reads it, with the time: the same form as on every other screen. */
export { when } from "../../lib/when";

/** A phrase ready to sit inside a sentence: no full stop at the end, so a sentence built around it never ends "..". */
export function bare(text: string | null | undefined): string {
  return (text ?? "").trim().replace(/[.!?]+$/, "");
}
