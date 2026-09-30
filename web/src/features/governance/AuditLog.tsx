/**
 * Every access decision, denials included and denials first.
 *
 * The default filter is "all", and denied rows are visually distinct. This is
 * the one screen where the temptation to show only successes is strongest and
 * most wrong: a permission that is missing and a person probing for data they
 * should not have look identical until somebody can count denials, and neither
 * appears at all if the default view hides them.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useDecisions } from "../../api/queries";
import { usePeople } from "../../api/people";
import { ClassBadge } from "../../components/ClassBadge";
import { Pagination } from "../../components/Pagination";
import { Empty, Failure, Loading, Section } from "../../components/states";

type Filter = "all" | "denied" | "allowed";
type ResourceFilter = "all" | "dataset" | "agent";
const PAGE_SIZE = 15;

export function AuditLog() {
  const [filter, setFilter] = useState<Filter>("all");
  const [resource, setResource] = useState<ResourceFilter>("all");
  const [page, setPage] = useState(1);

  const { data, isLoading, error } = useDecisions({
    allowed: filter === "denied" ? false : filter === "allowed" ? true : undefined,
    resource: resource === "all" ? undefined : resource,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  });
  const people = usePeople();
  const rows = data?.decisions ?? [];

  // Counted across the whole log, not just this page or this filter's
  // current page -- the same technique GateDecision.tsx's and
  // EgressApprovals.tsx's own queues use for their pending counts.
  const denialCount = useDecisions({ allowed: false, limit: 1, offset: 0 });
  const denials = denialCount.data?.total ?? 0;

  if (isLoading) return <Loading what="the audit log" />;
  if (error) return <Failure error={error} what="the audit log" />;

  return (
    <Section
      level="page"
      title="Who accessed what"
      description="Every request, and what happened to it. Each one is recorded twice: whether it was allowed, and whether the access was actually given. Those two can differ."
      actions={
        <div className="flex flex-wrap gap-2">
          <div className="flex gap-1 rounded border border-slate-300 bg-white p-0.5 text-sm">
            {(["all", "dataset", "agent"] as ResourceFilter[]).map((r) => (
              <button
                key={r}
                type="button"
                data-testid={`audit-resource-${r}`}
                onClick={() => {
                  setResource(r);
                  setPage(1);
                }}
                className={`rounded px-2 py-1 ${
                  resource === r ? "bg-indigo-600 text-white" : "text-slate-600"
                }`}
              >
                {r === "all" ? "All" : r === "dataset" ? "Dataset access" : "Agent activity"}
              </button>
            ))}
          </div>
          <div className="flex gap-1 rounded border border-slate-300 bg-white p-0.5 text-sm">
            {(["all", "denied", "allowed"] as Filter[]).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => {
                  setFilter(f);
                  setPage(1);
                }}
                className={`rounded px-2 py-1 ${
                  filter === f ? "bg-indigo-600 text-white" : "text-slate-600"
                }`}
              >
                {f === "denied" ? `Refused (${denials})` : f}
              </button>
            ))}
          </div>
        </div>
      }
    >
      {!rows.length ? (
        <Empty
          what="requests"
          hint="Nobody has asked to read anything yet."
        />
      ) : (
        <div className="overflow-x-auto rounded border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-2">When</th>
                <th>Who</th>
                <th>Question</th>
                <th>Access level</th>
                <th>What for</th>
                <th>Result</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody data-testid="audit-rows">
              {rows.map((d) => (
                <tr
                  key={d.id}
                  data-denied={!d.allowed}
                  className={`border-t border-slate-100 transition-colors ${
                    d.allowed ? "hover:bg-slate-50" : "bg-red-50 hover:bg-red-100"
                  }`}
                >
                  <td className="px-4 py-2 text-xs text-slate-500">{d.at}</td>
                  <td>
                    <span className="font-medium">{people.label(d.principal)}</span>
                    <span className="ml-1 text-xs text-slate-400">
                      {d.principal_roles?.join(", ")}
                    </span>
                  </td>
                  <td>
                    <span
                      title={
                        d.phase === "grant"
                          ? "Whether the access it allowed was actually given"
                          : "Whether the rules allowed it"
                      }
                      className={`rounded px-1.5 py-0.5 text-xs ${
                        d.phase === "grant"
                          ? "bg-violet-100 text-violet-900"
                          : "bg-slate-100 text-slate-700"
                      }`}
                    >
                      {d.phase === "grant" ? "was it given" : "was it allowed"}
                    </span>
                  </td>
                  <td>
                    {d.requested_class ? (
                      <ClassBadge value={d.requested_class} />
                    ) : (
                      <span className="text-xs text-slate-400">n/a</span>
                    )}
                  </td>
                  <td className="text-slate-600">{d.purpose || "none stated"}</td>
                  <td>
                    <span
                      data-testid={`audit-result-${d.id}`}
                      title={
                        d.active === false
                          ? "Given, and being switched on in storage. It takes effect by itself."
                          : undefined
                      }
                      className={`rounded px-2 py-0.5 text-xs font-medium ${
                        !d.allowed
                          ? "bg-red-200 text-red-900"
                          : d.active === false
                            ? "bg-lime-100 text-lime-900"
                            : "bg-emerald-100 text-emerald-900"
                      }`}
                    >
                      {!d.allowed ? "no" : d.active === false ? "yes, taking effect" : "yes"}
                    </span>
                  </td>
                  <td className="text-xs text-slate-600">
                    {d.reasons?.length ? (
                      <ul className="list-disc pl-4">
                        {d.reasons.map((r) => (
                          <li key={r}>{r}</li>
                        ))}
                      </ul>
                    ) : (
                      <span className="text-slate-400">none recorded</span>
                    )}
                    {d.dataset_version_id && (
                      <Link
                        to={`/versions/${d.dataset_version_id}`}
                        className="mt-1 inline-block text-sky-700 underline"
                      >
                        the data they asked for
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={data?.total ?? 0}
            onPageChange={setPage}
          />
        </div>
      )}
    </Section>
  );
}
