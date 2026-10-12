/**
 * A table's locked columns, on the page of the version that holds them.
 *
 * Some columns of a table are locked: each person's values are scrambled under a key made for that person, and destroying that key later makes
 * them unreadable for good, while the sealed table itself never changes. This panel says which columns are locked, who may read them, and how
 * to ask when you may not. It shows the shape of the table and never a value.
 *
 * What the screen offers follows what the platform says this person may do (`useLocking`), never a copy of the rules. The platform refuses
 * whatever the screen offers wrongly, and says why.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../api/client";
import {
  useDecideGroupSize,
  useGroupSizeReport,
  useLocking,
  useRequestLease,
  useRevokeLease,
  type Classification,
  type GroupSizeReport,
  type Locking,
  type Treatment,
} from "../../api/locking";
import { Failure, Section } from "../../components/states";
import { when } from "../../lib/when";

export const CLASSIFICATION_WORDS: Record<Classification, string> = {
  direct: "Names a person",
  phi: "Sensitive detail about a person",
  quasi: "Could identify someone in combination with others",
  none: "Not identifying",
};

export const TREATMENT_WORDS: Record<Treatment, string> = {
  lock: "Locked under the person's key",
  readable: "Readable",
  generalise: "Made coarser",
  "replaced by the person code": "Replaced by a person code",
};


export function Reasons({ error, fallback }: { error: unknown; fallback: string }) {
  const reasons = error instanceof ApiError && error.reasons.length ? error.reasons : [error instanceof Error ? error.message : fallback];
  return (
    <ul className="list-disc space-y-0.5 pl-4">
      {reasons.map((r) => (
        <li key={r}>{r}</li>
      ))}
    </ul>
  );
}

function AskForAccess({ locking }: { locking: Locking }) {
  const request = useRequestLease();
  const [purpose, setPurpose] = useState("");
  const [days, setDays] = useState(Math.min(3, locking.limits.days));
  const [rows, setRows] = useState(Math.min(5, locking.limits.rows));

  return (
    <form
      data-testid="ask-lease"
      className="space-y-3"
      // The browser's own range message would say it its own way. The platform states the limit in the rule's words, and it is what refuses.
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        request.mutate({ dataset_version_id: locking.version_id, purpose, days, rows });
      }}
    >
      <p className="text-sm text-slate-700">
        You cannot read the locked values yet. You can ask an approver of the department that owns this data to let you read some of them for a
        while. They decide, and it is recorded who asked, why, and what they read.
      </p>
      <label className="block text-sm">
        <span className="block text-slate-600">What you need them for</span>
        <textarea
          data-testid="lease-purpose"
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
          rows={2}
          className="mt-1 w-full max-w-xl rounded border border-slate-300 px-2 py-1"
          placeholder="For example: to see what the postcode column really holds before I write the rule that locks it"
        />
      </label>
      <div className="flex flex-wrap gap-4">
        <label className="text-sm">
          <span className="block text-slate-600">For how many days (up to {locking.limits.days})</span>
          <input
            data-testid="lease-days"
            type="number"
            min={1}
            max={locking.limits.days}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="mt-1 w-24 rounded border border-slate-300 px-2 py-1"
          />
        </label>
        <label className="text-sm">
          <span className="block text-slate-600">How many rows (up to {locking.limits.rows})</span>
          <input
            data-testid="lease-rows"
            type="number"
            min={1}
            max={locking.limits.rows}
            value={rows}
            onChange={(e) => setRows(Number(e.target.value))}
            className="mt-1 w-24 rounded border border-slate-300 px-2 py-1"
          />
        </label>
      </div>
      <p className="text-xs text-slate-500">
        The rows you can read are chosen for you and are the same every time you look. Asking again later opens the same rows.
      </p>
      <button
        type="submit"
        data-testid="lease-submit"
        disabled={!purpose.trim() || request.isPending}
        className="btn btn-primary"
      >
        {request.isPending ? "Asking" : "Ask for access"}
      </button>
      {request.error && (
        <div role="alert" data-testid="lease-refused" className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-800">
          <p className="font-medium">Your request was not made:</p>
          <Reasons error={request.error} fallback="The request failed." />
        </div>
      )}
    </form>
  );
}

function YourAccess({ locking }: { locking: Locking }) {
  const revoke = useRevokeLease();
  const rowsLink = `/versions/${locking.version_id}/locked-rows`;

  if (locking.mode === "full") {
    return (
      <div data-testid="locked-access" className="rounded border border-teal-300 bg-teal-50 p-4 text-sm text-teal-900">
        <p className="font-medium">You answer for this data, so you can read every locked value.</p>
        <p className="mt-1">Each time you do, it is recorded.</p>
        <Link to={rowsLink} data-testid="read-locked-rows" className="mt-2 inline-block text-sky-700 underline">
          Read the rows
        </Link>
      </div>
    );
  }
  if (locking.mode === "sample" && locking.lease) {
    const lease = locking.lease;
    return (
      <div data-testid="locked-access" className="rounded border border-teal-300 bg-teal-50 p-4 text-sm text-teal-900">
        <p className="font-medium">
          You hold a lease: {lease.row_limit} row{lease.row_limit === 1 ? "" : "s"} of {locking.records}, until {when(lease.expires_at)}.
        </p>
        <p className="mt-1">
          Approved by {lease.decided_by_label ?? "an approver"}{lease.decided_at ? ` on ${when(lease.decided_at)}` : ""} for: {lease.purpose}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-4">
          <Link to={rowsLink} data-testid="read-locked-rows" className="text-sky-700 underline">
            Read your {lease.row_limit} row{lease.row_limit === 1 ? "" : "s"}
          </Link>
          <button
            type="button"
            data-testid="end-own-lease"
            disabled={revoke.isPending}
            onClick={() => revoke.mutate(lease.id)}
            className="btn btn-secondary btn-sm"
          >
            {revoke.isPending ? "Ending" : "End this lease now"}
          </button>
        </div>
        {revoke.error && (
          <div role="alert" className="mt-2 text-xs text-red-800">
            <Reasons error={revoke.error} fallback="The lease could not be ended." />
          </div>
        )}
      </div>
    );
  }
  if (locking.waiting) {
    return (
      <div data-testid="locked-access" className="rounded border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
        <p className="font-medium">Your request is waiting for an approver.</p>
        <p className="mt-1">
          You asked on {when(locking.waiting.requested_at)} for {locking.waiting.row_limit} row{locking.waiting.row_limit === 1 ? "" : "s"} for{" "}
          {locking.waiting.days} day{locking.waiting.days === 1 ? "" : "s"}: {locking.waiting.purpose}
        </p>
        <Link to="/locked-column-access" className="mt-2 inline-block text-sky-700 underline">
          See your requests
        </Link>
      </div>
    );
  }
  if (locking.may_ask) {
    return (
      <div data-testid="locked-access">
        <AskForAccess locking={locking} />
      </div>
    );
  }
  return (
    <p data-testid="locked-access" className="rounded border border-slate-200 bg-white p-4 text-sm text-slate-700">
      Your role cannot read the locked values and cannot ask for them. The values are not needed to work with the readable columns.
    </p>
  );
}

function DecideGroupSize({ versionId }: { versionId: string }) {
  const decide = useDecideGroupSize(versionId);
  const [decision, setDecision] = useState<"accepted" | "will_change">("will_change");
  const [note, setNote] = useState("");
  return (
    <form
      data-testid="group-size-decide"
      className="mt-3 space-y-2 rounded border border-slate-200 bg-white p-3 text-sm"
      onSubmit={(e) => {
        e.preventDefault();
        decide.mutate({ decision, note });
      }}
    >
      <p className="font-medium text-slate-800">What will you do about it?</p>
      <label className="block">
        <input type="radio" checked={decision === "will_change"} onChange={() => setDecision("will_change")} className="mr-2" />
        Change the contract before the next table is sealed
      </label>
      <label className="block">
        <input type="radio" checked={decision === "accepted"} onChange={() => setDecision("accepted")} className="mr-2" />
        Accept the risk for this table
      </label>
      <label className="block">
        <span className="block text-slate-600">Why. This is recorded with your name.</span>
        <textarea
          data-testid="group-size-note"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={2}
          className="mt-1 w-full max-w-xl rounded border border-slate-300 px-2 py-1"
        />
      </label>
      <button
        type="submit"
        data-testid="group-size-submit"
        disabled={!note.trim() || decide.isPending}
        className="btn btn-primary"
      >
        {decide.isPending ? "File" : "Record this decision"}
      </button>
      {decide.error && (
        <div role="alert" className="text-xs text-red-800">
          <Reasons error={decide.error} fallback="The decision could not be recorded." />
        </div>
      )}
    </form>
  );
}

function GroupSizes({ versionId, mayDecide }: { versionId: string; mayDecide: boolean }) {
  const report = useGroupSizeReport(versionId);
  // A person who may not read the report is told nothing about it. The table is not hidden from them for that.
  if (report.isLoading || report.error || !report.data) return null;
  const r: GroupSizeReport = report.data;
  const nothingToCount = r.columns.length === 0;
  return (
    <div data-testid="group-sizes" className="mt-6">
      <h3 className="text-base font-semibold text-slate-900">Small groups</h3>
      <p className="mt-1 text-sm text-slate-600">
        A column such as age or postcode names nobody, but a rare combination of them can point at one person. This counts how many different
        people share each combination of the readable ones. It is advice, it never stopped the table being sealed, and it shows no values.
      </p>
      {nothingToCount ? (
        <p className="mt-2 text-sm text-slate-700">Every column that could identify someone in combination is locked, so there was nothing to count.</p>
      ) : r.people_in_small_groups === 0 ? (
        <p className="mt-2 rounded border border-teal-300 bg-teal-50 p-3 text-sm text-teal-900">
          No combination of {r.columns.join(" and ")} holds fewer than {r.threshold} people. The smallest holds {r.smallest_group}.
        </p>
      ) : (
        <div className="mt-2 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <p className="font-medium" data-testid="group-size-finding">
            {r.people_in_small_groups} of {r.people} people sit in a group smaller than {r.threshold}. The smallest group holds {r.smallest_group}{" "}
            {r.smallest_group === 1 ? "person" : "people"}.
          </p>
          <p className="mt-1">Counted over {r.columns.join(" and ")}, which are readable.</p>
          {r.advice.length > 0 && (
            <ul className="mt-2 list-disc pl-5" data-testid="group-size-advice">
              {r.advice.map((a) => (
                <li key={a.lock}>
                  Locking <strong>{a.lock}</strong> would leave {a.people_in_small_groups_after} people in a small group
                  {a.people_in_small_groups_after === 0 ? ", which is nobody" : ""}.
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {r.decision ? (
        <p data-testid="group-size-decided" className="mt-3 rounded border border-slate-200 bg-white p-3 text-sm">
          Decided by {r.decided_by_label ?? "an approver"} on {when(r.decided_at)}:{" "}
          <strong>{r.decision === "accepted" ? "the risk is accepted for this table" : "the contract will change before the next table"}</strong>.{" "}
          {r.decision_note}
        </p>
      ) : (
        mayDecide && r.people_in_small_groups > 0 && <DecideGroupSize versionId={versionId} />
      )}
    </div>
  );
}

/** What this person may do about the locked values of a table: read them, hold a permission, wait for one, or ask. Shown with the rest of the person's access on the version's page. */
export function LockedValuesAccess({ versionId }: { versionId: string }) {
  const locking = useLocking(versionId);
  if (locking.isLoading) return null;
  if (locking.error instanceof ApiError && locking.error.status === 404) return null;
  if (locking.error) return <Failure error={locking.error} what="what you may do about the locked values" />;
  return <YourAccess locking={locking.data!} />;
}

