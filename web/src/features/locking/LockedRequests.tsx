/**
 * Locked-column access: the requests waiting for you to decide, the leases in force in the departments you answer for, and your own.
 *
 * A lease lets somebody who is not the owning department's approver read a few rows of a table's locked columns for a few days, for a stated
 * purpose. It is decided by an approver of the department that owns the data, never by the person who asked, and ended early by either.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useDecideLease, useLeases, useRevokeLease, type LockedLease } from "../../api/locking";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { Reasons } from "./LockedColumns";

function when(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
}

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
          className="rounded bg-indigo-500 hover:bg-indigo-800 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
        >
          Approve
        </button>
        <button
          type="button"
          data-testid={`decline-${lease.id}`}
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

function End({ lease }: { lease: LockedLease }) {
  const revoke = useRevokeLease();
  return (
    <div>
      <button
        type="button"
        data-testid={`end-${lease.id}`}
        disabled={revoke.isPending}
        onClick={() => revoke.mutate(lease.id)}
        className="whitespace-nowrap rounded border border-slate-500 px-2 py-1 text-xs text-slate-900 disabled:opacity-50"
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
  testid,
}: {
  leases: LockedLease[];
  who: "asker" | "decider";
  action?: (lease: LockedLease) => React.ReactNode;
  testid: string;
}) {
  return (
    <table className="w-full text-sm" data-testid={testid}>
      <thead className="border-b border-slate-200 text-left text-slate-500">
        <tr>
          <th className="py-2 font-medium">{who === "asker" ? "Table" : "Who asked"}</th>
          <th className="py-2 font-medium">What for</th>
          <th className="py-2 font-medium">Rows and days</th>
          <th className="py-2 font-medium">State</th>
          <th className="py-2 font-medium" />
        </tr>
      </thead>
      <tbody>
        {leases.map((l) => (
          <tr key={l.id} className="border-b border-slate-100 align-top">
            <td className="py-2">
              {who === "decider" ? <span className="font-medium">{l.principal_label}</span> : null}
              <div>
                <Link className="text-sky-700 underline" to={`/versions/${l.dataset_version_id}`}>
                  Open the table
                </Link>
              </div>
            </td>
            <td className="py-2 max-w-xs">{l.purpose}</td>
            <td className="py-2">
              {l.row_limit} row{l.row_limit === 1 ? "" : "s"}, {l.days} day{l.days === 1 ? "" : "s"}
              <div className="text-xs text-slate-500">Asked {when(l.requested_at)}</div>
            </td>
            <td className="py-2">
              <StatusChip lease={l} />
              {l.expires_at && l.status === "approved" && <div className="mt-1 text-xs text-slate-500">Until {when(l.expires_at)}</div>}
              {l.decided_by_label && l.status !== "requested" && (
                <div className="mt-1 text-xs text-slate-500">
                  {l.status === "declined" ? "Declined" : "Approved"} by {l.decided_by_label}
                  {l.decision_note ? `: ${l.decision_note}` : ""}
                </div>
              )}
            </td>
            <td className="py-2">{action?.(l)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function LockedRequests() {
  const leases = useLeases();
  if (leases.isLoading) return <Loading what="locked-column access" />;
  if (leases.error) return <Failure error={leases.error} what="locked-column access" />;
  const d = leases.data!;
  const inForce = (l: LockedLease) => l.status === "approved" && l.expires_at !== null && new Date(l.expires_at) > new Date();

  return (
    <div className="p-6">
      <Section
        level="page"
        title="Locked-column access"
        description="A lease lets somebody who does not answer for a table read a few of its locked rows for a few days, for a stated purpose. An approver of the department that owns the data decides it, never the person who asked."
      >
        {d.to_decide.length > 0 || d.granted.length > 0 ? (
          <>
            <h2 className="mb-2 text-lg font-semibold text-slate-900">Waiting for you to decide</h2>
            {d.to_decide.length ? (
              <LeaseTable leases={d.to_decide} who="decider" testid="leases-to-decide" action={(l) => <Decide lease={l} />} />
            ) : (
              <Empty what="requests waiting for you" />
            )}

            <h2 className="mb-2 mt-8 text-lg font-semibold text-slate-900">In force in your departments</h2>
            {d.granted.length ? (
              <LeaseTable leases={d.granted} who="decider" testid="leases-granted" action={(l) => <End lease={l} />} />
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
      </Section>
    </div>
  );
}
