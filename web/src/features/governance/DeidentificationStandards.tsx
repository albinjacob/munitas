/**
 * De-identification standards: how much identifying detail a de-identified result may still hold, for each purpose it is released for.
 *
 * Anybody signed in may read them, so a reviewer can choose one and everybody can see what a result was held to. Only the data protection officer sets a standard, and the platform refuses
 * the rest. A change is a new version: the earlier ones stay, each with who made it, when and why, and a decision keeps the version it was judged by.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useStandardVersions, useChangeStandard, useCreateStandard, useStandards, type StandardLimits, type Kind, type Standard } from "../../api/standards";
import { Failure, Loading, Section } from "../../components/states";
import { notify } from "../../components/toast";
import { byOn } from "../../lib/when";
import { sentence } from "../../lib/text";
import { Reasons } from "../locking/LockedColumns";

/** A share as a person would say it: 0.98 is "98%". */
export function share(value: number | null | undefined): string {
  return value === null || value === undefined ? "No minimum" : `${Number((value * 100).toFixed(2))}%`;
}

function leaks(value: number | null | undefined): string {
  return value === null || value === undefined ? "No maximum" : value === 0 ? "None may remain" : String(value);
}

/** True when a standard puts no limit on anything, so every measured result passes it. */
export function limitsNothing(limits: StandardLimits): boolean {
  return limits.overall_recall === null && Object.values(limits.types).every((t) => t.recall === null && t.max_leaks === null);
}

export function StandardLimitsTable({ limits, kinds }: { limits: StandardLimits; kinds: Kind[] }) {
  const rows = kinds.filter((k) => limits.types[k.code] && (limits.types[k.code].recall !== null || limits.types[k.code].max_leaks !== null));
  return (
    <div>
      <p className="text-sm" data-testid="standard-overall">
        <span className="text-slate-500">Removed, at least, over all kinds together: </span>
        <span className="font-medium">{share(limits.overall_recall)}</span>
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
                <tr key={k.code} data-testid={`standard-kind-${k.code}`}>
                  <td>{k.label}</td>
                  <td>{share(limits.types[k.code].recall)}</td>
                  <td>{leaks(limits.types[k.code].max_leaks)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-1 text-sm text-slate-600">No kind of identifier has a limit of its own.</p>
      )}
      {limitsNothing(limits) && (
        <p className="mt-2 text-sm text-amber-800" data-testid="standard-limits-nothing">
          This standard sets no minimum and no maximum, so every measured result passes it.
        </p>
      )}
    </div>
  );
}

/** What a person types for one kind: percent for the recall, a whole number for the most left in the clear. Empty means no limit. */
type Draft = { overall: string; types: Record<string, { recall: string; most: string }> };

function draftOf(limits: StandardLimits): Draft {
  return {
    overall: limits.overall_recall === null ? "" : String(Number((limits.overall_recall * 100).toFixed(2))),
    types: Object.fromEntries(
      Object.entries(limits.types).map(([code, t]) => [code, { recall: t.recall === null ? "" : String(Number((t.recall * 100).toFixed(2))), most: t.max_leaks === null ? "" : String(t.max_leaks) }]),
    ),
  };
}

function limitsOf(draft: Draft): { limits: StandardLimits } | { problem: string } {
  const percent = (text: string): number | null | "bad" => {
    if (!text.trim()) return null;
    const n = Number(text);
    return Number.isFinite(n) && n >= 0 && n <= 100 ? n / 100 : "bad";
  };
  const overall = percent(draft.overall);
  if (overall === "bad") return { problem: "The overall figure must be a number from 0 to 100, or empty." };
  const types: StandardLimits["types"] = {};
  for (const [code, t] of Object.entries(draft.types)) {
    const recall = percent(t.recall);
    const most = t.most.trim() === "" ? null : Number(t.most);
    if (recall === "bad") return { problem: "Each least-removed figure must be a number from 0 to 100, or empty." };
    if (most !== null && (!Number.isInteger(most) || most < 0)) return { problem: "Each most-left-in-the-clear figure must be a whole number, zero or more, or empty." };
    if (recall !== null || most !== null) types[code] = { recall, max_leaks: most };
  }
  return { limits: { overall_recall: overall, types } };
}

