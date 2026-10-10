/**
 * Sealing a table from two files: the privacy contract and the rows. For a table of people the contract says which columns are locked; for an ordinary
 * table (`plain`) it says the table cannot be erased and why, and nothing is locked.
 *
 * The contract says, column by column, what is locked, what stays readable and how the person each row is about is found. The rows are a CSV
 * with a header. Pressing the button is the check: if anything in either file is wrong, nothing is sealed and every problem is listed together,
 * each naming its row and column, because a table with a dozen problems should take one look to understand and not a dozen attempts.
 *
 * An ordinary table that is too large to send in one go (over 5 MB) is sent in parts and written in the background, which `LargeTableUpload` shows.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import type { TableUploadOptions } from "../../api/tableUploads";
import { useSealTableFiles } from "../../api/locking";
import { Reasons } from "./LockedColumns";
import { LargeTableUpload } from "./TableUpload";

const MB = 1024 * 1024;
/** What one request carries. A larger file is sent in parts. */
export const SEND_WHOLE_LIMIT = 5 * MB;
/** A table about people is sealed at once only when it is small enough to be well under the 20,000 rows that route takes. */
export const PEOPLE_SEND_WHOLE_LIMIT = 1 * MB;

const DELIMITERS: { label: string; value: string }[] = [
  { label: "Commas", value: "," },
  { label: "Tabs", value: "\t" },
  { label: "Semicolons", value: ";" },
  { label: "Vertical bars", value: "|" },
];
const ENCODINGS: { label: string; value: string }[] = [
  { label: "UTF-8 (the usual)", value: "utf-8" },
  { label: "Windows-1252 (older Excel and Windows files)", value: "cp1252" },
];

/** How the rows file is laid out, from its name: the layout, and the compression if it ends in one. Null when the ending is not one that is read. */
export type Layout = "csv" | "tsv" | "jsonl" | "json" | "parquet" | "avro" | "orc" | "arrow" | "xlsx";
const BINARY_ENDINGS: [string, Layout][] = [[".parquet", "parquet"], [".avro", "avro"], [".orc", "orc"], [".arrow", "arrow"], [".feather", "arrow"], [".xlsx", "xlsx"]];

export function layoutOf(name: string): { kind: Layout; compression: "gzip" | "zstd" | null } | null {
  let lowered = name.toLowerCase();
  const binary = BINARY_ENDINGS.find(([ending]) => lowered.endsWith(ending));
  if (binary) return { kind: binary[1], compression: null };
  let compression: "gzip" | "zstd" | null = null;
  if (lowered.endsWith(".gz")) [lowered, compression] = [lowered.slice(0, -3), "gzip"];
  else if (lowered.endsWith(".zst")) [lowered, compression] = [lowered.slice(0, -4), "zstd"];
  const kinds: [string, Layout][] = [[".csv", "csv"], [".tsv", "tsv"], [".jsonl", "jsonl"], [".ndjson", "jsonl"], [".json", "json"]];
  const found = kinds.find(([ending]) => lowered.endsWith(ending));
  return found ? { kind: found[1], compression } : null;
}

const WORDS: Record<string, string> = {
  csv: "a CSV file",
  tsv: "a TSV file",
  jsonl: "a file with one JSON row per line",
  json: "a JSON list of rows",
  parquet: "a Parquet file",
  avro: "an Avro file",
  orc: "an ORC file",
  arrow: "an Arrow or Feather file",
  xlsx: "an Excel workbook",
};

/** A binary table file is always sent in parts and read by a worker, whatever its size: the browser cannot check one and the API does not open one. */
const isBinary = (kind: string | undefined) => kind === "parquet" || kind === "avro" || kind === "orc" || kind === "arrow" || kind === "xlsx";

