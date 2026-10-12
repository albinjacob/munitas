/**
 * On a result waiting for a decision: which de-identification standard to judge it by, and what the recommendation is under that standard. On a decided one: the standard it was judged by.
 *
 * The recommendation shown here is the one the platform works out when the decision is made, so the page cannot offer a release the decision would then refuse.
 */

import { Link } from "react-router-dom";
import { useJudgement, useStandards, type StandardLimits } from "../../api/standards";
import type { GateDetail } from "../../api/gates";
import { Failure } from "../../components/states";
import { sentence } from "../../lib/text";
import { StandardLimitsTable, limitsNothing } from "./DeidentificationStandards";

type Counts = { total: number; destroyed: number; effective: number; leaked: number };

/** What the run counted for each kind of identifier, beside the limits of the standard the result is being judged by. Shown only where the run kept the counts. */
export function KindResults({ detail, limits }: { detail: GateDetail; limits?: StandardLimits }) {
  const known = useStandards();
  const kept = (detail.metrics as unknown as { per_entity?: Record<string, Counts> }).per_entity;
  if (!kept || !known.data) return null;
  // A name the key labels by the part the person plays is counted with the person's names: a standard has one kind for them.
  const counts: Record<string, Counts> = {};
  for (const [code, c] of Object.entries(kept)) {
    const into = (counts[known.data.aliases[code] ?? code] ??= { total: 0, destroyed: 0, effective: 0, leaked: 0 });
    into.total += c.total;
    into.destroyed += c.destroyed;
    into.effective += c.effective;
    into.leaked += c.leaked;
  }
  const rows = known.data.kinds.filter((k) => counts[k.code]);
  if (rows.length === 0) return null;
  return (
    <div className="data-card mt-4">
      <table className="data-table w-full" data-testid="gate-kinds">
        <thead>
          <tr>
            <th>Kind of identifier</th>
            <th>Still readable after transcription</th>
            <th>Left in the clear</th>
            <th>Removed</th>
            {limits && <th>The standard asks</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((k) => {
            const c = counts[k.code];
            const kept = c.total - c.destroyed;
            const limit = limits?.types[k.code];
            const removed = kept ? c.effective / kept : 1;
            const missed = Boolean(limit) && ((limit!.recall !== null && removed < limit!.recall) || (limit!.max_leaks !== null && c.leaked > limit!.max_leaks));
            const ask = limit && (limit.recall !== null || limit.max_leaks !== null)
              ? [
                  limit.recall !== null ? `at least ${Number((limit.recall * 100).toFixed(2))}% removed` : null,
                  limit.max_leaks !== null ? (limit.max_leaks === 0 ? "none left in the clear" : `at most ${limit.max_leaks} left in the clear`) : null,
                ]
                  .filter(Boolean)
                  .join(", ")
              : "No limit set for this kind";
            return (
              <tr key={k.code} data-testid={`gate-kind-${k.code}`}>
                <td>{k.label}</td>
                <td>{kept}</td>
                <td>{c.leaked}</td>
                <td>{kept ? `${((c.effective / kept) * 100).toFixed(1)}%` : "None to remove"}</td>
                {limits && (
                  <td className={missed ? "font-medium text-amber-800" : "text-slate-700"} data-testid={missed ? `gate-kind-${k.code}-not-met` : undefined}>
                    {ask}
                    {missed ? ". Not met." : ""}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="px-3 py-2 text-xs text-slate-600" data-testid="gate-kinds-note">
        Only identifiers that the speech recognition left readable are counted. One that it had already garbled beyond recognition is not, because the pipeline cannot be held responsible for it.
      </p>
    </div>
  );
}

/** A waiting result: choose the standard, and see the recommendation under it. */
/** What a standard asks over all kinds together, in a sentence. */
function overallLine(name: string, limits: StandardLimits): string {
  return limits.overall_recall === null
    ? `The ${name} standard sets no overall limit.`
    : `The ${name} standard asks for at least ${Number((limits.overall_recall * 100).toFixed(2))}% of identifying detail removed, over all kinds together.`;
}

export function ChooseStandard({ detail, standardId, onChange }: { detail: GateDetail; standardId: string | null; onChange: (id: string | null) => void }) {
  const known = useStandards();
  const judged = useJudgement(detail.id, standardId);
  if (known.isLoading) return null;
  const all = known.data?.standards ?? [];
  const pass = judged.data?.recommendation === "pass";
  return (
    <div data-testid="gate-standard">
      <label className="flex max-w-xl flex-col gap-1 text-sm">
        <span className="font-medium text-slate-900">De-identification standard</span>
        <span className="text-slate-600">Choose the standard this result is judged by. It is kept with your decision.</span>
        <select data-testid="gate-standard-select" value={standardId ?? ""} onChange={(e) => onChange(e.target.value || null)} className="rounded border border-slate-400 px-2 py-1">
          <option value="">Platform default</option>
          {all.map((b) => (
            <option key={b.id} value={b.id!}>
              {b.name} (version {b.version})
            </option>
          ))}
        </select>
      </label>
      {judged.error ? (
        <div className="mt-3">
          <Failure error={judged.error} what="the recommendation under this standard" />
        </div>
      ) : judged.data ? (
        <div
          className={`mt-3 rounded border p-3 text-sm ${judged.data.recommendation === "pass" ? "border-green-300 bg-green-50 text-green-900" : "border-amber-300 bg-amber-50 text-amber-900"}`}
          data-testid="gate-recommendation"
        >
          <p className="font-medium">
            {pass ? `The measurement clears the ${judged.data.standard.name} standard.` : judged.data.recommendation === "fail" ? `The measurement does not clear the ${judged.data.standard.name} standard.` : "Nothing was measured, so no standard can be applied."}
          </p>
          <p className="mt-1">{sentence(judged.data.reason)}</p>
          {limitsNothing(judged.data.standard.limits) && <p className="mt-1">This standard sets no minimum and no maximum.</p>}
        </div>
      ) : null}
      {judged.data && (
        <p className="mt-3 text-sm text-slate-700" data-testid="gate-standard-asks">
          {overallLine(judged.data.standard.name, judged.data.standard.limits)}{" "}
          <Link to="/deidentification-standards" className="text-sky-700 underline">
            All of its limits are on the De-identification standards page.
          </Link>
        </p>
      )}
      <KindResults detail={detail} limits={judged.data?.standard.limits} />
    </div>
  );
}

/** A decided result that was judged by a standard: which one and which version, the recommendation under it, and when. */
export function JudgedBy({ detail }: { detail: GateDetail }) {
  const known = useStandards();
  if (!detail.standard_name) return null;
  const pass = detail.release_recommendation === "pass";
  const limits = detail.limits_used as unknown as StandardLimits | null;
  return (
    <div data-testid="gate-judged-by">
      <div
        className={`rounded border p-3 text-sm ${pass ? "border-green-300 bg-green-50 text-green-900" : "border-amber-300 bg-amber-50 text-amber-900"}`}
        data-testid="gate-recommendation"
      >
        <p className="font-medium">
          Judged by the {detail.standard_name} standard{detail.standard_version ? `, version ${detail.standard_version}` : ""}:{" "}
          {detail.release_recommendation === "pass" ? "it cleared the standard." : detail.release_recommendation === "fail" ? "it did not clear the standard." : "nothing was measured."}
        </p>
        {detail.release_reason && <p className="mt-1">{sentence(detail.release_reason)}</p>}
      </div>
      {limits && <p className="mt-3 text-sm text-slate-700" data-testid="gate-standard-asks">{overallLine(detail.standard_name, limits).replace(" standard asks", " standard asked").replace(" standard sets", " standard set")}</p>}
      {limits && known.data && (
        <details className="mt-3 text-sm" data-testid="gate-standard-limits">
          <summary className="cursor-pointer text-sky-700">All of the limits of the standard, as they were then</summary>
          <div className="mt-2">
            <StandardLimitsTable limits={limits} kinds={known.data.kinds} />
          </div>
        </details>
      )}
      <KindResults detail={detail} limits={limits ?? undefined} />
    </div>
  );
}
