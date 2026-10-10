/**
 * The Files part of the page of locked data requests: the requests to open real files that wait for you to decide, the permissions in force in the
 * departments you answer for, and your own.
 *
 * An approver may allow fewer files or fewer hours than were asked for, never more, and says why when declining. The platform chooses the files when it
 * approves, so the approver is never asked which, and the person asking cannot pick them.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useDecideFileLease, useEndFileLease, useFileLeases, type FileLease } from "../../api/fileLeases";
import { Empty, Failure, Loading } from "../../components/states";
import { when } from "../../lib/when";
import { Reasons } from "./LockedColumns";

const STATUS_WORDS: Record<FileLease["status"], string> = {
  requested: "Waiting for an approver",
  approved: "Allowed",
  declined: "Declined",
  ended: "Ended",
};

function files(n: number): string {
  return `${n} ${n === 1 ? "file" : "files"}`;
}

function hours(n: number): string {
  return `${n} ${n === 1 ? "hour" : "hours"}`;
}

function Status({ lease }: { lease: FileLease }) {
  const over = lease.status === "approved" && lease.expires_at !== null && new Date(lease.expires_at) <= new Date();
  const word = over ? "Ran out" : STATUS_WORDS[lease.status];
  const tone = lease.status === "approved" && !over ? "bg-teal-100 text-teal-900" : lease.status === "requested" ? "bg-amber-100 text-amber-900" : "bg-slate-200 text-slate-800";
  return <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${tone}`}>{word}</span>;
}

function Decide({ lease }: { lease: FileLease }) {
  const decide = useDecideFileLease();
  const [note, setNote] = useState("");
  const [count, setCount] = useState(lease.requested_count);
  const [length, setLength] = useState(lease.hours);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-700">
        <label>
          Files to allow{" "}
          <input
            data-testid={`file-allow-records-${lease.id}`}
            type="number"
            min={1}
            max={lease.requested_count}
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
            className="ml-1 w-16 rounded border border-slate-300 px-1 py-0.5"
          />
        </label>
        <label>
          Hours to allow{" "}
          <input
            data-testid={`file-allow-hours-${lease.id}`}
            type="number"
            min={1}
            max={lease.hours}
            value={length}
            onChange={(e) => setLength(Number(e.target.value))}
            className="ml-1 w-16 rounded border border-slate-300 px-1 py-0.5"
          />
        </label>
      </div>
      <input
        data-testid={`file-note-${lease.id}`}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="A note, recorded with your decision"
        className="w-72 rounded border border-slate-300 px-2 py-1 text-xs"
      />
      <div className="flex gap-2">
        <button
          type="button"
          data-testid={`file-approve-${lease.id}`}
          disabled={decide.isPending}
          onClick={() => decide.mutate({ id: lease.id, verb: "approve", note, records: count, hours: length })}
          className="rounded bg-indigo-500 px-2 py-1 text-xs font-medium text-white hover:bg-indigo-800 disabled:opacity-50"
        >
          Allow
        </button>
        <button
          type="button"
          data-testid={`file-decline-${lease.id}`}
          disabled={decide.isPending || !note.trim()}
          title={note.trim() ? undefined : "Say why in the note first"}
          onClick={() => decide.mutate({ id: lease.id, verb: "decline", note })}
          className="rounded border border-slate-700 px-2 py-1 text-xs font-medium text-slate-900 disabled:opacity-50"
        >
          Decline
        </button>
      </div>
      {decide.error && (
        <div role="alert" className="text-xs text-red-800">
          <Reasons error={decide.error} fallback="That could not be decided." />
        </div>
      )}
    </div>
  );
}

function End({ lease }: { lease: FileLease }) {
  const end = useEndFileLease();
  return (
    <div>
      <button
        type="button"
        data-testid={`file-end-${lease.id}`}
        disabled={end.isPending}
        onClick={() => end.mutate(lease.id)}
        className="whitespace-nowrap rounded border border-slate-500 px-2 py-1 text-xs text-slate-900 disabled:opacity-50"
      >
        {end.isPending ? "Ending" : "End now"}
      </button>
      {end.error && (
        <div role="alert" className="mt-1 text-xs text-red-800">
          <Reasons error={end.error} fallback="That could not be ended." />
        </div>
      )}
    </div>
  );
}

function FileTable({
  leases,
  who,
  testid,
  actionLabel,
  action,
}: {
  leases: FileLease[];
  who: "asker" | "decider";
  testid: string;
  actionLabel?: string;
  action?: (lease: FileLease) => React.ReactNode;
}) {
  return (
    <div className="data-card">
      <table className="data-table" data-testid={testid}>
        <thead>
          <tr>
            <th>Table</th>
            {who === "decider" && <th>Who asked</th>}
            <th>What for</th>
            <th>Where</th>
            <th>Files and hours</th>
            <th>State</th>
            <th>{action ? actionLabel ?? "Action" : ""}</th>
          </tr>
        </thead>
        <tbody>
          {leases.map((l) => (
            <tr key={l.id}>
              <td>
                <Link className="font-medium text-sky-700 underline" to={`/versions/${l.dataset_version_id}`}>
                  {l.dataset_name ?? "Open the table"}
                </Link>
                {l.dataset_version != null && <span className="ml-1 text-slate-500">v{l.dataset_version}</span>}
              </td>
              {who === "decider" && <td className="font-medium">{l.principal_label}</td>}
              <td className="max-w-xs">{l.purpose}</td>
              <td className="whitespace-nowrap">{l.kind === "notebook" ? "In a notebook" : "In the page"}</td>
              <td className="whitespace-nowrap">
                {l.status === "requested" ? `${files(l.requested_count)}, ${hours(l.hours)}` : `${files(l.granted_count ?? l.requested_count)}, ${hours(l.granted_hours ?? l.hours)}`}
                <div className="text-xs text-slate-500">Asked {when(l.requested_at)}</div>
              </td>
              <td>
                <Status lease={l} />
                {l.expires_at && l.status === "approved" && <div className="mt-1 text-xs text-slate-500">Until {when(l.expires_at)}</div>}
                {l.decided_by_label && l.status !== "requested" && (
                  <div className="mt-1 text-xs text-slate-500">
                    {l.status === "declined" ? "Declined" : "Allowed"} by {l.decided_by_label}
                    {l.decided_at ? ` on ${when(l.decided_at)}` : ""}
                    {l.decision_note ? `: ${l.decision_note}` : ""}
                  </div>
                )}
                {l.status === "ended" && l.ended_by_label && l.ended_at && (
                  <div className="mt-1 text-xs text-slate-500">
                    Ended by {l.ended_by_label} on {when(l.ended_at)}
                  </div>
                )}
              </td>
              <td>{action?.(l)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FileLeaseSections() {
  const leases = useFileLeases();
  if (leases.isLoading) return <Loading what="access to files" />;
  if (leases.error) return <Failure error={leases.error} what="access to files" />;
  const d = leases.data!;
  const inForce = (l: FileLease) => l.status === "approved" && l.expires_at !== null && new Date(l.expires_at) > new Date();
  return (
    <div data-testid="file-leases">
      {d.to_decide.length > 0 || d.granted.length > 0 ? (
        <>
          <h3 className="mb-2 text-base font-semibold text-slate-900">Waiting for you to decide</h3>
          {d.to_decide.length ? (
            <FileTable leases={d.to_decide} who="decider" testid="file-leases-to-decide" actionLabel="Your decision" action={(l) => <Decide lease={l} />} />
          ) : (
            <Empty what="requests to open files waiting for you" />
          )}
          <h3 className="mb-2 mt-6 text-base font-semibold text-slate-900">Allowed now in your departments</h3>
          {d.granted.length ? (
            <FileTable leases={d.granted} who="decider" testid="file-leases-granted" actionLabel="Action" action={(l) => <End lease={l} />} />
          ) : (
            <Empty what="permissions to open files in force" />
          )}
        </>
      ) : null}
      <h3 className="mb-2 mt-6 text-base font-semibold text-slate-900">Your requests to open files</h3>
      {d.mine.length ? (
        <FileTable leases={d.mine} who="asker" testid="file-leases-mine" action={(l) => (inForce(l) || l.status === "requested" ? <End lease={l} /> : null)} />
      ) : (
        <Empty what="requests to open files of your own" hint="To ask, open a version that holds real files and use the form in its Files section." />
      )}
    </div>
  );
}
