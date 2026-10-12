/**
 * Agent registry: every agent of the organisation in one list, with who answers for it, what it is held to, what it can do and what needs attention.
 *
 * Anybody signed in may read it. Acting on an agent is decided on the agent&rsquo;s own page.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useRegistry, type RegistryRow } from "../../api/agentOversight";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { when } from "../../lib/when";

function ToolsCell({ row }: { row: RegistryRow }) {
  if (row.tools.state === "not declared") return <span className="text-amber-800">Not declared</span>;
  if (row.tools.state === "no version") return <span className="text-slate-500">No version yet</span>;
  if (row.tools.state === "uses no tools") return <span>Uses no tools</span>;
  return <span className="font-mono text-xs">{row.tools.list.join(", ")}</span>;
}

export function AgentRegistry() {
  const data = useRegistry();
  const [text, setText] = useState("");
  const [flag, setFlag] = useState("");
  const rows = data.data?.agents ?? [];
  const flags = useMemo(() => {
    const seen = new Map<string, string>();
    rows.forEach((r) => r.flags.forEach((f) => seen.set(f.key, f.label)));
    return [...seen.entries()];
  }, [rows]);
  const shown = rows.filter(
    (r) =>
      (!text.trim() || `${r.name} ${r.department_name ?? ""} ${r.registered_by_label ?? r.registered_by} ${r.purpose}`.toLowerCase().includes(text.trim().toLowerCase())) &&
      (!flag || r.flags.some((f) => f.key === flag)),
  );
  return (
    <Section
      level="page"
      title="Agent registry"
      description="Every agent of the organisation: the department that owns it, who registered it, the risk level it is held at, what it is declared to use, where its model runs, and what needs attention. What an owner declares is shown as declared. What the platform checks is described on the agent's page."
    >
      {data.isLoading ? (
        <Loading what="the agents" />
      ) : data.error ? (
        <Failure error={data.error} what="the agents" />
      ) : rows.length === 0 ? (
        <Empty what="agents" hint="Nobody has registered an agent yet." />
      ) : (
        <>
          <p className="mb-3 text-sm text-slate-700" data-testid="registry-enforce">
            {data.data!.enforce ? "Agents are held to the organisation's risk levels." : "Agents are not yet held to the organisation's risk levels, so what needs attention is shown and nothing is refused because of it."}
          </p>
          <div className="mb-4 flex flex-wrap gap-3 text-sm">
            <label className="flex flex-col gap-1">
              Search
              <input data-testid="registry-search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Name, department, owner or purpose" className="w-72 rounded border border-slate-400 px-2 py-1" />
            </label>
            <label className="flex flex-col gap-1">
              Needs attention
              <select data-testid="registry-flag" value={flag} onChange={(e) => setFlag(e.target.value)} className="rounded border border-slate-400 px-2 py-1">
                <option value="">Any</option>
                {flags.map(([key, label]) => (
                  <option key={key} value={key}>{label}</option>
                ))}
              </select>
            </label>
            <p className="self-end text-slate-600" data-testid="registry-count">Showing {shown.length} of {rows.length}</p>
          </div>
          <div className="data-card">
            <table className="data-table w-full" data-testid="registry-table">
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>Department and owner</th>
                  <th>Risk level</th>
                  <th>Active version</th>
                  <th>Tools</th>
                  <th>Model</th>
                  <th>Last run</th>
                  <th>Needs attention</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.id} data-testid={`registry-row-${r.name}`} className="align-top">
                    <td>
                      <Link to={`/agents/${r.id}`} className="font-medium text-sky-700 underline">{r.name}</Link>
                      <span className="block text-xs text-slate-500">{r.run_by === "outside" ? "Run outside the platform" : "Run by the platform"}</span>
                    </td>
                    <td>
                      {r.department_name ?? <span className="text-slate-500">No department</span>}
                      <span className="block text-xs text-slate-500">Registered by {r.registered_by_label ?? r.registered_by}</span>
                      <span className="block text-xs text-slate-500">Sponsor: {r.sponsor ?? "none"}</span>
                    </td>
                    <td data-testid={`registry-level-${r.name}`}>
                      {r.risk_level ? (
                        <>
                          {r.risk_level.name}
                          {r.risk_level.framework_label && <span className="block text-xs text-slate-500">{r.risk_level.framework_label}</span>}
                        </>
                      ) : (
                        <span className="text-amber-800">{r.proposal_waiting ? "Proposed, not confirmed" : "Not classified"}</span>
                      )}
                    </td>
                    <td>{r.active_version ? `Version ${r.active_version}` : <span className="text-slate-500">None deployed</span>}</td>
                    <td><ToolsCell row={r} /></td>
                    <td>
                      {r.model_id ?? <span className="text-slate-500">None</span>}
                      {r.model_id && <span className="block text-xs text-slate-500">{r.model_hosting === "outside" ? "Runs outside the platform" : r.model_hosting === "platform" ? "Runs in the platform" : "Where it runs is not declared"}</span>}
                    </td>
                    <td>{r.last_run_at ? when(r.last_run_at) : <span className="text-slate-500">Never</span>}</td>
                    <td data-testid={`registry-flags-${r.name}`}>
                      {r.flags.length === 0 ? <span className="text-slate-500">Nothing</span> : (
                        <ul className="space-y-1">
                          {r.flags.map((f) => (
                            <li key={f.key}><span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-900">{f.label}</span></li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Section>
  );
}
