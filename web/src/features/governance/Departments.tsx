/**
 * Departments and who answers for them.
 *
 * Each department has approvers: data custodians who may approve access to its data and confirm sensitivity claims about it. Any one of them
 * can act, so a department is not blocked when one person is away. Any current approver can add another, for good or for a set time (cover
 * for leave ends by itself), or remove one; every change is recorded with who made it and why. A department always keeps one permanent
 * approver.
 */

import { useState } from "react";
import { useIdentity } from "../../identity/IdentityContext";
import { useOrganisation } from "../../api/queries";
import {
  useAddApprover,
  useApproverCandidates,
  useApproverHistory,
  useRemoveApprover,
} from "../../api/departments";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { notify } from "../../components/toast";

type Department = NonNullable<ReturnType<typeof useOrganisation>["data"]>["departments"][number];

// "20 Oct 2026", never "10/5/2026": that reads as 10 May to some people and 5 October to others.
const day = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "";

// The platform's reasons are short lower-case clauses; on screen they read as sentences.
const sentence = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

export function Departments() {
  const organisation = useOrganisation();

  return (
    <Section
      level="page"
      title="Departments"
      description="Each department's approvers are the data custodians who may approve access to its data and confirm claims about it. Any one of them can act, so a department is not blocked when one person is away."
    >
      {organisation.isLoading ? (
        <Loading what="departments" />
      ) : organisation.error ? (
        <Failure error={organisation.error} what="departments" />
      ) : !organisation.data?.departments.length ? (
        <Empty title="No departments yet" hint="A department is made when an organisation is set up." />
      ) : (
        <div className="space-y-6">
          {organisation.data.departments.map((d) => (
            <DepartmentCard key={d.id} department={d} />
          ))}
        </div>
      )}
    </Section>
  );
}

