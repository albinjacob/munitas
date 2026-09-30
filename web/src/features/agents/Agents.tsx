/**
 * Registered agents.
 *
 * Distinct from `/services`, which is fixed infrastructure nobody registers.
 * These are resources an end user created, the same way `/datasets` lists
 * what an end user registered, and each row says how many versions exist so
 * "nothing has ever been sealed for this agent" is visible without opening it.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useAgents } from "../../api/agents";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { Pagination } from "../../components/Pagination";

const PAGE_SIZE = 15;

export function Agents() {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const agents = useAgents({
    q: q || undefined,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  });

  return (
    <Section
      level="page"
      title="Agents"
      description="Registered by end users, the same way a dataset is. Versioned and content-addressed, not asserted."
      actions={
        <Link
          to="/agents/register"
          data-testid="agents-register-link"
          className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white"
        >
          Register an agent
        </Link>
      }
    >
      <div className="mb-4 flex flex-wrap items-end gap-3 rounded border border-slate-200 bg-white p-3">
        <label className="flex flex-col gap-1 text-xs text-slate-500">
          Search by name
          <input
            data-testid="agent-search"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
            placeholder="triage-agent"
            className="w-56 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
          />
        </label>
        {q && (
          <button
            type="button"
            onClick={() => {
              setQ("");
              setPage(1);
            }}
            className="pb-1 text-sm text-sky-700 underline"
          >
            Clear
          </button>
        )}
      </div>

      {agents.isLoading ? (
        <Loading what="agents" />
      ) : agents.error ? (
        <Failure error={agents.error} what="agents" />
      ) : !agents.data?.agents.length ? (
        <Empty
          what="agents"
          hint={
            q
              ? "Nothing matches that search."
              : "Nobody has registered one in this organisation yet."
          }
        />
      ) : (
        <>
        <p data-testid="agent-count" className="mb-2 text-sm text-slate-500">
          Showing {agents.data.shown} of {agents.data.total}
          {q ? " matching" : ""}.
        </p>
        <div className="overflow-x-auto rounded border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="whitespace-nowrap bg-slate-50 text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-2">Agent</th>
                <th className="px-3">Purpose</th>
                <th className="px-3">Department</th>
                <th className="px-3">Version</th>
              </tr>
            </thead>
            <tbody data-testid="agent-rows">
              {agents.data.agents.map((a) => (
                <tr
                  key={a.id}
                  className="border-t border-slate-100 transition-colors hover:bg-slate-50"
                >
                  <td className="px-4 py-2 font-medium">
                    <Link
                      to={`/agents/${a.id}`}
                      data-testid={`agent-${a.id}`}
                      className="text-sky-700 hover:underline"
                    >
                      {a.name}
                    </Link>
                  </td>
                  <td className="px-3 text-slate-600">{a.purpose}</td>
                  <td className="whitespace-nowrap px-3 text-slate-600">
                    {a.department_name ?? "no department"}
                  </td>
                  <td className="whitespace-nowrap px-3">
                    {a.version_count > 0 ? (
                      `v${a.latest_version} sealed`
                    ) : (
                      <span className="text-xs text-amber-800">
                        no version registered yet
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={agents.data.total}
          onPageChange={setPage}
        />
        </>
      )}
    </Section>
  );
}
