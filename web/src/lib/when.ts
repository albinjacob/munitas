/**
 * A moment as the person reading it would write it: the date and the time, in their own time zone.
 *
 * Every screen that shows a decision or a review says when it happened, not only who made it, because a decision somebody cannot date cannot be
 * weighed against what happened around it. The month is spelled out ("Oct 7, 2026, 12:15 PM") because "10/7/2026" is the 7th of October to one reader
 * and the 10th of July to another. Use this everywhere a time is shown, so that the form is the same on every screen.
 */
export function when(iso: string | null | undefined): string {
  if (!iso) return "";
  // A timestamp from the database may use a space where the standard has a T.
  const moment = new Date(iso.includes("T") ? iso : iso.replace(" ", "T"));
  return Number.isNaN(moment.getTime()) ? iso : moment.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** "by Imani on Oct 7, 2026, 12:15 PM": who, and when. The time is left out only when the platform has none for it. */
export function byOn(who: string | null | undefined, iso: string | null | undefined): string {
  return `by ${who ?? "somebody"}${iso ? ` on ${when(iso)}` : ""}`;
}
