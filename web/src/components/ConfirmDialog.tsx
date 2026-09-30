/**
 * An in-app confirmation, for the moment right before an irreversible or
 * consequential action actually happens.
 *
 * `window.confirm`/`window.prompt` work, but they are the one place in this
 * console where the browser's own raw chrome appears instead of anything
 * this project designed: a different font, a different button shape, no
 * indigo, no way to explain the consequence in more than one plain-text
 * line. That mismatch is worst exactly where it matters most, on the
 * actions (revoke access, refuse a request) that cannot be undone by
 * clicking again.
 *
 * Deliberately a single, generic dialog rather than one component per
 * caller: revoking and refusing both need "are you sure, and here is
 * exactly what happens" plus, for refusing, a reason the other person will
 * read. One shape covers both without inventing a second pattern.
 */

import { useEffect, useId, useRef, useState } from "react";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** What happens, stated plainly. Shown above the reason field, if any. */
  description: string;
  confirmLabel: string;
  /** Red confirm button for something that cannot be undone; indigo otherwise. */
  destructive?: boolean;
  /**
   * When set, the dialog collects a required line of text (the reason a
   * refusal is refused, for example) and hands it back to `onConfirm`
   * instead of confirming on an empty answer.
   */
  reasonLabel?: string;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  destructive,
  reasonLabel,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const [reason, setReason] = useState("");
  const titleId = useId();
  const firstFocusable = useRef<HTMLButtonElement | HTMLTextAreaElement>(null);

  useEffect(() => {
    if (open) {
      setReason("");
      // A dialog that opens without moving focus into itself leaves a
      // keyboard user still tabbing through whatever was behind it.
      firstFocusable.current?.focus();
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  const needsReason = Boolean(reasonLabel);
  const canConfirm = !needsReason || reason.trim().length > 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      onClick={onCancel}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-sm rounded-lg bg-white p-5 shadow-xl"
      >
        <h2 id={titleId} className="text-base font-semibold text-slate-900">
          {title}
        </h2>
        <p className="mt-1.5 text-sm text-slate-600">{description}</p>

        {needsReason && (
          <textarea
            ref={firstFocusable as React.RefObject<HTMLTextAreaElement>}
            data-testid="confirm-dialog-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder={reasonLabel}
            rows={3}
            className="mt-3 w-full rounded border border-slate-300 px-2.5 py-1.5 text-sm"
          />
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
          >
            Cancel
          </button>
          <button
            type="button"
            ref={needsReason ? undefined : (firstFocusable as React.RefObject<HTMLButtonElement>)}
            data-testid="confirm-dialog-confirm"
            disabled={!canConfirm}
            onClick={() => onConfirm(reason.trim())}
            className={`rounded px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 ${
              destructive
                ? "bg-red-700 hover:bg-red-800"
                : "bg-indigo-500 hover:bg-indigo-800"
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