function DepartmentCard({ department }: { department: Department }) {
  const { principal } = useIdentity();
  const add = useAddApprover();
  const remove = useRemoveApprover();

  // An approver is somebody listed who also holds the Data custodian role now. Listed without it, a person cannot act and is not counted.
  const iAmApprover = Boolean(principal && department.approvers.some((a) => a.person_id === principal.id && a.holds_role));
  const workingApprovers = department.approvers.filter((a) => a.holds_role);
  const mayReadHistory = iAmApprover || Boolean(principal?.roles.includes("dpo"));

  const [showHistory, setShowHistory] = useState(false);
  const history = useApproverHistory(department.id, showHistory && mayReadHistory);

  const [personId, setPersonId] = useState("");
  const [reason, setReason] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [removing, setRemoving] = useState<string | null>(null);
  const [removeReason, setRemoveReason] = useState("");

  // Only people who hold the Data custodian role today and are not already approvers, as the platform works it out.
  const candidatesQuery = useApproverCandidates(department.id, iAmApprover);
  const candidates = candidatesQuery.data?.candidates ?? [];

  return (
    <div data-testid={`department-${department.id}`} className="rounded border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-base font-semibold">{department.name}</h3>
        <span className="text-xs text-slate-500">
          {department.datasets} {department.datasets === 1 ? "dataset" : "datasets"}
        </span>
      </div>

      <h4 className="mt-3 text-sm font-medium text-slate-700">Approvers</h4>
      <ul className="mt-1 divide-y divide-slate-100 text-sm" data-testid={`approvers-${department.id}`}>
        {department.approvers.map((a) => (
          <li key={a.person_id} data-approver={a.person_id} className="flex flex-wrap items-center justify-between gap-2 py-2">
            <span>
              <span className="font-medium">{a.label}</span>{" "}
              <span className="text-slate-500">
                {a.valid_until ? `covering until ${day(a.valid_until)}` : "permanent"}, since {day(a.added_at)}
                {!a.holds_role && (
                  <span data-testid={`dormant-${a.person_id}`} className="text-amber-800">
                    . Does not hold the Data custodian role now, so cannot act until it is held again
                  </span>
                )}
              </span>
            </span>
            {iAmApprover && removing !== a.person_id && (
              <button
                type="button"
                data-testid={`remove-${department.id}-${a.person_id}`}
                onClick={() => {
                  setRemoving(a.person_id);
                  setRemoveReason("");
                }}
                className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
              >
                Remove
              </button>
            )}
            {iAmApprover && removing === a.person_id && (
              <form
                className="flex flex-wrap items-center gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  remove.mutate(
                    { departmentId: department.id, personId: a.person_id, reason: removeReason.trim() },
                    {
                      onSuccess: () => {
                        setRemoving(null);
                        notify(`${a.label} is no longer an approver of ${department.name}.`);
                      },
                    },
                  );
                }}
              >
                <input
                  data-testid={`remove-reason-${department.id}`}
                  value={removeReason}
                  onChange={(e) => setRemoveReason(e.target.value)}
                  placeholder="Why is this approver being removed?"
                  className="w-64 rounded border border-slate-300 px-2 py-1 text-xs"
                />
                <button
                  type="submit"
                  data-testid={`confirm-remove-${department.id}`}
                  disabled={!removeReason.trim() || remove.isPending}
                  className="rounded bg-red-700 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                >
                  Confirm removal
                </button>
                <button type="button" onClick={() => setRemoving(null)} className="text-xs text-slate-600 underline">
                  Cancel
                </button>
              </form>
            )}
          </li>
        ))}
      </ul>
      {remove.error && (
        <p role="alert" data-testid={`remove-error-${department.id}`} className="mt-2 text-xs text-red-800">
          {remove.error instanceof Error ? sentence(remove.error.message) : "That did not work."}
        </p>
      )}

      {iAmApprover && workingApprovers.length === 1 && (
        <p data-testid={`single-approver-${department.id}`} className="mt-2 text-xs text-amber-800">
          {department.name} has one approver. A claim about this department&apos;s data that the approver makes cannot be confirmed until a second
          approver is added.
        </p>
      )}

      {iAmApprover ? (
        <form
          data-testid={`add-form-${department.id}`}
          className="mt-4 space-y-2 rounded border border-slate-200 bg-slate-50 p-3"
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate(
              {
                departmentId: department.id,
                personId,
                reason: reason.trim(),
                // The end of the chosen day in the signed-in person's own time, not in UTC: cover that ends "on the 19th" must not show as the 20th.
                validUntil: endsOn ? new Date(`${endsOn}T23:59:59`).toISOString() : undefined,
              },
              {
                onSuccess: () => {
                  notify("Approver added.");
                  setPersonId("");
                  setReason("");
                  setEndsOn("");
                },
              },
            );
          }}
        >
          <h4 className="text-sm font-medium text-slate-700">Add an approver</h4>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-xs text-slate-600">
              Person
              <select
                data-testid={`add-person-${department.id}`}
                value={personId}
                onChange={(e) => setPersonId(e.target.value)}
                className="rounded border border-slate-300 px-2 py-1 text-sm"
              >
                <option value="">Choose a data custodian</option>
                {candidates.map((p) => (
                  <option key={p.person_id} value={p.person_id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-slate-600">
              Why
              <input
                data-testid={`add-reason-${department.id}`}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Covers while a custodian is on leave"
                className="w-64 rounded border border-slate-300 px-2 py-1 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-slate-600">
              Cover ends on (leave empty for a permanent approver)
              <input
                type="date"
                data-testid={`add-ends-${department.id}`}
                value={endsOn}
                onChange={(e) => setEndsOn(e.target.value)}
                className="rounded border border-slate-300 px-2 py-1 text-sm"
              />
            </label>
            <button
              type="submit"
              data-testid={`add-submit-${department.id}`}
              disabled={!personId || !reason.trim() || add.isPending}
              className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              Add approver
            </button>
          </div>
          <p className="text-xs text-slate-500">
            Only people who already hold the Data custodian role are listed. Somebody else has to ask for that role first.
          </p>
          {add.error && (
            <p role="alert" data-testid={`add-error-${department.id}`} className="text-xs text-red-800">
              {add.error instanceof Error ? sentence(add.error.message) : "That did not work."}
            </p>
          )}
        </form>
      ) : (
        <p className="mt-3 text-xs text-slate-500">Only an approver of this department can change this list.</p>
      )}

      {mayReadHistory && (
        <div className="mt-3">
          <button
            type="button"
            data-testid={`history-toggle-${department.id}`}
            onClick={() => setShowHistory((v) => !v)}
            className="text-xs text-sky-700 underline"
          >
            {showHistory ? "Hide history" : "Show history"}
          </button>
          {showHistory &&
            (history.isLoading ? (
              <Loading what="history" />
            ) : history.error ? (
              <Failure error={history.error} what="the history" />
            ) : (
              <table className="mt-2 w-full text-left text-xs" data-testid={`history-${department.id}`}>
                <thead className="text-slate-500">
                  <tr>
                    <th className="py-1">Approver</th>
                    <th>Added</th>
                    <th>Ended</th>
                  </tr>
                </thead>
                <tbody>
                  {(history.data?.history ?? []).map((h, i) => (
                    <tr key={`${h.person_id}-${i}`} className="border-t border-slate-100 align-top">
                      <td className="py-1 font-medium">{h.label}</td>
                      <td>
                        {day(h.added_at)} by {h.added_by_label ?? h.added_by}: {h.reason}
                        {h.valid_until ? `, covering until ${day(h.valid_until)}` : ""}
                      </td>
                      <td>
                        {h.removed_at
                          ? `${day(h.removed_at)} by ${h.removed_by_label ?? h.removed_by}: ${h.removal_reason ?? ""}`
                          : h.valid_until && new Date(h.valid_until) < new Date()
                            ? `${day(h.valid_until)}, cover ended`
                            : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ))}
        </div>
      )}
    </div>
  );
}
