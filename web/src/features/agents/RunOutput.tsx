/**
 * What a finished sandboxed run produced, and a person's review of it.
 *
 * An agent's output is whatever JSON its own code printed. When it is drafts (each with a note), they are shown as the text a person would read; any other output is shown as it is. A person other
 * than the one who started the run accepts it as it was, changes it, or refuses it, once. Munitas keeps who, when, how long it took, and a fingerprint of the text the reviewer ended with, never the text.
 */

import { useState } from "react";
import { useReviewRun } from "../../api/agentOversight";
import type { AgentRun } from "../../api/agents";
import { notify } from "../../components/toast";
import { byOn } from "../../lib/when";
import { Reasons } from "../locking/LockedColumns";

interface Draft {
  document?: string;
  note: string;
}

/** The drafts in a run's output, when it has them: each with a note to read. */
export function draftsOf(findings: unknown[]): Draft[] | null {
  for (const found of findings ?? []) {
    const drafts = (found as { drafts?: unknown })?.drafts;
    if (Array.isArray(drafts) && drafts.length > 0 && drafts.every((d) => typeof (d as Draft)?.note === "string")) return drafts as Draft[];
  }
  return null;
}

const OUTCOME_WORDS = { accepted: "Accepted as it was", changed: "Changed by the reviewer", refused: "Refused" } as const;

export function RunOutput({ run, agentId, me }: { run: AgentRun; agentId: string; me: string | undefined }) {
  const review = useReviewRun();
  const drafts = draftsOf(run.findings);
  const draftText = drafts ? drafts.map((d) => d.note).join("\n\n") : null;
  const [text, setText] = useState(draftText ?? "");
  const [reason, setReason] = useState("");
  const [refusing, setRefusing] = useState(false);
  const finished = run.status === "succeeded";
  const mayReview = finished && !run.review_outcome && Boolean(me) && me !== run.requested_by;
  const changed = draftText !== null && text.trim() !== draftText.trim();
  const send = (outcome: "accepted" | "changed" | "refused") =>
    review.mutate({ runId: run.id, agentId, outcome, final_text: outcome === "changed" ? text : undefined, reason: outcome === "refused" ? reason : undefined }, { onSuccess: () => notify("Review recorded.") });

  return (
    <div className="mt-2 space-y-2 text-sm" data-testid={`run-output-${run.id}`}>
      {drafts ? (
        <div className="space-y-2" data-testid={`run-findings-${run.id}`}>
          {drafts.map((d, i) => (
            <div key={d.document ?? i} className="rounded border border-slate-200 bg-slate-50 p-3">
              {d.document && <p className="mb-1 font-mono text-xs text-slate-500">{d.document}</p>}
              <pre className="whitespace-pre-wrap font-sans text-sm text-slate-800" data-testid={`run-draft-${run.id}-${i}`}>{d.note}</pre>
            </div>
          ))}
        </div>
      ) : (
        <pre data-testid={`run-findings-${run.id}`} className="overflow-x-auto rounded bg-slate-50 p-2 font-mono text-xs text-slate-700">
          {JSON.stringify(run.findings, null, 2)}
        </pre>
      )}

      {run.review_outcome && (
        <p data-testid={`run-review-${run.id}`} className="rounded border border-slate-200 p-3">
          <span className="font-medium">{OUTCOME_WORDS[run.review_outcome]}</span> {byOn(run.reviewed_by_label ?? run.reviewed_by, run.reviewed_at)}
          {run.review_seconds !== null ? `, ${run.review_seconds} ${run.review_seconds === 1 ? "second" : "seconds"} after the run ended` : ""}.
          {run.review_reason && <> <span className="text-slate-500">Reason: </span>{run.review_reason}</>}
        </p>
      )}

      {finished && !run.review_outcome && !mayReview && me === run.requested_by && (
        <p className="text-xs text-slate-500" data-testid={`review-someone-else-${run.id}`}>Someone else has to review this.</p>
      )}

      {mayReview && (
        <div className="space-y-2 rounded border border-slate-200 p-3" data-testid={`review-form-${run.id}`}>
          <p className="font-medium">Review what it produced</p>
          {drafts && (
            <label className="flex flex-col gap-1">
              <span className="text-slate-600">Correct the draft here if it needs it. Only a fingerprint of what you end with is kept, never the text.</span>
              <textarea rows={Math.min(20, Math.max(8, text.split("\n").length + 1))} data-testid={`review-text-${run.id}`} value={text} onChange={(e) => setText(e.target.value)} className="rounded border border-slate-400 px-2 py-1 font-sans text-sm" />
            </label>
          )}
          {refusing && (
            <label className="flex max-w-xl flex-col gap-1">
              Why it is refused
              <textarea rows={2} data-testid={`review-reason-${run.id}`} value={reason} onChange={(e) => setReason(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
            </label>
          )}
          {review.error ? <div role="alert" className="text-red-800"><Reasons error={review.error} fallback="The review could not be recorded." /></div> : null}
          <div className="flex flex-wrap gap-3">
            <button type="button" data-testid={`review-accept-${run.id}`} disabled={review.isPending || changed || refusing} onClick={() => send("accepted")} className="btn btn-primary">
              Accept it as it is
            </button>
            {drafts && (
              <button type="button" data-testid={`review-change-${run.id}`} disabled={review.isPending || !changed || !text.trim() || refusing} onClick={() => send("changed")} className="btn btn-secondary">
                Save my changes
              </button>
            )}
            {!refusing ? (
              <button type="button" data-testid={`review-refuse-open-${run.id}`} onClick={() => setRefusing(true)} className="btn btn-danger-outline">
                Refuse it
              </button>
            ) : (
              <button type="button" data-testid={`review-refuse-send-${run.id}`} disabled={review.isPending || !reason.trim()} onClick={() => send("refused")} className="btn btn-danger">
                Refuse it
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
