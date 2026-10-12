/**
 * Datasets: every dataset in the organisation, how open it is, and what each one holds.
 *
 * Four cards that are shortcuts to filters, then the list: the same filters, the same sortable columns, the same words about where a dataset came from and
 * who owns it, and the versions of each one a click away. Each dataset shows the widest access level any of its versions has reached, because that decides
 * who could have seen it. The colours keep the console's rule: red is Raw and nothing else.
 *
 * Counts are the platform's own totals. The amber "waiting" mark appears only when requests really are waiting for the person. The search in the header
 * narrows this list; its words are in the address (`?q=`), so a link to a search works.
 */

import { Fragment, useEffect, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Ico } from "../../components/Ico";
import { useHeaderSearch } from "../../components/HeaderSearch";
import { useIdentity } from "../../identity/IdentityContext";
import { INTAKE_ROLES, useHoldsRole } from "../../identity/mayDo";
import { useAccessPreview, useDatasets, useDatasetVersions, useOrganisation } from "../../api/queries";
import { useLeases } from "../../api/locking";
import { useFileLeases } from "../../api/fileLeases";
import { CLASS_LABEL, CLASS_ORDER, type AccessPreview, type DatasetRow, type VisibilityClass } from "../../api/types";
import { licenceAllows, provenanceLabel } from "../../api/provenance";
import { TONE, wording } from "../../components/AccessMark";
import { size } from "../../lib/size";
import { when } from "../../lib/when";

export const KIND_CHOICES = ["audio", "image", "text", "tabular", "other", "multimodal"];
export const FORMAT_CHOICES = ["wav", "mp3", "flac", "ogg", "m4a", "png", "jpeg", "gif", "webp", "json", "jsonl", "xml", "txt", "csv", "tsv", "parquet", "xlsx", "pdf", "zip", "table"];
export const STRUCTURE_CHOICES = ["structured", "semi-structured", "unstructured", "mixed"];
export const formatName = (f: string) => (f === "table" ? "Table" : f.toUpperCase());

const cap = (w: string) => w.charAt(0).toUpperCase() + w.slice(1);
const DAY = 86_400_000;
const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

// ------------------------------------------------------------------------------------------------------------------------------ the page