export function LockedColumnsSection({ versionId }: { versionId: string }) {
  const locking = useLocking(versionId);
  // Most tables were not sealed under a privacy contract and have no locked columns: nothing to show, and nothing to explain.
  if (locking.isLoading) return null;
  if (locking.error instanceof ApiError && locking.error.status === 404) return null;
  if (locking.error) return <Failure error={locking.error} what="what is locked in this table" />;
  const l = locking.data!;

  return (
    <Section
      title="Locked columns"
      description="Each person's identifying values in this table are scrambled under a key made for that person. Destroying that key later makes them unreadable for good, and the sealed table never changes."
    >
      <div className="data-card"><table className="data-table" data-testid="locked-columns">
        <thead>
          <tr>
            <th>Column</th>
            <th>What it is</th>
            <th>What was done to it</th>
          </tr>
        </thead>
        <tbody>
          {l.columns.map((c) => (
            <tr key={c.name} className="border-b border-slate-100">
              <td className="font-mono text-xs">{c.name}</td>
              <td>{CLASSIFICATION_WORDS[c.classification]}</td>
              <td>
                <span
                  className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                    c.treatment === "readable" ? "bg-slate-100 text-slate-800" : "bg-indigo-100 text-indigo-900"
                  }`}
                >
                  {TREATMENT_WORDS[c.treatment]}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>
      <p className="mt-2 text-xs text-slate-500">
        {l.records} row{l.records === 1 ? "" : "s"}. A person code is a label that stands for one person and means nothing on its own.
      </p>

      <GroupSizes versionId={versionId} mayDecide={l.mode === "full"} />
    </Section>
  );
}
