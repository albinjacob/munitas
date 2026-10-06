/**
 * The first question when bringing data in: will one person ever need to be erased from it?
 *
 * It comes before any file is chosen because it cannot be answered later. Locking happens as the data arrives, and an upload that was stored as it
 * was cannot be locked afterwards, so the answer decides which way in the person is offered. "No" is the ordinary upload and the default.
 */

export type Erasure = "no" | "yes";
export type Bringing = "table" | "recordings";

export function EraseChoice({
  erasure,
  onErasure,
  bringing,
  onBringing,
}: {
  erasure: Erasure;
  onErasure: (value: Erasure) => void;
  bringing: Bringing;
  onBringing: (value: Bringing) => void;
}) {
  return (
    <div data-testid="erase-choice" className="space-y-3 rounded border border-slate-200 bg-slate-50 p-3">
      <fieldset>
        <legend className="text-sm font-medium text-slate-900">Will you ever need to erase one person from this data?</legend>
        <p className="mt-0.5 text-xs text-slate-600">Decide now: locking cannot be added after the upload.</p>
        <label className="mt-2 flex items-start gap-2 text-sm">
          <input type="radio" name="erasure" data-testid="erase-no" checked={erasure === "no"} onChange={() => onErasure("no")} className="mt-1" />
          <span>
            <span className="font-medium">No. Store the files as they are.</span>
            <span className="block text-xs text-slate-600">Nothing in them is locked.</span>
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
              <span className="block text-xs text-slate-600">A CSV, and a contract that says which columns are locked.</span>
            </span>
          </label>
          <label className="mt-2 flex items-start gap-2 text-sm">
            <input
              type="radio"
              name="bringing"
              data-testid="bringing-recordings"
              checked={bringing === "recordings"}
              onChange={() => onBringing("recordings")}
              className="mt-1"
            />
            <span>
              <span className="font-medium">Recordings, one patient in each</span>
              <span className="block text-xs text-slate-600">Audio files, and a manifest that names the patient for each.</span>
            </span>
          </label>
        </fieldset>
      )}
    </div>
  );
}
