/**
 * Registered pipelines.
 *
 * Distinct from the two built-in kinds (de-identify, count records), which
 * ship with the platform and are never registered by anyone. These are
 * resources an end user brought themselves: a DAG of steps, versioned and
 * sealed the same way an agent's code is, offered from any sealed dataset
 * version's own "Run a pipeline" picker once registered here.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { usePipelines } from "../../api/dag_pipelines";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { Pagination } from "../../components/Pagination";

const PAGE_SIZE = 15;

export function Pipelines() {
  const [page, setPage] = useState(1);
  const pipelines = usePipelines(PAGE_SIZE, (page - 1) * PAGE_SIZE);

  return (
    <Section
      level="page"
      title="Pipelines"
      description="Registered by end users, the same way an agent is. A DAG of steps, versioned and content-addressed, not asserted."
      actions={
        <Link
          to="/pipelines/register"
          data-testid="pipelines-register-link"
          className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white"
        >
          Register a pipeline
        </Link>
      }
    >
      {pipelines.isLoading ? (
        <Loading what="pipelines" />
      ) : pipelines.error ? (
        <Failure error={pipelines.error} what="pipelines" />
      ) : !pipelines.data?.pipelines.length ? (
        <Empty what="pipelines" hint="Nobody has registered one in this organisation yet." />
      ) : (
        <>
        <p data-testid="pipeline-count" className="mb-2 text-sm text-slate-500">
          Showing {pipelines.data.shown} of {pipelines.data.total}.
        </p>
        <div className="overflow-x-auto rounded border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="whitespace-nowrap bg-slate-50 text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-2">Pipeline</th>
                <th className="px-3">Department</th>
                <th className="px-3">Version</th>
              </tr>
            </thead>
            <tbody data-testid="pipeline-rows">
              {pipelines.data.pipelines.map((p) => (
                <tr
                  key={p.id}
                  className="border-t border-slate-100 transition-colors hover:bg-slate-50"
                >
                  <td className="px-4 py-2 font-medium">
                    <Link
                      to={`/pipelines/${p.id}`}
                      data-testid={`pipeline-${p.id}`}
                      className="text-sky-700 hover:underline"
                    >
                      {p.name}
                    </Link>
                  </td>
                  <td className="whitespace-nowrap px-3 text-slate-600">
                    {p.department_name ?? "no department"}
                  </td>
                  <td className="whitespace-nowrap px-3">
                    {p.version_count > 0 ? (
                      `v${p.latest_version} sealed`
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
          total={pipelines.data.total}
          onPageChange={setPage}
        />
        </>
      )}
    </Section>
  );
}
