/**
 * Every action run, with how each one started.
 *
 * `action_run` is the lower-level record: one action (one step of a
 * pipeline, such as transcribe or redact) executed once, with the input and
 * output versions it touched. Before this screen, that history existed only
 * in the database: `trigger_kind`, `triggered_by` and `schedule_id` were
 * recorded on every row but nothing in the console ever showed them, so a
 * run a schedule fired looked identical to one nobody could account for.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useActionRuns } from "../../api/queries";
import { ClassBadge } from "../../components/ClassBadge";
import { Pagination } from "../../components/Pagination";
import { Empty, Failure, Loading, Section } from "../../components/states";

const STATUS_STYLE: Record<string, string> = {
  running: "bg-sky-100 text-sky-900",
  succeeded: "bg-green-100 text-green-900",
  failed: "bg-red-100 text-red-900",
};

const PAGE_SIZE = 15;

export function ActionRuns() {
  const [page, setPage] = useState(1);
  const { data, isLoading, error } = useActionRuns(PAGE_SIZE, (page - 1) * PAGE_SIZE);

  if (isLoading) return <Loading what="action runs" />;
  if (error) return <Failure error={error} what="action runs" />;

  return (
    <Section
      level="page"
      title="Action runs"
      description="Every action a pipeline executed, and how it started: run by hand, or fired by a schedule. Distinct from a whole pipeline run, which is a sequence of these."
    >
      {!data?.action_runs.length ? (
        <Empty what="action runs" hint="Nothing has run yet." />
      ) : (
        <>
        <p data-testid="action-run-count" className="mb-2 text-sm text-slate-500">
          Showing {data.shown} of {data.total}.
        </p>
        <div className="overflow-x-auto rounded border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-2">Action</th>
                <th>Status</th>
                <th>Started</th>
                <th>Started by</th>
                <th>Output</th>
              </tr>
            </thead>
            <tbody data-testid="action-run-rows">
              {data.action_runs.map((run) => (
                <tr key={run.id} className="border-t border-slate-100 transition-colors hover:bg-slate-50" data-testid={`action-run-${run.id}`}>
                  <td className="px-4 py-2 font-medium text-slate-900">
                    {run.action_name}
                  </td>
                  <td>
                    <span
                      data-testid={`action-run-status-${run.id}`}
                      className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[run.status] ?? "bg-slate-100 text-slate-600"}`}
                    >
                      {run.status}
                    </span>
                  </td>
                  <td className="text-xs text-slate-500">
                    {new Date(run.started_at).toLocaleString()}
                  </td>
                  <td className="text-xs text-slate-600">
                    {run.trigger_kind === "scheduled" ? (
                      <span data-testid={`action-run-trigger-${run.id}`}>
                        a schedule ({run.schedule_id})
                      </span>
                    ) : (
                      <span data-testid={`action-run-trigger-${run.id}`}>
                        {run.triggered_by_label ?? run.triggered_by ?? "not recorded"}, by hand
                      </span>
                    )}
                  </td>
                  <td>
                    {run.output_version ? (
                      <span className="flex items-center gap-1.5">
                        <Link
                          className="text-sky-700 underline"
                          to={`/versions/${run.output_version}`}
                        >
                          the version it produced
                        </Link>
                        {run.output_class && <ClassBadge value={run.output_class} />}
                      </span>
                    ) : (
                      <span className="text-xs text-slate-400">none yet</span>
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
          total={data.total}
          onPageChange={setPage}
        />
        </>
      )}
    </Section>
  );
}
