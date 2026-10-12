/**
 * Roles somebody holds, asks pending, and what is overdue a look.
 *
 * Sits under the directory because it answers the next question that screen
 * raises: it says who is registered and what they hold, and this says how
 * that changes and who last checked it was still right.
 *
 * Nothing here decides anything. Every button asks the platform, and the
 * platform refuses with a reason that is shown as written rather than
 * translated into an apology. A researcher pressing Approve is refused by
 * the policy engine, not by this file declining to draw the button.
 */

import { useState } from "react";
import {
  useAskForRole,
  useAttestRole,
  useDecideRole,
  useRoles,
  type RoleAsk,
  type RoleHeld,
} from "../../api/roleGrants";
import { usePeople } from "../../api/people";
import { roleLabel } from "../../api/roles";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { notify } from "../../components/toast";
import { useIdentity } from "../../identity/IdentityContext";
import { when } from "../../lib/when";


/**
 * Whether anybody has confirmed this is still needed, and how long ago.
 *
 * Never confirmed is shown as exactly that rather than as a blank, because a
 * blank reads as missing data instead of as work nobody has done.
 */
function reviewState(grant: RoleHeld): { text: string; overdue: boolean } {
  if (grant.expired) return { text: "lapsed", overdue: true };
  if (!grant.attested_at) return { text: "never checked", overdue: true };
  return { text: `checked ${when(grant.attested_at)}`, overdue: false };
}

export function RolesHeld() {
  const { principal } = useIdentity();
  const roles = useRoles(Boolean(principal));

  if (!principal) return <Loading what="your identity" />;
  if (roles.isLoading) return <Loading what="roles" />;
  if (roles.error) return <Failure error={roles.error} what="roles" />;

  const held = roles.data?.held ?? [];
  const pending = roles.data?.pending ?? [];

  return (
    <>
      <Section
        title="Role requests waiting for a decision"
        description="Anybody may ask for a role. A data custodian, other than the person who asked, decides it."
      >
        {pending.length === 0 ? (
          <Empty what="role requests" hint="Nothing is waiting for a decision." />
        ) : (
          <div data-testid="role-asks" className="space-y-3">
            {pending.map((ask) => (
              <Ask key={ask.id} ask={ask} mine={ask.principal === principal.id} />
            ))}
          </div>
        )}
      </Section>

      <Section
        title="Roles held"
        description="Roles granted through a request. Each one lapses on the date shown and has to be requested again."
      >
        {held.length === 0 ? (
          <Empty
            what="roles"
            hint="Nobody holds a role granted this way yet."
          />
        ) : (
          <div className="data-card"><table className="data-table" data-testid="roles-held">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                <th>Person</th>
                <th>Role</th>
                <th>Granted by</th>
                <th>Lapses</th>
                <th>Last checked</th>
                <th>Still needed?</th>
              </tr>
            </thead>
            <tbody>
              {held.map((grant) => (
                <Held
                  key={grant.id}
                  grant={grant}
                  mine={grant.principal === principal.id}
                />
              ))}
            </tbody>
          </table></div>
        )}
      </Section>

      <AskForRole />
    </>
  );
}

