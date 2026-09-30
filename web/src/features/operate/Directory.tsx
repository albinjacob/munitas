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

import { usePolicyRoles } from "../../api/queries";
import { roleLabel } from "../../api/roles";
import { Loading, Section } from "../../components/states";
import { useIdentity } from "../../identity/IdentityContext";
import { RolesHeld } from "./RolesHeld";

export function Directory() {
  const { principals: active, retained, loading } = useIdentity();
  const policy = usePolicyRoles();

  if (loading) return <Loading what="the directory" />;


  function approves(roles: string[]): boolean {
    return Boolean(
      policy.data && roles.some((r) => policy.data!.approver_roles.includes(r)),
    );
  }

  return (
    <>
      <Section
        level="page"
        title="Directory"
        description="Who the organisation registered. Approvals reference these entries, which is why an entry cannot simply be removed."
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
              {active.map((p) => (
                <tr key={p.id} data-person={p.id} className="border-t border-slate-100 transition-colors hover:bg-slate-50">
                  <td className="px-4 py-2">
                    <span className="font-medium">{p.label}</span>
                    <code className="ml-2 font-mono text-[11px] text-slate-400">
                      {p.id}
                    </code>
                  </td>
                  <td>{p.roles.map(roleLabel).join(", ")}</td>
                  <td>
                    {p.department_name ?? (
                      <span className="text-xs text-slate-400">none</span>
                    )}
                  </td>
                  <td>
                    {approves(p.roles) ? (
                      <span className="rounded bg-teal-100 px-2 py-0.5 text-xs font-medium text-teal-900">
                        {p.department_name} only
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
                {retained.map((p) => (
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
