/**
 * Access rules: what people in this organisation may ask for, and the limits on each request.
 *
 * Anybody signed in may read them, because people should know what they may ask for before they ask. Nothing on the page changes a rule. Below the rules a person can
 * try a request of a given size and see whether it would be accepted and, if not, which rule says no. The answer is the one a real request gets.
 */

import { useState } from "react";
import { useAccessRules, useWhatWouldHappen, type AccessRule, type AccessRules as Rules } from "../../api/accessRules";
import { Failure, Loading, Section } from "../../components/states";
import { Link } from "react-router-dom";
import { byOn } from "../../lib/when";
import { Reasons } from "../locking/LockedColumns";

function shown(rule: AccessRule, value: number | string[], words: Record<string, string>): string {
  if (rule.kind === "number") return `${value} ${value === 1 ? (rule.unit ?? "").replace(/s$/, "") : rule.unit}`;
  const roles = value as string[];
  return roles.length === 0 ? "Nobody" : roles.map((r) => words[r] ?? r).join(", ");
}

function RuleRow({ rule, words }: { rule: AccessRule; words: Record<string, string> }) {
  return (
    <tr data-testid={`rule-${rule.key}`}>
      <td>
        <span className="font-medium">{rule.label}</span>
        <div className="text-xs text-slate-600">{rule.about}</div>
      </td>
      <td data-testid={`rule-${rule.key}-value`}>{shown(rule, rule.value, words)}</td>
      <td className="text-slate-700">{shown(rule, rule.default, words)}</td>
      <td className="text-slate-700">{rule.kind === "number" ? shown(rule, rule.maximum!, words) : rule.allowed.map((r) => words[r] ?? r).join(", ")}</td>
      <td data-testid={`rule-${rule.key}-who`} className="text-xs text-slate-700">
        {rule.source === "organisation" ? (
          <>
            <div>{byOn(rule.set_by, rule.set_at)}</div>
            <div>{rule.reason}</div>
          </>
        ) : (
          "Not changed"
        )}
      </td>
    </tr>
  );
}

function Tester({ data }: { data: Rules }) {
  const ask = useWhatWouldHappen();
  const [capability, setCapability] = useState("locked_rows");
  const [role, setRole] = useState(data.roles[0]);
  const [size, setSize] = useState(10);
  const [length, setLength] = useState(1);
  const rows = capability === "locked_rows";
  return (
    <Section level="section" title="What would happen" description="Try a request of a given size to see whether the rules above would accept it. Nothing is sent to an approver.">
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
        <button type="submit" data-testid="test-run" disabled={ask.isPending} className="rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">
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

export function AccessRules() {
  const rules = useAccessRules();
  if (rules.isLoading) return <Loading what="the access rules" />;
  if (rules.error) return <Failure error={rules.error} what="the access rules" />;
  const data = rules.data!;
  const groups = [...new Set(data.rules.map((r) => r.group))];
  return (
    <Section
      level="page"
      title="Access rules"
      description="How much people in this organisation may ask for, and for how long, when they need to ask. An approver of the department that owns the data still decides each request. To change a rule, ask your platform administrator."
    >
      <p className="mb-4 text-sm text-slate-600" data-testid="rules-to-roles">
        What each role may read without asking, and which roles may approve, is the same in every organisation and is on the <Link to="/roles" className="text-sky-700 underline">What each role can do</Link> page.
      </p>
      {groups.map((group) => (
        <Section key={group} level="section" title={group}>
          <div className="data-card">
            <table className="data-table w-full table-fixed" data-testid={`rules-${group}`}>
              <colgroup>
                <col style={{ width: "34%" }} />
                <col style={{ width: "19%" }} />
                <col style={{ width: "17%" }} />
                <col style={{ width: "17%" }} />
                <col style={{ width: "13%" }} />
              </colgroup>
              <thead className="whitespace-normal">
                <tr>
                  <th>Rule</th>
                  <th>In force now</th>
                  <th>Default</th>
                  <th>Platform maximum</th>
                  <th>Last change</th>
                </tr>
              </thead>
              <tbody>
                {data.rules
                  .filter((r) => r.group === group)
                  .map((r) => (
                    <RuleRow key={r.key} rule={r} words={data.role_words} />
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