function LimitsEditor({ kinds, draft, onChange }: { kinds: Kind[]; draft: Draft; onChange: (d: Draft) => void }) {
  const groups = [...new Set(kinds.map((k) => k.group))];
  const set = (code: string, field: "recall" | "most", value: string) =>
    onChange({ ...draft, types: { ...draft.types, [code]: { recall: draft.types[code]?.recall ?? "", most: draft.types[code]?.most ?? "", [field]: value } } });
  return (
    <div className="space-y-4 text-sm">
      <label className="flex flex-col gap-1">
        Removed, at least, over all kinds together (percent)
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
                  <th>Left in the clear, at most (number)</th>
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
      <p className="text-slate-600">Leave a figure empty if this standard should not ask for it. An empty removed figure means no minimum, and an empty left-in-the-clear figure means no maximum.</p>
    </div>
  );
}

/** A new standard starts from the platform's own values, so that making one never quietly drops a limit the built-in standard has. */
function NewStandard({ kinds, start, onDone }: { kinds: Kind[]; start: StandardLimits; onDone: () => void }) {
  const create = useCreateStandard();
  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [draft, setDraft] = useState<Draft>(draftOf(start));
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <form
      className="space-y-4 rounded border border-slate-200 p-4"
      data-testid="new-standard"
      onSubmit={(e) => {
        e.preventDefault();
        const made = limitsOf(draft);
        if ("problem" in made) return setProblem(made.problem);
        setProblem(null);
        create.mutate({ name, limits: made.limits, reason }, { onSuccess: () => { notify("Standard created."); onDone(); } });
      }}
    >
      <label className="flex max-w-xl flex-col gap-1 text-sm">
        Name the standard after the audience a result is released to.
        <input data-testid="new-standard-name" value={name} onChange={(e) => setName(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      <LimitsEditor kinds={kinds} draft={draft} onChange={setDraft} />
      <label className="flex max-w-xl flex-col gap-1 text-sm">
        Why is it set this way?
        <textarea data-testid="new-standard-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      {problem && <p role="alert" className="text-sm text-red-800">{problem}</p>}
      {create.error ? <div role="alert" className="text-sm text-red-800"><Reasons error={create.error} fallback="The standard could not be created." /></div> : null}
      <div className="flex gap-3">
        <button type="submit" data-testid="new-standard-save" disabled={create.isPending || !name.trim() || !reason.trim()} className="btn btn-primary">
          {create.isPending ? "Creating" : "Create the standard"}
        </button>
        <button type="button" onClick={onDone} className="btn btn-secondary">
          Cancel
        </button>
      </div>
    </form>
  );
}

function ChangeStandard({ standard, kinds, onDone }: { standard: Standard; kinds: Kind[]; onDone: () => void }) {
  const change = useChangeStandard(standard.id!);
  const [reason, setReason] = useState("");
  const [draft, setDraft] = useState<Draft>(draftOf(standard.limits));
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <form
      className="mt-3 space-y-4 rounded border border-slate-200 p-4"
      data-testid="change-standard"
      onSubmit={(e) => {
        e.preventDefault();
        const made = limitsOf(draft);
        if ("problem" in made) return setProblem(made.problem);
        setProblem(null);
        change.mutate({ limits: made.limits, reason }, { onSuccess: () => { notify("New version saved."); onDone(); } });
      }}
    >
      <p className="text-sm text-slate-700">Saving adds a new version. Earlier versions stay as they are, and a decision already made keeps the version it was judged by.</p>
      <LimitsEditor kinds={kinds} draft={draft} onChange={setDraft} />
      <label className="flex max-w-xl flex-col gap-1 text-sm">
        Why is it changed?
        <textarea data-testid="change-standard-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      {problem && <p role="alert" className="text-sm text-red-800">{problem}</p>}
      {change.error ? <div role="alert" className="text-sm text-red-800"><Reasons error={change.error} fallback="The standard could not be changed." /></div> : null}
      <div className="flex gap-3">
        <button type="submit" data-testid="change-standard-save" disabled={change.isPending || !reason.trim()} className="btn btn-primary">
          {change.isPending ? "Saving" : "Save as a new version"}
        </button>
        <button type="button" onClick={onDone} className="btn btn-secondary">
          Cancel
        </button>
      </div>
    </form>
  );
}

