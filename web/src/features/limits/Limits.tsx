/**
 * Limits: what people in this organisation may ask for, and the limits on each request, and how much may be brought in as tables.
 *
 * Anybody signed in may read them, because people should know what they may ask for before they ask. Nothing on the page changes a limit. Below the limits a person can
 * try a request of a given size and see whether it would be accepted and, if not, which limit says no. The answer is the one a real request gets.
 */

import { useState } from "react";
import { useLimits, useWhatWouldHappen, type Limit, type LimitsResponse } from "../../api/limits";
import { Failure, Loading, Section } from "../../components/states";
import { Link } from "react-router-dom";
import { byOn } from "../../lib/when";
import { Reasons } from "../locking/LockedColumns";

function shown(limit: Limit, value: number | string[], words: Record<string, string>): string {
  if (limit.kind === "number") return `${value} ${value === 1 ? (limit.unit ?? "").replace(/s$/, "") : limit.unit}`;
  const roles = value as string[];
  return roles.length === 0 ? "Nobody" : roles.map((r) => words[r] ?? r).join(", ");
}

function LimitRow({ limit, words }: { limit: Limit; words: Record<string, string> }) {
  return (
    <tr data-testid={`limit-${limit.key}`}>
      <td>
        <span className="font-medium">{limit.label}</span>
        <div className="text-xs text-slate-600">{limit.about}</div>
      </td>
      <td data-testid={`limit-${limit.key}-value`}>{shown(limit, limit.value, words)}</td>
      <td className="text-slate-700">{shown(limit, limit.default, words)}</td>
      <td className="text-slate-700">{limit.kind === "number" ? shown(limit, limit.maximum!, words) : limit.allowed.map((r) => words[r] ?? r).join(", ")}</td>
      <td data-testid={`limit-${limit.key}-who`} className="text-xs text-slate-700">
        {limit.source === "organisation" ? (
          <>
            <div>{byOn(limit.set_by, limit.set_at)}</div>
            <div>{limit.reason}</div>
          </>
        ) : (
          "Not changed"
        )}
      </td>
    </tr>
  );
}

function Tester({ data }: { data: LimitsResponse }) {
  const ask = useWhatWouldHappen();
  const [capability, setCapability] = useState("locked_rows");
  const [role, setRole] = useState(data.roles[0]);
  const [size, setSize] = useState(10);
  const [length, setLength] = useState(1);
  const rows = capability === "locked_rows";
  return (
    <Section level="section" title="What would happen" description="Try a request of a given size to see whether the limits above would accept it. Nothing is sent to an approver.">
      <form
        className="flex flex-wrap items-end gap-3 text-sm"
        onSubmit={(e) => {
          e.preventDefault();
          ask.mutate({ capability, role, size, length });
        }}
      >
        <label className="flex flex-col gap-1">
          What the person asks for
          <select data-testid="test-capability" value={capability} onChange={(e) => setCapability(e.target.value)} className="rounded border border-slate-400 px-2 py-1">
            {Object.entries(data.capabilities).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Role of the person asking
          <select data-testid="test-role" value={role} onChange={(e) => setRole(e.target.value)} className="rounded border border-slate-400 px-2 py-1">
            {data.roles.map((r) => (
              <option key={r} value={r}>
                {data.role_words[r] ?? r}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          {rows ? "How many rows" : "How many files"}
          <input data-testid="test-size" type="number" min={0} value={size} onChange={(e) => setSize(Number(e.target.value))} className="w-24 rounded border border-slate-400 px-2 py-1" />
        </label>
        <label className="flex flex-col gap-1">
          {rows ? "For how many days" : "For how many hours"}
          <input data-testid="test-length" type="number" min={0} value={length} onChange={(e) => setLength(Number(e.target.value))} className="w-24 rounded border border-slate-400 px-2 py-1" />
        </label>
        <button type="submit" data-testid="test-run" disabled={ask.isPending} className="btn btn-primary">
          {ask.isPending ? "Asking" : "Would it be accepted?"}
        </button>
      </form>
      {ask.data && (
        <div
          data-testid="test-answer"
          role="status"
          className={`mt-3 max-w-xl rounded border p-3 text-sm ${ask.data.allowed ? "border-teal-300 bg-teal-50 text-teal-900" : "border-amber-300 bg-amber-50 text-amber-900"}`}
        >
          {ask.data.allowed ? (
            <p className="font-medium">It would be accepted.</p>
          ) : (
            <>
              <p className="font-medium">It would be refused:</p>
              <ul className="mt-1 list-disc pl-5">
                {ask.data.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      {ask.error ? (
        <div role="alert" className="mt-2 text-sm text-red-800">
          <Reasons error={ask.error} fallback="That could not be tried." />
        </div>
      ) : null}
    </Section>
  );
}

export function Limits() {
  const result = useLimits();
  if (result.isLoading) return <Loading what="the limits" />;
  if (result.error) return <Failure error={result.error} what="the limits" />;
  const data = result.data!;
  const groups = [...new Set(data.limits.map((r) => r.group))];
  return (
    <Section
      level="page"
      title="Limits"
      description="The limits in this organisation: how much people may ask for and for how long, and how much may be brought in as tables. An approver of the department that owns the data still decides each request. To change a limit, ask your platform administrator."
    >
      <p className="mb-4 text-sm text-slate-600" data-testid="limits-to-roles">
        What each role may read without asking, and which roles may approve, is the same in every organisation and is on the <Link to="/roles" className="text-sky-700 underline">What each role can do</Link> page.
      </p>
      {groups.map((group) => (
        <Section key={group} level="section" title={group}>
          <div className="data-card">
            <table className="data-table w-full table-fixed" data-testid={`limits-${group}`}>
              <colgroup>
                <col style={{ width: "34%" }} />
                <col style={{ width: "19%" }} />
                <col style={{ width: "17%" }} />
                <col style={{ width: "17%" }} />
                <col style={{ width: "13%" }} />
              </colgroup>
              <thead className="whitespace-normal">
                <tr>
                  <th>Limit</th>
                  <th>In force now</th>
                  <th>Default</th>
                  <th>Platform maximum</th>
                  <th>Last change</th>
                </tr>
              </thead>
              <tbody>
                {data.limits
                  .filter((r) => r.group === group)
                  .map((r) => (
                    <LimitRow key={r.key} limit={r} words={data.role_words} />
                  ))}
              </tbody>
            </table>
          </div>
        </Section>
      ))}
      <Tester data={data} />
    </Section>
  );
}