export function Datasets() {
  const { principal } = useIdentity();
  const mayBringIn = useHoldsRole(INTAKE_ROLES);

  // The first card counts what was registered in this many days.
  const days = 7;
  // The words searched for are in the address; the header search writes them.
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  useHeaderSearch({
    value: q,
    onChange: (value) => setParams(value ? { q: value } : {}, { replace: true }),
    placeholder: "Search datasets by name",
    testid: "dataset-search",
  });
  const [debounced, setDebounced] = useState(q.trim());
  // The same filters as the old page, and the cards are shortcuts to some of them.
  const [klass, setKlass] = useState("");
  const [kind, setKind] = useState("");
  const [format, setFormat] = useState("");
  const [structure, setStructure] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  // A dataset is registered (nothing in it yet), active (some data can still be read) or deleted (every version's data was deleted, the record kept).
  // Deleted ones stay out of the list unless asked for.
  const [state, setState] = useState<"" | "registered" | "active">("");
  const [showDeleted, setShowDeleted] = useState(false);
  const [sort, setSort] = useState("registered");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const PAGE = 15;
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    const id = setTimeout(() => setDebounced(q.trim()), 300);
    return () => clearTimeout(id);
  }, [q]);
  useEffect(() => setPage(1), [debounced, klass, kind, format, structure, departmentId, state, showDeleted]);

  // The numbers on the cards are the platform's own totals, one small query each, so they stay exact however many datasets there are.
  const all = useDatasets({ limit: 1 });
  const holding = useDatasets({ state: "active", limit: 1 });
  const raw = useDatasets({ widest_class: "RAW", limit: 1 });
  const published = useDatasets({ widest_class: "PUBLISHED", limit: 1 });
  // The badge on the first card counts what was registered in the chosen period, from the newest hundred.
  const recent = useDatasets({ sort: "registered", direction: "desc", limit: 100 });
  const organisation = useOrganisation();
  const list = useDatasets({
    q: debounced || undefined,
    widest_class: klass || undefined,
    kind: kind || undefined,
    format: format || undefined,
    structure: structure || undefined,
    department_id: departmentId || undefined,
    state: state || undefined,
    include_deleted: showDeleted || undefined,
    sort,
    direction,
    limit: PAGE,
    offset: (page - 1) * PAGE,
  });
  const preview = useAccessPreview({ datasetIds: (list.data?.datasets ?? []).filter((d) => d.version_count > 0).map((d) => d.id) });
  const departments = new Map((organisation.data?.departments ?? []).map((dep) => [dep.id, dep.name]));
  const filtered = Boolean(debounced || klass || kind || format || structure || departmentId || state || showDeleted);
  const clearFilters = () => {
    setParams({}, { replace: true });
    setDebounced("");
    setKlass("");
    setKind("");
    setFormat("");
    setStructure("");
    setDepartmentId("");
    setState("");
    setShowDeleted(false);
  };
  /** Pressing the heading of the column the list is ordered by turns it round; pressing another orders by that one, newest or first alphabetically. */
  const changeSort = (field: string) => {
    if (field === sort) setDirection(direction === "asc" ? "desc" : "asc");
    else {
      setSort(field);
      setDirection(field === "registered" || field === "versions" ? "desc" : "asc");
    }
    setPage(1);
  };

  const leases = useLeases();
  const fileLeases = useFileLeases();
  const waiting = (leases.data?.to_decide.length ?? 0) + (fileLeases.data?.to_decide.length ?? 0);

  const total = all.data?.total ?? 0;
  const sample: DatasetRow[] = recent.data?.datasets ?? [];
  const windowStart = startOfDay(new Date()) - (days - 1) * DAY;
  const inWindow = sample.filter((d) => new Date(d.created_at).getTime() >= windowStart);
  const windowFull = inWindow.length === sample.length && (recent.data?.total ?? 0) > sample.length;

  const share = (n: number | undefined) => (total > 0 && n !== undefined ? `${Math.round((n / total) * 100)}% of all` : "");

  if (!principal) return null;
  const waitingText = waiting === 1 ? "1 request is waiting for you" : `${waiting} requests are waiting for you`;

  return (
    <>
      <div className="ds-hero">
        <div>
          <h1>Datasets</h1>
          <p>Everything registered in {principal.tenant_id}, except datasets whose data was deleted, which are listed when you ask. Each dataset shows the widest access level any of its versions has reached, because that decides who could have seen it.</p>
        </div>
        <div className="ds-actions">
          {waiting > 0 && (
            <Link to="/locked-column-access" className="ds-live" data-testid="ds-waiting">
              <span className="ds-pulse" />
              {waitingText}
            </Link>
          )}
          {mayBringIn && (
            <Link to="/datasets/register" className="ds-btn primary" data-testid="datasets-register-link">
              <Ico name="plus" />
              Bring a dataset in
            </Link>
          )}
        </div>
      </div>

      <div className="ds-stats">
        <StatCard
          testid="ds-stat-all"
          tone="blue"
          icon="database"
          value={all.data ? total : null}
          name="Datasets"
          pressed={!filtered}
          onClick={clearFilters}
          badge={
            recent.data ? (
              inWindow.length > 0 ? (
                <span className="ds-trend up">
                  <Ico name="up" className="" />+{inWindow.length}
                  {windowFull ? "+" : ""} in {days} days
                </span>
              ) : (
                <span className="ds-trend flat">
                  <Ico name="flat" className="" />
                  none new
                </span>
              )
            ) : null
          }
        />
        <StatCard
          testid="ds-stat-holding"
          tone="indigo"
          icon="layers"
          value={holding.data?.total ?? null}
          name="Active"
          pressed={state === "active"}
          onClick={() => setState(state === "active" ? "" : "active")}
          badge={<span className="ds-trend flat">{share(holding.data?.total)}</span>}
        />
        <StatCard
          testid="ds-stat-raw"
          tone="red"
          icon="lock"
          value={raw.data?.total ?? null}
          name="Raw, not yet released"
          pressed={klass === "RAW"}
          onClick={() => setKlass(klass === "RAW" ? "" : "RAW")}
          badge={<span className="ds-trend flat">{share(raw.data?.total)}</span>}
        />
        <StatCard
          testid="ds-stat-published"
          tone="green"
          icon="globe"
          value={published.data?.total ?? null}
          name="Published"
          pressed={klass === "PUBLISHED"}
          onClick={() => setKlass(klass === "PUBLISHED" ? "" : "PUBLISHED")}
          badge={<span className="ds-trend flat">{share(published.data?.total)}</span>}
        />
      </div>

      <section className="ds-card ds-tablecard" data-testid="ds-table">
        <div className="ds-pad" style={{ paddingBottom: 12 }}>
          <div className="ds-cardhead" style={{ marginBottom: 14 }}>
            <div>
              <h2>{filtered ? "Matching datasets" : "All datasets"}</h2>
              <p data-testid="dataset-count">{list.data ? `Showing ${list.data.shown} of ${list.data.total}${filtered ? " matching" : ""}.` : "Loading"}</p>
            </div>
          </div>
          <div className="ds-filters" data-testid="ds-filters">
            <Field label="Access level">
              <select data-testid="dataset-class" value={klass} onChange={(e) => setKlass(e.target.value)}>
                <option value="">Any</option>
                {CLASS_ORDER.map((c) => (
                  <option key={c} value={c}>
                    {CLASS_LABEL[c]}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Kind">
              <select data-testid="dataset-kind-filter" value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="">Any</option>
                {KIND_CHOICES.map((o) => (
                  <option key={o} value={o}>
                    {cap(o)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Format">
              <select data-testid="dataset-format-filter" value={format} onChange={(e) => setFormat(e.target.value)}>
                <option value="">Any</option>
                {FORMAT_CHOICES.map((o) => (
                  <option key={o} value={o}>
                    {formatName(o)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Structure">
              <select data-testid="dataset-structure-filter" value={structure} onChange={(e) => setStructure(e.target.value)}>
                <option value="">Any</option>
                {STRUCTURE_CHOICES.map((o) => (
                  <option key={o} value={o}>
                    {cap(o)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Department">
              <select data-testid="dataset-department" value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
                <option value="">Any</option>
                {(organisation.data?.departments ?? []).map((dep) => (
                  <option key={dep.id} value={dep.id}>
                    {dep.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="State">
              <select data-testid="dataset-state" value={state} onChange={(e) => setState(e.target.value as "" | "registered" | "active")}>
                <option value="">Any</option>
                <option value="registered">Registered</option>
                <option value="active">Active</option>
              </select>
            </Field>
            <label className="ds-check">
              <input type="checkbox" data-testid="dataset-show-deleted" checked={showDeleted} disabled={state !== ""} onChange={(e) => setShowDeleted(e.target.checked)} />
              Also show deleted datasets
            </label>
            {filtered && (
              <button type="button" className="ds-link" data-testid="ds-clear" onClick={clearFilters}>
                Clear
              </button>
            )}
          </div>
        </div>
        {list.error ? (
          <div className="ds-note" role="alert">
            The datasets could not be loaded.
          </div>
        ) : list.isLoading ? (
          <div className="ds-note">Loading</div>
        ) : list.data && list.data.datasets.length === 0 ? (
          <div className="ds-note">{filtered ? "Nothing matches those filters. Change them or press Clear." : "Nothing has been brought into the platform yet."}</div>
        ) : (
          <>
            <table className="ds-table">
              <thead>
                <tr>
                  <SortHead label="Dataset" field="name" sort={sort} direction={direction} onSort={changeSort} />
                  <SortHead label="Kind" field="kind" sort={sort} direction={direction} onSort={changeSort} />
                  <SortHead label="Format" field="format" sort={sort} direction={direction} onSort={changeSort} />
                  <SortHead label="Structure" field="structure" sort={sort} direction={direction} onSort={changeSort} />
                  <SortHead label="Versions" field="versions" sort={sort} direction={direction} onSort={changeSort} />
                  <SortHead label="Widest access" field="access" sort={sort} direction={direction} onSort={changeSort} />
                  <SortHead label="Registered" field="registered" sort={sort} direction={direction} onSort={changeSort} />
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.data?.datasets.map((d) => {
                  const readable = preview.data?.datasets[d.id];
                  return (
                    <Fragment key={d.id}>
                      <tr data-testid="ds-row" data-dataset={d.name}>
                        <td style={{ minWidth: 180 }}>
                          <span className="mono" data-testid="ds-name">
                            {d.name}
                          </span>
                          <span className={`ds-state ${d.state}`} data-testid="dataset-state-cell">
                            {d.state === "deleted" ? "Deleted, record kept" : cap(d.state)}
                          </span>
                          <Provenance d={d} department={departments.get(d.department_id ?? "")} />
                        </td>
                        <td>
                          {(d.kinds ?? []).length === 0 ? (
                            <span className="sub">Nothing in it yet</span>
                          ) : (
                            <span data-testid="dataset-kind">{(d.kinds ?? []).map(cap).join(" + ")}</span>
                          )}
                          {(d.kinds ?? []).length > 1 && <span className="sub">Multimodal</span>}
                        </td>
                        <td>
                          {(d.formats ?? []).length === 0 ? (
                            <span className="sub">None</span>
                          ) : (
                            <>
                              <span className="mono" data-testid="dataset-format">
                                {(d.formats ?? []).map(formatName).join(", ")}
                              </span>
                              {(d.files ?? 0) > 0 && (
                                <span className="sub" style={{ whiteSpace: "nowrap" }}>
                                  {d.files} {d.files === 1 ? "file" : "files"}, {size(d.bytes ?? 0)}
                                </span>
                              )}
                            </>
                          )}
                          {d.is_table && d.table_missing && (
                            <span className="ds-marker" data-testid="table-missing" title="Open a version to see why it has no table copy">
                              Stored as a table, but not every version has a table copy
                            </span>
                          )}
                        </td>
                        <td>
                          {d.structure ? (
                            <>
                              <span data-testid="dataset-structure">{cap(d.structure)}</span>
                              {d.structure === "mixed" && <span className="sub">{(d.structures ?? []).map(cap).join(", ")}</span>}
                            </>
                          ) : (
                            <span className="sub">None</span>
                          )}
                        </td>
                        <td>
                          {d.version_count}
                          {d.version_count > 0 && (
                            <span className="sub">
                              <span data-testid="dataset-readable">{preview.error ? "Couldn't check" : readable ? `${readable.readable} of ${readable.total}` : ""}</span>{" "}
                              {!preview.error && readable ? "you can read" : ""}
                            </span>
                          )}
                        </td>
                        <td style={{ whiteSpace: "nowrap" }}>
                          {d.widest_class ? <ClassPill value={d.widest_class} /> : <span className="sub">Nothing in it yet</span>}
                        </td>
                        <td data-testid="dataset-registered">
                          {when(d.created_at).replace(/ (AM|PM)$/, "\u00a0$1")}
                        </td>
                        <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                          {d.version_count > 0 ? (
                            <button type="button" className="ds-link" data-testid="ds-show-versions" aria-expanded={open === d.id} onClick={() => setOpen(open === d.id ? null : d.id)}>
                              {open === d.id ? "Hide versions" : "Show versions"}
                            </button>
                          ) : (
                            <Link to={`/datasets/${d.id}/ingest`} className="ds-link" data-testid="ds-bring-in">
                              Bring data in
                            </Link>
                          )}
                        </td>
                      </tr>
                      {open === d.id && (
                        <tr className="ds-expand">
                          <td colSpan={8}>
                            <Versions datasetId={d.id} preview={preview.data} previewFailed={Boolean(preview.error)} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
            <div className="ds-pager">
              <span data-testid="ds-range-text">
                Showing {(page - 1) * PAGE + 1} to {(page - 1) * PAGE + (list.data?.datasets.length ?? 0)} of {list.data?.total ?? 0}
              </span>
              <div className="btns">
                <button type="button" className="btn btn-secondary btn-sm" data-testid="ds-prev" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                  Previous
                </button>
                <button type="button" className="btn btn-secondary btn-sm" data-testid="ds-next" disabled={!list.data || page * PAGE >= list.data.total} onClick={() => setPage((p) => p + 1)}>
                  Next
                </button>
              </div>
            </div>
          </>
        )}
      </section>
    </>
  );
}

function StatCard({
  testid,
  tone,
  icon,
  value,
  name,
  badge,
  pressed,
  onClick,
}: {
  testid: string;
  tone: "blue" | "green" | "red" | "indigo";
  icon: string;
  value: number | null;
  name: string;
  badge: ReactNode;
  pressed: boolean;
  onClick: () => void;
}) {
  return (
    <button type="button" className="ds-card ds-stat" data-testid={testid} aria-pressed={pressed} onClick={onClick}>
      <div className="top">
        <span className={`ds-ico ${tone}`}>
          <Ico name={icon} />
        </span>
        {badge}
      </div>
      <div className="val" data-testid={`${testid}-value`}>
        {value === null ? "…" : value.toLocaleString()}
      </div>
      <div className="name">{name}</div>
    </button>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="ds-field">
      {label}
      {children}
    </label>
  );
}

function SortHead({ label, field, sort, direction, onSort }: { label: string; field: string; sort: string; direction: "asc" | "desc"; onSort: (field: string) => void }) {
  const active = sort === field;
  return (
    <th aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className="ds-sort" data-testid={`sort-${field}`} onClick={() => onSort(field)}>
        {label}
        <span aria-hidden="true" className={active ? "on" : ""}>
          {active ? (direction === "asc" ? "\u25B2" : "\u25BC") : "\u2195"}
        </span>
      </button>
    </th>
  );
}

/** The class in the console's own colours: red is Raw and nothing else, and the ramp cools as the data opens. */
function ClassPill({ value, title }: { value: VisibilityClass; title?: string }) {
  return (
    <span className={`ds-pill ${value}`} data-testid={`class-badge-${value}`} title={title ? `${title} (${value})` : value}>
      {CLASS_LABEL[value]}
    </span>
  );
}

/** Sealed and current class together: a version that was promoted has not been rewritten, and showing only today's class would hide that. */
function ClassPair({ sealed, current }: { sealed: VisibilityClass; current: VisibilityClass }) {
  if (sealed === current) return <ClassPill value={current} title="Never promoted" />;
  return (
    <span className="ds-pair">
      <ClassPill value={sealed} title="Class at seal time, unchanged" />
      <span aria-label="promoted to" className="ds-arrow">
        &rarr;
      </span>
      <ClassPill value={current} title="Current class, from the transition log" />
    </span>
  );
}

/** Where it came from, what its licence allows, who owns it, and a correction of how it was first registered, which keeps its own line. */
function Provenance({ d, department }: { d: DatasetRow; department?: string }) {
  const licence = d.license_tag ? ` Licence ${d.license_tag}: ${licenceAllows(d.license_export_unmodified, d.license_export_modified)}.` : "";
  return (
    <>
      <span className="sub" data-testid="dataset-provenance">
        {provenanceLabel(d.provenance)}.{licence}
      </span>
      {department && (
        <span className="sub" data-testid="dataset-owner">
          Owned by {department}.
        </span>
      )}
      {d.provenance_registered_as && d.provenance_overridden_at && (
        <span className="sub warn" data-testid="dataset-provenance-corrected">
          Registered as "{provenanceLabel(d.provenance_registered_as)}". Changed on {when(d.provenance_overridden_at)}, when its licence was read.
        </span>
      )}
    </>
  );
}

/** One version's access, in the words the whole console uses for it. */
function AccessText({ access, failed }: { access: AccessPreview["versions"][string] | undefined; failed: boolean }) {
  if (failed) return <span className="ds-acc no">Couldn't check</span>;
  if (!access) return <span className="ds-acc no">Checking</span>;
  const w = wording(access);
  const tone = w.tone === TONE.yes ? "yes" : w.tone === TONE.wait ? "wait" : "no";
  return (
    <span className={`ds-acc ${tone}`} data-testid="access-mark" data-mark={access.mark}>
      {w.label}
      {w.detail && <span className="sub">{w.detail}</span>}
    </span>
  );
}

/** The versions of one dataset: each one's class (as sealed and as it is now), what it holds, and whether the person can read it. */
function Versions({ datasetId, preview, previewFailed }: { datasetId: string; preview: AccessPreview | undefined; previewFailed: boolean }) {
  const { data, isLoading, error } = useDatasetVersions(datasetId);
  if (isLoading) return <div className="ds-note">Loading versions</div>;
  if (error) return <div className="ds-note" role="alert">The versions could not be loaded.</div>;
  if (!data?.length) return <div className="ds-note">No versions in this dataset.</div>;
  return (
    <table className="ds-vtable" data-testid="ds-versions">
      <thead>
        <tr>
          <th>Version</th>
          <th>Access level</th>
          <th>Holds</th>
          <th>Your access</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {data.map((v) => (
          <tr key={v.dataset_version_id}>
            <td>v{v.version}</td>
            <td>
              <ClassPair sealed={v.sealed_class} current={v.current_class} />
            </td>
            <td data-testid="version-holds-count">
              {(v.record_count ?? 0).toLocaleString()} {v.holds_rows ? ((v.record_count ?? 0) === 1 ? "row" : "rows") : (v.record_count ?? 0) === 1 ? "file" : "files"}
            </td>
            <td>
              <AccessText access={preview?.versions[v.dataset_version_id]} failed={previewFailed} />
            </td>
            <td style={{ textAlign: "right" }}>
              <Link to={`/versions/${v.dataset_version_id}`} className="ds-link">
                {preview?.versions[v.dataset_version_id]?.mark === "removed" ? "Details" : "Open"}
              </Link>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
