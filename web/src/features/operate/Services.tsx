/**
 * What the workloads have been doing.
 *
 * The alternative was letting an administrator act as a service to see what it
 * can reach. This is better: the pipeline and the agent already run for real and
 * leave audit rows, so this is evidence rather than a simulation, and it cannot
 * be wrong about what a service is permitted because it is reporting what
 * actually happened.
 */

import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { ClassBadge } from "../../components/ClassBadge";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { SERVICES } from "../../identity/services";

interface RunRow {
  operator: string;
  runs: number;
  succeeded: number;
  failed: number;
  last_run: string | null;
}

interface DecisionRow {
  principal: string;
  allowed: number;
  denied: number;
  last_decision: string | null;
}

interface OrgRow {
  departments: {
    id: string;
    name: string;
    approvers: { person_id: string; label: string; holds_role: boolean }[];
    datasets: number;
  }[];
  datasets_without_a_department: number;
}

export function Services() {
  const activity = useQuery({
    queryKey: ["services"],
    queryFn: () => api.get<{ runs: RunRow[]; decisions: DecisionRow[] }>("/services"),
  });
  const org = useQuery({
    queryKey: ["organisation"],
    queryFn: () => api.get<OrgRow>("/organisation"),
  });

  return (
    <>
      <Section
        level="page"
        title="Services"
        description="You cannot act as one of these. This is what they have done, which is a stronger thing to look at than what they are permitted to do."
      >
        <ul className="grid gap-3 md:grid-cols-2">
          {SERVICES.map((s) => {
            const decisions = activity.data?.decisions.find(
              (d) => d.principal === s.id,
            );
            return (
              <li
                key={s.id}
                data-testid={`service-${s.id}`}
                className="rounded border border-slate-200 bg-white p-4"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{s.label}</span>
                  <span className="flex items-center gap-1.5 text-xs text-slate-500">
                    reads down to <ClassBadge value={s.floor} />
                  </span>
                </div>
                <p className="mt-1.5 text-sm text-slate-600">{s.description}</p>
                {decisions ? (
                  <p className="mt-2 text-xs text-slate-500">
                    {decisions.allowed} granted,{" "}
                    <span
                      className={decisions.denied > 0 ? "font-medium text-amber-800" : ""}
                    >
                      {decisions.denied} refused
                    </span>
                  </p>
                ) : (
                  <p className="mt-2 text-xs text-slate-400">
                    no access decisions recorded
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      </Section>

      <Section
        title="Pipeline activity"
        description="Runs are counted from what the workflow engine recorded, not from what a service reports about itself."
      >
        {activity.isLoading ? (
          <Loading what="service activity" />
        ) : activity.error ? (
          <Failure error={activity.error} what="service activity" />
        ) : !activity.data?.runs.length ? (
          <Empty what="pipeline runs" />
        ) : (
          <div className="overflow-x-auto rounded border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-4 py-2">Operator</th>
                  <th>Runs</th>
                  <th>Succeeded</th>
                  <th>Failed</th>
                  <th>Last run</th>
                </tr>
              </thead>
              <tbody>
                {activity.data.runs.map((r) => (
                  <tr key={r.operator} className="border-t border-slate-100 transition-colors hover:bg-slate-50">
                    <td className="px-4 py-2 font-medium">{r.operator}</td>
                    <td className="tabular-nums">{r.runs}</td>
                    <td className="tabular-nums">{r.succeeded}</td>
                    <td
                      className={`tabular-nums ${r.failed > 0 ? "font-medium text-amber-800" : ""}`}
                    >
                      {r.failed}
                    </td>
                    <td className="text-xs text-slate-500">{r.last_run}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section
        title="Who owns what"
        description="Access to an asset is approved by the custodian of the department that owns it, and by nobody else."
      >
        {org.isLoading ? (
          <Loading what="the organisation" />
        ) : org.error ? (
          <Failure error={org.error} what="the organisation" />
        ) : org.data ? (
          <>
            {org.data.departments.length ? (
              <div className="overflow-x-auto rounded border border-slate-200 bg-white">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
                    <tr>
                      <th className="px-4 py-2">Department</th>
                      <th>Approvers</th>
                      <th>Datasets owned</th>
                    </tr>
                  </thead>
                  <tbody>
                    {org.data.departments.map((d) => (
                      <tr key={d.id} className="border-t border-slate-100 transition-colors hover:bg-slate-50">
                        <td className="px-4 py-2 font-medium">{d.name}</td>
                        <td>{d.approvers.map((a) => (a.holds_role ? a.label : `${a.label} (cannot act)`)).join(", ")}</td>
                        <td className="tabular-nums">{d.datasets}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty what="departments" />
            )}

            {org.data.datasets_without_a_department > 0 && (
              <p
                data-testid="unowned-warning"
                className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"
              >
                <strong>
                  {org.data.datasets_without_a_department} datasets have no
                  owning department.
                </strong>{" "}
                Nobody can approve access to them, because there is nobody
                accountable for them. They predate the organisation model, and
                assigning an owner is a decision for the organisation rather
                than something this platform should guess.
              </p>
            )}
          </>
        ) : null}
      </Section>
    </>
  );
}
