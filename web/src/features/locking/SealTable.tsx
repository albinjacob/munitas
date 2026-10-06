/**
 * Sealing a table from two files: the privacy contract and the rows.
 *
 * The contract says, column by column, what is locked, what stays readable and how the person each row is about is found. The rows are a CSV
 * with a header. Pressing the button is the check: if anything in either file is wrong, nothing is sealed and every problem is listed together,
 * each naming its row and column, because a table with a dozen problems should take one look to understand and not a dozen attempts.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useSealTableFiles } from "../../api/locking";
import { Reasons } from "./LockedColumns";

export function SealTablePanel({ datasetId }: { datasetId: string }) {
  const seal = useSealTableFiles(datasetId);
  const [contract, setContract] = useState<File | null>(null);
  const [rows, setRows] = useState<File | null>(null);
  const done = seal.data;

  if (done) {
    const r = done.group_size_report;
    return (
      <div data-testid="table-sealed" className="space-y-2 rounded border border-teal-300 bg-teal-50 p-4 text-sm text-teal-900">
        <p className="font-medium">
          Sealed as version {done.version}: {done.records} row{done.records === 1 ? "" : "s"} about {done.people} {done.people === 1 ? "person" : "people"}.
        </p>
        <p>
          Their identifying values are locked, each under a key made for that person. Nothing about this version can change from here; a later table
          becomes a new version.
        </p>
        {r.people_in_small_groups > 0 && (
          <p data-testid="sealed-small-groups">
            {r.people_in_small_groups} {r.people_in_small_groups === 1 ? "person sits" : "people sit"} in a group smaller than {r.threshold}. It did not stop
            the seal. The version's page says what locking each column would leave.
          </p>
        )}
        <Link to={`/versions/${done.id}`} data-testid="open-sealed-table" className="inline-block text-sky-700 underline">
          Open this version
        </Link>
      </div>
    );
  }

  return (
    <form
      data-testid="seal-table-form"
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (contract && rows) seal.mutate({ contract, rows });
      }}
    >
      <p className="text-sm text-slate-700">
        Use this for a table of people's records. Two files: a <strong>contract</strong> that says which columns are locked and how each row's
        person is found (YAML or JSON), and the <strong>rows</strong> as a CSV with a header. Up to 20,000 rows and 5 MB.
      </p>
      <div className="flex flex-wrap gap-6">
        <label className="text-sm">
          <span className="block text-slate-600">The contract</span>
          <input
            data-testid="seal-table-contract"
            type="file"
            accept=".yaml,.yml,.json,application/json,text/yaml"
            onChange={(e) => setContract(e.target.files?.[0] ?? null)}
            className="mt-1 block text-xs"
          />
        </label>
        <label className="text-sm">
          <span className="block text-slate-600">The rows</span>
          <input
            data-testid="seal-table-rows"
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => setRows(e.target.files?.[0] ?? null)}
            className="mt-1 block text-xs"
          />
        </label>
      </div>
      <button
        type="submit"
        data-testid="seal-table-submit"
        disabled={!contract || !rows || seal.isPending}
        className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
      >
        {seal.isPending ? "Sealing" : "Seal as a table"}
      </button>
      {seal.error && (
        <div data-testid="seal-table-refused" role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-800">
          <p className="font-medium">Nothing was sealed. Fix these first:</p>
          <Reasons error={seal.error} fallback="Sealing failed." />
        </div>
      )}
    </form>
  );
}
