/**
 * Release bars: how much identifying detail a de-identified result may still hold, for each purpose it is released for.
 *
 * Anybody signed in may read them, so a reviewer can choose one and everybody can see what a result was held to. Only the data protection officer sets a bar, and the platform refuses
 * the rest. A change is a new version: the earlier ones stay, each with who made it, when and why, and a decision keeps the version it was judged by.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useBarVersions, useChangeBar, useCreateBar, useReleaseBars, type Bars, type Kind, type ReleaseBar } from "../../api/releaseBars";
import { Failure, Loading, Section } from "../../components/states";
import { notify } from "../../components/toast";
import { byOn } from "../../lib/when";
import { sentence } from "../../lib/text";
import { Reasons } from "../locking/LockedColumns";

/** A share as a person would say it: 0.98 is "98%". */
export function share(value: number | null | undefined): string {
  return value === null || value === undefined ? "No limit" : `${Number((value * 100).toFixed(2))}%`;
}

function leaks(value: number | null | undefined): string {
  return value === null || value === undefined ? "No limit" : value === 0 ? "None allowed" : String(value);
}

/** True when a bar puts no limit on anything, so every measured result passes it. */
export function limitsNothing(bars: Bars): boolean {
  return bars.overall_recall === null && Object.values(bars.types).every((t) => t.recall === null && t.max_leaks === null);
}

