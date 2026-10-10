/**
 * The rows of a locked table, with the locked values opened, for somebody the platform lets read them.
 *
 * Either every row (the owning department's approver) or the fixed few rows a lease opens. Reading is recorded. A person who was erased shows
 * as erased, and everything else about their row still reads: erasure removed a key, not a row.
 */

import { useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useLocking, usePersonRows } from "../../api/locking";
import { Failure, Loading, Section } from "../../components/states";

function Cell({ value }: { value: unknown }) {
  // A person code is one unbroken label, so it is not wrapped across lines.
  if (typeof value === "string" && /^T-[0-9a-f]{16}$/.test(value)) {
    return <span className="whitespace-nowrap font-mono text-xs">{value}</span>;
  }
  if (value !== null && typeof value === "object" && "erased_on" in (value as Record<string, unknown>)) {
    return (
      <span data-testid="erased-cell" className="rounded bg-slate-200 px-1.5 py-0.5 text-xs text-slate-700">
        Erased on {String((value as { erased_on: string }).erased_on)}
      </span>
    );
  }
  if (value === null || value === undefined) return <span className="text-slate-400">empty</span>;
  if (typeof value === "boolean") return <>{value ? "yes" : "no"}</>;
  return <>{String(value)}</>;
}

export function LockedRows() {
  const { versionId } = useParams();
  const locking = useLocking(versionId);
  // The place each page started, so a person can go back as well as on: the first is the start of the table.
  const [places, setPlaces] = useState<(string | null)[]>([null]);
  const pageSize = useRef(0);
  const rows = usePersonRows(versionId, places[places.length - 1]);

  if (rows.isLoading || locking.isLoading) return <Loading what="the rows" />;
  if (rows.error) {
    return (
      <div>
        <Link className="text-sm text-blue-700 hover:underline" to={`/versions/${versionId}`}>
          Back to this version
        </Link>
        <div className="mt-4">
          <Failure error={rows.error} what="these rows" verb="read" />
        </div>
      </div>
    );
  }
  const data = rows.data!;
  // The platform's column order, with the person code last, so the table reads the way the contract was written.
  const names = (locking.data?.columns ?? []).map((c) => c.name);
  // The column that identified each person now holds their person code, so a separate person-code column would only repeat it.
  const replaced = (locking.data?.columns ?? []).filter((c) => c.treatment === "replaced by the person code").map((c) => c.name);
  const extra = (data.rows.length ? Object.keys(data.rows[0]).filter((k) => !names.includes(k)) : []).filter(
    (k) => !(k === "person_code" && replaced.length > 0),
  );
  const columns = [...names.filter((n) => data.rows.length === 0 || n in data.rows[0]), ...extra];
  const total = data.total ?? locking.data?.records ?? data.count;
  const page = places.length;
  if (page === 1 && data.count) pageSize.current = data.count;
  const firstRow = (page - 1) * pageSize.current + 1;
  const paged = data.next !== null || page > 1;

  return (
    <div>
      <Link className="text-sm text-blue-700 hover:underline" to={`/versions/${versionId}`}>
        Back to this version
      </Link>
      <Section
        level="page"
        title="Locked columns, opened"
        description="Everything you read here is recorded: who, when, and for what."
      >
        <p
          data-testid="locked-rows-banner"
          className={`mb-4 rounded border p-3 text-sm ${
            data.mode === "full" ? "border-teal-300 bg-teal-50 text-teal-900" : "border-amber-300 bg-amber-50 text-amber-900"
          }`}
        >
          {data.mode === "full"
            ? paged
              ? `You answer for this data, so you can read all ${total.toLocaleString()} rows, ${data.count.toLocaleString()} at a time. Page ${page}.`
              : `You answer for this data, so all ${data.count} row${data.count === 1 ? "" : "s"} are shown.`
            : `Your lease opens ${data.count} of ${total} rows. They are the same rows every time, and a later lease opens the same ones.`}
        </p>
        {paged && (
          <div className="mb-3 flex items-center gap-3 text-sm" data-testid="locked-rows-pager">
            <button
              type="button"
              data-testid="locked-rows-previous"
              disabled={page === 1}
              onClick={() => setPlaces((p) => p.slice(0, -1))}
              className="rounded border border-slate-300 px-3 py-1 disabled:opacity-40"
            >
              Previous page
            </button>
            <span>
              Rows {firstRow.toLocaleString()} to {(firstRow + data.count - 1).toLocaleString()} of {total.toLocaleString()}
            </span>
            <button
              type="button"
              data-testid="locked-rows-next"
              disabled={!data.next}
              onClick={() => data.next && setPlaces((p) => [...p, data.next])}
              className="rounded border border-slate-300 px-3 py-1 disabled:opacity-40"
            >
              Next page
            </button>
          </div>
        )}
        <div className="data-card">
          <table className="data-table" data-testid="locked-rows">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c} className="py-2 pr-4 font-medium">
                    {c === "person_code" ? "person code" : replaced.includes(c) ? `${c} (now a person code)` : c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, i) => (
                <tr key={i} className="border-b border-slate-100">
                  {columns.map((c) => (
                    <td key={c} className="py-2 pr-4">
                      <Cell value={row[c]} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}
