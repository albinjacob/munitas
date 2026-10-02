/**
 * Producing records for a legal matter, from the two sides of it.
 *
 * `HoldExports` is for a platform administrator and sits under a legal hold in force: ask for records, approve
 * what a different administrator asked for, and make a download link for a package that is ready.
 * `CustodianExports` is for the person the hold names as custodian: confirm that the scope is what the demand asks
 * and no wider, and be given the package's passphrase once.
 *
 * Nobody on either side reads the records. A package is built by the platform, signed, and encrypted, so the
 * screens show names, sizes and hashes only.
 */

import { useState } from "react";
import { API_BASE } from "../../api/client";
import {
  EXPORT_COPY,
  useApproveExport,
  useConfirmExport,
  useExports,
  useMakeLink,
  useManifest,
  useReadPassphrase,
  useRequestExport,
  useScope,
  type LegalExport,
} from "../../api/legalExport";
import { Failure } from "../../components/states";
import { notify } from "../../components/toast";
import { useIdentity } from "../../identity/IdentityContext";
import { bare, when } from "./dates";

function megabytes(bytes: number | null): string {
  if (bytes === null) return "";
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function Line({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="text-slate-800">{value}</dd>
    </div>
  );
}

export function StatusLabel({ status }: { status: LegalExport["status"] }) {
  const copy = EXPORT_COPY[status];
  return (
    <span data-testid="export-status" className={`rounded px-2 py-0.5 text-xs font-medium ${copy.tone}`}>
      {copy.label}
    </span>
  );
}

function ExportDetails({ e }: { e: LegalExport }) {
  const [open, setOpen] = useState(false);
  const manifest = useManifest(e.id, open && Boolean(e.file_count));
  return (
    <>
      <dl className="mt-2 grid gap-x-6 gap-y-2 md:grid-cols-2">
        <Line label="Demanded by" value={`${e.demand_authority}, reference ${e.demand_reference}, ${when(e.demanded_on)}`} />
        <Line label="Asks for" value={e.demand_text} />
        <Line label="Goes to" value={`${e.recipient_name}, ${e.recipient_organisation}`} />
        <Line label="Asked for by" value={`${e.requested_by_label} on ${when(e.requested_at)}`} />
        {e.approved_by_label && (
          <Line label="Approved by" value={`${e.approved_by_label} on ${when(e.approved_at)}${e.approval_note ? `: ${bare(e.approval_note)}` : ""}`} />
        )}
        {e.confirmed_by_label && (
          <Line label="Scope confirmed by" value={`${e.confirmed_by_label} on ${when(e.confirmed_at)}${e.confirm_note ? `: ${bare(e.confirm_note)}` : ""}`} />
        )}
        {e.refusal_reason && <Line label="Refused" value={e.refusal_reason} />}
        {e.failure && <Line label="Why it could not be produced" value={e.failure} />}
        {e.status === "ready" && (
          <>
            <Line label="Package" value={`${e.file_count} files, ${megabytes(e.package_bytes)}, encrypted`} />
            <Line label="Kept until" value={when(e.expires_at)} />
          </>
        )}
      </dl>
      {Boolean(e.file_count) && (
        <div className="mt-2">
          <button type="button" onClick={() => setOpen(!open)} className="text-xs text-sky-700 underline">
            {open ? "Hide the list of files" : "Show the list of files"}
          </button>
          {open && manifest.data && (
            <table data-testid="export-manifest" className="mt-2 w-full text-xs">
              <thead>
                <tr className="text-left uppercase tracking-wide text-slate-500">
                  <th className="pb-1 pr-4">File</th>
                  <th className="pb-1 pr-4">Size</th>
                  <th className="pb-1">SHA-256 fingerprint</th>
                </tr>
              </thead>
              <tbody>
                {manifest.data.files.map((f) => (
                  <tr key={f.path} className="border-t border-slate-100">
                    <td className="py-1 pr-4 font-mono">{f.path}</td>
                    <td className="py-1 pr-4">{megabytes(f.bytes)}</td>
                    <td className="py-1 font-mono">{f.sha256.slice(0, 16)}&hellip;</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </>
  );
}

/** For a platform administrator, under a hold in force. */
export function HoldExports({ holdId }: { holdId: string }) {
  const { principal } = useIdentity();
  const all = useExports(Boolean(principal));
  const mine = (all.data?.exports ?? []).filter((e) => e.hold_id === holdId);
  const [asking, setAsking] = useState(false);

  return (
    <div data-testid="hold-exports" className="mt-4 border-t border-slate-100 pt-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Producing records for this matter</h3>
        {!asking && (
          <button
            type="button"
            data-testid="ask-for-records"
            onClick={() => setAsking(true)}
            className="rounded border border-slate-300 px-3 py-1 text-sm text-slate-800"
          >
            Ask for records to be produced
          </button>
        )}
      </div>
      <p className="mt-1 text-xs text-slate-500">
        A platform administrator asks, a different one approves, and the custodian this hold names confirms the
        scope. Nobody reads the records: the platform builds an encrypted, signed package.
      </p>
      {asking && <AskForm holdId={holdId} done={() => setAsking(false)} />}
      <div className="mt-3 space-y-3">
        {mine.map((e) => (
          <ExportCard key={e.id} e={e} me={principal?.id ?? ""} />
        ))}
      </div>
    </div>
  );
}

function ExportCard({ e, me }: { e: LegalExport; me: string }) {
  const [note, setNote] = useState("");
  const approve = useApproveExport(e.id);
  const link = useMakeLink(e.id);
  const [made, setMade] = useState<{ url: string; expires: string; uses: number } | null>(null);

  return (
    <div data-export={e.demand_reference} className="rounded border border-slate-200 bg-slate-50 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{e.demand_reference}</span>
        <StatusLabel status={e.status} />
      </div>
      <ExportDetails e={e} />

      {e.status === "requested" && (
        <>
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <label className="grow text-sm">
              <span className="block text-slate-600">A note, which is recorded (required to decline)</span>
              <input
                data-testid="export-note"
                value={note}
                onChange={(ev) => setNote(ev.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
              />
            </label>
            <button
              type="button"
              data-testid="export-approve"
              disabled={approve.isPending}
              onClick={() => approve.mutate({ approve: true, note }, { onSuccess: () => notify("The export is approved.") })}
              className="rounded bg-green-700 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
            >
              Approve it
            </button>
            <button
              type="button"
              disabled={approve.isPending}
              onClick={() => approve.mutate({ approve: false, note }, { onSuccess: () => notify("The export is declined.") })}
              className="rounded bg-slate-200 px-3 py-1.5 text-sm font-medium text-slate-800"
            >
              Decline
            </button>
          </div>
          {e.requested_by === me && (
            <p className="mt-2 text-xs text-slate-500">You asked for this one, so a different administrator approves it.</p>
          )}
          {approve.error && (
            <div className="mt-2">
              <Failure error={approve.error} what="this export" verb="decide" />
            </div>
          )}
        </>
      )}

      {e.status === "ready" && (
        <div className="mt-3">
          <button
            type="button"
            data-testid="export-make-link"
            disabled={link.isPending}
            onClick={() =>
              link.mutate(undefined, {
                onSuccess: (r) =>
                  setMade({ url: `${API_BASE}${r.download_path}`, expires: r.expires_at, uses: r.uses }),
              })
            }
            className="rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
          >
            Make a download link
          </button>
          <p className="mt-1 text-xs text-slate-500">
            The link is shown once, lasts a few days and works a few times. The recipient also needs the passphrase,
            which only the custodian is given.
          </p>
          {made && (
            <div data-testid="export-link" className="mt-2 rounded border border-slate-300 bg-white p-2">
              <div className="break-all font-mono text-xs">{made.url}</div>
              <div className="mt-1 text-xs text-slate-500">
                Works {made.uses} times until {when(made.expires)}. It is not shown again.
              </div>
            </div>
          )}
          {link.error && (
            <div className="mt-2">
              <Failure error={link.error} what="a download link" verb="make" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AskForm({ holdId, done }: { holdId: string; done: () => void }) {
  const scope = useScope(holdId, true);
  const ask = useRequestExport();
  const [form, setForm] = useState({
    demand_authority: "", demand_reference: "", demanded_on: "", demand_text: "",
    recipient_name: "", recipient_organisation: "", recipient_email: "",
  });
  const [chosen, setChosen] = useState<string[]>([]);
  const [audit, setAudit] = useState(true);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const input = "mt-1 w-full rounded border border-slate-300 px-2 py-1";
  const ready = Object.values(form).every((v) => v.trim()) && chosen.length > 0;

  return (
    <form
      data-testid="export-form"
      className="mt-3 grid gap-3 rounded border border-slate-200 bg-white p-3 text-sm md:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault();
        ask.mutate(
          { hold_id: holdId, ...form, dataset_ids: chosen, include_audit: audit },
          { onSuccess: () => { notify("The request is recorded and waits for a second administrator."); done(); } },
        );
      }}
    >
      <label>
        <span className="block text-slate-600">Who is demanding the records</span>
        <input data-testid="export-authority" value={form.demand_authority} onChange={set("demand_authority")} className={input} />
      </label>
      <label>
        <span className="block text-slate-600">Their reference for the demand</span>
        <input data-testid="export-reference" value={form.demand_reference} onChange={set("demand_reference")} className={input} />
      </label>
      <label>
        <span className="block text-slate-600">Date of the demand</span>
        <input data-testid="export-demanded-on" type="date" value={form.demanded_on} onChange={set("demanded_on")} className={input} />
      </label>
      <label>
        <span className="block text-slate-600">What the demand asks for, in its words</span>
        <input data-testid="export-text" value={form.demand_text} onChange={set("demand_text")} className={input} />
      </label>
      <label>
        <span className="block text-slate-600">Who receives the package</span>
        <input data-testid="export-recipient" value={form.recipient_name} onChange={set("recipient_name")} className={input} />
      </label>
      <label>
        <span className="block text-slate-600">Their organisation</span>
        <input data-testid="export-recipient-org" value={form.recipient_organisation} onChange={set("recipient_organisation")} className={input} />
      </label>
      <label className="md:col-span-2">
        <span className="block text-slate-600">Their email address</span>
        <input data-testid="export-recipient-email" value={form.recipient_email} onChange={set("recipient_email")} className={input} />
      </label>
      <fieldset className="md:col-span-2">
        <legend className="text-slate-600">Which datasets (every sealed version of each is included)</legend>
        {scope.isLoading ? (
          <p className="text-xs text-slate-500">Loading the datasets</p>
        ) : (
          <div data-testid="export-datasets" className="mt-1 space-y-1">
            {(scope.data?.datasets ?? []).map((d) => (
              <label key={d.id} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={chosen.includes(d.id)}
                  onChange={(ev) => setChosen(ev.target.checked ? [...chosen, d.id] : chosen.filter((x) => x !== d.id))}
                />
                {d.name} <span className="text-xs text-slate-500">{d.versions} version(s), {megabytes(d.bytes)}</span>
              </label>
            ))}
          </div>
        )}
        <label className="mt-2 flex items-center gap-2">
          <input type="checkbox" checked={audit} onChange={(ev) => setAudit(ev.target.checked)} />
          Include the audit trail, the list of who was allowed or refused what, for those datasets
        </label>
      </fieldset>
      <div className="md:col-span-2 flex items-center gap-3">
        <button
          type="submit"
          data-testid="export-submit"
          disabled={!ready || ask.isPending}
          className="rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
        >
          {ask.isPending ? "Recording" : "Record the request"}
        </button>
        <button type="button" onClick={done} className="text-sm text-slate-600 underline">Cancel</button>
      </div>
      {ask.error && (
        <div className="md:col-span-2">
          <Failure error={ask.error} what="this request" verb="record" />
        </div>
      )}
    </form>
  );
}

/** For the custodian a hold names: confirm the scope of an approved export, and be given the passphrase once. */
export function CustodianExports() {
  const { closed, principal } = useIdentity();
  const all = useExports(Boolean(closed || principal));
  const mine = (all.data?.exports ?? []).filter((e) => ["approved", "confirmed", "producing", "ready", "failed"].includes(e.status));
  if (mine.length === 0) return null;
  return (
    <section data-testid="custodian-exports" className="mt-4 rounded border border-slate-200 bg-white p-4 text-sm">
      <h2 className="font-semibold">Records being produced for a legal matter</h2>
      <p className="mt-1 text-slate-600">
        You answer for the records, so nothing is produced until you confirm that what is asked for matches the
        demand and goes no further. You are also the only person given the passphrase that opens the package.
      </p>
      <div className="mt-3 space-y-3">
        {mine.map((e) => (
          <CustodianCard key={e.id} e={e} />
        ))}
      </div>
    </section>
  );
}

function CustodianCard({ e }: { e: LegalExport }) {
  const [note, setNote] = useState("");
  const confirm = useConfirmExport(e.id);
  const reveal = useReadPassphrase(e.id);
  const scope = useScope(e.hold_id, e.status === "approved");
  const [passphrase, setPassphrase] = useState<string | null>(null);
  const names = (scope.data?.datasets ?? []).filter((d) => e.dataset_ids.includes(d.id));

  return (
    <div data-export={e.demand_reference} className="rounded border border-slate-200 bg-slate-50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{e.demand_reference}: matter {e.matter_number}</span>
        <StatusLabel status={e.status} />
      </div>
      <ExportDetails e={e} />
      {e.status === "approved" && (
        <>
          <p className="mt-2 text-slate-700">
            The datasets named: {names.length ? names.map((d) => d.name).join(", ") : `${e.dataset_ids.length} dataset(s)`}.
          </p>
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <label className="grow">
              <span className="block text-slate-600">A note, which is recorded (required to decline)</span>
              <input
                data-testid="confirm-note"
                value={note}
                onChange={(ev) => setNote(ev.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
              />
            </label>
            <button
              type="button"
              data-testid="confirm-scope"
              disabled={confirm.isPending}
              onClick={() => confirm.mutate({ approve: true, note }, { onSuccess: () => notify("The scope is confirmed. The package is being prepared.") })}
              className="rounded bg-green-700 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
            >
              The scope is right
            </button>
            <button
              type="button"
              disabled={confirm.isPending}
              onClick={() => confirm.mutate({ approve: false, note }, { onSuccess: () => notify("The scope is declined.") })}
              className="rounded bg-slate-200 px-3 py-1.5 text-sm font-medium text-slate-800"
            >
              It goes further than the demand
            </button>
          </div>
          {confirm.error && (
            <div className="mt-2">
              <Failure error={confirm.error} what="this export" verb="confirm" />
            </div>
          )}
        </>
      )}
      {e.status === "ready" && (
        <div className="mt-3">
          {passphrase ? (
            <div data-testid="export-passphrase" className="rounded border border-amber-300 bg-amber-50 p-2">
              <div className="font-mono text-base">{passphrase}</div>
              <div className="mt-1 text-xs text-amber-900">
                Shown once and not kept. Give it to the recipient separately from the download link.
              </div>
            </div>
          ) : e.passphrase_revealed_at ? (
            <p className="text-slate-600">You read the passphrase on {when(e.passphrase_revealed_at)}. It is not kept.</p>
          ) : (
            <button
              type="button"
              data-testid="read-passphrase"
              disabled={reveal.isPending}
              onClick={() => reveal.mutate(undefined, { onSuccess: (r) => setPassphrase(r.passphrase) })}
              className="rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
            >
              Show me the passphrase, once
            </button>
          )}
          {reveal.error && (
            <div className="mt-2">
              <Failure error={reveal.error} what="the passphrase" verb="read" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
