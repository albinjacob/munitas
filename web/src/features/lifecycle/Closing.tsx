/**
 * Closing the organisation: where it stands, and the two things a person can do about it.
 *
 * Starting a closing and cancelling one are offered to everybody who is shown
 * this page, and the platform refuses anybody who may not, with the reason. A
 * member who presses the button is told that only a data custodian of the
 * organisation, or a platform administrator, may. That is the platform deciding,
 * not this file declining to draw a button.
 */

import { useState } from "react";
import {
  PHASE_COPY,
  useCancelClosing,
  useClosing,
  useOrganisations,
  useRetire,
  type Closing as ClosingStatus,
  type OrganisationRow,
} from "../../api/lifecycle";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { notify } from "../../components/toast";
import { useIdentity } from "../../identity/IdentityContext";
import { when } from "./dates";
import { CustodianExports } from "./HoldExports";

export function PhaseBadge({ phase }: { phase: ClosingStatus["phase"] }) {
  const copy = PHASE_COPY[phase];
  return (
    <span data-testid="phase-badge" className={`rounded px-2 py-0.5 text-xs font-medium ${copy.tone}`}>
      {copy.label}
    </span>
  );
}

/** The sentence under the badge: what is true now and what happens next. */
export function Standing({ status }: { status: ClosingStatus }) {
  const hold =
    status.hold === "active"
      ? " A legal hold is in force, so nothing will be deleted while it stands."
      : status.hold === "pending"
        ? " A legal hold is waiting for a second administrator, and nothing will be deleted while it waits."
        : "";
  if (status.phase === "retiring") {
    return (
      <p>
        Closing down started on {when(status.retired_at)}
        {status.requested_by ? `, asked for by ${status.requested_by}` : ""}. People can read until{" "}
        <strong>{when(status.retiring_until)}</strong> ({status.days_left} days left) and may cancel until then.
        After that, nobody can do anything until <strong>{when(status.closing_until)}</strong>, when everything
        inside is deleted.{hold}
      </p>
    );
  }
  if (status.phase === "closing") {
    return (
      <p>
        The time to cancel ended on {when(status.retiring_until)}. Everything inside is deleted on{" "}
        <strong>{when(status.closing_until)}</strong> ({status.days_left} days left).{hold}
      </p>
    );
  }
  return (
    <p>
      {PHASE_COPY[status.phase].plain}
      {hold}
    </p>
  );
}

