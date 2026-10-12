/**
 * Risk levels: the list an organisation holds its agents to.
 *
 * Anybody signed in may read it. Only the AI risk owner writes it, and the platform refuses the rest. A change is a new version: the earlier ones stay, each with who made it,
 * when and why, and an agent keeps the level it was confirmed at.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useRiskLevels, useWriteRiskLevels, type RiskLevel, type RiskLevelList } from "../../api/agentOversight";
import { Failure, Loading, Section } from "../../components/states";
import { notify } from "../../components/toast";
import { byOn } from "../../lib/when";
import { sentence } from "../../lib/text";
import { Reasons } from "../locking/LockedColumns";

function LevelsTable({ levels }: { levels: RiskLevel[] }) {
  return (
    <div className="data-card">
      <table className="data-table w-full table-fixed" data-testid="levels-table">
        <colgroup>
          <col className="w-[12%]" />
          <col className="w-[34%]" />
          <col className="w-[16%]" />
          <col className="w-[13%]" />
          <col className="w-[13%]" />
          <col className="w-[12%]" />
        </colgroup>
        <thead>
          <tr>
            <th>Level</th>
            <th>What an agent at this level is like</th>
            <th>Framework</th>
            <th>Second approver</th>
            <th>May be deployed</th>
            <th>Re-confirm every</th>
          </tr>
        </thead>
        <tbody>
          {levels.map((level) => (
            <tr key={level.key} data-testid={`level-${level.key}`}>
              <td className="font-medium">{level.name}</td>
              <td>{level.description}</td>
              <td>{level.framework_label ?? <span className="text-slate-500">None named</span>}</td>
              <td>{level.second_approver ? "Yes" : "No"}</td>
              <td>{level.blocks ? "No" : "Yes"}</td>
              <td>{level.reconfirm_days ? `${level.reconfirm_days} days` : <span className="text-slate-500">Never</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type Row = { name: string; description: string; framework_label: string; second_approver: boolean; blocks: boolean; reconfirm_days: string };

function rowOf(level: RiskLevel): Row {
  return { name: level.name, description: level.description, framework_label: level.framework_label ?? "", second_approver: level.second_approver, blocks: level.blocks, reconfirm_days: level.reconfirm_days ? String(level.reconfirm_days) : "" };
}

function Editor({ start, onDone }: { start: RiskLevelList; onDone: () => void }) {
  const write = useWriteRiskLevels();
  const [rows, setRows] = useState<Row[]>(start.levels.map(rowOf));
  const [enforce, setEnforce] = useState(start.enforce);
  const [reason, setReason] = useState("");
  const change = (i: number, patch: Partial<Row>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const move = (i: number, by: number) => {
    const j = i + by;
    if (j < 0 || j >= rows.length) return;
    const next = [...rows];
    [next[i], next[j]] = [next[j], next[i]];
    setRows(next);
  };
  return (
    <form
      className="space-y-4 rounded border border-slate-200 p-4"
      data-testid="levels-editor"
      onSubmit={(e) => {
        e.preventDefault();
        write.mutate(
          { levels: rows.map((r) => ({ name: r.name, description: r.description, framework_label: r.framework_label.trim() || null, second_approver: r.second_approver, blocks: r.blocks, reconfirm_days: r.reconfirm_days.trim() ? Number(r.reconfirm_days) : null })), enforce, reason },
          { onSuccess: () => { notify("Risk levels saved."); onDone(); } },
        );
      }}
    >
      <p className="text-sm text-slate-700">List the levels from the least strict to the most strict. Saving adds a new version. An agent keeps the level it was confirmed at.</p>
      {rows.map((row, i) => (
        <fieldset key={i} className="space-y-2 rounded border border-l-4 border-slate-300 border-l-indigo-500 bg-white p-3 text-sm shadow-sm" data-testid={`level-row-${i}`}>
          <legend className="px-1 text-slate-600">Level {i + 1}</legend>
          <div className="flex flex-wrap gap-3">
            <label className="flex flex-col gap-1">
              Name
              <input data-testid={`level-name-${i}`} value={row.name} onChange={(e) => change(i, { name: e.target.value })} className="w-48 rounded border border-slate-400 px-2 py-1" />
            </label>
            <label className="flex flex-col gap-1">
              Framework it corresponds to, if any
              <input data-testid={`level-framework-${i}`} value={row.framework_label} onChange={(e) => change(i, { framework_label: e.target.value })} className="w-64 rounded border border-slate-400 px-2 py-1" />
            </label>
          </div>
          <label className="flex max-w-xl flex-col gap-1">
            What an agent at this level is like
            <textarea data-testid={`level-description-${i}`} rows={2} value={row.description} onChange={(e) => change(i, { description: e.target.value })} className="rounded border border-slate-400 px-2 py-1" />
          </label>
          <label className="flex max-w-xs flex-col gap-1">
            Re-confirm every (days), empty for never
            <input data-testid={`level-reconfirm-${i}`} inputMode="numeric" value={row.reconfirm_days} onChange={(e) => change(i, { reconfirm_days: e.target.value })} className="w-32 rounded border border-slate-400 px-2 py-1" />
          </label>
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2">
              <input type="checkbox" data-testid={`level-second-${i}`} checked={row.second_approver} onChange={(e) => change(i, { second_approver: e.target.checked })} />A second approver is needed to widen an agent at this level
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" data-testid={`level-blocks-${i}`} checked={row.blocks} onChange={(e) => change(i, { blocks: e.target.checked })} />An agent at this level may not be deployed
            </label>
          </div>
          <div className="flex gap-2">
            <button type="button" onClick={() => move(i, -1)} disabled={i === 0} className="btn btn-secondary btn-sm">Move up</button>
            <button type="button" onClick={() => move(i, 1)} disabled={i === rows.length - 1} className="btn btn-secondary btn-sm">Move down</button>
            <button type="button" onClick={() => setRows(rows.filter((_, j) => j !== i))} disabled={rows.length === 1} className="btn btn-secondary btn-sm">Remove</button>
          </div>
        </fieldset>
      ))}
      <button type="button" data-testid="level-add" onClick={() => setRows([...rows, { name: "", description: "", framework_label: "", second_approver: false, blocks: false, reconfirm_days: "" }])} className="btn btn-secondary">
        Add a level
      </button>
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" data-testid="levels-enforce" className="mt-1" checked={enforce} onChange={(e) => setEnforce(e.target.checked)} />
        <span>
          Hold agents to these levels. An agent then needs a confirmed level before it can start a run, and a new version that widens what an agent can do has to be approved before it is deployed.
        </span>
      </label>
      <label className="flex max-w-xl flex-col gap-1 text-sm">
        Why are the levels written this way?
        <textarea data-testid="levels-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      {write.error ? <div role="alert" className="text-sm text-red-800"><Reasons error={write.error} fallback="The risk levels could not be saved." /></div> : null}
      <div className="flex gap-3">
        <button type="submit" data-testid="levels-save" disabled={write.isPending || !reason.trim()} className="btn btn-primary">
          {write.isPending ? "Saving" : "Save as a new version"}
        </button>
        <button type="button" onClick={onDone} className="btn btn-secondary">
          Cancel
        </button>
      </div>
    </form>
  );
}

export function RiskLevels() {
  const data = useRiskLevels();
  const [editing, setEditing] = useState(false);
  const [showing, setShowing] = useState(false);
  if (data.isLoading) return <Loading what="the risk levels" />;
  if (data.error) return <Failure error={data.error} what="the risk levels" />;
  const d = data.data!;
  const c = d.current;
  const earlier = d.history.filter((v) => v.version !== c.version);   // the version in force is shown above, so "earlier" leaves it out
  return (
    <Section
      level="page"
      title="Risk levels"
      description={`The levels an organisation holds its agents to, from the least strict to the most strict. The person who registers an agent proposes a level for it, and ${d.confirm_words} confirms it. The level decides whether widening the agent needs a second approver, and whether it may be deployed at all.`}
    >
      <p className="mb-4 text-sm text-slate-600">
        Each agent&rsquo;s level is shown on its own page, and every agent is listed in the <Link to="/agent-registry" className="text-sky-700 underline">Agent registry</Link>.
      </p>
      <Section level="section" title="The levels in force" description={c.built_in ? "This organisation has not written its own levels, so these built-in ones apply." : undefined}>
        {!c.built_in && (
          <p className="mb-2 text-sm text-slate-700" data-testid="levels-set">
            Version {c.version} {byOn(c.set_by_label ?? c.set_by, c.set_at)}. <span className="text-slate-500">Reason: </span>
            {sentence(c.reason)}
          </p>
        )}
        <LevelsTable levels={c.levels} />
        <p className="mt-3 text-sm" data-testid="levels-enforce-state">
          {c.enforce
            ? "Agents are held to these levels: an agent needs a confirmed level to start a run, and a version that widens an agent is approved before it is deployed."
            : "Agents are not yet held to these levels. Levels and changes are recorded and shown, but nothing is refused because of them, apart from a level that may not be deployed."}
        </p>
        {d.may_manage ? (
          editing ? (
            <div className="mt-4"><Editor start={c} onDone={() => setEditing(false)} /></div>
          ) : (
            <button type="button" data-testid="levels-edit" onClick={() => setEditing(true)} className="btn btn-primary mt-4">
              {c.built_in ? "Write the organisation's levels" : "Change the levels"}
            </button>
          )
        ) : (
          <p className="mt-4 text-sm text-slate-600" data-testid="levels-cannot-manage">{d.may_manage_reasons.length ? sentence(d.may_manage_reasons[0]) : "You may not set the risk levels."}</p>
        )}
      </Section>

      {earlier.length > 0 && (
        <Section level="section" title="Earlier versions">
          <button type="button" data-testid="levels-history-toggle" onClick={() => setShowing(!showing)} className="btn btn-link">
            {showing ? "Hide the earlier versions" : `Show the ${earlier.length} earlier ${earlier.length === 1 ? "version" : "versions"}`}
          </button>
          {showing && (
            <ol className="mt-3 space-y-3" data-testid="levels-history">
              {earlier.map((v) => (
                <li key={v.version} className="rounded border border-slate-200 p-3" data-testid={`levels-version-${v.version}`}>
                  <p className="text-sm font-medium">
                    Version {v.version} <span className="font-normal text-slate-600">{byOn(v.set_by_label ?? v.set_by, v.set_at)}</span>
                  </p>
                  <p className="mb-2 text-sm text-slate-700"><span className="text-slate-500">Reason: </span>{sentence(v.reason)}</p>
                  <LevelsTable levels={v.levels} />
                </li>
              ))}
            </ol>
          )}
        </Section>
      )}
    </Section>
  );
}