function Ask({ ask, mine }: { ask: RoleAsk; mine: boolean }) {
  const [reason, setReason] = useState("");
  const decide = useDecideRole(ask.id);

  return (
    <div className="rounded border border-slate-200 bg-white p-3">
      <p className="text-sm">
        <span className="font-medium">{ask.label}</span> asks to hold{" "}
        <span className="font-medium">{roleLabel(ask.role)}</span> for{" "}
        {ask.requested_days} days.
      </p>
      <p className="mt-1 text-sm text-slate-600">{ask.justification}</p>

      {mine ? (
        // Shown rather than hidden, and said plainly. Hiding it would leave
        // somebody wondering why their own ask has no buttons.
        <p className="mt-2 text-xs text-slate-500">
          This is your own ask, so somebody else decides it.
        </p>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <label className="grow text-sm">
              <span className="block text-slate-600">
                Why, which is recorded with the decision
              </span>
              <input
                data-testid="decide-reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
              />
            </label>
            <button
              type="button"
              data-testid="decide-approve"
              disabled={!reason.trim() || decide.isPending}
              onClick={() =>
                decide.mutate(
                  { outcome: "approve", reason },
                  { onSuccess: () => notify(`${roleLabel(ask.role)} granted.`) },
                )
              }
              className="btn btn-primary"
            >
              {decide.isPending ? "Granting" : "Grant it"}
            </button>
            <button
              type="button"
              data-testid="decide-reject"
              disabled={!reason.trim() || decide.isPending}
              onClick={() =>
                decide.mutate(
                  { outcome: "reject", reason },
                  { onSuccess: () => notify("Refused.") },
                )
              }
              className="btn btn-secondary"
            >
              {decide.isPending ? "Refusing" : "Refuse"}
            </button>
          </div>
          {decide.error && (
            <div className="mt-2">
              <Failure error={decide.error} what="this decision" verb="record" />
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Held({ grant, mine }: { grant: RoleHeld; mine: boolean }) {
  const people = usePeople();
  const attest = useAttestRole(grant.id);
  const review = reviewState(grant);

  return (
    <tr className="border-b border-slate-100">
      <td className="py-2 pr-6 align-top font-medium">{grant.label}</td>
      <td className="py-2 pr-6 align-top">{roleLabel(grant.role)}</td>
      {/* Named, not identified. Every other table on this console shows a
          person rather than the id stored against the row. */}
      <td className="py-2 pr-6 align-top text-slate-600">
        {people.label(grant.approved_by)}
        <div className="text-xs text-slate-500">{when(grant.created_at)}</div>
      </td>
      <td className="py-2 pr-6 align-top text-slate-600">
        {when(grant.expires_at)}
      </td>
      <td className="py-2 pr-6 align-top">
        <span className={review.overdue ? "text-amber-800" : "text-slate-600"}>
          {review.text}
        </span>
      </td>
      <td className="py-2 align-top">
        {mine ? (
          <span className="text-xs text-slate-500">somebody else checks</span>
        ) : (
          <div className="flex gap-2">
            <button
              type="button"
              data-testid="attest-keep"
              disabled={attest.isPending}
              onClick={() =>
                attest.mutate(
                  { still_needed: true },
                  { onSuccess: () => notify("Marked still needed.") },
                )
              }
              className="btn btn-primary btn-sm"
            >
              {attest.isPending ? "Marking" : "Still needed"}
            </button>
            <button
              type="button"
              data-testid="attest-withdraw"
              disabled={attest.isPending}
              onClick={() =>
                attest.mutate(
                  { still_needed: false },
                  { onSuccess: () => notify("Withdrawn.") },
                )
              }
              className="btn btn-danger btn-sm"
            >
              {attest.isPending ? "Withdrawing" : "Withdraw"}
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}

/** The roles a person may ask for: every role a person can hold, except the one that administers roles. */
const REQUESTABLE_ROLES = ["ai_risk_owner", "analyst", "data_custodian", "deid_reviewer", "dpo", "network_architect", "notebook_explore", "pipeline_operator", "privacy_auditor"];

function AskForRole() {
  const [role, setRole] = useState("");
  const [justification, setJustification] = useState("");
  const ask = useAskForRole();

  return (
    <Section
      title="Ask for a role"
      description="Request a role for yourself. Somebody else decides it, and it lapses after a set time."
    >
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="block text-slate-600">Role</span>
          <select
            data-testid="ask-role"
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">Choose a role</option>
            {REQUESTABLE_ROLES.map((r) => (
              <option key={r} value={r}>
                {roleLabel(r)}
              </option>
            ))}
          </select>
        </label>
        <label className="grow text-sm">
          <span className="block text-slate-600">
            Why you need it, which is recorded with the request
          </span>
          <input
            data-testid="ask-reason"
            value={justification}
            onChange={(e) => setJustification(e.target.value)}
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
          />
        </label>
        <button
          type="button"
          data-testid="ask-submit"
          disabled={!role.trim() || !justification.trim() || ask.isPending}
          onClick={() =>
            ask.mutate({ role, justification }, {
              onSuccess: () => {
                setRole("");
                setJustification("");
              },
            })
          }
          className="btn btn-primary"
        >
          {ask.isPending ? "Sending" : "Send request"}
        </button>
      </div>
      {ask.error && (
        <div className="mt-3">
          <Failure error={ask.error} what="this request" verb="record" />
        </div>
      )}
    </Section>
  );
}