export function Closing() {
  const { principal } = useIdentity();
  const own = useClosing(undefined, Boolean(principal));
  const isAdmin = Boolean(principal?.roles.includes("platform_admin"));
  const all = useOrganisations(isAdmin);
  const retire = useRetire();
  const cancel = useCancelClosing();
  const [asking, setAsking] = useState<string | null>(null);

  if (!principal) return <Loading what="your identity" />;
  if (own.isLoading) return <Loading what="the organisation" />;
  if (own.error) return <Failure error={own.error} what="where the organisation stands" />;
  const status = own.data!;

  return (
    <>
      <Section
        level="page"
        title="Closing down the organisation"
        description={
          isAdmin
            ? "Closing down an organisation takes two stages, and ends with everything inside it being deleted."
            : `Closing down ${status.tenant_id} takes two stages, and ends with everything inside it being deleted.`
        }
      >
        <ol data-testid="closing-stages" className="grid gap-3 text-sm md:grid-cols-3">
          <li className="rounded border border-slate-200 bg-white p-3">
            <div className="font-medium">1. Closing down, 15 days</div>
            <p className="mt-1 text-slate-600">
              Nothing can be added or changed. People can still read what the organisation holds, and a data
              custodian can cancel the closing down.
            </p>
          </li>
          <li className="rounded border border-slate-200 bg-white p-3">
            <div className="font-medium">2. Closed to its people, 15 days</div>
            <p className="mt-1 text-slate-600">
              Nobody in the organisation can do anything. Only a platform administrator can act, and only to
              place a legal hold.
            </p>
          </li>
          <li className="rounded border border-slate-200 bg-white p-3">
            <div className="font-medium">3. Deleted</div>
            <p className="mt-1 text-slate-600">
              Everything inside the organisation is deleted, unless a legal hold stands over it. A short record
              of the deletion is kept.
            </p>
          </li>
        </ol>
      </Section>

      {!isAdmin && (
      <Section title={`Where ${status.tenant_id} stands`}>
        <div data-testid="closing-status" className="rounded border border-slate-200 bg-white p-4 text-sm">
          <div className="mb-2 flex items-center gap-2">
            <PhaseBadge phase={status.phase} />
            {status.retire_reason && <span className="text-slate-500">Reason given: {status.retire_reason}</span>}
          </div>
          <Standing status={status} />

          {status.phase === "active" && (
            <div className="mt-4">
              <button
                type="button"
                data-testid="close-organisation"
                onClick={() => setAsking(status.tenant_id)}
                className="rounded bg-red-700 px-3 py-1.5 text-sm font-medium text-white"
              >
                Close down this organisation
              </button>
              <p className="mt-2 text-xs text-slate-500">
                Only a data custodian of the organisation, or a platform administrator, may close it down.
              </p>
            </div>
          )}

          {status.can_cancel && (
            <div className="mt-4">
              <button
                type="button"
                data-testid="cancel-closing"
                disabled={cancel.isPending}
                onClick={() =>
                  cancel.mutate({}, { onSuccess: () => notify("The closing is cancelled. The organisation is open again.") })
                }
                className="rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
              >
                {cancel.isPending ? "Cancelling" : "Cancel the closing down"}
              </button>
            </div>
          )}
          {cancel.error && (
            <div className="mt-3">
              <Failure error={cancel.error} what="the closing down" verb="cancel" />
            </div>
          )}
        </div>
      </Section>
      )}

      {isAdmin && (
        <Section
          title="Every organisation"
          description="For platform administrators. Dates and states only, never what an organisation holds."
        >
          {all.isLoading ? (
            <Loading what="organisations" />
          ) : all.error ? (
            <Failure error={all.error} what="the organisations" />
          ) : (all.data?.organisations.length ?? 0) === 0 ? (
            <Empty what="organisations" />
          ) : (
            <div className="overflow-x-auto rounded border border-slate-200 bg-white">
              <table className="w-full text-sm" data-testid="all-organisations">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                    <th className="p-3">Organisation</th>
                    <th className="p-3">Standing</th>
                    <th className="p-3">Next date</th>
                    <th className="p-3">Legal hold</th>
                    <th className="p-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {all.data!.organisations.map((o) => (
                    <Row key={o.tenant_id} org={o} onClose={() => setAsking(o.tenant_id)} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      )}

      {!isAdmin && <CustodianExports />}

      <ConfirmDialog
        open={asking !== null}
        title={`Close down ${asking ?? ""}?`}
        description={
          "Nothing is deleted yet. For 15 days people can read what the organisation holds and cancel. " +
          "For another 15 days nobody can do anything. Then everything inside it is deleted, unless a " +
          "legal hold stands over it."
        }
        confirmLabel="Close it down"
        destructive
        reasonLabel="Why it is being closed down, which is recorded"
        onCancel={() => setAsking(null)}
        onConfirm={(reason) => {
          const target = asking!;
          retire.mutate(
            { tenant_id: target === status.tenant_id ? undefined : target, reason },
            {
              onSuccess: () => {
                setAsking(null);
                notify(`${target} is closing down.`);
              },
              onError: () => setAsking(null),
            },
          );
        }}
      />
      {retire.error && <Failure error={retire.error} what="this organisation" verb="close down" />}
    </>
  );
}

function Row({ org, onClose }: { org: OrganisationRow; onClose: () => void }) {
  const cancel = useCancelClosing();
  const held = org.phase === "purge_due" && org.hold !== "none";
  const next =
    org.phase === "retiring" ? `Cancel by ${when(org.retiring_until)}` :
    org.phase === "closing" ? `Deleted on ${when(org.closing_until)}` :
    held ? "Not deleted while the hold stands" :
    org.phase === "purge_due" ? "At the next sweep" :
    org.phase === "purged" ? `Deleted ${when(org.purged_at)}` : "";
  return (
    <tr data-org={org.tenant_id} className="border-t border-slate-100">
      <td className="p-3 align-top">
        <div className="font-medium">{org.tenant_id}</div>
        {org.note && <div className="text-xs text-slate-500">{org.note}</div>}
      </td>
      <td className="p-3 align-top">
        {held ? (
          <span data-testid="phase-badge" className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900">
            Held, time is up
          </span>
        ) : (
          <PhaseBadge phase={org.phase} />
        )}
      </td>
      <td className="p-3 align-top text-slate-600">{next}</td>
      <td className="p-3 align-top text-slate-600">
        {org.hold === "active" ? "In force" : org.hold === "pending" ? "Waiting for approval" : "None"}
      </td>
      <td className="p-3 align-top">
        {org.phase === "active" && (
          <button type="button" onClick={onClose} className="rounded border border-red-300 px-2 py-1 text-xs text-red-800">
            Close down
          </button>
        )}
        {org.phase === "retiring" && (
          <button
            type="button"
            disabled={cancel.isPending}
            onClick={() =>
              cancel.mutate({ tenant_id: org.tenant_id }, { onSuccess: () => notify(`${org.tenant_id} is open again.`) })
            }
            className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-800"
          >
            Cancel closing down
          </button>
        )}
      </td>
    </tr>
  );
}
