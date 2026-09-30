/**
 * The visibility class, shown consistently everywhere.
 *
 * One colour means exactly one thing. Red is raw, and nothing else in this
 * console is red. The moment a colour starts doing double duty, someone reads a
 * screen wrongly, and here that means misjudging how sensitive a dataset is.
 *
 * The ramp runs hot to cool as data becomes less restricted, which is the
 * opposite of the usual "green means go" instinct and is correct: green here
 * means the data is safe to see, not that a process succeeded.
 *
 * The badge carries the reader's words, not the stored value: a custodian
 * deciding whether to release a recording should not have to read
 * OPEN_FOR_ANNOTATION off a table cell. The exact value is still reachable,
 * on the tooltip and in the data-testid, which is what the console suite
 * compares against the API.
 */

import type { VisibilityClass } from "../api/types";
import { CLASS_LABEL } from "../api/types";

const STYLES: Record<VisibilityClass, string> = {
  RAW: "bg-red-100 text-red-900 border-red-300",
  UNDER_REVIEW: "bg-orange-100 text-orange-900 border-orange-300",
  OPEN_FOR_ANNOTATION: "bg-amber-100 text-amber-900 border-amber-300",
  OPEN_FOR_TRAINING: "bg-sky-100 text-sky-900 border-sky-300",
  PUBLISHED: "bg-emerald-100 text-emerald-900 border-emerald-300",
};

export function ClassBadge({
  value,
  title,
}: {
  value: VisibilityClass;
  title?: string;
}) {
  return (
    <span
      data-testid={`class-badge-${value}`}
      title={title ? `${title} (${value})` : value}
      className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${STYLES[value]}`}
    >
      {CLASS_LABEL[value]}
    </span>
  );
}

/**
 * Sealed and current class together.
 *
 * This is the single most important thing the console says. A version sealed as
 * UNDER_REVIEW and currently OPEN_FOR_TRAINING has not been rewritten, copied
 * or moved: one row was appended to the transition log and one grant was
 * added. Showing only the current class would hide that, and hiding it would
 * turn the platform's central claim back into a slogan.
 */
export function ClassPair({
  sealed,
  current,
}: {
  sealed: VisibilityClass;
  current: VisibilityClass;
}) {
  if (sealed === current) {
    return <ClassBadge value={current} title="Never promoted" />;
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <ClassBadge value={sealed} title="Class at seal time, unchanged" />
      <span aria-label="promoted to" className="text-slate-400">
        &rarr;
      </span>
      <ClassBadge value={current} title="Current class, from the transition log" />
    </span>
  );
}
