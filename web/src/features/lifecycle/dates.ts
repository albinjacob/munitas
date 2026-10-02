/** A date as a person reads it: 14 October 2026. Empty for nothing, never "Invalid Date". */
export function when(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" }) : "";
}

/** A phrase ready to sit inside a sentence: no full stop at the end, so a sentence built around it never ends "..". */
export function bare(text: string | null | undefined): string {
  return (text ?? "").trim().replace(/[.!?]+$/, "");
}
