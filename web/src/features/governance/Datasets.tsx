/**
 * Dataset list, filtered, expanding to versions.
 *
 * Filtering is on the server. A console that fetches everything and hides most
 * of it in the browser gets slower as the platform gets more useful, which is
 * the wrong way round.
 *
 * The sensitivity shown is the **widest** any version has reached, not the
 * newest version's. A dataset whose latest version is raw but which has an older
 * version released is not a raw dataset from an exposure point of view, and
 * showing the newest would understate who could have seen it.
 */

import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAccessPreview, useDatasetVersions, useDatasets } from "../../api/queries";
import type { AccessPreview } from "../../api/types";
import { CLASS_LABEL, CLASS_ORDER } from "../../api/types";
import { licenceAllows, provenanceLabel } from "../../api/provenance";
import type { DatasetRow } from "../../api/types";
import { AccessMark } from "../../components/AccessMark";
import { ClassBadge, ClassPair } from "../../components/ClassBadge";
import { Pagination } from "../../components/Pagination";
import { Empty, Failure, Loading, Section } from "../../components/states";

const MODALITIES = ["audio", "text", "image", "tabular"];

/** The database's timestamp, which puts a space where ISO puts a T. */
function day(timestamp: string): string {
  return new Date(timestamp.replace(" ", "T")).toLocaleDateString();
}

/**
 * Where it came from and what its licence allows, under the name.
 *
 * A correction gets its own line and keeps it. The fetch screen said so once,
 * at the moment the licence was read; anybody arriving later saw only the
 * corrected answer, with no sign the registration had said something else.
 */
/**
 * Whether this dataset is also stored as a table, which is what lets a standard tool read its rows and a legal export
 * hand over only the rows for named people. A dataset of files shows nothing. A table dataset says so, and a table
 * dataset with a version that has no table copy says that too, because that version cannot be filtered.
 */
function TableMarker({ d }: { d: DatasetRow }) {
  if (!d.is_table) return null;
  return d.table_missing ? (
    <span
      data-testid="table-missing"
      title="Open a version to see why it has no table copy"
      className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-normal text-amber-900"
    >
      a table, but not every version has a table copy
    </span>
  ) : (
    <span data-testid="table-marker" className="ml-2 rounded bg-sky-100 px-1.5 py-0.5 text-xs font-normal text-sky-900">
      a table
    </span>
  );
}

function Provenance({ d }: { d: DatasetRow }) {
  const licence = d.license_tag
    ? ` Licence ${d.license_tag}: ${licenceAllows(d.license_export_unmodified, d.license_export_modified)}.`
    : "";
  return (
    <>
      <span data-testid="dataset-provenance" className="block text-xs font-normal text-slate-500">
        {provenanceLabel(d.provenance)}.{licence}
      </span>
      {d.provenance_registered_as && d.provenance_overridden_at && (
        <span
          data-testid="dataset-provenance-corrected"
          className="block text-xs font-normal text-amber-800"
        >
          Registered as "{provenanceLabel(d.provenance_registered_as)}". Changed on{" "}
          {day(d.provenance_overridden_at)}, when its licence was read.
        </span>
      )}
    </>
  );
}

