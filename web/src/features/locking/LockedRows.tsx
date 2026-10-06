/**
 * The rows of a locked table, with the locked values opened, for somebody the platform lets read them.
 *
 * Either every row (the owning department's approver) or the fixed few rows a lease opens. Reading is recorded. A person who was erased shows
 * as erased, and everything else about their row still reads: erasure removed a key, not a row.
 */

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
  const rows = usePersonRows(versionId);

  if (rows.isLoading || locking.isLoading) return <Loading what="the rows" />;
  if (rows.error) {
    return (
      <div className="p-6">
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
  const total = locking.data?.records ?? data.count;

  return (
    <div className="p-6">
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
            ? `You answer for this data, so all ${data.count} row${data.count === 1 ? "" : "s"} are shown.`
            : `Your lease opens ${data.count} of ${total} rows. They are the same rows every time, and a later lease opens the same ones.`}
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="locked-rows">
            <thead className="border-b border-slate-200 text-left text-slate-500">
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
