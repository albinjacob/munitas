/**
 * Approved tools: the tools an organisation has approved in advance for new versions of its agents.
 *
 * Anybody signed in may read it. Only the AI risk owner writes it, and the platform refuses the rest. A change is a new version: the earlier ones stay, each with who made it, when and why.
 * A version of an agent that adds only listed tools needs no request, or only the approval of the department that owns the agent.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useToolList, useWriteToolList, type ToolEntry, type ToolList as ToolListData, type ToolListPage } from "../../api/agentOversight";
import { Failure, Loading, Section } from "../../components/states";
import { notify } from "../../components/toast";
import { byOn } from "../../lib/when";
import { sentence } from "../../lib/text";
import { Reasons } from "../locking/LockedColumns";

const TIER_WORDS: Record<number, string> = {
  1: "Pre-approved: nobody is asked",
  2: "Department approval only: the officer is not asked",
};

function EntriesTable({ entries, page }: { entries: ToolEntry[]; page: ToolListPage }) {
  const levelName = (key: string) => page.levels.find((l) => l.key === key)?.name ?? key;
  return (
    <div className="data-card">
      <table className="data-table w-full table-fixed" data-testid="tools-table">
        <colgroup>
          <col className="w-[16%]" />
          <col className="w-[22%]" />
          <col className="w-[14%]" />
          <col className="w-[10%]" />
          <col className="w-[22%]" />
          <col className="w-[16%]" />
        </colgroup>
        <thead>
          <tr>
            <th>Tool</th>
            <th>What it does</th>
            <th>How far it is approved</th>
            <th>Up to level</th>
            <th>Conditions and reason</th>
            <th>Look at it again by</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.tool} data-testid={`tool-${e.tool}`}>
              <td className="break-words font-mono text-sm font-medium">{e.tool}</td>
              <td>
                {e.description}
                <span className="mt-1 block text-xs text-slate-600">{page.reach[e.reach] ?? e.reach}</span>
              </td>
              <td>{TIER_WORDS[e.tier] ?? `Tier ${e.tier}`}</td>
              <td>{levelName(e.ceiling)}</td>
              <td>
                {e.conditions ? <span className="block">{sentence(e.conditions)}</span> : null}
                <span className="block text-slate-600">{sentence(e.reason)}</span>
              </td>
              <td>
                {e.review_by}
                {e.overdue ? <span className="mt-1 block rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-900">Past this date, so it approves nothing</span> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type Row = { tool: string; description: string; reach: string; tier: string; ceiling: string; conditions: string; reason: string; review_by: string };

const rowOf = (e: ToolEntry): Row => ({ tool: e.tool, description: e.description, reach: e.reach, tier: String(e.tier), ceiling: e.ceiling, conditions: e.conditions, reason: e.reason, review_by: e.review_by });

function Editor({ start, page, onDone }: { start: ToolListData; page: ToolListPage; onDone: () => void }) {
  const write = useWriteToolList();
  const deployable = page.levels.filter((l) => !l.blocks);
  const blank: Row = { tool: "", description: "", reach: "reads", tier: "1", ceiling: deployable[deployable.length - 1]?.key ?? "", conditions: "", reason: "", review_by: "" };
  const [rows, setRows] = useState<Row[]>(start.entries.map(rowOf));
  const [reason, setReason] = useState("");
  const change = (i: number, patch: Partial<Row>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <form
      className="space-y-4 rounded border border-slate-200 p-4"
      data-testid="tools-editor"
      onSubmit={(e) => {
        e.preventDefault();
        write.mutate(
          { entries: rows.map((r) => ({ ...r, tier: Number(r.tier) })), reason },
          { onSuccess: () => { notify("Approved tools saved."); onDone(); } },
        );
      }}
    >
      <p className="text-sm text-slate-700">Every tool that stays on the list is listed here. Saving adds a new version and the earlier ones stay. A version of an agent that was registered before keeps the decision it was given.</p>
      {rows.map((row, i) => (
        <fieldset key={i} className="space-y-2 rounded border border-l-4 border-slate-300 border-l-indigo-500 bg-white p-3 text-sm shadow-sm" data-testid={`tool-row-${i}`}>
          <legend className="px-1 text-slate-600">Tool {i + 1}</legend>
          <div className="flex flex-wrap gap-3">
            <label className="flex flex-col gap-1">
              Name, as the agent declares it
              <input data-testid={`tool-name-${i}`} value={row.tool} onChange={(e) => change(i, { tool: e.target.value })} className="w-64 rounded border border-slate-400 px-2 py-1 font-mono" />
            </label>
            <label className="flex flex-col gap-1">
              What it does
              <select data-testid={`tool-reach-${i}`} value={row.reach} onChange={(e) => change(i, { reach: e.target.value, tier: page.tier_one_reach.includes(e.target.value) ? row.tier : "2" })} className="rounded border border-slate-400 px-2 py-1">
                {Object.entries(page.reach).map(([key, words]) => (
                  <option key={key} value={key}>{words}</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              How far it is approved
              <select data-testid={`tool-tier-${i}`} value={row.tier} onChange={(e) => change(i, { tier: e.target.value })} className="rounded border border-slate-400 px-2 py-1">
                <option value="1" disabled={!page.tier_one_reach.includes(row.reach)}>{TIER_WORDS[1]}</option>
                <option value="2">{TIER_WORDS[2]}</option>
              </select>
            </label>
            <label className="flex flex-col gap-1">
              Approved up to this level
              <select data-testid={`tool-ceiling-${i}`} value={row.ceiling} onChange={(e) => change(i, { ceiling: e.target.value })} className="rounded border border-slate-400 px-2 py-1">
                {deployable.map((l) => (
                  <option key={l.key} value={l.key}>{l.name}</option>
                ))}
              </select>
            </label>
          </div>
          <label className="flex max-w-xl flex-col gap-1">
            Description
            <textarea data-testid={`tool-description-${i}`} rows={2} value={row.description} onChange={(e) => change(i, { description: e.target.value })} className="rounded border border-slate-400 px-2 py-1" />
          </label>
          <label className="flex max-w-xl flex-col gap-1">
            Conditions it is approved under, for example who confirms what it creates
            <textarea data-testid={`tool-conditions-${i}`} rows={2} value={row.conditions} onChange={(e) => change(i, { conditions: e.target.value })} className="rounded border border-slate-400 px-2 py-1" />
          </label>
          <label className="flex max-w-xl flex-col gap-1">
            Why it is approved in advance
            <textarea data-testid={`tool-reason-${i}`} rows={2} value={row.reason} onChange={(e) => change(i, { reason: e.target.value })} className="rounded border border-slate-400 px-2 py-1" />
          </label>
          <label className="flex max-w-xs flex-col gap-1">
            Look at it again by
            <input data-testid={`tool-review-${i}`} type="date" value={row.review_by} onChange={(e) => change(i, { review_by: e.target.value })} className="w-44 rounded border border-slate-400 px-2 py-1" />
          </label>
          <button type="button" onClick={() => setRows(rows.filter((_, j) => j !== i))} className="btn btn-secondary btn-sm">Remove from the list</button>
        </fieldset>
      ))}
      <button type="button" data-testid="tool-add" onClick={() => setRows([...rows, blank])} className="btn btn-secondary">
        Add a tool
      </button>
      <label className="flex max-w-xl flex-col gap-1 text-sm">
        Why is the list written this way?
        <textarea data-testid="tools-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      {write.error ? <div role="alert" className="text-sm text-red-800"><Reasons error={write.error} fallback="The approved tools could not be saved." /></div> : null}
      <div className="flex gap-3">
        <button type="submit" data-testid="tools-save" disabled={write.isPending || !reason.trim()} className="btn btn-primary">
          {write.isPending ? "Saving" : "Save as a new version"}
        </button>
        <button type="button" onClick={onDone} className="btn btn-secondary">
          Cancel
        </button>
      </div>
    </form>
  );
}

export function ToolList() {
  const data = useToolList();
  const [editing, setEditing] = useState(false);
  const [showing, setShowing] = useState(false);
  if (data.isLoading) return <Loading what="the approved tools" />;
  if (data.error) return <Failure error={data.error} what="the approved tools" />;
  const page = data.data!;
  const c = page.current;
  const earlier = page.history.filter((v) => v.version !== c.version);
  return (
    <Section
      level="page"
      title="Approved tools"
      description="Tools the organisation has approved in advance, each up to the risk level it was approved for. When a new version of an agent adds only these tools, it does not wait for the people who would otherwise decide it. A tool that is not on the list is decided in the usual way."
    >
      <p className="mb-4 text-sm text-slate-600">
        A tool is approved by its name, which the owner of the agent declares. Which hosts an agent may call and which data it may read are decided separately. See what each new version changed on the <Link to="/agent-changes" className="text-sky-700 underline">Agent changes</Link> page.
      </p>
      <Section level="section" title="The tools in force" description={c.version === null ? "This organisation has not approved any tools in advance, so every tool a new version adds is decided in the usual way." : undefined}>
        {c.version !== null && (
          <p className="mb-2 text-sm text-slate-700" data-testid="tools-set">
            Version {c.version} {byOn(c.set_by_label ?? c.set_by, c.set_at)}. <span className="text-slate-500">Reason: </span>
            {sentence(c.reason)}
          </p>
        )}
        {c.entries.length > 0 && <EntriesTable entries={c.entries} page={page} />}
        {page.may_manage ? (
          editing ? (
            <div className="mt-4"><Editor start={c} page={page} onDone={() => setEditing(false)} /></div>
          ) : (
            <button type="button" data-testid="tools-edit" onClick={() => setEditing(true)} className="btn btn-primary mt-4">
              {c.version === null ? "Write the approved tools" : "Change the approved tools"}
            </button>
          )
        ) : (
          <p className="mt-4 text-sm text-slate-600" data-testid="tools-cannot-manage">{page.may_manage_reasons.length ? sentence(page.may_manage_reasons[0]) : "You may not write the approved tools."}</p>
        )}
      </Section>

      {earlier.length > 0 && (
        <Section level="section" title="Earlier versions">
          <button type="button" data-testid="tools-history-toggle" onClick={() => setShowing(!showing)} className="btn btn-link">
            {showing ? "Hide the earlier versions" : `Show the ${earlier.length} earlier ${earlier.length === 1 ? "version" : "versions"}`}
          </button>
          {showing && (
            <ol className="mt-3 space-y-3" data-testid="tools-history">
              {earlier.map((v) => (
                <li key={v.version} className="rounded border border-slate-200 p-3" data-testid={`tools-version-${v.version}`}>
                  <p className="text-sm font-medium">
                    Version {v.version} <span className="font-normal text-slate-600">{byOn(v.set_by_label ?? v.set_by, v.set_at)}</span>
                  </p>
                  <p className="mb-2 text-sm text-slate-700"><span className="text-slate-500">Reason: </span>{sentence(v.reason)}</p>
                  <EntriesTable entries={v.entries} page={page} />
                </li>
              ))}
            </ol>
          )}
        </Section>
      )}
    </Section>
  );
}
