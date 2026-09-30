/**
 * Asking for access to a version.
 *
 * The researcher's landing page told people to open a dataset and ask, and then
 * gave them nowhere to ask. An instruction with no route is worse than no
 * instruction: it reads as a broken feature rather than a missing one.
 *
 * Everything typed here is shown to the custodian who decides, so the form says
 * that before it is filled in rather than after it is sent. Purpose and
 * justification are two questions and not one: the purpose is the thing the
 * grant is bounded to and appears on every later screen, and the justification
 * is the argument for it. Collapsing them gives a custodian one paragraph and
 * nothing to bound the grant with.
 */

import { useState } from "react";
import { useRequestAccess } from "../../api/queries";
import { usePeople } from "../../api/people";
import type { LeaseRequest } from "../../api/types";

/** Long enough to do the work, short enough that nobody keeps it by accident. */
const TTL_CHOICES = [4, 24, 72, 168];

function ttlLabel(hours: number): string {
  if (hours < 24) return `${hours} hours`;
  const days = hours / 24;
  return days === 1 ? "1 day" : `${days} days`;
}

export function RequestAccess({
  versionId,
  principal,
  existing,
}: {
  versionId: string;
  principal: string;
  /** This person's requests for this version, so the form is not the only truth. */
  existing: LeaseRequest[];
}) {
  const ask = useRequestAccess();
  const people = usePeople();
  const [purpose, setPurpose] = useState("");
  const [justification, setJustification] = useState("");
  const [ttl, setTtl] = useState(TTL_CHOICES[1]);

  // Shown on blur, not every keystroke: the submit button is disabled until
  // the form is already valid, so a click never fires to hang a "show
  // errors now" trigger off of. Blur is the substitute.
  const [purposeTouched, setPurposeTouched] = useState(false);
  const [justificationTouched, setJustificationTouched] = useState(false);

  const pending = existing.find((r) => r.state === "pending");
  const refused = existing
    .filter((r) => r.state === "rejected")
    .sort((a, b) => (b.decided_at ?? "").localeCompare(a.decided_at ?? ""))[0];

  // Confirmation first, and the order is load-bearing.
  //
  // A successful submit invalidates the request list, so the refetch lands a
  // moment later and makes `pending` true. With the pending branch above this
  // one, somebody who had just pressed the button was told "you asked for this
  // on 4 August and it is waiting", which is true and reads as though the click
  // did nothing. Answer the action that was just taken, then describe the state
  // on every later visit.
  if (ask.isSuccess) {
    return (
      <p
        data-testid="request-sent"
        className="rounded border border-teal-300 bg-teal-50 p-4 text-sm text-teal-900"
      >
        Your request has gone to the person answerable for this data. Nothing is
        readable until they grant it.
      </p>
    );
  }

  // Asking twice for the same version is how a queue fills with duplicates that
  // a custodian has to read before discovering they are the same request. Said
  // here rather than refused on submit, so the person knows before they type.
  if (pending) {
    return (
      <p
        data-testid="request-pending"
        className="rounded border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900"
      >
        You asked for this on{" "}
        {new Date(pending.created_at).toLocaleDateString()} and it is waiting
        with the person answerable for it. You will see it here once they decide.
      </p>
    );
  }

  const ready = purpose.trim().length > 0 && justification.trim().length > 0;

  return (
    <form
      data-testid="request-access"
      onSubmit={(e) => {
        e.preventDefault();
        if (!ready) return;
        ask.mutate({
          principal,
          dataset_version_id: versionId,
          purpose: purpose.trim(),
          justification: justification.trim(),
          ttl_hours: ttl,
        });
      }}
      className="space-y-4 rounded border border-slate-200 bg-white p-4"
    >
      {/*
        A previous refusal is shown beside the form, not instead of it. Asking
        again is legitimate when the reason has been addressed, and hiding the
        reason is how somebody sends the same request unchanged.
      */}
      {refused && (
        <p
          data-testid="request-refused-before"
          className="rounded border border-slate-300 bg-slate-50 p-3 text-sm text-slate-700"
        >
          You asked before and {people.label(refused.decided_by)} refused. If you
          are asking again, say what has changed.
        </p>
      )}

      <div>
        <label
          htmlFor="purpose"
          className="block text-sm font-medium text-slate-700"
        >
          What will you use it for?
        </label>
        <p className="mt-0.5 text-xs text-slate-500">
          The grant is bounded to this. Using the data for something else is not
          covered by it.
        </p>
        <input
          id="purpose"
          data-testid="request-purpose"
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
          onBlur={() => setPurposeTouched(true)}
          placeholder="measure recall of the de-identification model"
          className="mt-1.5 w-full rounded border border-slate-300 px-3 py-1.5 text-sm"
        />
        {purposeTouched && purpose.trim().length === 0 && (
          <p className="mt-1 text-xs text-red-800" role="alert">
            Say what you will use this for.
          </p>
        )}
      </div>

      <div>
        <label
          htmlFor="justification"
          className="block text-sm font-medium text-slate-700"
        >
          Why do you need this rather than something less sensitive?
        </label>
        <textarea
          id="justification"
          data-testid="request-justification"
          value={justification}
          onChange={(e) => setJustification(e.target.value)}
          onBlur={() => setJustificationTouched(true)}
          rows={3}
          className="mt-1.5 w-full rounded border border-slate-300 px-3 py-1.5 text-sm"
        />
        {justificationTouched && justification.trim().length === 0 && (
          <p className="mt-1 text-xs text-red-800" role="alert">
            Say why this data, specifically, is needed.
          </p>
        )}
      </div>

      <div>
        <label
          htmlFor="ttl"
          className="block text-sm font-medium text-slate-700"
        >
          For how long?
        </label>
        <select
          id="ttl"
          data-testid="request-ttl"
          value={ttl}
          onChange={(e) => setTtl(Number(e.target.value))}
          className="mt-1.5 rounded border border-slate-300 px-3 py-1.5 text-sm"
        >
          {TTL_CHOICES.map((h) => (
            <option key={h} value={h}>
              {ttlLabel(h)}
            </option>
          ))}
        </select>
        <p className="mt-0.5 text-xs text-slate-500">
          Access ends by itself. Nobody has to remember to take it away.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          data-testid="request-submit"
          disabled={!ready || ask.isPending}
          className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {ask.isPending ? "Sending" : "Ask for access"}
        </button>
        <span className="text-xs text-slate-500">
          Sent under your name, with everything you have written here.
        </span>
      </div>

      {ask.error && (
        <p className="text-xs text-red-800" role="alert">
          {ask.error instanceof Error
            ? ask.error.message
            : "That did not work."}
        </p>
      )}
    </form>
  );
}