function Versions({
  datasetId,
  preview,
  previewFailed,
}: {
  datasetId: string;
  preview: AccessPreview | undefined;
  previewFailed: boolean;
}) {
  const { data, isLoading, error } = useDatasetVersions(datasetId);

  if (isLoading) return <Loading what="versions" />;
  if (error) return <Failure error={error} what="versions" />;
  if (!data?.length) return <Empty what="versions in this dataset" />;

  return (
    // overflow-x-auto and whitespace-nowrap: this table used to assume the
    // width of the desktop row it renders inside, which has plenty to
    // spare. Rendered inside the mobile card list instead, the same four
    // headers with no gap between them ran together into one unreadable
    // word -- scrollable with real spacing costs the desktop case nothing
    // and fixes the narrow one.
    <div className="overflow-x-auto">
      <table className="w-full min-w-max text-sm">
        <thead className="whitespace-nowrap text-left text-xs uppercase text-slate-500">
          <tr>
            <th className="py-1 pr-4">Version</th>
            <th className="pr-4">Access level</th>
            <th className="pr-4">Records</th>
            <th className="pr-4">Your access</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.map((v) => (
            <tr key={v.dataset_version_id} className="whitespace-nowrap border-t border-slate-100 transition-colors hover:bg-slate-50">
              <td className="py-1.5 pr-4 tabular-nums">v{v.version}</td>
              <td className="pr-4">
                <ClassPair sealed={v.sealed_class} current={v.current_class} />
              </td>
              <td className="pr-4 tabular-nums">{v.record_count ?? 0}</td>
              <td className="pr-4">
                <AccessMark
                  access={preview?.versions[v.dataset_version_id]}
                  failed={previewFailed}
                />
              </td>
              <td className="text-right">
                <Link
                  to={`/versions/${v.dataset_version_id}`}
                  className="text-sky-700 underline"
                >
                  Open
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const PAGE_SIZE = 15;

/**
 * Whether the viewport is at least Tailwind's own `md` breakpoint (768px),
 * checked for real rather than left to CSS alone.
 *
 * The table below and its mobile card equivalent are structurally
 * different markup (a `<table>` needs `<tr>`/`<td>`, a card list does not),
 * so there is no single element either layout's "show versions" panel
 * could share. Toggling both with CSS (`hidden md:block` / `md:hidden`)
 * would still mount both, one of them merely invisible -- and a test
 * asserting on a shared testid inside that panel would then find two
 * matches instead of one, even though only one is ever on screen. Gating
 * which layout mounts at all on a real `matchMedia` check, not just which
 * one is visible, keeps exactly one instance of that panel in the DOM.
 * Defaults to `true` (desktop): this is a client-only SPA with no SSR
 * pass to mismatch against, and every existing test already runs at a
 * desktop viewport, so this default is also what they already expect
 * before the media query listener's first real reading arrives.
 */
function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(true);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    setIsDesktop(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setIsDesktop(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return isDesktop;
}

export function Datasets() {
  const [q, setQ] = useState("");
  const [klass, setKlass] = useState("");
  const [modality, setModality] = useState("");
  const [emptyOnly, setEmptyOnly] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const isDesktop = useIsDesktop();

  const { data, isLoading, error } = useDatasets({
    q: q || undefined,
    widest_class: klass || undefined,
    modality: modality || undefined,
    has_versions: emptyOnly ? false : undefined,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  });

  // One question for every dataset on screen, so the counts and the version
  // marks come from the same answer.
  const preview = useAccessPreview({
    datasetIds: (data?.datasets ?? [])
      .filter((d) => d.version_count > 0)
      .map((d) => d.id),
  });

  const filtered = Boolean(q || klass || modality || emptyOnly);

  return (
    <Section
      level="page"
      title="Datasets"
      description="The access level shown is the widest any version has reached, because that is what determines who could have seen it."
      actions={
        <Link
          to="/datasets/register"
          data-testid="datasets-register-link"
          className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white"
        >
          Bring a dataset in
        </Link>
      }
    >
      <div className="mb-4 flex flex-wrap items-end gap-3 rounded border border-slate-200 bg-white p-3">
        <label className="flex flex-col gap-1 text-xs text-slate-500">
          Search by name
          <input
            data-testid="dataset-search"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
            placeholder="encounters"
            className="w-56 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
          />
        </label>

        <label className="flex flex-col gap-1 text-xs text-slate-500">
          Access level
          <select
            data-testid="dataset-class"
            value={klass}
            onChange={(e) => {
              setKlass(e.target.value);
              setPage(1);
            }}
            className="rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
          >
            <option value="">Any</option>
            {CLASS_ORDER.map((c) => (
              <option key={c} value={c}>
                {CLASS_LABEL[c]}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-xs text-slate-500">
          Kind of data
          <select
            data-testid="dataset-modality"
            value={modality}
            onChange={(e) => {
              setModality(e.target.value);
              setPage(1);
            }}
            className="rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
          >
            <option value="">Any</option>
            {MODALITIES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 pb-1 text-sm text-slate-700">
          <input
            type="checkbox"
            data-testid="dataset-empty-only"
            checked={emptyOnly}
            onChange={(e) => {
              setEmptyOnly(e.target.checked);
              setPage(1);
            }}
          />
          Registered but still empty
        </label>

        {filtered && (
          <button
            type="button"
            onClick={() => {
              setQ("");
              setKlass("");
              setModality("");
              setEmptyOnly(false);
              setPage(1);
            }}
            className="pb-1 text-sm text-sky-700 underline"
          >
            Clear
          </button>
        )}
      </div>

      {isLoading ? (
        <Loading what="datasets" />
      ) : error ? (
        <Failure error={error} what="datasets" />
      ) : !data?.datasets.length ? (
        <Empty
          what="datasets"
          hint={
            filtered
              ? "Nothing matches those filters."
              : "Nothing has been brought into the platform yet."
          }
        />
      ) : (
        <>
          <p data-testid="dataset-count" className="mb-2 text-sm text-slate-500">
            {/*
              Says what is hidden, still, even with real pages: "15 of 283
              matching" tells you there is more before you ever reach the
              page controls at the bottom.
            */}
            Showing {data.shown} of {data.total}
            {filtered ? " matching" : ""}.
          </p>

          {/*
            A table with five data columns plus an action does not fit a
            phone screen -- confirmed live, even with `overflow-x-auto` the
            first column alone (name, plus the provenance line under it)
            used the full width before a horizontal scroll could reach
            anything else, and scrolling sideways loses the row's own name
            off the left edge regardless. Below `md`, the same rows render
            as stacked label/value cards instead. `isDesktop` picks which
            one actually mounts (see its own comment for why this can't be
            a plain CSS show/hide of both).
          */}
          {isDesktop ? (
          <div className="overflow-x-auto rounded border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <thead className="whitespace-nowrap bg-slate-50 text-left text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-4 py-2">Dataset</th>
                  <th className="px-3">Kind</th>
                  <th className="px-3">Versions</th>
                  <th className="px-3">You can read</th>
                  <th className="px-3">Most widely released</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.datasets.map((d) => (
                  <Fragment key={d.id}>
                    <tr data-dataset={d.name} className="border-t border-slate-100 transition-colors hover:bg-slate-50">
                      <td className="px-4 py-2 font-medium">
                        {d.name}
                        <Provenance d={d} />
                        <TableMarker d={d} />
                      </td>
                      <td className="whitespace-nowrap px-3 text-slate-600">
                        {d.modality?.length ? (
                          d.modality.join(", ")
                        ) : (
                          <span className="text-xs text-slate-400">not recorded</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3 tabular-nums">{d.version_count}</td>
                      <td data-testid="dataset-readable" className="whitespace-nowrap px-3 tabular-nums">
                        {d.version_count === 0
                          ? ""
                          : preview.error
                            ? "Couldn't check"
                            : preview.data?.datasets[d.id]
                              ? `${preview.data.datasets[d.id].readable} of ${preview.data.datasets[d.id].total}`
                              : ""}
                      </td>
                      <td className="whitespace-nowrap px-3">
                        {d.widest_class ? (
                          <ClassBadge value={d.widest_class} />
                        ) : (
                          // "none" read like a failure. It is an empty shelf:
                          // somebody registered this and never put anything in
                          // it, which is a real state worth naming.
                          <span className="text-xs text-slate-500">
                            registered, nothing in it yet
                          </span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-4 py-2 text-right">
                        {d.version_count > 0 ? (
                          <button
                            type="button"
                            onClick={() => setOpen(open === d.id ? null : d.id)}
                            className="text-sky-700 underline"
                          >
                            {open === d.id ? "Hide versions" : "Show versions"}
                          </button>
                        ) : (
                          <Link to={`/datasets/${d.id}/ingest`} className="text-sky-700 underline">
                            Bring data in
                          </Link>
                        )}
                      </td>
                    </tr>
                    {open === d.id && (
                      <tr className="bg-slate-50">
                        <td colSpan={6} className="px-4 py-3">
                          <Versions
                            datasetId={d.id}
                            preview={preview.data}
                            previewFailed={Boolean(preview.error)}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          ) : (
          <ul className="space-y-3">
            {data.datasets.map((d) => (
              <li
                key={d.id}
                data-dataset={d.name}
                className="rounded border border-slate-200 bg-white p-4 text-sm"
              >
                <div className="font-medium">
                  {d.name}
                  <Provenance d={d} />
                  <TableMarker d={d} />
                </div>
                <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
                  <dt className="text-slate-500">Kind</dt>
                  <dd className="text-right text-slate-700">
                    {d.modality?.length ? (
                      d.modality.join(", ")
                    ) : (
                      <span className="text-slate-400">not recorded</span>
                    )}
                  </dd>
                  <dt className="text-slate-500">Versions</dt>
                  <dd className="text-right tabular-nums text-slate-700">
                    {d.version_count}
                  </dd>
                  <dt className="text-slate-500">You can read</dt>
                  <dd data-testid="dataset-readable" className="text-right tabular-nums text-slate-700">
                    {d.version_count === 0
                      ? "—"
                      : preview.error
                        ? "Couldn't check"
                        : preview.data?.datasets[d.id]
                          ? `${preview.data.datasets[d.id].readable} of ${preview.data.datasets[d.id].total}`
                          : "—"}
                  </dd>
                  <dt className="text-slate-500">Most widely released</dt>
                  <dd className="text-right">
                    {d.widest_class ? (
                      <ClassBadge value={d.widest_class} />
                    ) : (
                      <span className="text-xs text-slate-500">
                        registered, nothing in it yet
                      </span>
                    )}
                  </dd>
                </dl>
                <div className="mt-3 border-t border-slate-100 pt-2">
                  {d.version_count > 0 ? (
                    <button
                      type="button"
                      onClick={() => setOpen(open === d.id ? null : d.id)}
                      className="text-sky-700 underline"
                    >
                      {open === d.id ? "Hide versions" : "Show versions"}
                    </button>
                  ) : (
                    <Link to={`/datasets/${d.id}/ingest`} className="text-sky-700 underline">
                      Bring data in
                    </Link>
                  )}
                </div>
                {open === d.id && (
                  <div className="mt-3 border-t border-slate-100 pt-3">
                    <Versions
                      datasetId={d.id}
                      preview={preview.data}
                      previewFailed={Boolean(preview.error)}
                    />
                  </div>
                )}
              </li>
            ))}
          </ul>
          )}

          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={data.total}
            onPageChange={setPage}
          />
        </>
      )}
    </Section>
  );
}
