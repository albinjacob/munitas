/**
 * Legal holds: an instruction from outside the platform to keep an organisation's
 * records, and the deletions that the platform has carried out.
 *
 * A hold reaches a platform administrator in writing, from a court, a regulator or
 * the organisation's lawyers. One administrator records it here, and a different
 * one approves it. This page draws the buttons for both and the platform refuses
 * the one who should not press them, with the reason, so an administrator who tries
 * to approve their own hold sees exactly why they cannot.
 */

import { useState } from "react";
import {
  useDecideHold,
  useDeletions,
  useHolds,
  useOrganisations,
  usePlaceHold,
  useReleaseHold,
  type Hold,
} from "../../api/lifecycle";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { notify } from "../../components/toast";
import { useIdentity } from "../../identity/IdentityContext";
import { bare, when } from "./dates";
import { HoldExports } from "./HoldExports";

const STATUS_COPY: Record<Hold["status"], { label: string; tone: string }> = {
  proposed: { label: "Waiting for a second administrator", tone: "bg-amber-100 text-amber-900" },
  active: { label: "In force", tone: "bg-red-100 text-red-900" },
  declined: { label: "Declined", tone: "bg-slate-200 text-slate-800" },
  lapsed: { label: "Lapsed, nobody approved it in time", tone: "bg-slate-200 text-slate-800" },
  released: { label: "Released", tone: "bg-slate-200 text-slate-800" },
};

