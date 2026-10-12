/**
 * Words from the platform on a screen.
 *
 * The platform gives reasons as short lower-case clauses ("nothing measured it"), and a pipeline's author writes its own. On a screen each reads as
 * a sentence: a capital at the start and a full stop at the end, whatever the source wrote.
 *
 * A reason may begin with a file name ("notes.txt is neither a file nor an answer key"). A name is data, and changing a letter of it names a
 * different file, so a first word that is a name (it holds a dot, a slash, an underscore or a digit) is left exactly as it was written.
 */
export function sentence(text: string | null | undefined): string {
  const t = (text ?? "").trim();
  if (!t) return "";
  const firstWord = t.split(/\s/, 1)[0];
  const isName = /[A-Za-z0-9][._/][A-Za-z0-9]|\d/.test(firstWord);
  return (isName ? t.charAt(0) : t.charAt(0).toUpperCase()) + t.slice(1) + (/[.!?]$/.test(t) ? "" : ".");
}

/** Words that start a sentence ("the AI risk owner" as "The AI risk owner"). */
export function sentenceStart(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** An internal name ("custom_step") as words a person would write ("Custom step"). */
export function words(name: string): string {
  const t = name.replace(/[_-]+/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}