function History({ standard, kinds }: { standard: Standard; kinds: Kind[] }) {
  const history = useStandardVersions(standard.id!);
  if (history.isLoading) return <Loading what="the versions" />;
  if (history.error) return <Failure error={history.error} what="the versions" />;
  return (
    <ol className="mt-3 space-y-3" data-testid="standard-history">
      {history.data!.versions.map((v) => (
        <li key={v.version} className="rounded border border-slate-200 p-3" data-testid={`standard-version-${v.version}`}>
          <p className="text-sm font-medium">
            Version {v.version} <span className="font-normal text-slate-600">{byOn(v.set_by_label ?? v.set_by, v.set_at)}</span>
          </p>
          <p className="text-sm text-slate-700">
            <span className="text-slate-500">Reason: </span>
            {sentence(v.reason)}
          </p>
          <div className="mt-2">
            <StandardLimitsTable limits={v.limits} kinds={kinds} />
          </div>
        </li>
      ))}
    </ol>
  );
}

function StandardCard({ standard, kinds, maySet }: { standard: Standard; kinds: Kind[]; maySet: boolean }) {
  const [editing, setEditing] = useState(false);
  const [showing, setShowing] = useState(false);
  return (
    <div className="rounded border border-slate-200 p-4" data-testid={`standard-card-${standard.name}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-base font-semibold text-slate-900">{standard.name}</h3>
        {standard.built_in ? (
          <span className="text-sm text-slate-600">The platform&rsquo;s own values. Nobody sets them for an organisation.</span>
        ) : (
          <span className="text-sm text-slate-600" data-testid="standard-set">
            Version {standard.version} {byOn(standard.set_by_label ?? standard.set_by, standard.set_at)}
          </span>
        )}
      </div>
      {!standard.built_in && standard.reason && (
        <p className="text-sm text-slate-700">
          <span className="text-slate-500">Reason: </span>
          {sentence(standard.reason)}
        </p>
      )}
      <div className="mt-2">
        <StandardLimitsTable limits={standard.limits} kinds={kinds} />
      </div>
      {!standard.built_in && (
        <div className="mt-3 flex gap-3 text-sm">
          {maySet && !editing && (
            <button type="button" data-testid="change-standard-open" onClick={() => setEditing(true)} className="btn btn-secondary">
              Change this standard
            </button>
          )}
          {standard.versions > 1 && (
            <button type="button" data-testid="standard-history-toggle" onClick={() => setShowing(!showing)} className="btn btn-link">
              {showing ? "Hide the earlier versions" : `Show all ${standard.versions} versions`}
            </button>
          )}
        </div>
      )}
      {editing && <ChangeStandard standard={standard} kinds={kinds} onDone={() => setEditing(false)} />}
      {showing && <History standard={standard} kinds={kinds} />}
    </div>
  );
}

export function DeidentificationStandards() {
  const data = useStandards();
  const [creating, setCreating] = useState(false);
  if (data.isLoading) return <Loading what="the de-identification standards" />;
  if (data.error) return <Failure error={data.error} what="the de-identification standards" />;
  const d = data.data!;
  return (
    <Section
      level="page"
      title="De-identification standards"
      description="A de-identification standard is what a de-identified result has to meet before it can be released to a particular audience. The pipeline run that made the result is measured against an answer key, a list of the identifying details that really are in the data, prepared beforehand. The measurement gives two figures: how much identifying detail was removed, and how much was left in the clear. The person who releases a result chooses a standard, and the platform checks the measurement against it at that moment."
    >
      <p className="mb-4 text-sm text-slate-600" data-testid="standards-lead">
        The decision to release a result is on the <Link to="/gates" className="text-sky-700 underline">De-identification results</Link> page.
      </p>

      <div data-testid="standards-how-to-read" className="mb-6 max-w-3xl rounded border border-slate-300 bg-white p-4 text-sm text-slate-800">
        <h2 className="text-base font-semibold text-slate-900">How to read a standard</h2>
        <dl className="mt-2 space-y-2">
          <div>
            <dt className="font-medium">Kind of identifier</dt>
            <dd>A kind of detail that points to a person, such as a name or a phone number. A kind that is not listed has no limit of its own.</dd>
          </div>
          <div>
            <dt className="font-medium">Removed, at least</dt>
            <dd>The lowest share of the identifying details of that kind in the answer key that the pipeline run must have removed. &ldquo;No minimum&rdquo; means no share is required.</dd>
          </div>
          <div>
            <dt className="font-medium">Left in the clear, at most</dt>
            <dd>
              The most identifying details of that kind that may still be readable in the result. &ldquo;None may remain&rdquo; means not one. &ldquo;No maximum&rdquo; means any number
              passes.
            </dd>
          </div>
          <div>
            <dt className="font-medium">Over all kinds together</dt>
            <dd>The lowest share of identifying detail that must have been removed across every kind at once. A result can pass this and still fail the figures of one kind.</dd>
          </div>
        </dl>

        <h3 className="mt-5 text-sm font-semibold text-slate-900">An example</h3>
        <p className="mt-1" data-testid="standards-example-result">
          A pipeline run is measured against an answer key that marks 100 names and 100 addresses in the data. It removes all 100 names and 94 of the 100 addresses, so 6 addresses are left in the clear.
          Taken together, it removes 194 of the 200 details, which is 97%.
        </p>
        <p className="mt-2">The same result is judged by two different standards:</p>
        <div className="data-card mt-2">
          <table className="data-table w-full" data-testid="standards-example">
            <thead>
              <tr>
                <th>Standard</th>
                <th>What it asks</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              <tr data-testid="standards-example-internal">
                <td className="font-medium">For an internal team</td>
                <td>Over all kinds together: removed at least 95%. Names: none may remain. Addresses: removed at least 90%, no maximum.</td>
                <td>
                  <span className="font-medium">Clears it.</span> Overall, 97% was removed, which is at least 95%. No name was left in the clear. 94% of the addresses were removed, which is at least 90%.
                </td>
              </tr>
              <tr data-testid="standards-example-public">
                <td className="font-medium">For a public release</td>
                <td>Over all kinds together: removed at least 99%. Names: none may remain. Addresses: removed at least 99%, no maximum.</td>
                <td>
                  <span className="font-medium">Does not clear it.</span> Overall, 97% was removed, which is under 99%. 94% of the addresses were removed, which is under 99%. No name was left in the clear, so that figure is met.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-slate-600">A result has to meet every figure of the standard it is judged by. Missing even one means it does not clear the standard.</p>
      </div>

      <Section level="section" title="Your organisation&rsquo;s standards" description="Each standard is named for the audience it is for. A change adds a version, and the earlier ones stay.">
        <div className="space-y-4">
          {d.standards.length === 0 && (
            <p className="text-sm text-slate-700" data-testid="standards-none">
              This organisation has not set a standard yet, so every result is judged by the Platform default standard below.
            </p>
          )}
          {d.standards.map((b) => (
            <StandardCard key={b.id} standard={b} kinds={d.kinds} maySet={d.may_set} />
          ))}
        </div>
        {d.may_set ? (
          creating ? (
            <div className="mt-4">
              <NewStandard kinds={d.kinds} start={d.platform_default.limits} onDone={() => setCreating(false)} />
            </div>
          ) : (
            <button type="button" data-testid="new-standard-open" onClick={() => setCreating(true)} className="btn btn-primary mt-4">
              Create a standard
            </button>
          )
        ) : (
          <p className="mt-4 text-sm text-slate-600" data-testid="standards-cannot-set">
            {d.may_set_reasons.length ? sentence(d.may_set_reasons[0]) : "You may not set a de-identification standard."}
          </p>
        )}
      </Section>

      <Section level="section" title="The standard the platform starts with" description="Used when no other standard is chosen, and for the recommendation a pipeline run records when it finishes. It is a starting point: an organisation is expected to set standards of its own.">
        <StandardCard standard={d.platform_default} kinds={d.kinds} maySet={false} />
      </Section>
    </Section>
  );
}