export function SealTablePanel({ datasetId, plain = false }: { datasetId: string; plain?: boolean }) {
  const seal = useSealTableFiles(datasetId);
  const [contract, setContract] = useState<File | null>(null);
  const [rows, setRows] = useState<File | null>(null);
  const [delimiter, setDelimiter] = useState<string | null>(null);
  const [encoding, setEncoding] = useState("utf-8");
  const [header, setHeader] = useState(true);
  const [missing, setMissing] = useState("");
  const [sheet, setSheet] = useState("");
  const [started, setStarted] = useState<{ contract: File; rows: File; options: TableUploadOptions } | null>(null);
  const done = seal.data;
  const layout = rows ? layoutOf(rows.name) : null;
  const delimited = layout !== null && (layout.kind === "csv" || layout.kind === "tsv");

  function declared(): TableUploadOptions {
    if (layout?.kind === "xlsx") return sheet.trim() ? { sheet: sheet.trim() } : {};
    if (!delimited) return {};
    const chosen = delimiter ?? (layout?.kind === "tsv" ? "\t" : ",");
    const words = missing.split(",").map((w) => w.trim()).filter(Boolean);
    return {
      ...(chosen !== "," ? { delimiter: chosen } : {}),
      ...(encoding !== "utf-8" ? { encoding } : {}),
      ...(!header ? { header: false } : {}),
      ...(words.length ? { null_values: ["", ...words] } : {}),
    };
  }

  if (started) {
    return <LargeTableUpload datasetId={datasetId} contract={started.contract} rows={started.rows} options={started.options} />;
  }

  if (done) {
    const r = done.group_size_report;
    if (done.plain || !r) {
      return (
        <div data-testid="table-sealed" className="space-y-2 rounded border border-teal-300 bg-teal-50 p-4 text-sm text-teal-900">
          <p className="font-medium">
            Sealed as version {done.version}: {done.records} row{done.records === 1 ? "" : "s"}.
          </p>
          <p>
            Nothing in it is locked. It starts at the most restricted level, so nobody can read it until the person who answers for it allows it. Nothing about
            this version can change from here; a later table becomes a new version.
          </p>
          <Link to={`/versions/${done.id}`} data-testid="open-sealed-table" className="inline-block text-sky-700 underline">
            Open this version
          </Link>
        </div>
      );
    }
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

  const large = rows !== null && (rows.size > (plain ? SEND_WHOLE_LIMIT : PEOPLE_SEND_WHOLE_LIMIT) || isBinary(layout?.kind));

  return (
    <form
      data-testid="seal-table-form"
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!contract || !rows) return;
        if (large) setStarted({ contract, rows, options: declared() });
        else seal.mutate({ contract, rows, options: declared() });
      }}
    >
      {plain ? (
        <p className="text-sm text-slate-700">
          Two files: a <strong>contract</strong> that names each column and its type, marks the key (one column, several, or none), and says the table cannot be
          erased and why (YAML or JSON), and the <strong>rows</strong> as a CSV or TSV with a header, a JSON list, a JSONL file with one row per line (any of them may
          be compressed with .gz or .zst), or a Parquet, Avro, ORC, Arrow, Feather or Excel (.xlsx) file. A text file of up to 5 MB is sealed at once. A larger one, or any
          Parquet, Avro, ORC, Arrow, Feather or Excel file, up to 16 GB unless your organisation has set a lower limit, is sent in
          parts and written in the background, and you can watch it here, stop it, or carry on after a break.{" "}
          <a data-testid="example-contract" className="text-sky-700 underline" href="/privacy-contract-plain-example.yaml" download>
            Download an example contract
          </a>{" "}
          and change its columns to match yours.
        </p>
      ) : (
        <p className="text-sm text-slate-700">
          Two files: a <strong>contract</strong> that says which columns are locked and which column identifies each row's person (YAML or JSON), and the{" "}
          <strong>rows</strong> as a CSV or TSV with a header, a JSON list, a JSONL file with one row per line (any of them may be compressed with .gz or .zst), or
          a Parquet, Avro, ORC, Arrow, Feather or Excel (.xlsx) file. A text file of up to 1 MB is sealed at once. A larger one, or any Parquet,
          Avro, ORC, Arrow, Feather or Excel file, is sent in parts and checked and locked in the background, and you can watch it here, stop it, or carry on
          after a break. The file you send is not kept once the table is written.{" "}
          <a data-testid="example-contract" className="text-sky-700 underline" href="/privacy-contract-example.yaml" download>
            Download an example contract
          </a>{" "}
          and change its columns to match yours.
        </p>
      )}
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
            accept=".csv,.tsv,.json,.jsonl,.ndjson,.gz,.zst,.parquet,.avro,.orc,.arrow,.feather,.xlsx,text/csv,application/json"
            onChange={(e) => {
              setRows(e.target.files?.[0] ?? null);
              setDelimiter(null);
            }}
            className="mt-1 block text-xs"
          />
        </label>
      </div>
      {rows && (
        <div data-testid="table-options" className="space-y-2 rounded border border-slate-200 bg-slate-50 p-3 text-sm">
          {layout ? (
            <p data-testid="detected-format">
              Read as {WORDS[layout.kind]}
              {layout.compression ? `, compressed with ${layout.compression}` : ""}
              {large ? (isBinary(layout.kind) ? ". It will be sent in parts and written in the background." : `. It is over ${plain ? 5 : 1} MB, so it will be sent in parts and written in the background.`) : "."}
            </p>
          ) : (
            <p data-testid="detected-format" className="text-red-800">
              This ending is not one that is read. Use .csv, .tsv, .json, .jsonl or .ndjson, with .gz or .zst after it if the file is compressed, or .parquet, .avro, .orc,
              .arrow, .feather or .xlsx.
            </p>
          )}
          {delimited && (
            <div className="flex flex-wrap items-end gap-4">
              <label>
                <span className="block text-slate-600">Each value is separated by</span>
                <select
                  data-testid="option-delimiter"
                  value={delimiter ?? (layout?.kind === "tsv" ? "\t" : ",")}
                  onChange={(e) => setDelimiter(e.target.value)}
                  className="mt-1 rounded border border-slate-300 bg-white px-2 py-1"
                >
                  {DELIMITERS.map((d) => (
                    <option key={d.label} value={d.value}>
                      {d.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="block text-slate-600">The text is written in</span>
                <select data-testid="option-encoding" value={encoding} onChange={(e) => setEncoding(e.target.value)} className="mt-1 rounded border border-slate-300 bg-white px-2 py-1">
                  {ENCODINGS.map((d) => (
                    <option key={d.value} value={d.value}>
                      {d.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2">
                <input data-testid="option-header" type="checkbox" checked={header} onChange={(e) => setHeader(e.target.checked)} />
                <span>The first line holds the column names</span>
              </label>
              <label>
                <span className="block text-slate-600">Words that mean a value is missing, apart from an empty field</span>
                <input
                  data-testid="option-missing"
                  value={missing}
                  onChange={(e) => setMissing(e.target.value)}
                  placeholder="NULL, N/A"
                  className="mt-1 rounded border border-slate-300 bg-white px-2 py-1"
                />
              </label>
            </div>
          )}
          {layout?.kind === "xlsx" && (
            <label className="block">
              <span className="block text-slate-600">The sheet to read (the first sheet when this is left empty)</span>
              <input
                data-testid="option-sheet"
                value={sheet}
                onChange={(e) => setSheet(e.target.value)}
                placeholder="Sheet1"
                className="mt-1 rounded border border-slate-300 bg-white px-2 py-1"
              />
            </label>
          )}
          {large && <p className="text-xs text-slate-600">If an upload of this file was interrupted before, choose the same file again and it carries on from where it stopped.</p>}
        </div>
      )}
      <button
        type="submit"
        data-testid="seal-table-submit"
        disabled={!contract || !rows || seal.isPending || (rows !== null && layout === null)}
        className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
      >
        {seal.isPending ? "Sealing" : large ? "Send and write as a table" : "Seal as a table"}
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
