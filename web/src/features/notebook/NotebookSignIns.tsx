/**
 * Your notebooks: every notebook that has signed in as you, when, from where, until when, when it was last used, and the buttons to end one or all of them.
 *
 * Ending a notebook stops it at its next request, so a notebook that has been used in the last few minutes may be in the middle of work. The page says which, and
 * asks first. One that has not been used lately is ended at once. The data protection officer and the privacy auditor may switch to everybody's in the
 * organisation. Nobody else sees another person's.
 */

import { useState } from "react";
import { useHeaderSearch } from "../../components/HeaderSearch";
import { useIdentity } from "../../identity/IdentityContext";
import { useNotebookSignIns, useRevokeAllNotebookSignIns, useRevokeNotebookSignIn, type NotebookSignIn } from "../../api/notebookSignIn";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { when } from "../../lib/when";
import { Reasons } from "../locking/LockedColumns";
import { NotebookStart } from "./NotebookStart";

const STATE_WORDS: Record<NotebookSignIn["status"], string> = {
  approved: "In force",
  denied: "Refused",
  revoked: "Ended by the person",
  expired: "Not approved in time",
  pending: "Waiting",
};

function State({ s }: { s: NotebookSignIn }) {
  const over = s.status === "approved" && !s.in_force;
  const word = over ? "Ran out" : STATE_WORDS[s.status];
  const tone = s.in_force ? "bg-teal-100 text-teal-900" : "bg-slate-200 text-slate-800";
  return <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${tone}`}>{word}</span>;
}

/** The question before ending something that may be in the middle of work. */
function Confirm({
  testid,
  what,
  busy,
  error,
  onYes,
  onNo,
}: {
  testid: string;
  what: NotebookSignIn[];
  busy: boolean;
  error: unknown;
  onYes: () => void;
  onNo: () => void;
}) {
  const inUse = what.filter((s) => s.in_use);
  const minutes = what[0]?.in_use_minutes ?? 10;
  return (
    <div data-testid={testid} role="alertdialog" className="mt-2 max-w-xl rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
      <p className="font-medium">
        {what.length === 1 ? "End this notebook's sign-in?" : `End ${what.length} notebook sign-ins?`}
      </p>
      {inUse.length > 0 && (
        <>
          <p className="mt-1">
            {inUse.length === 1 ? "This notebook was" : `${inUse.length} of these were`} used in the last {minutes} minutes and may be in the middle of work. It stops at its next request, and
            anything it was doing is cut off:
          </p>
          <ul className="mt-1 list-disc pl-5">
            {inUse.map((s) => (
              <li key={s.id} data-testid={`${testid}-in-use`}>
                {s.label}, last used {s.last_used_at ? when(s.last_used_at) : "just now"}
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="mt-1">The notebook can sign in again with a new code.</p>
      <div className="mt-2 flex gap-2">
        <button
          type="button"
          data-testid={`${testid}-yes`}
          disabled={busy}
          onClick={onYes}
          className="btn btn-danger btn-sm"
        >
          {busy ? "Ending" : what.length === 1 ? "Yes, end it" : "Yes, end them all"}
        </button>
        <button type="button" data-testid={`${testid}-no`} onClick={onNo} className="btn btn-secondary btn-sm">
          Keep {what.length === 1 ? "it" : "them"}
        </button>
      </div>
      {error ? (
        <div role="alert" className="mt-1 text-xs text-red-800">
          <Reasons error={error} fallback="That could not be ended." />
        </div>
      ) : null}
    </div>
  );
}

function End({ s }: { s: NotebookSignIn }) {
  const revoke = useRevokeNotebookSignIn();
  const [asking, setAsking] = useState(false);
  return (
    <div>
      <button
        type="button"
        data-testid={`notebook-revoke-${s.id}`}
        disabled={revoke.isPending}
        onClick={() => (s.in_use ? setAsking(true) : revoke.mutate(s.id))}
        className="btn btn-secondary btn-sm whitespace-nowrap"
      >
        {revoke.isPending ? "Ending" : "End now"}
      </button>
      {asking && (
        <Confirm
          testid={`notebook-confirm-${s.id}`}
          what={[s]}
          busy={revoke.isPending}
          error={revoke.error}
          onYes={() => revoke.mutate(s.id, { onSuccess: () => setAsking(false) })}
          onNo={() => setAsking(false)}
        />
      )}
      {!asking && revoke.error ? (
        <div role="alert" className="mt-1 text-xs text-red-800">
          <Reasons error={revoke.error} fallback="That could not be ended." />
        </div>
      ) : null}
    </div>
  );
}

function EndAll({ live }: { live: NotebookSignIn[] }) {
  const revokeAll = useRevokeAllNotebookSignIns();
  const [asking, setAsking] = useState(false);
  if (live.length === 0) return null;
  return (
    <div className="mb-4">
      <button
        type="button"
        data-testid="notebook-revoke-all"
        disabled={revokeAll.isPending}
        onClick={() => setAsking(true)}
        className="btn btn-secondary"
      >
        End all of my notebook sign-ins
      </button>
      <p className="mt-1 text-xs text-slate-600">For a lost laptop, a notebook you shared, or when you cannot tell which one to end.</p>
      {asking && (
        <Confirm
          testid="notebook-confirm-all"
          what={live}
          busy={revokeAll.isPending}
          error={revokeAll.error}
          onYes={() => revokeAll.mutate(undefined, { onSuccess: () => setAsking(false) })}
          onNo={() => setAsking(false)}
        />
      )}
    </div>
  );
}

export function NotebookSignIns() {
  const { principal } = useIdentity();
  const mayWatchAll = Boolean(principal?.roles.some((r) => r === "dpo" || r === "privacy_auditor"));
  const [everyone, setEveryone] = useState(false);
  const list = useNotebookSignIns(everyone && mayWatchAll);
  const [q, setQ] = useState("");
  useHeaderSearch({ value: q, onChange: setQ, placeholder: everyone && mayWatchAll ? "Search by notebook or person" : "Search by notebook name", testid: "notebook-search" });
  if (list.isLoading) return <Loading what="your notebooks" />;
  if (list.error) return <Failure error={list.error} what="your notebooks" />;
  const rows = list.data!.sign_ins;
  const live = rows.filter((s) => s.in_force);
  const words = q.trim().toLowerCase();
  const shown = words ? rows.filter((s) => `${s.label} ${everyone && mayWatchAll ? s.person : ""}`.toLowerCase().includes(words)) : rows;
  return (
    <Section
      level="page"
      title="Your notebooks"
      description="Each notebook that signed in as you. A notebook can find datasets, ask for access in your name, open the few files you were allowed in a notebook, and make a dataset from a query. It cannot decide anybody's request or change anything about roles or settings."
    >
      <NotebookStart />
      {mayWatchAll && (
        <label className="mb-3 flex items-center gap-2 text-sm text-slate-800">
          <input type="checkbox" data-testid="notebooks-everyone" checked={everyone} onChange={(e) => setEveryone(e.target.checked)} />
          Show everybody's in the organisation
        </label>
      )}
      {!everyone && <EndAll live={live} />}
      {rows.length === 0 ? (
        <Empty what="notebook sign-ins" hint="A notebook that signs in as you appears here. The steps above show how." />
      ) : (
        <div className="data-card">
          <table className="data-table" data-testid="notebook-sign-ins">
            <thead>
              <tr>
                <th>Notebook</th>
                {everyone && mayWatchAll && <th>Person</th>}
                <th>Started</th>
                <th>Until</th>
                <th>Last used</th>
                <th>State</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {shown.length === 0 && (
                <tr>
                  <td colSpan={7} className="text-center text-slate-500">
                    No notebook matches that search.
                  </td>
                </tr>
              )}
              {shown.map((s) => (
                <tr key={s.id} data-testid={`notebook-row-${s.id}`}>
                  <td>
                    <span className="font-medium">{s.label}</span>
                    <div className="text-xs text-slate-500">from {s.created_ip ?? "an address that was not recorded"}</div>
                  </td>
                  {everyone && mayWatchAll && <td>{s.person}</td>}
                  <td className="whitespace-nowrap">{when(s.created_at)}</td>
                  <td className="whitespace-nowrap">{s.expires_at ? when(s.expires_at) : "never approved"}</td>
                  <td>
                    {s.last_used_at ? (
                      <>
                        <span className="whitespace-nowrap">{when(s.last_used_at)}</span>
                        {s.in_use && <span className="ml-1 rounded bg-amber-100 px-1 text-xs text-amber-900">in use now</span>}
                        <div className="text-xs text-slate-500">from {s.last_ip}</div>
                        {s.address_changes > 0 && (
                          <div data-testid={`notebook-moved-${s.id}`} className="mt-1 text-xs font-medium text-amber-800">
                            It has been used from {s.address_changes === 1 ? "another address" : `${s.address_changes} other addresses`} since it started.
                          </div>
                        )}
                      </>
                    ) : (
                      "not used yet"
                    )}
                    {s.ended_at && <div className="text-xs text-slate-500">Ended {when(s.ended_at)}</div>}
                  </td>
                  <td>
                    <State s={s} />
                  </td>
                  <td>{s.in_force && !everyone ? <End s={s} /> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
