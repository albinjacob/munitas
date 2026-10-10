/**
 * Asking to open a few of a version's real files, on the page of the version, and what that says while it waits and while it is in force.
 *
 * Real files are opened only with the approval of somebody who answers for the data. The person says what the files are for, where they will open them (in this
 * page, or in a notebook through the client library), how many they need and for how long; an approver of the owning department, who is not them, decides and may
 * allow fewer; the platform chooses the files and they do not change. What the approval lets the person do is said in those words, and not under the technical name
 * for it. A person may hold one for each place at once.
 */

import { useState } from "react";
import { useEndFileLease, useRequestFileLease, useVersionFileLease, type FileLease } from "../../api/fileLeases";
import { useRecordList } from "../../api/recordFiles";
import { when } from "../../lib/when";
import { Reasons } from "./LockedColumns";

type Kind = "console" | "notebook";

const PLACE: Record<Kind, string> = { console: "in this page", notebook: "in a notebook" };

function hours(n: number): string {
  return `${n} ${n === 1 ? "hour" : "hours"}`;
}

function files(n: number): string {
  return `${n} ${n === 1 ? "file" : "files"}`;
}

function Waiting({ lease }: { lease: FileLease }) {
  const end = useEndFileLease();
  return (
    <div data-testid={`file-lease-waiting-${lease.kind}`} className="space-y-2 text-sm text-slate-800">
      <p className="font-medium">Your request to open these files {PLACE[lease.kind]} is waiting for a decision.</p>
      <p>
        You asked for {files(lease.requested_count)} for {hours(lease.hours)}, for: {lease.purpose}. Asked {when(lease.requested_at)}. An approver of the
        department that owns this data, who is not you, decides, and may allow fewer files or fewer hours than you asked for.
      </p>
      <button
        type="button"
        data-testid={`file-lease-withdraw-${lease.kind}`}
        disabled={end.isPending}
        onClick={() => end.mutate(lease.id)}
        className="rounded border border-slate-500 px-2 py-1 text-xs text-slate-900 disabled:opacity-50"
      >
        {end.isPending ? "Withdrawing" : "Withdraw the request"}
      </button>
      {end.error && (
        <div role="alert" className="text-xs text-red-800">
          <Reasons error={end.error} fallback="That could not be withdrawn." />
        </div>
      )}
    </div>
  );
}

function InForce({ lease }: { lease: FileLease }) {
  const end = useEndFileLease();
  const n = lease.records.length;
  return (
    <div data-testid={`file-lease-active-${lease.kind}`} className="space-y-2 text-sm text-slate-800">
      <p className="font-medium">
        You may open {files(n)} of this version {PLACE[lease.kind]}, until {lease.expires_at ? when(lease.expires_at) : "it ends"}.
      </p>
      <p>
        For: {lease.purpose}. Allowed by {lease.decided_by_label ?? "an approver"}
        {lease.decided_at ? ` on ${when(lease.decided_at)}` : ""}
        {lease.decision_note ? `: ${lease.decision_note}` : ""}. The platform chose these files and they do not change. Every file you open is written down.
        The rest of the files in this version stay closed to you.
      </p>
      {lease.kind === "notebook" && (
        <p>
          In a notebook, use the Munitas client library: <code>list_files</code> lists these files and <code>open_file</code> opens one to read. What you read is
          yours to look after.
        </p>
      )}
      <button
        type="button"
        data-testid={`file-lease-end-${lease.kind}`}
        disabled={end.isPending}
        onClick={() => end.mutate(lease.id)}
        className="rounded border border-slate-500 px-2 py-1 text-xs text-slate-900 disabled:opacity-50"
      >
        {end.isPending ? "Ending" : "End now"}
      </button>
      {end.error && (
        <div role="alert" className="text-xs text-red-800">
          <Reasons error={end.error} fallback="That could not be ended." />
        </div>
      )}
    </div>
  );
}

