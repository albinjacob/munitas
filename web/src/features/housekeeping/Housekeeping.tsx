/**
 * Storage housekeeping: what is held, what could be freed, what was.
 *
 * None of this was visible anywhere but the database, and that is how three
 * bugs in this area survived five weeks: a reclaimer that freed nothing while
 * reporting success, fixtures writing objects where the platform would never
 * read them, and a volume pool filling with throwaway buckets until every
 * write failed with an error naming neither volumes nor the pool.
 *
 * Which half of this page somebody sees is decided by the policy engine, not
 * here. Asking without an organisation is the platform-wide question, and
 * asking with one is that organisation's own record. The screen asks both and
 * shows whichever comes back, so the same page serves the person running the
 * machine and the custodian of one organisation without either being told
 * about the other's view.
 *
 * Freeing storage is offered only where the platform allows it, and the
 * button never acts on its first press: it rehearses, shows exactly what
 * would go, and only then offers to mean it. A dashboard that destroys data
 * in one click is the thing this platform's whole argument is against.
 */

import { Fragment, useState } from "react";
import {
  usePlatformHousekeeping,
  useReclaim,
  useTenantHousekeeping,
  type FreedVersion,
  type StoragePermissions,
  type TableCopies,
  type TenantStorage,
} from "../../api/housekeeping";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { useIdentity } from "../../identity/IdentityContext";

