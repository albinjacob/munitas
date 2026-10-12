/**
 * The first rows of a version's table, for somebody who may read it.
 *
 * Looking at rows is a read, so nothing is fetched until the person asks, and the platform records each time they do. A person who may not read the
 * version is told so, with what to do next, and sees no row. A decimal or a date is shown as the text it was stored as, so no digit is lost.
 */

import { useState } from "react";
import { useVersionPreview } from "../../api/queries";
import { Reasons } from "../locking/LockedColumns";

/** A column's stored type in the words a person would use. A decimal keeps its precision and scale, which are what matter about it. */
const TYPE_WORDS: Record<string, string> = {
  string: "text", long: "whole number", int: "whole number", double: "number", float: "number", boolean: "true or false", date: "date",
  timestamp: "date and time", timestamptz: "date and time, with a time zone",
};
const typeWords = (type: string) => TYPE_WORDS[type] ?? type;

export function FirstRows({ versionId }: { versionId: string }) {
  const [asked, setAsked] = useState(false);
  const preview = useVersionPreview(versionId, asked);

  if (!asked) {
    return (
      <button
        type="button"
        data-testid="preview-show"
        onClick={() => setAsked(true)}
        className="btn btn-secondary mt-3"
      >
        Show the first rows
      </button>
    );
  }
  if (preview.isLoading) return <p className="mt-3 text-sm text-slate-600">Reading the first rows.</p>;
  if (preview.error) {
    return (
      <div data-testid="preview-refused" role="alert" className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
        <p className="font-medium">The rows are not shown.</p>
        <Reasons error={preview.error} fallback="The rows could not be read." />
      </div>
    );
  }
  const data = preview.data!;
  return (
    <div data-testid="preview-rows" className="mt-3 space-y-2">
      <p className="text-xs text-slate-600">
        The first {data.shown.toLocaleString()} of {data.rows_total?.toLocaleString() ?? "the"} rows, as they are stored. Looking at them has been recorded.
      </p>
      <div className="overflow-x-auto rounded border border-slate-200">
        <table className="min-w-full text-xs">
          <thead className="bg-slate-50 text-left text-slate-600">
            <tr>
              {data.columns.map((c) => (
                <th key={c.name} className="whitespace-nowrap px-2 py-1 font-medium">
                  {c.name}
                  <span className="block font-normal text-slate-400">{typeWords(c.type)}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, i) => (
              <tr key={i} className="border-t border-slate-100">
                {data.columns.map((c) => (
                  <td key={c.name} className="whitespace-nowrap px-2 py-1 font-mono">
                    {row[c.name] === null || row[c.name] === undefined ? <span className="text-slate-400">missing</span> : String(row[c.name])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