export function LegalHolds() {
  const { principal } = useIdentity();
  const holds = useHolds(Boolean(principal));
  const deletions = useDeletions(Boolean(principal));

  if (!principal) return <Loading what="your identity" />;
  if (holds.isLoading) return <Loading what="legal holds" />;
  if (holds.error) return <Failure error={holds.error} what="legal holds" />;

  const all = holds.data?.holds ?? [];
  const waiting = all.filter((h) => h.status === "proposed");
  const inForce = all.filter((h) => h.status === "active");
  const ended = all.filter((h) => !["proposed", "active"].includes(h.status));

  return (
    <>
      <Section
        level="page"
        title="Legal holds"
        description="A legal hold keeps a whole organisation's records and stops them being deleted. It is recorded by one platform administrator and approved by a different one."
      >
        {null}
      </Section>

      <Section title="Waiting for a second administrator" description="Already stops the organisation being deleted, until it is decided or lapses.">
        {waiting.length === 0 ? (
          <Empty what="holds waiting" hint="Nothing is waiting for a decision." />
        ) : (
          <div data-testid="holds-waiting" className="space-y-3">
            {waiting.map((h) => (
              <HoldCard key={h.id} hold={h} mine={h.placed_by === principal.id} />
            ))}
          </div>
        )}
      </Section>

      <Section title="In force" description="Nothing in the organisation is deleted while one of these stands. Each is reviewed by the date shown.">
        {inForce.length === 0 ? (
          <Empty what="holds in force" />
        ) : (
          <div data-testid="holds-in-force" className="space-y-3">
            {inForce.map((h) => (
              <HoldCard key={h.id} hold={h} mine={h.placed_by === principal.id} />
            ))}
          </div>
        )}
      </Section>

      <PlaceHold />

      {ended.length > 0 && (
        <Section title="Ended">
          <div className="space-y-3">
            {ended.map((h) => (
              <HoldCard key={h.id} hold={h} mine={false} />
            ))}
          </div>
        </Section>
      )}

      <Section
        title="Deleted organisations"
        description="What is kept after an organisation has been deleted. It names no contact detail and holds none of the organisation's records."
      >
        {deletions.isLoading ? (
          <Loading what="deletion records" />
        ) : deletions.error ? (
          <Failure error={deletions.error} what="the deletion records" />
        ) : (deletions.data?.deletions.length ?? 0) === 0 ? (
          <Empty what="deletions" hint="No organisation has been deleted yet." />
        ) : (
          <div data-testid="deletion-records" className="space-y-3">
            {deletions.data!.deletions.map((d) => (
              <div key={d.tenant_id} data-deletion={d.original_tenant_id ?? d.tenant_id} className="rounded border border-slate-200 bg-white p-3 text-sm">
                <div className="font-medium">{d.original_tenant_id ?? d.tenant_id}</div>
                <p className="mt-1 text-xs text-slate-500">
                  Filed under <code>{d.tenant_id}</code>, so the name {d.original_tenant_id ?? d.tenant_id} can be used again.
                </p>
                <p className="mt-1 text-slate-600">
                  Closing down was asked for by {d.retire_requested_by ?? "somebody"} on {when(d.retired_at)}
                  {d.retire_reason ? `, because: ${bare(d.retire_reason)}` : ""}. Deleted on {when(d.purged_at)} by{" "}
                  {d.purged_by}.
                </p>
                <p className="mt-1 text-slate-600">
                  Removed {Object.values(d.rows_removed).reduce((a, b) => a + b, 0)} records in{" "}
                  {Object.keys(d.rows_removed).length} kinds, and {d.files_removed} stored files.
                  {d.buckets_left.length > 0 && ` ${d.buckets_left.length} external storage bucket was left for a person to remove.`}{" "}
                  Removed {d.identities_removed} sign-in accounts.
                </p>
                <p className="mt-1 text-slate-600">
                  {d.audit_rows_kept === 0
                    ? "There were no audit rows of who read what to keep."
                    : d.audit_removed_at
                      ? `${d.audit_rows_kept} audit rows of who read what were kept until ${when(d.audit_kept_until)} and have been removed.`
                      : `${d.audit_rows_kept} audit rows of who read what are kept until ${when(d.audit_kept_until)}, and then removed.`}
                </p>
                {d.holds.length > 0 && (
                  <p className="mt-1 text-slate-600">
                    Legal holds that applied:{" "}
                    {d.holds.map((h) => `${h.matter_number} (${h.issuing_authority}, ${h.status})`).join("; ")}.
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}

function Field({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="text-slate-800">{value}</dd>
    </div>
  );
}

function HoldCard({ hold, mine }: { hold: Hold; mine: boolean }) {
  const [note, setNote] = useState("");
  const [releasing, setReleasing] = useState(false);
  const decide = useDecideHold(hold.id);
  const release = useReleaseHold(hold.id);
  const copy = STATUS_COPY[hold.status];

  return (
    <div data-hold={hold.matter_number} className="rounded border border-slate-200 bg-white p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">
          {hold.matter_number}: {hold.matter_name}
        </span>
        <span className={`rounded px-2 py-0.5 text-xs font-medium ${copy.tone}`}>{copy.label}</span>
        <span className="text-slate-500">on {hold.tenant_id}</span>
      </div>
      <dl className="mt-3 grid gap-x-6 gap-y-2 md:grid-cols-2">
        <Field label="Issued by" value={`${hold.issuing_authority}, reference ${hold.authority_reference}`} />
        <Field label="Contact" value={`${hold.attorney_name}, ${hold.attorney_email}`} />
        <Field label="What triggered it" value={hold.triggering_event} />
        <Field label="Notice received" value={when(hold.notice_received_on)} />
        <Field label="What must be kept" value={hold.preserve} />
        <Field
          label="Records covered"
          value={hold.data_from || hold.data_to ? `${when(hold.data_from) || "the start"} to ${when(hold.data_to) || "now"}` : "All of them"}
        />
        <Field
          label="Temporary custodian"
          value={`${hold.custodian_label}${hold.custodian_acknowledged_at ? `, acknowledged ${when(hold.custodian_acknowledged_at)}` : ", has not acknowledged it yet"}`}
        />
        <Field label="Recorded by" value={`${hold.placed_by_label} on ${when(hold.placed_at)}`} />
        {hold.decided_by_label && (
          <Field
            label={hold.status === "declined" ? "Declined by" : "Approved by"}
            value={`${hold.decided_by_label} on ${when(hold.decided_at)}${hold.decision_note ? `: ${hold.decision_note}` : ""}`}
          />
        )}
        {hold.status === "active" && <Field label="Review by" value={when(hold.review_due_on)} />}
        {hold.status === "proposed" && <Field label="Lapses if not approved by" value={when(hold.expires_unapproved_at)} />}
        {hold.released_by_label && (
          <Field label="Released by" value={`${hold.released_by_label} on ${when(hold.released_at)}: ${hold.release_reason}`} />
        )}
      </dl>

      {hold.status === "proposed" && (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="grow text-sm">
            <span className="block text-slate-600">A note, which is recorded with the decision (required to decline)</span>
            <input
              data-testid="hold-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
            />
          </label>
          <button
            type="button"
            data-testid="hold-approve"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ approve: true, note }, { onSuccess: () => notify("The legal hold is in force.") })}
            className="rounded bg-green-700 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
          >
            {decide.isPending ? "Approving" : "Approve it"}
          </button>
          <button
            type="button"
            data-testid="hold-decline"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ approve: false, note }, { onSuccess: () => notify("The legal hold is declined.") })}
            className="rounded bg-slate-200 px-3 py-1.5 text-sm font-medium text-slate-800 disabled:bg-slate-100"
          >
            Decline
          </button>
        </div>
      )}
      {hold.status === "proposed" && mine && (
        <p className="mt-2 text-xs text-slate-500">You recorded this one, so a different administrator approves it.</p>
      )}
      {decide.error && (
        <div className="mt-2">
          <Failure error={decide.error} what="this hold" verb="decide" />
        </div>
      )}

      {hold.status === "active" && (
        <div className="mt-3">
          <button
            type="button"
            data-testid="hold-release"
            onClick={() => setReleasing(true)}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-800"
          >
            Release this hold
          </button>
          <p className="mt-1 text-xs text-slate-500">
            Releasing starts the closing period again, so a release made by mistake still leaves 15 days.
          </p>
          {release.error && (
            <div className="mt-2">
              <Failure error={release.error} what="this hold" verb="release" />
            </div>
          )}
        </div>
      )}
      {hold.status === "active" && <HoldExports holdId={hold.id} />}
      <ConfirmDialog
        open={releasing}
        title={`Release ${hold.matter_number}?`}
        description="The organisation's records are no longer protected by this hold. The closing period starts again, and everything inside is deleted when it ends unless another hold stands."
        confirmLabel="Release it"
        destructive
        reasonLabel="Why it is released, which is recorded"
        onCancel={() => setReleasing(false)}
        onConfirm={(reason) =>
          release.mutate(
            { reason },
            { onSuccess: () => { setReleasing(false); notify("The legal hold is released."); }, onError: () => setReleasing(false) },
          )
        }
      />
    </div>
  );
}

function PlaceHold() {
  const { everyone } = useIdentity();
  const orgs = useOrganisations(true);
  const place = usePlaceHold();
  const empty = {
    tenant_id: "", matter_name: "", matter_number: "", description: "", triggering_event: "",
    issuing_authority: "", authority_reference: "", attorney_name: "", attorney_email: "",
    notice_received_on: "", preserve: "", data_from: "", data_to: "", custodian_id: "",
  };
  const [form, setForm] = useState(empty);
  const set = (k: keyof typeof empty) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const people = everyone.filter((p) => p.kind === "human" && p.tenant_id === form.tenant_id);

  const input = "mt-1 w-full rounded border border-slate-300 px-2 py-1";
  const ready = Object.entries(form).every(([k, v]) => k === "data_from" || k === "data_to" || v.trim());

  return (
    <Section
      title="Record a legal hold"
      description="Copy the notice you received. Every part of it is kept with the hold, and a different platform administrator then approves it."
    >
      <form
        data-testid="place-hold"
        className="grid gap-3 rounded border border-slate-200 bg-white p-4 text-sm md:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          const { data_from, data_to, ...rest } = form;
          place.mutate(
            { ...rest, ...(data_from ? { data_from } : {}), ...(data_to ? { data_to } : {}) },
            { onSuccess: () => { setForm(empty); notify("The legal hold is recorded and waits for a second administrator."); } },
          );
        }}
      >
        <label>
          <span className="block text-slate-600">Organisation it applies to</span>
          <select data-testid="hold-tenant" value={form.tenant_id} onChange={set("tenant_id")} className={input}>
            <option value="">Choose one</option>
            {(orgs.data?.organisations ?? []).filter((o) => o.phase !== "purged").map((o) => (
              <option key={o.tenant_id} value={o.tenant_id}>{o.tenant_id}</option>
            ))}
          </select>
        </label>
        <label>
          <span className="block text-slate-600">Temporary custodian, who answers for the records and acknowledges the hold</span>
          <select data-testid="hold-custodian" value={form.custodian_id} onChange={set("custodian_id")} className={input}>
            <option value="">{form.tenant_id ? "Choose a person" : "Choose the organisation first"}</option>
            {people.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
        </label>
        <label>
          <span className="block text-slate-600">Matter name</span>
          <input data-testid="hold-matter-name" value={form.matter_name} onChange={set("matter_name")} className={input} />
        </label>
        <label>
          <span className="block text-slate-600">Matter number</span>
          <input data-testid="hold-matter-number" value={form.matter_number} onChange={set("matter_number")} className={input} />
        </label>
        <label>
          <span className="block text-slate-600">Issued by (court, regulator or law firm)</span>
          <input data-testid="hold-authority" value={form.issuing_authority} onChange={set("issuing_authority")} className={input} />
        </label>
        <label>
          <span className="block text-slate-600">Their reference for the notice</span>
          <input data-testid="hold-reference" value={form.authority_reference} onChange={set("authority_reference")} className={input} />
        </label>
        <label>
          <span className="block text-slate-600">Contact at the issuing authority</span>
          <input data-testid="hold-attorney" value={form.attorney_name} onChange={set("attorney_name")} className={input} />
        </label>
        <label>
          <span className="block text-slate-600">Their email address</span>
          <input data-testid="hold-attorney-email" value={form.attorney_email} onChange={set("attorney_email")} className={input} />
        </label>
        <label>
          <span className="block text-slate-600">What triggered the notice</span>
          <input data-testid="hold-trigger" value={form.triggering_event} onChange={set("triggering_event")} className={input} />
        </label>
        <label>
          <span className="block text-slate-600">Date the notice was received</span>
          <input data-testid="hold-received" type="date" value={form.notice_received_on} onChange={set("notice_received_on")} className={input} />
        </label>
        <label className="md:col-span-2">
          <span className="block text-slate-600">What the matter is about</span>
          <textarea data-testid="hold-description" rows={2} value={form.description} onChange={set("description")} className={input} />
        </label>
        <label className="md:col-span-2">
          <span className="block text-slate-600">What must be kept</span>
          <textarea data-testid="hold-preserve" rows={2} value={form.preserve} onChange={set("preserve")} className={input} />
        </label>
        <label>
          <span className="block text-slate-600">Records from (optional)</span>
          <input type="date" value={form.data_from} onChange={set("data_from")} className={input} />
        </label>
        <label>
          <span className="block text-slate-600">Records to (optional)</span>
          <input type="date" value={form.data_to} onChange={set("data_to")} className={input} />
        </label>
        <div className="md:col-span-2">
          <button
            type="submit"
            data-testid="hold-submit"
            disabled={!ready || place.isPending}
            className="rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
          >
            {place.isPending ? "Recording" : "Record the legal hold"}
          </button>
          {place.error && (
            <div className="mt-3">
              <Failure error={place.error} what="this legal hold" verb="record" />
            </div>
          )}
        </div>
      </form>
    </Section>
  );
}