export function BarLimits({ bars, kinds }: { bars: Bars; kinds: Kind[] }) {
  const rows = kinds.filter((k) => bars.types[k.code] && (bars.types[k.code].recall !== null || bars.types[k.code].max_leaks !== null));
  return (
    <div>
      <p className="text-sm" data-testid="bar-overall">
        <span className="text-slate-500">Identifying detail removed, over all kinds together: </span>
        <span className="font-medium">{share(bars.overall_recall)}</span>
      </p>
      {rows.length > 0 ? (
        <div className="data-card mt-2">
          <table className="data-table w-full table-fixed">
            <colgroup>
              <col className="w-1/2" />
              <col className="w-1/4" />
              <col className="w-1/4" />
            </colgroup>
            <thead>
              <tr>
                <th>Kind of identifier</th>
                <th>Removed, at least</th>
                <th>Left in the clear, at most</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((k) => (
                <tr key={k.code} data-testid={`bar-kind-${k.code}`}>
                  <td>{k.label}</td>
                  <td>{share(bars.types[k.code].recall)}</td>
                  <td>{leaks(bars.types[k.code].max_leaks)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-1 text-sm text-slate-600">No kind of identifier has a limit of its own.</p>
      )}
      {limitsNothing(bars) && (
        <p className="mt-2 text-sm text-amber-800" data-testid="bar-limits-nothing">
          This bar puts no limit on anything, so every measured result passes it.
        </p>
      )}
    </div>
  );
}

/** What a person types for one kind: percent for the recall, a whole number for the most left in the clear. Empty means no limit. */
type Draft = { overall: string; types: Record<string, { recall: string; most: string }> };

function draftOf(bars: Bars): Draft {
  return {
    overall: bars.overall_recall === null ? "" : String(Number((bars.overall_recall * 100).toFixed(2))),
    types: Object.fromEntries(
      Object.entries(bars.types).map(([code, t]) => [code, { recall: t.recall === null ? "" : String(Number((t.recall * 100).toFixed(2))), most: t.max_leaks === null ? "" : String(t.max_leaks) }]),
    ),
  };
}

function barsOf(draft: Draft): { bars: Bars } | { problem: string } {
  const percent = (text: string): number | null | "bad" => {
    if (!text.trim()) return null;
    const n = Number(text);
    return Number.isFinite(n) && n >= 0 && n <= 100 ? n / 100 : "bad";
  };
  const overall = percent(draft.overall);
  if (overall === "bad") return { problem: "The overall figure must be a number from 0 to 100, or empty." };
  const types: Bars["types"] = {};
  for (const [code, t] of Object.entries(draft.types)) {
    const recall = percent(t.recall);
    const most = t.most.trim() === "" ? null : Number(t.most);
    if (recall === "bad") return { problem: "Each least-removed figure must be a number from 0 to 100, or empty." };
    if (most !== null && (!Number.isInteger(most) || most < 0)) return { problem: "Each most-left-in-the-clear figure must be a whole number, zero or more, or empty." };
    if (recall !== null || most !== null) types[code] = { recall, max_leaks: most };
  }
  return { bars: { overall_recall: overall, types } };
}

function LimitsEditor({ kinds, draft, onChange }: { kinds: Kind[]; draft: Draft; onChange: (d: Draft) => void }) {
  const groups = [...new Set(kinds.map((k) => k.group))];
  const set = (code: string, field: "recall" | "most", value: string) =>
    onChange({ ...draft, types: { ...draft.types, [code]: { recall: draft.types[code]?.recall ?? "", most: draft.types[code]?.most ?? "", [field]: value } } });
  return (
    <div className="space-y-4 text-sm">
      <label className="flex flex-col gap-1">
        Identifying detail removed, over all kinds together (percent)
        <input data-testid="limit-overall" inputMode="decimal" value={draft.overall} onChange={(e) => onChange({ ...draft, overall: e.target.value })} className="w-32 rounded border border-slate-400 px-2 py-1" />
      </label>
      {groups.map((group) => (
        <div key={group}>
          <p className="mb-1 font-medium text-slate-800">Kinds that {group}</p>
          <div className="data-card">
            <table className="data-table w-full table-fixed">
              <colgroup>
                <col className="w-1/2" />
                <col className="w-1/4" />
                <col className="w-1/4" />
              </colgroup>
              <thead>
                <tr>
                  <th>Kind of identifier</th>
                  <th>Removed, at least (percent)</th>
                  <th>Left in the clear, at most</th>
                </tr>
              </thead>
              <tbody>
                {kinds
                  .filter((k) => k.group === group)
                  .map((k) => (
                    <tr key={k.code}>
                      <td>{k.label}</td>
                      <td>
                        <input aria-label={`${k.label}: least removed`} data-testid={`limit-${k.code}-recall`} inputMode="decimal" value={draft.types[k.code]?.recall ?? ""} onChange={(e) => set(k.code, "recall", e.target.value)} className="w-24 rounded border border-slate-400 px-2 py-1" />
                      </td>
                      <td>
                        <input aria-label={`${k.label}: most left in the clear`} data-testid={`limit-${k.code}-most`} inputMode="numeric" value={draft.types[k.code]?.most ?? ""} onChange={(e) => set(k.code, "most", e.target.value)} className="w-24 rounded border border-slate-400 px-2 py-1" />
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
      <p className="text-slate-600">Leave a figure empty to put no limit on it.</p>
    </div>
  );
}

/** A new bar starts from the platform's own values, so that making one never quietly drops a limit the built-in bar has. */
function NewBar({ kinds, start, onDone }: { kinds: Kind[]; start: Bars; onDone: () => void }) {
  const create = useCreateBar();
  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [draft, setDraft] = useState<Draft>(draftOf(start));
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <form
      className="space-y-4 rounded border border-slate-200 p-4"
      data-testid="new-bar"
      onSubmit={(e) => {
        e.preventDefault();
        const made = barsOf(draft);
        if ("problem" in made) return setProblem(made.problem);
        setProblem(null);
        create.mutate({ name, bars: made.bars, reason }, { onSuccess: () => { notify("Release bar created."); onDone(); } });
      }}
    >
      <label className="flex max-w-xl flex-col gap-1 text-sm">
        What is it called? Name the audience a result is released to.
        <input data-testid="new-bar-name" value={name} onChange={(e) => setName(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      <LimitsEditor kinds={kinds} draft={draft} onChange={setDraft} />
      <label className="flex max-w-xl flex-col gap-1 text-sm">
        Why is it set this way?
        <textarea data-testid="new-bar-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      {problem && <p role="alert" className="text-sm text-red-800">{problem}</p>}
      {create.error ? <div role="alert" className="text-sm text-red-800"><Reasons error={create.error} fallback="The release bar could not be created." /></div> : null}
      <div className="flex gap-3">
        <button type="submit" data-testid="new-bar-save" disabled={create.isPending || !name.trim() || !reason.trim()} className="rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300">
          {create.isPending ? "Creating" : "Create the release bar"}
        </button>
        <button type="button" onClick={onDone} className="rounded border border-slate-400 px-3 py-1.5 text-sm">
          Cancel
        </button>
      </div>
    </form>
  );
}

function ChangeBar({ bar, kinds, onDone }: { bar: ReleaseBar; kinds: Kind[]; onDone: () => void }) {
  const change = useChangeBar(bar.id!);
  const [reason, setReason] = useState("");
  const [draft, setDraft] = useState<Draft>(draftOf(bar.bars));
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <form
      className="mt-3 space-y-4 rounded border border-slate-200 p-4"
      data-testid="change-bar"
      onSubmit={(e) => {
        e.preventDefault();
        const made = barsOf(draft);
        if ("problem" in made) return setProblem(made.problem);
        setProblem(null);
        change.mutate({ bars: made.bars, reason }, { onSuccess: () => { notify("New version saved."); onDone(); } });
      }}
    >
      <p className="text-sm text-slate-700">Saving adds a new version. Earlier versions stay as they are, and a decision already made keeps the version it was judged by.</p>
      <LimitsEditor kinds={kinds} draft={draft} onChange={setDraft} />
      <label className="flex max-w-xl flex-col gap-1 text-sm">
        Why is it changed?
        <textarea data-testid="change-bar-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      {problem && <p role="alert" className="text-sm text-red-800">{problem}</p>}
      {change.error ? <div role="alert" className="text-sm text-red-800"><Reasons error={change.error} fallback="The release bar could not be changed." /></div> : null}
      <div className="flex gap-3">
        <button type="submit" data-testid="change-bar-save" disabled={change.isPending || !reason.trim()} className="rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300">
          {change.isPending ? "Saving" : "Save as a new version"}
        </button>
        <button type="button" onClick={onDone} className="rounded border border-slate-400 px-3 py-1.5 text-sm">
          Cancel
        </button>
      </div>
    </form>
  );
}

function History({ bar, kinds }: { bar: ReleaseBar; kinds: Kind[] }) {
  const history = useBarVersions(bar.id!);
  if (history.isLoading) return <Loading what="the versions" />;
  if (history.error) return <Failure error={history.error} what="the versions" />;
  return (
    <ol className="mt-3 space-y-3" data-testid="bar-history">
      {history.data!.versions.map((v) => (
        <li key={v.version} className="rounded border border-slate-200 p-3" data-testid={`bar-version-${v.version}`}>
          <p className="text-sm font-medium">
            Version {v.version} <span className="font-normal text-slate-600">{byOn(v.set_by_label ?? v.set_by, v.set_at)}</span>
          </p>
          <p className="text-sm text-slate-700">
            <span className="text-slate-500">Reason: </span>
            {sentence(v.reason)}
          </p>
          <div className="mt-2">
            <BarLimits bars={v.bars} kinds={kinds} />
          </div>
        </li>
      ))}
    </ol>
  );
}

function BarCard({ bar, kinds, maySet }: { bar: ReleaseBar; kinds: Kind[]; maySet: boolean }) {
  const [editing, setEditing] = useState(false);
  const [showing, setShowing] = useState(false);
  return (
    <div className="rounded border border-slate-200 p-4" data-testid={`release-bar-${bar.name}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-base font-semibold text-slate-900">{bar.name}</h3>
        {bar.built_in ? (
          <span className="text-sm text-slate-600">The platform&rsquo;s own values. Nobody sets them for an organisation.</span>
        ) : (
          <span className="text-sm text-slate-600" data-testid="bar-set">
            Version {bar.version} {byOn(bar.set_by_label ?? bar.set_by, bar.set_at)}
          </span>
        )}
      </div>
      {!bar.built_in && bar.reason && (
        <p className="text-sm text-slate-700">
          <span className="text-slate-500">Reason: </span>
          {sentence(bar.reason)}
        </p>
      )}
      <div className="mt-2">
        <BarLimits bars={bar.bars} kinds={kinds} />
      </div>
      {!bar.built_in && (
        <div className="mt-3 flex gap-3 text-sm">
          {maySet && !editing && (
            <button type="button" data-testid="change-bar-open" onClick={() => setEditing(true)} className="rounded border border-slate-400 px-3 py-1">
              Change
            </button>
          )}
          {bar.versions > 1 && (
            <button type="button" data-testid="bar-history-toggle" onClick={() => setShowing(!showing)} className="text-sky-700 underline">
              {showing ? "Hide the earlier versions" : `Show all ${bar.versions} versions`}
            </button>
          )}
        </div>
      )}
      {editing && <ChangeBar bar={bar} kinds={kinds} onDone={() => setEditing(false)} />}
      {showing && <History bar={bar} kinds={kinds} />}
    </div>
  );
}

export function ReleaseBars() {
  const data = useReleaseBars();
  const [creating, setCreating] = useState(false);
  if (data.isLoading) return <Loading what="the release bars" />;
  if (data.error) return <Failure error={data.error} what="the release bars" />;
  const d = data.data!;
  return (
    <Section
      level="page"
      title="Release bars"
      description="How much identifying detail a de-identified result may still hold, for the audience it is released to. The person who releases a result chooses a bar, and the platform checks the result against it at that moment. An identifier is a detail that points to a person, such as a name or a phone number."
    >
      <p className="mb-4 text-sm text-slate-600" data-testid="bars-lead">
        The decision to release a result is on the <Link to="/gates" className="text-sky-700 underline">De-identification results</Link> page.
      </p>

      <Section level="section" title="Your organisation&rsquo;s bars" description="Each is named for the audience it is for. A change adds a version, and the earlier ones stay.">
        <div className="space-y-4">
          {d.release_bars.length === 0 && (
            <p className="text-sm text-slate-700" data-testid="bars-none">
              This organisation has not set a release bar yet, so every result is judged by the Platform default bar below.
            </p>
          )}
          {d.release_bars.map((b) => (
            <BarCard key={b.id} bar={b} kinds={d.kinds} maySet={d.may_set} />
          ))}
        </div>
        {d.may_set ? (
          creating ? (
            <div className="mt-4">
              <NewBar kinds={d.kinds} start={d.platform_default.bars} onDone={() => setCreating(false)} />
            </div>
          ) : (
            <button type="button" data-testid="new-bar-open" onClick={() => setCreating(true)} className="mt-4 rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white">
              Create a release bar
            </button>
          )
        ) : (
          <p className="mt-4 text-sm text-slate-600" data-testid="bars-cannot-set">
            {d.may_set_reasons.length ? sentence(d.may_set_reasons[0]) : "You may not set a release bar."}
          </p>
        )}
      </Section>

      <Section level="section" title="The bar the platform starts with" description="Used when no other bar is chosen, and for the recommendation a run records when it finishes. It is a starting point to argue with, not a standard.">
        <BarCard bar={d.platform_default} kinds={d.kinds} maySet={false} />
      </Section>
    </Section>
  );
}