/** Bytes as somebody reading a screen would say them. */
function size(bytes: number): string {
  if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} bytes`;
}

function when(iso: string): string {
  return new Date(iso).toLocaleString();
}

/**
 * What an organisation is, in the reader's words.
 *
 * `purpose` is the platform's own vocabulary and does not belong on a screen
 * somebody is working on: "canary" means nothing to a reader, and copy.spec.ts
 * exists because this project has leaked its own tracker onto a page before.
 * Only the distinction that matters is shown, which is whether anything here
 * is somebody's real data.
 */
function kindOf(purpose: string): "Real" | "Test" | "Closed" {
  if (purpose === "production") return "Real";
  if (purpose === "retired") return "Closed";
  return "Test";
}

/**
 * Real organisations first, then closed ones, then the test ones.
 *
 * Grouped rather than sorted, and the difference matters. Sorting would put
 * them in an order somebody has to notice; a divider states the boundary, so
 * a reader scanning for a customer never has to work out whether the row they
 * have landed on is somebody's real data or a fixture. The same reason the
 * rows no longer carry a "Test" badge each: the heading says it once.
 *
 * An empty group is dropped rather than shown as a heading with nothing
 * under it, which reads as something having gone missing.
 */
function grouped(rows: TenantStorage[]): { label: string; rows: TenantStorage[] }[] {
  const order: { kind: ReturnType<typeof kindOf>; label: string }[] = [
    { kind: "Real", label: "Real organisations" },
    { kind: "Closed", label: "Closed organisations" },
    { kind: "Test", label: "Test organisations, nobody's real data" },
  ];
  return order
    .map(({ kind, label }) => ({
      label,
      rows: rows.filter((row) => kindOf(row.purpose) === kind),
    }))
    .filter((group) => group.rows.length > 0);
}

const BACKEND_NAMES: Record<string, string> = {
  seaweedfs: "SeaweedFS, on this machine's own disk",
  r2: "Cloudflare R2",
};

/**
 * Where one organisation's files actually are.
 *
 * Named by backend rather than by bucket alone, because a bucket name on its
 * own does not say whether the bytes are on this machine or in somebody's
 * cloud account, and those are very different answers to "where is our data".
 * An organisation with no versions yet is said to have written nothing rather
 * than being given a blank cell, which reads as missing information.
 */
function Where({
  row,
  localPath,
}: {
  row: TenantStorage;
  localPath: string;
}) {
  const backends = row.backends ?? [];

  if (backends.length === 0) {
    return <span className="text-slate-400">nothing written yet</span>;
  }

  return (
    <div className="space-y-1">
      {backends.map((backend) => (
        <div key={backend}>
          <span className="text-slate-700">
            {BACKEND_NAMES[backend] ?? backend}
          </span>
          {backend === "seaweedfs" && (
            <>
              <span className="ml-2 font-mono text-xs text-slate-500">
                {row.bucket}
              </span>
              {localPath && (
                <div className="font-mono text-[11px] text-slate-400">
                  {localPath}
                </div>
              )}
            </>
          )}
        </div>
      ))}
    </div>
  );
}

export function Housekeeping() {
  const { principal } = useIdentity();
  const platform = usePlatformHousekeeping(Boolean(principal));
  const own = useTenantHousekeeping(principal?.tenant_id);

  if (!principal) return <Loading what="your identity" />;

  const seesPlatform = Boolean(platform.data);
  const seesOwn = Boolean(own.data);

  // Refused both. The reasons came from the policy engine, so they are shown
  // rather than replaced with a generic apology.
  if (!platform.isLoading && !own.isLoading && !seesPlatform && !seesOwn) {
    return (
      <div className="p-6">
        <h1 className="mb-4 text-xl font-semibold text-slate-900">
          Storage housekeeping
        </h1>
        <Failure
          error={own.error ?? platform.error}
          what="storage housekeeping"
        />
      </div>
    );
  }

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold text-slate-900">
        Storage housekeeping
      </h1>
      <p className="mt-1 mb-6 text-sm text-slate-500">
        What is stored, what could be freed, and what already was.
      </p>

      {platform.isLoading && <Loading what="storage across the platform" />}
      {seesPlatform && <PlatformView data={platform.data!} />}

      {seesOwn && <OwnOrganisation data={own.data!} />}
    </div>
  );
}

/**
 * Where a table was asked for and was not written. A version that looks fine and lacks its table is the quiet kind of
 * fault, so it is counted here, and a failure the platform did not recognise raises an alert the way a stalled storage
 * permission does.
 */
function TableCopiesSection({ copies, named }: { copies: TableCopies; named: boolean }) {
  return (
    <Section
      title="Table copies"
      description="A version whose rows are written as a table can be read by a standard tool and filtered for a legal export. Where one was asked for and was not written, it is listed here with the reason."
    >
      {copies.alert && (
        <div
          role="alert"
          data-testid="table-copies-alert"
          className="mb-3 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-900"
        >
          <strong className="font-semibold">
            {copies.failed} {copies.failed === 1 ? "version has" : "versions have"} no table copy because writing it
            failed.
          </strong>{" "}
          The version is sealed and its files are safe. Someone who runs the platform needs to look at the reason below.
        </div>
      )}
      <dl data-testid="table-copies" className="grid grid-cols-2 gap-3 text-sm md:grid-cols-5">
        {[
          ["Written as a table", copies.projected],
          ["Failed", copies.failed],
          ["Skipped, with a reason", copies.skipped],
          ["Files, never a table", copies.files],
          ["Sealed before reasons were recorded", copies.unrecorded],
        ].map(([label, n]) => (
          <div key={label as string} className="rounded border border-slate-200 bg-white p-3">
            <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
            <dd className="mt-1 text-lg tabular-nums">{n}</dd>
          </div>
        ))}
      </dl>
      {copies.lacking.length > 0 && (
        <table data-testid="table-copies-lacking" className="mt-4 w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
              <th className="pb-2 pr-4">{named ? "Dataset" : "Organisation"}</th>
              <th className="pb-2 pr-4">What happened</th>
              <th className="pb-2">When</th>
            </tr>
          </thead>
          <tbody>
            {copies.lacking.map((l) => (
              <tr key={l.dataset_version_id} className="border-t border-slate-100 align-top">
                <td className="py-2 pr-4">
                  {named ? `${l.dataset_name} v${l.version}` : l.tenant_id}
                </td>
                <td className="py-2 pr-4">
                  <span
                    className={`mr-2 rounded px-1.5 py-0.5 text-xs font-medium ${
                      l.outcome === "failed" ? "bg-red-100 text-red-900" : "bg-amber-100 text-amber-900"
                    }`}
                  >
                    {l.outcome === "failed" ? "Failed" : "Skipped"}
                  </span>
                  {l.reason}
                </td>
                <td className="py-2 whitespace-nowrap text-slate-600">{when(l.noted_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}

function ActivationAlert({ status }: { status: StoragePermissions }) {
  const minutes = status.failing_since
    ? Math.max(1, Math.round((Date.now() - new Date(status.failing_since).getTime()) / 60000))
    : null;
  const waiting =
    status.parked_runs === 0
      ? ""
      : status.parked_runs === 1
        ? " 1 run is waiting and will continue by itself."
        : ` ${status.parked_runs} runs are waiting and will continue by themselves.`;
  return (
    <div
      data-testid="activation-alert"
      role="alert"
      className="mb-6 rounded border border-red-300 bg-red-50 p-4 text-sm text-red-900"
    >
      <p className="font-medium">
        New storage access has not taken effect
        {minutes ? ` for ${minutes} minute${minutes === 1 ? "" : "s"}` : ""}.
      </p>
      <p className="mt-1">{status.reason}</p>
      <p className="mt-1">
        {status.retryable
          ? `The platform keeps retrying, so nothing needs re-running once the cause is fixed.${waiting}`
          : `Retrying will not fix this. Once the cause is fixed, run reconcile-grants.py in the API container and access takes effect.${waiting}`}
      </p>
    </div>
  );
}

function PlatformView({
  data,
}: {
  data: NonNullable<ReturnType<typeof usePlatformHousekeeping>["data"]>;
}) {
  const pool = data.volumes;
  const storagePath = data.storage_path;

  return (
    <>
      {data.storage_permissions.alert && (
        <ActivationAlert status={data.storage_permissions} />
      )}
      <TableCopiesSection copies={data.table_copies} named={false} />
      <Section
        title="Storage the engine has set aside"
        description="Each organisation's files live in containers of one gigabyte, claimed when it first writes and never shared. Running out stops every write."
      >
        {pool.used === null ? (
          <p className="text-sm text-slate-500">
            The storage engine did not answer, so this number is unknown.{" "}
            {pool.reason}
          </p>
        ) : (
          <>
            <p data-testid="volumes-used" className="text-sm text-slate-700">
              <span className="text-2xl font-semibold text-slate-900">
                {pool.used}
              </span>{" "}
              containers in use.
            </p>
            <table className="mt-3 w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="pb-2">Where</th>
                  <th className="pb-2">Containers</th>
                </tr>
              </thead>
              <tbody>
                {pool.by_collection.map((row) => (
                  <tr key={row.collection} className="border-b border-slate-100">
                    <td className="py-2 font-mono text-xs">{row.collection}</td>
                    <td className="py-2">{row.volumes}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </Section>

      <Section
        title="Every organisation"
        description="Where each one's files are kept, and how much of it could be freed. Only test organisations are ever freeable."
      >
        <table className="w-full text-sm" data-testid="tenant-storage">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
              <th className="pb-2 pr-6">Organisation</th>
              <th className="w-[26rem] pb-2 pr-6">What it is for</th>
              <th className="pb-2 pr-6">Where its files are</th>
              <th className="pb-2 pr-4 text-right">Versions</th>
              <th className="pb-2 pr-4 text-right">Could be freed</th>
              <th className="pb-2 text-right">Already freed</th>
            </tr>
          </thead>
          <tbody>
            {grouped(data.tenants).map((group) => (
              <Fragment key={group.label}>
                <tr>
                  <th
                    colSpan={6}
                    className="border-b border-slate-300 pt-5 pb-1 text-left text-xs font-semibold uppercase tracking-wide text-slate-500"
                  >
                    {group.label}
                  </th>
                </tr>
                {group.rows.map((row) => (
              <tr key={row.tenant_id} className="border-b border-slate-100">
                <td className="py-2 pr-6 align-top font-medium">{row.tenant_id}</td>
                <td className="py-2 pr-6 align-top text-slate-600">
                  {row.note ?? (
                    <span className="text-slate-400">
                      nobody wrote down why this exists
                    </span>
                  )}
                </td>
                <td className="py-2 pr-6 align-top">
                  <Where row={row} localPath={storagePath} />
                </td>
                <td className="py-2 pr-4 text-right align-top">{row.versions}</td>
                <td className="py-2 pr-4 text-right align-top">
                  {row.reclaimable_versions > 0 ? (
                    <span className="font-medium text-amber-800">
                      {row.reclaimable_versions}
                    </span>
                  ) : (
                    <span className="text-slate-400">nothing</span>
                  )}
                </td>
                <td className="py-2 text-right align-top text-slate-600">
                  {row.reclaimed_versions > 0
                    ? `${row.reclaimed_versions} (${size(row.bytes_reclaimed)})`
                    : "none"}
                </td>
              </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      </Section>

      <FreeStorage />

      <Section
        title="Test organisations waiting to be removed"
        description="The verification suite creates one per run for checks that can only be proved once. They are swept after every run."
      >
        {data.probes_waiting.length === 0 ? (
          <Empty
            what="test organisations"
            hint="Nothing is waiting. The last verification run swept what it made."
          />
        ) : (
          <table className="w-full text-sm" data-testid="probes-waiting">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="pb-2">Organisation</th>
                <th className="pb-2">Kind</th>
                <th className="pb-2">Versions</th>
              </tr>
            </thead>
            <tbody>
              {data.probes_waiting.map((row) => (
                <tr key={row.tenant_id} className="border-b border-slate-100">
                  <td className="py-2 font-mono text-xs">{row.tenant_id}</td>
                  <td className="py-2 text-slate-500">{row.purpose}</td>
                  <td className="py-2">{row.versions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
    </>
  );
}

/**
 * Rehearse, then mean it.
 *
 * Two presses, deliberately. The first asks the platform what would go and
 * shows it; only then does the second appear. The reason is required because
 * it is written beside the bytes: a version whose files vanish with nothing
 * saying why is indistinguishable from one that never had any.
 */
function FreeStorage() {
  const [reason, setReason] = useState("");
  const [days, setDays] = useState(1);
  const [rehearsed, setRehearsed] = useState<FreedVersion[] | null>(null);
  const [totals, setTotals] = useState({ objects: 0, bytes: 0 });
  const reclaim = useReclaim();

  const canAsk = reason.trim().length > 0 && !reclaim.isPending;

  async function rehearse() {
    const result = await reclaim.mutateAsync({
      reason,
      older_than_days: days,
      dry_run: true,
    });
    setRehearsed(result.freed);
    setTotals({ objects: result.objects, bytes: result.bytes });
  }

  async function perform() {
    await reclaim.mutateAsync({
      reason,
      older_than_days: days,
      dry_run: false,
    });
    setRehearsed(null);
    setReason("");
  }

  return (
    <Section
      title="Free old test files"
      description="Deletes the stored files of old test versions. The records survive, and what was freed is written down against them."
    >
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="block text-slate-600">Older than</span>
          <select
            data-testid="reclaim-days"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="mt-1 rounded border border-slate-300 px-2 py-1"
          >
            <option value={1}>1 day</option>
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
          </select>
        </label>
        <label className="grow text-sm">
          <span className="block text-slate-600">
            Why, which is recorded beside what was freed
          </span>
          <input
            data-testid="reclaim-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Old verification leftovers"
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
          />
        </label>
        <button
          type="button"
          data-testid="reclaim-rehearse"
          disabled={!canAsk}
          onClick={rehearse}
          className="rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
        >
          Show what would go
        </button>
      </div>

      {reclaim.error && (
        <div className="mt-3">
          <Failure error={reclaim.error} what="what could be freed" />
        </div>
      )}

      {rehearsed && (
        <div className="mt-4" data-testid="reclaim-rehearsal">
          {rehearsed.length === 0 ? (
            <p className="text-sm text-slate-600">
              Nothing is old enough to free.
            </p>
          ) : (
            <>
              <p className="text-sm text-slate-700">
                {rehearsed.length} version(s), {totals.objects} file(s),{" "}
                {size(totals.bytes)} would be freed. The records stay; only the
                files go, and this cannot be undone.
              </p>
              <button
                type="button"
                data-testid="reclaim-confirm"
                disabled={reclaim.isPending}
                onClick={perform}
                className="mt-3 rounded bg-red-700 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
              >
                {reclaim.isPending ? "Freeing" : `Free ${size(totals.bytes)} for good`}
              </button>
            </>
          )}
        </div>
      )}
    </Section>
  );
}

function OwnOrganisation({
  data,
}: {
  data: NonNullable<ReturnType<typeof useTenantHousekeeping>["data"]>;
}) {
  return (
    <>
    <TableCopiesSection copies={data.table_copies} named />
    <Section
      title={`What has been freed from ${data.tenant_id}`}
      description="Deleting stored files is not silent. Each one is written down here: what went, when, at whose hand, and why."
    >
      {data.reclaimed.length === 0 ? (
        <Empty
          what="deletions"
          hint="Nothing has been freed from this organisation."
        />
      ) : (
        <table className="w-full text-sm" data-testid="own-reclaimed">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
              <th className="pb-2">Dataset</th>
              <th className="pb-2">Version</th>
              <th className="pb-2">Freed</th>
              <th className="pb-2">When</th>
              <th className="pb-2">By</th>
              <th className="pb-2">Why</th>
            </tr>
          </thead>
          <tbody>
            {data.reclaimed.map((row) => (
              <tr
                key={`${row.dataset_name}-${row.version}-${row.reclaimed_at}`}
                className="border-b border-slate-100"
              >
                <td className="py-2 font-medium">{row.dataset_name}</td>
                <td className="py-2">v{row.version}</td>
                <td className="py-2">
                  {size(row.bytes_freed)} in {row.object_count} file(s)
                </td>
                <td className="py-2 text-slate-600">{when(row.reclaimed_at)}</td>
                <td className="py-2 text-slate-600">{row.reclaimed_by}</td>
                <td className="py-2 text-slate-600">{row.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
    </>
  );
}
