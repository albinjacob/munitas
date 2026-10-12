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
import { when } from "../../lib/when";
import { sentence } from "../../lib/text";
import { roleLabel } from "../../api/roles";

type Filter = "all" | "denied" | "allowed";
type ResourceFilter = "all" | "dataset" | "agent";
const PAGE_SIZE = 15;

/** What the platform writes when somebody reads the locked data audit is a list name and what was searched for; the screen says it in words. */
const AUDIT_LISTS: Record<string, string> = {
  reads: "locked column reads",
  column_leases: "locked column leases",
  file_reads: "locked file opens",
  file_leases: "locked file leases",
  keys: "keys",
  erasures: "people who were erased",
  small_groups: "small groups",
  contracts: "how columns were treated",
};
function purposeText(purpose: string | null | undefined): string {
  if (!purpose) return "none stated";
  const m = purpose.match(/^read audit evidence: ([a-z_]+)(.*)$/);
  if (!m || !AUDIT_LISTS[m[1]!]) return purpose === "read audit evidence" ? "Read the locked data audit" : purpose;
  const rest = m[2]!.replace(/^ \((.*?)\)/, " (searched for $1)");
  return `Read the locked data audit: ${AUDIT_LISTS[m[1]!]}${rest}`;
}

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
      title="Access decisions"
      description="Every request, and what happened to it. Each one is recorded twice: whether it was allowed, and whether the access was actually given. Those two can differ. What happened to locked data itself, such as the values read and the keys used, is in the Locked data audit."
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
                className={`btn btn-sm ${resource === r ? "btn-selected" : "btn-ghost"}`}
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
                className={`btn btn-sm ${filter === f ? "btn-selected" : "btn-ghost"}`}
              >
                {f === "denied" ? `Refused (${denials})` : f === "allowed" ? "Allowed" : "All"}
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
                <th className="pr-4">Who</th>
                <th className="pr-4">What was checked</th>
                <th className="pr-4">Access level</th>
                <th className="pr-4">What for</th>
                <th className="pr-4">Result</th>
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
                  <td className="px-4 py-2 pr-4 align-top text-xs text-slate-500">{when(d.at)}</td>
                  <td className="pr-4 align-top">
                    <span className="font-medium">{people.label(d.principal)}</span>
                    <span className="ml-1 block text-xs text-slate-400">
                      {d.principal_roles?.map(roleLabel).join(", ")}
                    </span>
                  </td>
                  <td className="pr-4 align-top">
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
                      {d.phase === "grant" ? "Whether it was given" : "Whether it was allowed"}
                    </span>
                  </td>
                  <td className="pr-4 align-top">
                    {d.requested_class ? (
                      <ClassBadge value={d.requested_class} />
                    ) : (
                      <span className="text-xs text-slate-500">Not applicable</span>
                    )}
                  </td>
                  <td className="pr-4 align-top text-slate-600">{purposeText(d.purpose)}</td>
                  <td className="pr-4 align-top">
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
                          <li key={r}>{sentence(r)}</li>
                        ))}
                      </ul>
                    ) : (
                      <span className="block text-slate-400">No reason recorded</span>
                    )}
                    {d.dataset_version_id && (
                      <Link
                        to={`/versions/${d.dataset_version_id}`}
                        className="mt-1 inline-block text-sky-700 underline"
                      >
                        Open the dataset version
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
