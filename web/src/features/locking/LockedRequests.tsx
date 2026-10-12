/**
 * Locked data requests: the requests waiting for you to decide, what is allowed now in the departments you answer for, and your own, for the locked
 * rows of a table and, below them, for its real files.
 *
 * A lease lets somebody who is not the owning department's approver read a few rows of a table's locked columns for a few days, for a stated
 * purpose. It is decided by an approver of the department that owns the data, never by the person who asked, and ended early by either.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useDecideLease, useLeases, useRevokeLease, type LockedLease } from "../../api/locking";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { FileLeaseSections } from "./FileLeaseSections";
import { Reasons } from "./LockedColumns";
import { when } from "../../lib/when";


const STATUS_WORDS: Record<LockedLease["status"], string> = {
  requested: "Waiting for an approver",
  approved: "In force",
  declined: "Declined",
  revoked: "Ended early",
};

function StatusChip({ lease }: { lease: LockedLease }) {
  const over = lease.status === "approved" && lease.expires_at !== null && new Date(lease.expires_at) < new Date();
  const word = over ? "Ran out" : STATUS_WORDS[lease.status];
  const tone =
    lease.status === "approved" && !over
      ? "bg-teal-100 text-teal-900"
      : lease.status === "requested"
        ? "bg-amber-100 text-amber-900"
        : "bg-slate-200 text-slate-800";
  return <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${tone}`}>{word}</span>;
}

function Decide({ lease }: { lease: LockedLease }) {
  const decide = useDecideLease();
  const [note, setNote] = useState("");
  return (
    <div className="space-y-2">
      <input
        data-testid={`note-${lease.id}`}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="A note, recorded with your decision"
        className="w-72 rounded border border-slate-300 px-2 py-1 text-xs"
      />
      <div className="flex gap-2">
        <button
          type="button"
          data-testid={`approve-${lease.id}`}
          disabled={decide.isPending}
          onClick={() => decide.mutate({ id: lease.id, verb: "approve", note })}
          className="btn btn-primary btn-sm"
        >
          Approve
        </button>
        <button
          type="button"
          data-testid={`decline-${lease.id}`}
          disabled={decide.isPending || !note.trim()}
          title={note.trim() ? undefined : "Say why in the note first"}
          onClick={() => decide.mutate({ id: lease.id, verb: "decline", note })}
          className="btn btn-secondary btn-sm"
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

function End({ lease }: { lease: LockedLease }) {
  const revoke = useRevokeLease();
  return (
    <div>
      <button
        type="button"
        data-testid={`end-${lease.id}`}
        disabled={revoke.isPending}
        onClick={() => revoke.mutate(lease.id)}
        className="btn btn-secondary btn-sm whitespace-nowrap"
      >
        {revoke.isPending ? "Ending" : "End now"}
      </button>
      {revoke.error && (
        <div role="alert" className="mt-1 text-xs text-red-800">
          <Reasons error={revoke.error} fallback="That could not be ended." />
        </div>
      )}
    </div>
  );
}

function LeaseTable({
  leases,
  who,
  action,
  actionLabel,
  testid,
}: {
  leases: LockedLease[];
  who: "asker" | "decider";
  action?: (lease: LockedLease) => React.ReactNode;
  actionLabel?: string;
  testid: string;
}) {
  return (
    <div className="overflow-x-auto rounded border border-slate-200 bg-white">
      <table className="w-full text-sm" data-testid={testid}>
        <thead className="whitespace-nowrap bg-slate-50 text-left text-xs uppercase text-slate-500">
          <tr>
            <th className="px-4 py-2">Table</th>
            {who === "decider" && <th className="px-2">Who asked</th>}
            <th className="px-2">What for</th>
            <th className="px-2">Rows and days</th>
            <th className="px-2">State</th>
            <th className="px-2">{action ? actionLabel ?? "Action" : ""}</th>
          </tr>
        </thead>
        <tbody>
          {leases.map((l) => (
            <tr key={l.id} className="border-t border-slate-100 align-top transition-colors hover:bg-slate-50">
              <td className="px-4 py-2">
                <Link className="font-medium text-sky-700 underline" to={`/versions/${l.dataset_version_id}`}>
                  {l.dataset_name ?? "Open the table"}
                </Link>
                {l.dataset_version != null && <span className="ml-1 text-slate-500">v{l.dataset_version}</span>}
              </td>
              {who === "decider" && <td className="px-2 py-2 font-medium">{l.principal_label}</td>}
              <td className="max-w-xs px-2 py-2">{l.purpose}</td>
              <td className="whitespace-nowrap px-2 py-2">
                {l.row_limit} row{l.row_limit === 1 ? "" : "s"}, {l.days} day{l.days === 1 ? "" : "s"}
                <div className="text-xs text-slate-500">Asked {when(l.requested_at)}</div>
              </td>
              <td className="px-2 py-2">
                <StatusChip lease={l} />
                {l.expires_at && l.status === "approved" && <div className="mt-1 text-xs text-slate-500">Until {when(l.expires_at)}</div>}
                {l.decided_by_label && l.status !== "requested" && (
                  <div className="mt-1 text-xs text-slate-500">
                    {l.status === "declined" ? "Declined" : "Approved"} by {l.decided_by_label}
                    {l.decided_at ? ` on ${when(l.decided_at)}` : ""}
                    {l.decision_note ? `: ${l.decision_note}` : ""}
                  </div>
                )}
              </td>
              <td className="px-2 py-2">{action?.(l)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function LockedRequests() {
  const leases = useLeases();
  if (leases.isLoading) return <Loading what="locked data requests" />;
  if (leases.error) return <Failure error={leases.error} what="locked data requests" />;
  const d = leases.data!;
  const inForce = (l: LockedLease) => l.status === "approved" && l.expires_at !== null && new Date(l.expires_at) > new Date();

  return (
    <div>
      <Section
        level="page"
        title="Locked data requests"
        description="Somebody who does not answer for a table can ask to read a few of its locked rows, or to open a few of its real files, for a stated purpose and a limited time. An approver of the department that owns the data decides, never the person who asked."
      >
        <h2 className="mb-3 text-xl font-semibold text-slate-900">Locked rows</h2>
        {d.to_decide.length > 0 || d.granted.length > 0 ? (
          <>
            <h2 className="mb-2 text-lg font-semibold text-slate-900">Waiting for you to decide</h2>
            {d.to_decide.length ? (
              <LeaseTable leases={d.to_decide} who="decider" testid="leases-to-decide" actionLabel="Your decision" action={(l) => <Decide lease={l} />} />
            ) : (
              <Empty what="requests waiting for you" />
            )}

            <h2 className="mb-2 mt-8 text-lg font-semibold text-slate-900">In force in your departments</h2>
            {d.granted.length ? (
              <LeaseTable leases={d.granted} who="decider" testid="leases-granted" actionLabel="Action" action={(l) => <End lease={l} />} />
            ) : (
              <Empty what="leases in force" />
            )}
          </>
        ) : null}

        <h2 className="mb-2 mt-8 text-lg font-semibold text-slate-900">Your requests and leases</h2>
        {d.mine.length ? (
          <LeaseTable
            leases={d.mine}
            who="asker"
            testid="leases-mine"
            action={(l) => (inForce(l) ? <End lease={l} /> : null)}
          />
        ) : (
          <Empty what="requests of your own" hint="To ask for access, open a table that has locked columns and use the form there." />
        )}

        <h2 className="mb-3 mt-12 border-t border-slate-200 pt-8 text-xl font-semibold text-slate-900">Files</h2>
        <FileLeaseSections />
      </Section>
    </div>
  );
}