function Form({
  versionId,
  kinds,
  caps,
  hasApprover,
  says,
}: {
  versionId: string;
  kinds: Kind[];
  caps: Record<string, { records: number; hours: number }>;
  hasApprover: boolean;
  says: string | null;
}) {
  const request = useRequestFileLease(versionId);
  const [purpose, setPurpose] = useState("");
  const [kind, setKind] = useState<Kind>(kinds[0]);
  const cap = caps[kind];
  const [count, setCount] = useState(Math.min(5, cap.records));
  const [length, setLength] = useState(1);
  if (!hasApprover) {
    return (
      <p data-testid="file-lease-nobody" className="text-sm text-slate-800">
        Nobody can decide a request to open these files, because this data belongs to no department. Give it an owning department first.
      </p>
    );
  }
  return (
    <form
      data-testid="file-lease-form"
      className="space-y-3 text-sm"
      onSubmit={(e) => {
        e.preventDefault();
        request.mutate({ kind, purpose: purpose.trim(), records: Math.min(count, cap.records), hours: Math.min(length, cap.hours) });
      }}
    >
      <p className="text-slate-800">
        {says ? `${says} ` : ""}Say what they are for, where you will open them, how many you need and for how long. An approver of the owning department, who is
        not you, decides, and may allow fewer. The platform chooses which files; you cannot pick them, and they stay the same until the approval ends.
      </p>
      {kinds.length === 1 && (
        <p className="text-slate-800">
          {kind === "console"
            ? "You open them in this page: you listen, and a file plays here and cannot be downloaded."
            : "You open them in a notebook, with the Munitas client library. What you read is yours to look after."}
        </p>
      )}
      {kinds.length > 1 && (
        <fieldset className="space-y-1">
          <legend className="font-medium text-slate-800">Where will you open them?</legend>
          {kinds.map((k) => (
            <label key={k} className="flex items-center gap-2">
              <input
                type="radio"
                name="file-lease-kind"
                data-testid={`file-lease-kind-${k}`}
                checked={kind === k}
                onChange={() => {
                  setKind(k);
                  setCount(Math.min(count, caps[k].records));
                  setLength(Math.min(length, caps[k].hours));
                }}
              />
              <span>
                {k === "console"
                  ? "In this page: you listen, and a file plays here and cannot be downloaded"
                  : "In a notebook: you read them with the Munitas client library, and what you read is yours to look after"}
              </span>
            </label>
          ))}
        </fieldset>
      )}
      <label className="block">
        <span className="font-medium text-slate-800">What the files are for</span>
        <input
          data-testid="file-lease-purpose"
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
          placeholder="listen to a few calls to judge how long they run"
          className="mt-1 w-full max-w-xl rounded border border-slate-300 px-2 py-1"
        />
      </label>
      <div className="flex flex-wrap gap-4">
        <label className="block">
          <span className="font-medium text-slate-800">How many files (up to {cap.records})</span>
          <input
            data-testid="file-lease-records"
            type="number"
            min={1}
            max={cap.records}
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
            className="mt-1 block w-28 rounded border border-slate-300 px-2 py-1"
          />
        </label>
        <label className="block">
          <span className="font-medium text-slate-800">For how long (up to {hours(cap.hours)})</span>
          <input
            data-testid="file-lease-hours"
            type="number"
            min={1}
            max={cap.hours}
            value={length}
            onChange={(e) => setLength(Number(e.target.value))}
            className="mt-1 block w-28 rounded border border-slate-300 px-2 py-1"
          />
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          data-testid="file-lease-submit"
          disabled={request.isPending || !purpose.trim()}
          className="rounded bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-800 disabled:opacity-50"
        >
          {request.isPending ? "Asking" : "Ask to open these files"}
        </button>
        <span className="text-xs text-slate-600">Sent under your name, with what you have written here.</span>
      </div>
      {request.error && (
        <div role="alert" data-testid="file-lease-error" className="text-xs text-red-800">
          <Reasons error={request.error} fallback="That was not accepted." />
        </div>
      )}
    </form>
  );
}

export function FileLeasePanel({ versionId, says }: { versionId: string; says: string | null }) {
  const state = useVersionFileLease(versionId);
  const list = useRecordList(versionId);
  if (state.isLoading || state.error || !state.data) return null;
  const data = state.data;
  const basis = list.data?.basis;
  const now = Date.now();
  const live = (l: FileLease) => l.status === "requested" || (l.status === "approved" && l.expires_at !== null && new Date(l.expires_at).getTime() > now);
  const own = data.leases.filter(live);
  const held = (k: Kind) => own.some((l) => l.kind === k);
  // Where the files are open to this person for another reason (a sample drawn for them, synthetic files, a result they are judging) there is nothing to ask for here.
  const openForAnotherReason = basis !== undefined && basis !== "none" && basis !== "lease";
  const askable = data.kinds.filter((k) => !held(k) && data.caps[k]) as Kind[];
  if (openForAnotherReason && own.length === 0) return null;
  if (own.length === 0 && askable.length === 0) return null;
  const inForce = own.some((l) => l.status === "approved");
  return (
    <div data-testid="file-lease-panel" className={`space-y-4 rounded border p-3 ${inForce ? "border-teal-300 bg-teal-50" : "border-slate-200 bg-white"}`}>
      {own.map((l) => (l.status === "requested" ? <Waiting key={l.id} lease={l} /> : <InForce key={l.id} lease={l} />))}
      {!openForAnotherReason && askable.length > 0 && (
        <div className={own.length ? "border-t border-slate-200 pt-3" : undefined}>
          <h3 className="mb-2 text-base font-semibold text-slate-900">{own.length ? "Ask for another way to open them" : "Ask to open some of these files"}</h3>
          <Form versionId={versionId} kinds={askable} caps={data.caps} hasApprover={data.has_approver} says={own.length ? null : says} />
        </div>
      )}
    </div>
  );
}
