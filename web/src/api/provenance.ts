/**
 * Where a dataset came from, and what its licence allows, in words.
 *
 * One copy, shared by the register form that asks the question and the
 * Datasets list that shows the answer, so the two cannot describe the same
 * choice differently.
 */

export type Provenance = "external_public" | "external_licensed" | "internal_regulated";

export const PROVENANCE: { value: Provenance; label: string; hint: string }[] = [
  {
    value: "external_public",
    label: "Already public",
    hint: "A public benchmark or open corpus. May be exported once it reaches general use.",
  },
  {
    value: "external_licensed",
    label: "From outside, under licence",
    hint: "Came from somewhere else under terms that do not cover republishing.",
  },
  {
    value: "internal_regulated",
    label: "Collected here, regulated",
    hint: "Recordings, records or any data about identifiable people. The usual case.",
  },
];

export function provenanceLabel(value: string): string {
  return PROVENANCE.find((p) => p.value === value)?.label ?? value;
}

/**
 * What a looked-up licence allows, from its two answers. The licence decides
 * whether data may leave at all; a version this platform changed counts as
 * modified, which some licences allow and some do not.
 */
export function licenceAllows(unmodified: boolean | null, modified: boolean | null): string {
  if (unmodified && modified) return "may be shared";
  if (unmodified) return "may be shared unchanged only";
  return "may not be shared outside";
}
