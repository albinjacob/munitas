/**
 * Locking audit: how locking was used in this organisation.
 *
 * For the privacy auditor and the data protection officer. It shows who read locked columns and who was refused, which keys were made, opened
 * and destroyed, the leases, how each table's columns were treated, who was erased, and the small-group findings. It shows no name, value or
 * identifier from any table: the evidence is about use, so it can be read by people who may not read the data.
 */

import { useState, type ReactNode } from "react";
import { useIdentity } from "../../identity/IdentityContext";
import { useLockingAudit } from "../../api/locking";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { CLASSIFICATION_WORDS, TREATMENT_WORDS } from "./LockedColumns";

function when(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
}

const ACTION_WORDS: Record<string, string> = {
  make_org: "A key was made for the organisation",
  make_person: "A key was made for a person",
  unwrap: "A key was opened",
  destroy: "A key was destroyed",
};

function Table({ head, children, testid }: { head: string[]; children: ReactNode; testid: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid={testid}>
        <thead className="border-b border-slate-200 text-left text-slate-500">
          <tr>
            {head.map((h) => (
              <th key={h} className="py-2 pr-4 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

const KEYS_SHOWN = 25;

export function LockingAudit() {
  const { principal } = useIdentity();
  const [allKeys, setAllKeys] = useState(false);
  const may = principal?.roles.some((r) => r === "privacy_auditor" || r === "dpo") ?? false;
  const audit = useLockingAudit(may);

  if (!may) {
    return (
      <div className="p-6">
        <Section level="page" title="Locking audit">
          <p className="text-sm text-slate-700">This page is for the privacy auditor and the data protection officer.</p>
        </Section>
      </div>
    );
  }
  if (audit.isLoading) return <Loading what="the locking audit" />;
  if (audit.error) return <Failure error={audit.error} what="the locking audit" />;
  const a = audit.data!;

  return (
    <div className="p-6" data-testid="locking-audit">
      <Section
        level="page"
        title="Locking audit"
        description="How locked columns were used in this organisation. No name, value or identifier from any table appears here, and your reading of this page is recorded too."
      >
        <h2 className="mb-2 text-lg font-semibold text-slate-900">Who read locked columns, and who was refused</h2>
        {a.reads.length ? (
          <Table testid="audit-reads" head={["When", "Who", "Result", "What for"]}>
            {a.reads.map((r, i) => (
              <tr key={i} className="border-b border-slate-100 align-top">
                <td className="py-2 pr-4">{when(r.at)}</td>
                <td className="py-2 pr-4">{r.principal_label}</td>
                <td className="py-2 pr-4">
                  {r.allowed ? (
                    <span className="rounded bg-teal-100 px-1.5 py-0.5 text-xs font-medium text-teal-900">Read</span>
                  ) : (
                    <span className="rounded bg-red-100 px-1.5 py-0.5 text-xs font-medium text-red-900">Refused</span>
                  )}
                  {!r.allowed && r.reasons[0] && <div className="mt-1 max-w-xs text-xs text-slate-600">{r.reasons[0]}</div>}
                </td>
                <td className="py-2 pr-4 text-xs text-slate-600">
                  {r.purpose.replace(/^read locked columns:? ?/, "") || (r.allowed ? "as the owning department's approver" : "asked without a lease")}
                </td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty what="reads of locked columns yet" />
        )}

        <h2 className="mb-2 mt-8 text-lg font-semibold text-slate-900">Leases</h2>
        {a.leases.length ? (
          <Table testid="audit-leases" head={["Asked", "Who", "What for", "Rows and days", "State"]}>
            {a.leases.map((l) => (
              <tr key={l.id} className="border-b border-slate-100 align-top">
                <td className="py-2 pr-4">{when(l.requested_at)}</td>
                <td className="py-2 pr-4">{l.principal_label ?? l.principal}</td>
                <td className="py-2 pr-4 max-w-xs">{l.purpose}</td>
                <td className="py-2 pr-4">
                  {l.row_limit} / {l.days}
                </td>
                <td className="py-2 pr-4">
                  {l.status === "requested" ? "Waiting" : l.status === "approved" ? "Approved" : l.status === "declined" ? "Declined" : "Ended early"}
                  {l.decided_by_label ? ` by ${l.decided_by_label}` : ""}
                </td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty what="leases yet" />
        )}

        <h2 className="mb-2 mt-8 text-lg font-semibold text-slate-900">How each table's columns were treated</h2>
        {a.contracts.length ? (
          a.contracts.map((c) => (
            <div key={c.schema_id} className="mb-4" data-testid="audit-contract">
              <p className="text-sm font-medium text-slate-800">
                {c.name}, version {c.version}
              </p>
              <Table testid="audit-treatments" head={["Column", "What it is", "What was done to it"]}>
                {c.treatments.map((t) => (
                  <tr key={t.name} className="border-b border-slate-100">
                    <td className="py-1.5 pr-4 font-mono text-xs">{t.name}</td>
                    <td className="py-1.5 pr-4">{CLASSIFICATION_WORDS[t.classification]}</td>
                    <td className="py-1.5 pr-4">{TREATMENT_WORDS[t.treatment]}</td>
                  </tr>
                ))}
              </Table>
            </div>
          ))
        ) : (
          <Empty what="tables sealed under a privacy contract yet" />
        )}

        <h2 className="mb-2 mt-8 text-lg font-semibold text-slate-900">Small groups</h2>
        {a.group_sizes.length ? (
          <Table testid="audit-group-sizes" head={["Counted", "People", "In small groups", "Decision"]}>
            {a.group_sizes.map((g) => (
              <tr key={g.dataset_version_id} className="border-b border-slate-100 align-top">
                <td className="py-2 pr-4">{g.columns.length ? g.columns.join(", ") : "nothing to count"}</td>
                <td className="py-2 pr-4">{g.people}</td>
                <td className="py-2 pr-4">
                  {g.people_in_small_groups} (smaller than {g.threshold})
                </td>
                <td className="py-2 pr-4">
                  {g.decision === "accepted" ? "Risk accepted" : g.decision === "will_change" ? "Contract will change" : "Not decided"}
                  {g.decided_by_label ? ` by ${g.decided_by_label}` : ""}
                </td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty what="group-size findings yet" />
        )}

        <h2 className="mb-2 mt-8 text-lg font-semibold text-slate-900">Keys</h2>
        <p className="mb-2 text-sm text-slate-600">
          Each person's key is made when they first appear, opened when their locked values are read, and destroyed when they are erased.
        </p>
        {a.key_uses.length ? (
          <>
          <Table testid="audit-keys" head={["When", "What happened", "By", "Person code"]}>
            {(allKeys ? a.key_uses : a.key_uses.slice(0, KEYS_SHOWN)).map((k, i) => (
              <tr key={i} className="border-b border-slate-100">
                <td className="py-1.5 pr-4">{when(k.at)}</td>
                <td className="py-1.5 pr-4">{ACTION_WORDS[k.action] ?? k.action}</td>
                <td className="py-1.5 pr-4">{k.actor_label}</td>
                <td className="py-1.5 pr-4 font-mono text-xs">{k.person_code ?? ""}</td>
              </tr>
            ))}
          </Table>
          {a.key_uses.length > KEYS_SHOWN && (
            <button
              type="button"
              data-testid="audit-keys-toggle"
              onClick={() => setAllKeys(!allKeys)}
              className="mt-2 text-sm text-sky-700 underline"
            >
              {allKeys ? `Show only the latest ${KEYS_SHOWN}` : `Show all ${a.key_uses.length}`}
            </button>
          )}
          </>
        ) : (
          <Empty what="key uses yet" />
        )}

        <h2 className="mb-2 mt-8 text-lg font-semibold text-slate-900">People who were erased</h2>
        {a.erasures.length ? (
          <Table testid="audit-erasures" head={["When", "Person code", "Asked by"]}>
            {a.erasures.map((e, i) => (
              <tr key={i} className="border-b border-slate-100">
                <td className="py-1.5 pr-4">{when(e.at)}</td>
                <td className="py-1.5 pr-4 font-mono text-xs">{e.record_id.replace(/^person:/, "")}</td>
                <td className="py-1.5 pr-4">{e.requested_by_label}</td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty what="erasures yet" />
        )}
      </Section>
    </div>
  );
}
