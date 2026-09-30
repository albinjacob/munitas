/**
 * Every role, and what it can reach.
 *
 * The floors and the approving roles are **fetched from the policy engine**,
 * not restated here. A page that describes the rules and disagrees with them is
 * worse than no page, because it is believed, and this project has already had
 * a screen and a policy drift apart once.
 *
 * Readable without choosing a persona. It describes the rules rather than
 * revealing any data, and needing an identity before you can read what the
 * identities mean is backwards.
 *
 * The Internal section is hidden once a customer persona is confirmed
 * acting, the same mechanism (and the same non-guarantee) `AppShell.tsx`'s
 * nav already uses: `roles` decides relevance, not permission. There is no
 * authentication in this console (`IdentityContext.tsx`'s `authenticated` is
 * always `false`), so this cannot be a real access boundary, only a default
 * that follows whichever persona is currently chosen. Anyone can switch
 * persona and see it, the same as anyone can already type `/services`
 * directly regardless of who is acting. Nobody having chosen a persona yet
 * is not the same as a customer looking, so the neutral pre-choice state
 * keeps the page complete, matching its own stated purpose above.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { VisibilityClass } from "../../api/types";
import { ClassBadge } from "../../components/ClassBadge";
import { Failure, Loading, Section } from "../../components/states";
import { roleLabel } from "../../api/roles";
import { SERVICES } from "../../identity/services";
import { useIdentity } from "../../identity/IdentityContext";

interface PolicyRoles {
  class_order: Record<VisibilityClass, number>;
  role_floor: Record<string, number>;
  approver_roles: string[];
}

/**
 * Roles held by whoever runs or supports the platform itself, not by
 * whoever the platform governs data on behalf of. `access.rego` already
 * describes both this way ("runs the system", "support and reliability
 * work"), so this reuses that distinction rather than inventing a new one.
 */
const INTERNAL_ROLES = new Set(["platform_admin", "hybridops"]);

/** Every other human role: the customer's own staff. */
const CUSTOMER_ROLES = new Set([
  "data_custodian",
  "dpo",
  "notebook_explore",
  "pipeline_operator",
  "analyst",
]);

/** Which roles belong to people. Everything else is a workload. */
const HUMAN_ROLES = new Set([...CUSTOMER_ROLES, ...INTERNAL_ROLES]);

function floorClass(
  floor: number,
  order: Record<string, number>,
): VisibilityClass {
  const found = Object.entries(order).find(([, v]) => v === floor);
  return (found?.[0] as VisibilityClass) ?? "PUBLISHED";
}

function RoleTable({
  roles,
  data,
  caption,
}: {
  roles: string[];
  data: PolicyRoles;
  caption: string;
}) {
  return (
    <div className="overflow-x-auto rounded border border-slate-200 bg-white">
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
          <tr>
            <th className="px-4 py-2">Role</th>
            <th>Reads down to</th>
            <th>May approve access</th>
          </tr>
        </thead>
        <tbody>
          {roles.map((role) => {
            const klass = floorClass(data.role_floor[role], data.class_order);
            const approves = data.approver_roles.includes(role);
            return (
              <tr
                key={role}
                data-role={role}
                data-floor={klass}
                className="border-t border-slate-100 transition-colors hover:bg-slate-50"
              >
                <td className="px-4 py-2">
                  <span className="font-medium">{roleLabel(role)}</span>
                  <code className="ml-2 font-mono text-[11px] text-slate-400">
                    {role}
                  </code>
                </td>
                <td>
                  <ClassBadge value={klass} />
                </td>
                <td>
                  {approves ? (
                    <span className="rounded bg-teal-100 px-2 py-0.5 text-xs font-medium text-teal-900">
                      yes, for their own department
                    </span>
                  ) : (
                    <span className="text-xs text-slate-400">no</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function Roles() {
  const { principal } = useIdentity();
  const { data, isLoading, error } = useQuery({
    queryKey: ["policy-roles"],
    queryFn: () => api.get<PolicyRoles>("/policy/roles"),
  });

  if (isLoading) return <Loading what="the roles" />;
  if (error) return <Failure error={error} what="the roles" />;
  if (!data) return null;

  const all = Object.keys(data.role_floor).sort();
  const customer = all.filter((r) => CUSTOMER_ROLES.has(r));
  const internal = all.filter((r) => INTERNAL_ROLES.has(r));
  const workload = all.filter((r) => !HUMAN_ROLES.has(r));

  // Hidden only once a customer persona is confirmed, not by default. Nobody
  // having chosen yet is not the same as a customer being the one looking:
  // this page's whole purpose is to explain every role before anyone picks
  // one (see the module comment), so the neutral state stays complete.
  const confirmedCustomer =
    Boolean(principal) && !principal!.roles.some((r) => INTERNAL_ROLES.has(r));

  return (
    <div className="mx-auto max-w-4xl px-4 py-10" data-testid="roles-page">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Application roles</h1>
          <p className="mt-2 max-w-2xl text-slate-600">
            What each role may read without asking, and who may grant the rest.
            This page is generated from the rules themselves, so it cannot say
            one thing while the system does another.
          </p>
        </div>
        {principal ? (
          <Link to="/" className="shrink-0 text-sm text-sky-700 underline">
            Back to the console
          </Link>
        ) : (
          <Link to="/auth/login" className="shrink-0 text-sm text-sky-700 underline">
            Back to sign in
          </Link>
        )}
      </div>

      <div className="mt-8">
        <Section
          title="Customer"
          description="Every human role reads only published data without a lease. That is the design: people reach data through a workspace holding a credential for them, and anything more sensitive is approved by somebody else."
        >
          <RoleTable roles={customer} data={data} caption="Customer-facing roles" />
        </Section>

        {!confirmedCustomer && (
          <Section
            title="Internal"
            description="Runs and supports the platform itself, not the data it governs on a customer's behalf. Holds no more standing access to customer data than any other role. Hidden once you are acting as a customer persona, the same way the sidebar's own links vary by persona rather than by any real permission check."
          >
            <RoleTable roles={internal} data={data} caption="Internal roles" />
          </Section>
        )}

        <Section
          title="Services"
          description="You cannot act as one of these. Only services reach below published without a lease, because they run inside the boundary and produce the thing everyone else waits for. Also called a principal in the policy engine and the audit log."
        >
          <RoleTable roles={workload} data={data} caption="Workload roles" />
          <ul className="mt-4 space-y-2">
            {SERVICES.map((s) => (
              <li key={s.id} className="text-sm text-slate-600">
                <span className="font-medium text-slate-800">{s.label}</span>{" "}
                {s.description}
              </li>
            ))}
          </ul>
        </Section>

        <Section
          title="How access below your floor is granted"
          description="A lease, and only a lease."
        >
          <ol className="list-decimal space-y-2 rounded border border-slate-200 bg-white p-4 pl-8 text-sm text-slate-700">
            <li>
              You ask for one version of one dataset, saying what you need it
              for and for how long. Versions of the same dataset can differ in
              sensitivity, so the version is what is decided on.
            </li>
            <li>
              The custodian of the department that owns the data approves it.
              Nobody else can, including administrators, and you cannot approve
              your own.
            </li>
            <li>
              The lease expires on its own. Both the request and the decision are
              recorded, refusals included.
            </li>
          </ol>
        </Section>
      </div>
    </div>
  );
}
