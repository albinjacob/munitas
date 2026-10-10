/**
 * The first question when bringing data in: will one person ever need to be erased from it?
 *
 * It comes before any file is chosen because it cannot be answered later. Locking happens as the data arrives, and an upload that was stored as it
 * was cannot be locked afterwards, so the answer decides which way in the person is offered. "No" is the ordinary upload and the default.
 */

export type Erasure = "no" | "yes";
export type Bringing = "table" | "files";
/** What is brought in when nobody will need to be erased: files as they are, or an ordinary table. */
export type Plain = "files" | "table";

export function EraseChoice({
  erasure,
  onErasure,
  bringing,
  onBringing,
  plain,
  onPlain,
}: {
  erasure: Erasure;
  onErasure: (value: Erasure) => void;
  bringing: Bringing;
  onBringing: (value: Bringing) => void;
  plain: Plain;
  onPlain: (value: Plain) => void;
}) {
  return (
    <div data-testid="erase-choice" className="space-y-3 rounded border border-slate-200 bg-slate-50 p-3">
      <fieldset>
        <legend className="text-sm font-medium text-slate-900">Will you ever need to erase one person from this data?</legend>
        <p className="mt-0.5 text-xs text-slate-600">Decide now: locking cannot be added after the upload.</p>
        <label className="mt-2 flex items-start gap-2 text-sm">
          <input type="radio" name="erasure" data-testid="erase-no" checked={erasure === "no"} onChange={() => onErasure("no")} className="mt-1" />
          <span>
            <span className="font-medium">No. Store what I bring as it is.</span>
            <span className="block text-xs text-slate-600">Nothing in it is locked.</span>
          </span>
        </label>
        <label className="mt-2 flex items-start gap-2 text-sm">
          <input type="radio" name="erasure" data-testid="erase-yes" checked={erasure === "yes"} onChange={() => onErasure("yes")} className="mt-1" />
          <span>
            <span className="font-medium">Yes. Lock what identifies a person, with one key for each person.</span>
            <span className="block text-xs text-slate-600">
              Erasing a person destroys their key, so what was locked for them can no longer be read, and the rest of their data stays. Locked data is hidden,
              not anonymised: it is still personal data until the person is erased.
            </span>
          </span>
        </label>
      </fieldset>

      {erasure === "no" && (
        <fieldset className="border-t border-slate-200 pt-3">
          <legend className="text-sm font-medium text-slate-900">What are you bringing in?</legend>
          <label className="mt-2 flex items-start gap-2 text-sm">
            <input type="radio" name="plain" data-testid="plain-files" checked={plain === "files"} onChange={() => onPlain("files")} className="mt-1" />
            <span>
              <span className="font-medium">Files</span>
              <span className="block text-xs text-slate-600">Any files: audio, images, documents, a spreadsheet. They are stored as they are, and read as files.</span>
            </span>
          </label>
          <label className="mt-2 flex items-start gap-2 text-sm">
            <input type="radio" name="plain" data-testid="plain-table" checked={plain === "table"} onChange={() => onPlain("table")} className="mt-1" />
            <span>
              <span className="font-medium">A table</span>
              <span className="block text-xs text-slate-600">
                Rows and columns that are not about people, such as counts for each ward. A contract names the columns, and the rows are a CSV, TSV,
                JSON or JSONL file, of any size up to 16 GB. Once somebody is allowed to read it, it can be queried.
              </span>
            </span>
          </label>
        </fieldset>
      )}

      {erasure === "yes" && (
        <fieldset className="border-t border-slate-200 pt-3">
          <legend className="text-sm font-medium text-slate-900">What are you bringing in?</legend>
          <label className="mt-2 flex items-start gap-2 text-sm">
            <input
              type="radio"
              name="bringing"
              data-testid="bringing-table"
              checked={bringing === "table"}
              onChange={() => onBringing("table")}
              className="mt-1"
            />
            <span>
              <span className="font-medium">A table of people's records</span>
              <span className="block text-xs text-slate-600">The rows as a CSV, TSV, JSON, JSONL, Parquet, Avro, ORC, Arrow, Feather or Excel file, of any size, and a privacy contract: a file that says which columns identify a person and are locked.</span>
            </span>
          </label>
          <label className="mt-2 flex items-start gap-2 text-sm">
            <input
              type="radio"
              name="bringing"
              data-testid="bringing-files"
              checked={bringing === "files"}
              onChange={() => onBringing("files")}
              className="mt-1"
            />
            <span>
              <span className="font-medium">Files, one person in each</span>
              <span className="block text-xs text-slate-600">Audio files, and a manifest: a CSV file that says which person each file is about.</span>
            </span>
          </label>
        </fieldset>
      )}
    </div>
  );
}
