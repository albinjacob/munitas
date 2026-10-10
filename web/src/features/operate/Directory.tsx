/**
 * Everyone the organisation registered, including people who can no longer act.
 *
 * The chooser answers "who can I usefully be". This answers "who is registered",
 * which is a different question and the one an administrator or a data
 * protection officer actually asks.
 *
 * People who hold no appointment are shown rather than filtered. They are here
 * because they approved something and the foreign key on `access_lease` refuses
 * to remove anyone whose approval is on record. That is the constraint doing its
 * job: deleting them would leave a lease whose authorisation cannot be
 * attributed, and who authorised something is the one fact an audit cannot lose.
 */

import { useState } from "react";
import { usePolicyRoles } from "../../api/queries";
import { roleLabel } from "../../api/roles";
import { useHeaderSearch } from "../../components/HeaderSearch";
import { Loading, Section } from "../../components/states";
import { useIdentity } from "../../identity/IdentityContext";
import { RolesHeld } from "./RolesHeld";

export function Directory() {
  const { principals: all, retained: allRetained, loading, tenant } = useIdentity();
  // The platform administrator is offered the people of every organisation, because the legal-holds page needs them. This page is the
  // people of one organisation, so it narrows to the one the signed-in person belongs to.
  const active = all.filter((p) => p.tenant_id === tenant);
  const retained = allRetained.filter((p) => p.tenant_id === tenant);
  const policy = usePolicyRoles();
  const [q, setQ] = useState("");
  useHeaderSearch({ value: q, onChange: setQ, placeholder: "Search people by name, role or department", testid: "directory-search" });

  if (loading) return <Loading what="the directory" />;


  const words = q.trim().toLowerCase();
  const matches = (p: { id: string; label: string; roles: string[]; approver_of: string[] }) =>
    !words || `${p.label} ${p.id} ${p.roles.map(roleLabel).join(" ")} ${p.approver_of.join(" ")}`.toLowerCase().includes(words);
  const shownActive = active.filter(matches);
  const shownRetained = retained.filter(matches);

  function approves(roles: string[]): boolean {
    return Boolean(
      policy.data && roles.some((r) => policy.data!.approver_roles.includes(r)),
    );
  }

  return (
    <>
      <Section
        level="page"
        title="People and roles"
        description="Everybody registered in this organisation, the role each person holds, and the department each data custodian approves for."
      >
        <div className="overflow-x-auto rounded border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-2">Person</th>
                <th>Role</th>
                <th>Department</th>
                <th>May approve</th>
              </tr>
            </thead>
            <tbody data-testid="directory-rows">
              {words && shownActive.length === 0 && shownRetained.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-3 text-center text-slate-500">
                    Nobody matches that search.
                  </td>
                </tr>
              )}
              {shownActive.map((p) => (
                <tr key={p.id} data-person={p.id} className="border-t border-slate-100 transition-colors hover:bg-slate-50">
                  <td className="px-4 py-2">
                    <span className="font-medium">{p.label}</span>
                    <code className="ml-2 font-mono text-[11px] text-slate-400">
                      {p.id}
                    </code>
                  </td>
                  <td>{p.roles.map(roleLabel).join(", ")}</td>
                  <td>
                    {p.approver_of.length ? (
                      p.approver_of.join(", ")
                    ) : (
                      <span className="text-xs text-slate-400">none</span>
                    )}
                  </td>
                  <td>
                    {approves(p.roles) && p.approver_of.length ? (
                      <span className="rounded bg-teal-100 px-2 py-0.5 text-xs font-medium text-teal-900">
                        {p.approver_of.join(", ")} only
                      </span>
                    ) : (
                      <span className="text-xs text-slate-400">no</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {retained.length > 0 && (
        <Section
          title="Retained, no longer appointed"
          description="They cannot approve anything and are not offered as a choice. They stay because approvals on record point at them."
        >
          <div className="overflow-x-auto rounded border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <tbody data-testid="retained-rows">
                {shownRetained.map((p) => (
                  <tr
                    key={p.id}
                    data-person={p.id}
                    className="border-t border-slate-100 transition-colors hover:bg-slate-50"
                  >
                    <td className="px-4 py-2">
                      <span className="font-medium text-slate-600">{p.label}</span>
                      <code className="ml-2 font-mono text-[11px] text-slate-400">
                        {p.id}
                      </code>
                    </td>
                    <td className="text-slate-500">
                      {p.roles.map(roleLabel).join(", ")}
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-500">
                      Held no department when last seen, so approves nothing.
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-xs text-slate-500">
            Removing one of these is refused by the database, because a lease
            they approved would then name an approver that cannot be resolved.
            An organisation that wants them gone has to decide what happens to
            the approvals first, which is a decision rather than a cleanup.
          </p>
        </Section>
      )}

      {/* The next question this screen raises: not only who is registered
          and what they hold, but how that changed and who last checked it
          was still right. */}
      <RolesHeld />
    </>
  );
}
