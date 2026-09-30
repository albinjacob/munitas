/**
 * A brief, low-key confirmation after a state-changing action, not a modal
 * and not confetti. A governance console approving access to health data is
 * the wrong place for celebratory animation, but leaving every action to
 * confirm itself only by a row quietly vanishing from a list is easy to
 * miss, especially the ones (Home.tsx's approve/refuse/revoke) whose whole
 * visible effect IS a row disappearing. One line, bottom-right, gone in a
 * couple of seconds, is the smallest thing that still says "that worked."
 *
 * A plain module-level subscriber list rather than React Context: nothing
 * here needs to be provided per-subtree or overridden anywhere, so a
 * context provider would only add indirection between `notify()` and the
 * one host that renders its result.
 */

import { useEffect, useState } from "react";

export interface Toast {
  id: number;
  message: string;
  tone: "success" | "error";
}

let nextId = 1;
let toasts: Toast[] = [];
const listeners = new Set<(toasts: Toast[]) => void>();

function emit() {
  for (const listener of listeners) listener(toasts);
}

export function notify(message: string, tone: Toast["tone"] = "success") {
  const toast: Toast = { id: nextId++, message, tone };
  toasts = [...toasts, toast];
  emit();
  setTimeout(() => {
    toasts = toasts.filter((t) => t.id !== toast.id);
    emit();
  }, 2600);
}

export function ToastHost() {
  const [current, setCurrent] = useState<Toast[]>(toasts);

  useEffect(() => {
    listeners.add(setCurrent);
    return () => {
      listeners.delete(setCurrent);
    };
  }, []);

  if (!current.length) return null;

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {current.map((t) => (
        <div
          key={t.id}
          role="status"
          // Deliberately not emerald: that colour already means "PUBLISHED"
          // on a class badge elsewhere in this console (see ClassBadge.tsx),
          // and a colour that means two things is the exact failure this
          // project's own palette rule exists to prevent. Red for the error
          // tone matches the existing precedent in Failure/AuditLog, both
          // already using it for "refused/wrong," a distinct context from
          // ClassBadge's RAW.
          className={`pointer-events-auto rounded px-4 py-2 text-sm font-medium text-white shadow-lg ${
            t.tone === "success" ? "bg-slate-800" : "bg-red-700"
          }`}
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}
