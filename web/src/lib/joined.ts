/** Names in a sentence: "Cardiology", "Cardiology and Oncology", "Cardiology, Oncology and Radiology". */
/** As `joined`, but a long list is cut to its first few and says how many more: "Imani, Marlow, Hartley and 7 others". */
export function joinedMax(names: string[], max: number): string {
  if (names.length <= max) return joined(names);
  const rest = names.length - max;
  return `${names.slice(0, max).join(", ")} and ${rest} other${rest === 1 ? "" : "s"}`;
}

export function joined(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}
