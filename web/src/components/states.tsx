/**
 * Loading, empty and error states.
 *
 * Collected here because they are the parts most often written carelessly, and
 * in a governance tool a careless empty state is a lie. "No results" and "the
 * query failed" look identical if both render as a blank table, and one of those
 * means there is nothing to worry about while the other means you cannot tell.
 */

import type { ReactNode } from "react";
import { ApiError } from "../api/client";

/**
 * A skeleton, not a spinner or bare text.
 *
 * A blank screen for a second reads as "did this break", and a spinner alone
 * gives no sense of what is coming or how long it takes. Shapes roughly the
 * size of what is about to render make the wait feel shorter and stop the
 * layout jumping the instant real content lands. `what` is kept for screen
 * readers (`role="status"`, visually hidden) rather than shown as text: it
 * was never the thing telling a sighted reader anything the shapes do not.
 */
export function Loading({ what }: { what: string }) {
  return (
    <div className="animate-pulse space-y-2 p-1" role="status">
      <span className="sr-only">Loading {what}…</span>
      <div className="h-4 w-2/5 rounded bg-slate-200" />
      <div className="h-14 rounded bg-slate-100" />
      <div className="h-14 rounded bg-slate-100" />
      <div className="h-14 w-4/5 rounded bg-slate-100" />
    </div>
  );
}

/**
 * A quiet visual anchor for "nothing here," not decoration for its own sake.
 * A sentence floating alone in a dashed box reads as a dead end; the same
 * sentence under a small icon reads as a state the page is actively in, the
 * same distinction a folder-empty illustration makes in a file browser.
 */
function EmptyIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 40 40"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="mx-auto mb-3 h-9 w-9 text-slate-300"
    >
      <path d="M6 16 12 6h16l6 10" />
      <path d="M6 16v16a2 2 0 0 0 2 2h24a2 2 0 0 0 2-2V16" />
      <path d="M6 16h9a1 1 0 0 1 1 1 4 4 0 0 0 8 0 1 1 0 0 1 1-1h9" />
    </svg>
  );
}

export function Empty({ what, hint }: { what: string; hint?: string }) {
  return (
    <div className="rounded border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
      <EmptyIcon />
      <p className="font-medium text-slate-700">No {what}.</p>
      {hint && <p className="mt-1">{hint}</p>}
    </div>
  );
}

/**
 * A failure, stated plainly.
 *
 * Policy denials get their reasons rendered as a list rather than folded into a
 * sentence, because each reason is a separate fact and the reader usually needs
 * one specific one.
 */
export function Failure({
  error,
  what,
  verb = "load",
}: {
  error: unknown;
  what: string;
  /**
   * What was being attempted, when it was not a read.
   *
   * "Could not load this decision" is the wrong sentence for a button that
   * was refused: nothing was being loaded, somebody was trying to do
   * something and the platform said no. The default stays "load" because
   * most failures on this console are a screen failing to fetch.
   */
  verb?: string;
}) {
  const api = error instanceof ApiError ? error : null;

  return (
    <div
      role="alert"
      data-testid="failure"
      className="rounded border border-red-300 bg-red-50 p-4 text-sm text-red-900"
    >
      <p className="font-semibold">
        Could not {verb} {what}.
      </p>
      {api?.reasons.length ? (
        <>
          <p className="mt-2">It was refused, for these reasons:</p>
          <ul className="mt-1 list-disc pl-5">
            {api.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </>
      ) : (
        <p className="mt-1 font-mono text-xs">
          {error instanceof Error ? error.message : String(error)}
        </p>
      )}
      {api?.status === 0 || !api ? (
        // No shell command here. Somebody reading this screen is doing their
        // job, not operating the platform, and telling them to run a container
        // command is an instruction they cannot act on and did not ask for.
        <p className="mt-2 text-xs text-red-800">
          If nothing on this page loads, the platform is not responding. Whoever
          runs it will need to look.
        </p>
      ) : null}
    </div>
  );
}

export function Section({
  title,
  description,
  children,
  actions,
  level = "section",
}: {
  title: string;
  description?: string;
  children: ReactNode;
  actions?: ReactNode;
  /**
   * "page" for the one Section that names the page itself (a dataset's
   * name, "Datasets", "Register an agent"); "section" (the default) for
   * everything nested under that. Without this, a page's own title and its
   * subsections rendered at the same size and weight, so "Versions" read as
   * no less important than the agent it belongs to -- the same mistake this
   * file's own comment warns about for empty and loading states, just for
   * hierarchy instead of honesty. Sized to match the `<h1>` already
   * hand-written on the handful of pages that have one
   * (`Housekeeping.tsx`, `PipelineRun.tsx`), rather than inventing a third
   * size those pages would then disagree with.
   */
  level?: "page" | "section";
}) {
  const Heading = level === "page" ? "h1" : "h2";
  return (
    <section className={level === "page" ? "mb-6" : "mb-8"}>
      <div className="mb-3 flex items-start justify-between gap-4">
        <div>
          <Heading
            className={
              level === "page"
                ? "text-xl font-semibold text-slate-900"
                : "text-lg font-semibold text-slate-900"
            }
          >
            {title}
          </Heading>
          {description && (
            <p
              className={
                level === "page"
                  ? "mt-1 text-sm text-slate-600"
                  : "mt-0.5 text-sm text-slate-500"
              }
            >
              {description}
            </p>
          )}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}
