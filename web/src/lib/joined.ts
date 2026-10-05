/** Names in a sentence: "Cardiology", "Cardiology and Oncology", "Cardiology, Oncology and Radiology". */
export function joined(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}
