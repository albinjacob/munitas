/**
 * Locked data audit: how locking was used in this organisation, for locked columns and for locked files.
 *
 * For the privacy auditor and the data protection officer. It shows who read locked columns and who opened locked files, who was refused, the leases for
 * both, how each table's columns were treated, the small-group findings, which keys were made, opened and destroyed, and who was erased. It shows no
 * name, value, file name or identifier from any table: the evidence is about use, so it can be read by people who may not read the data.
 *
 * Every list is asked for a page at a time, newest first, so a list can be as long as the organisation's history. A bar of filters at the top (person, table,
 * from and to, and a search) narrows every list that can use them, and each list has its own choice of result, its own count of the lines that match, and its
 * own previous and next. A list that does not take a filter says so rather than quietly ignoring it.
 */

import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useIdentity } from "../../identity/IdentityContext";
import { ApiError } from "../../api/client";
import { useAuditPage, type AuditEvidence, type AuditSection, type LockedLease } from "../../api/locking";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { CLASSIFICATION_WORDS, TREATMENT_WORDS } from "./LockedColumns";
import { when } from "../../lib/when";

type Evidence = AuditEvidence;

const ACTION_WORDS: Record<string, string> = {
  make_org: "A key was made for the organisation",
  make_person: "A key was made for a person",
  unwrap: "A key was opened",
  destroy: "A key was destroyed",
};

/** Why the platform let a person open a locked file, in words. */
const BASIS_WORDS: Record<string, string> = {
  lease: "With an approver's permission",
  own_sample: "From a development sample drawn for them",
  synthetic: "Synthetic files, with no real person in them",
  own_development_output: "Made by their own run on development data",
  review: "To judge a result waiting for their decision",
};

/** The choices of result for each list, in words. The platform says which values a list takes; these are what they are called here. */
const RESULT_WORDS: Record<string, Record<string, string>> = {
  reads: { allowed: "Read", refused: "Refused" },
  column_leases: { requested: "Waiting", approved: "Approved", declined: "Declined", revoked: "Ended early" },
  file_reads: { allowed: "Opened", refused: "Refused" },
  file_leases: { requested: "Waiting", approved: "Approved", declined: "Declined", ended: "Ended or ran out" },
  keys: { make_org: "Made for the organisation", make_person: "Made for a person", unwrap: "Opened", destroy: "Destroyed" },
  small_groups: { accepted: "Risk accepted", will_change: "Contract will change", undecided: "Not decided" },
};

const SECTIONS = [
  { id: "audit-section-column-reads", label: "Reads", group: "Locked columns" },
  { id: "audit-section-column-leases", label: "Leases", group: "Locked columns" },
  { id: "audit-section-treatments", label: "How columns were treated", group: "Locked columns" },
  { id: "audit-section-small-groups", label: "Small groups", group: "Locked columns" },
  { id: "audit-section-file-reads", label: "Opens", group: "Locked files" },
  { id: "audit-section-file-leases", label: "Leases", group: "Locked files" },
  { id: "audit-section-keys", label: "Keys", group: "Keys and erasure" },
  { id: "audit-section-erasures", label: "People who were erased", group: "Keys and erasure" },
];

function plural(n: number, one: string, many: string = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

const by = (who: string | null | undefined, at: string | null | undefined) => `${who ? ` by ${who}` : ""}${at ? ` on ${when(at)}` : ""}`;

/** What became of a request for locked rows, one fact to a line: who decided it, and how it ended if it has. */
function rowLeaseLines(l: LockedLease): string[] {
  if (l.status === "requested") return ["Waiting for an approver"];
  if (l.status === "declined") return [`Declined${by(l.decided_by_label, l.decided_at)}`];
  const decided = `Approved${by(l.decided_by_label, l.decided_at)}`;
  if (l.status === "revoked") return [decided, `Ended early${by(l.revoked_by_label, l.revoked_at)}`];
  if (l.expires_at && new Date(l.expires_at) < new Date()) return [decided, `Ran out on ${when(l.expires_at)}`];
  return [decided, l.expires_at ? `In force until ${when(l.expires_at)}` : "In force"];
}

/** The same for a request to open locked files. A lease that was ended by nobody ran out. */
function fileLeaseLines(l: Evidence["file_leases"][number]): string[] {
  if (l.status === "requested") return ["Waiting for an approver"];
  if (l.status === "declined") return [`Declined${by(l.decided_by_label, l.decided_at)}`];
  const ended = l.status === "ended";
  if (ended && !l.decided_at) return [`Ended before it was decided${by(l.ended_by_label, l.ended_at)}`];
  const decided = `Approved${by(l.decided_by_label, l.decided_at)}`;
  if (ended) return [decided, l.ended_by_label ? `Ended early${by(l.ended_by_label, l.ended_at)}` : `Ran out on ${when(l.ended_at ?? l.expires_at)}`];
  if (l.expires_at && new Date(l.expires_at) < new Date()) return [decided, `Ran out on ${when(l.expires_at)}`];
  return [decided, l.expires_at ? `In force until ${when(l.expires_at)}` : "In force"];
}

function Lines({ lines }: { lines: string[] }) {
  return (
    <>
      {lines.map((line, i) => (
        <div key={i} className={i === 0 ? "" : "text-xs text-slate-600"}>
          {line}
        </div>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------------------------------------------- filters

interface Filters {
  person: string;
  table: string;
  file: string;
  from: string;
  to: string;
  q: string;
}
const NO_FILTERS: Filters = { person: "", table: "", file: "", from: "", to: "", q: "" };
const PAGE_SIZES = [10, 25, 50, 100];

/** The filters as the platform takes them. A date is the person's own day: from its start, to the start of the day after. */
function filterQuery(f: Filters) {
  const day = (iso: string, plus: number) => {
    const d = new Date(`${iso}T00:00:00`);
    d.setDate(d.getDate() + plus);
    return d.toISOString();
  };
  return {
    person: f.person.trim() || undefined,
    table: f.table.trim() || undefined,
    file: f.file.trim() || undefined,
    q: f.q.trim() || undefined,
    date_from: f.from ? day(f.from, 0) : undefined,
    date_to: f.to ? day(f.to, 1) : undefined,
  };
}

function FilterBar({ applied, onChange, onClear, size, onSize }: { applied: Filters; onChange: (f: Filters) => void; onClear: () => void; size: number; onSize: (n: number) => void }) {
  const [draft, setDraft] = useState(applied);
  // Typing is sent once it pauses, so a list is not asked for on every key.
  useEffect(() => {
    if (JSON.stringify(draft) === JSON.stringify(applied)) return;
    const id = setTimeout(() => onChange(draft), 350);
    return () => clearTimeout(id);
  }, [draft]); // eslint-disable-line react-hooks/exhaustive-deps
  // A search set from a list (a person code that was selected) shows in the bar.
  useEffect(() => {
    if (JSON.stringify(draft) !== JSON.stringify(applied)) setDraft(applied);
  }, [applied]); // eslint-disable-line react-hooks/exhaustive-deps
  const any = Object.values(draft).some(Boolean);
  const input = "rounded border border-slate-400 px-2 py-1 text-sm";
  return (
    <div className="mb-6 rounded border border-slate-200 bg-white p-3" data-testid="audit-filters">
      <p className="mb-2 text-sm font-medium text-slate-900">Find lines</p>
      <p className="mb-2 text-xs text-slate-600">
        These narrow every list below that can use them. Each list also has its own choice of result. A list that cannot use a filter says so.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Person
          <input data-testid="filter-person" className={`${input} w-44`} placeholder="Part of a name" value={draft.person} onChange={(e) => setDraft({ ...draft, person: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Table
          <input data-testid="filter-table" className={`${input} w-52`} placeholder="Part of a table name" value={draft.table} onChange={(e) => setDraft({ ...draft, table: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          File
          <input data-testid="filter-file" className={`${input} w-52`} placeholder="Part of a file name" value={draft.file} onChange={(e) => setDraft({ ...draft, file: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          From
          <input data-testid="filter-from" type="date" className={input} value={draft.from} onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          To (including this day)
          <input data-testid="filter-to" type="date" className={input} value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Search the words
          <input data-testid="filter-q" className={`${input} w-52`} placeholder="Reason, purpose, column" value={draft.q} onChange={(e) => setDraft({ ...draft, q: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Lines per page
          <select data-testid="filter-size" className={input} value={size} onChange={(e) => onSize(Number(e.target.value))}>
            {PAGE_SIZES.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          data-testid="filter-clear"
          disabled={!any}
          onClick={() => {
            setDraft(NO_FILTERS);
            onChange(NO_FILTERS);
            onClear();
          }}
          className="rounded border border-slate-500 px-3 py-1 text-sm text-slate-900 disabled:opacity-40"
        >
          Clear filters
        </button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------------------- the page parts

function Contents() {
  const groups = [...new Set(SECTIONS.map((s) => s.group))];
  return (
    <nav aria-label="Contents of this page" data-testid="audit-contents" className="mb-6 flex flex-col gap-1 text-sm">
      {groups.map((g) => (
        <p key={g} className="text-slate-700">
          <span className="font-medium text-slate-900">{g}: </span>
          {SECTIONS.filter((s) => s.group === g).map((s, i) => (
            <span key={s.id}>
              {i > 0 && ", "}
              <a href={`#${s.id}`} className="text-sky-700 underline">
                {s.label}
              </a>
            </span>
          ))}
        </p>
      ))}
    </nav>
  );
}

/** A section with its own heading and a line saying what it is for. */
function Block({ id, title, about, children }: { id: string; title: string; about?: string; children: ReactNode }) {
  return (
    <section id={id} className="mb-10 scroll-mt-4" aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`} className="mb-1 text-lg font-semibold text-slate-900">
        {title}
      </h3>
      {about && <p className="mb-2 text-sm text-slate-600">{about}</p>}
      {children}
    </section>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mb-8">
      <h2 className="mb-4 border-b border-slate-300 pb-1 text-xl font-semibold text-slate-900">{title}</h2>
      {children}
    </div>
  );
}

function Table({ head, children, testid }: { head: string[]; children: ReactNode; testid: string }) {
  return (
    <div className="data-card">
      <table className="data-table" data-testid={testid}>
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} className="py-2 pr-4 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

const FILTER_WORDS: Record<string, string> = { person: "person", table: "table", file: "file", date: "dates", q: "search" };

/**
 * One list, a page at a time: the choice of result, how many lines match, the lines, and previous and next.
 * The page starts again at the first whenever the filters or the result change.
 */
function PagedList<T>({
  section,
  testid,
  filters,
  cleared,
  size,
  code,
  onEnded,
  empty,
  children,
}: {
  section: AuditSection;
  testid: string;
  filters: Filters;
  /** Counts up each time the filters are cleared, which also puts every list's own choice of result back to All. */
  cleared: number;
  /** Lines to a page. Contracts are whole blocks of columns, so that list keeps its own small page. */
  size?: number;
  /** A person code selected in the keys list. It narrows the lists that carry the code, and no other. */
  code?: string;
  /** Called when the platform says the person is no longer signed in, so the page can say so once instead of in every list. */
  onEnded: () => void;
  empty: string;
  children: (rows: T[]) => ReactNode;
}) {
  const [page, setPage] = useState(1);
  const [result, setResult] = useState("");
  const query = { ...filterQuery(filters), ...(code ? { q: code } : {}) };
  const key = JSON.stringify(query);
  useEffect(() => setPage(1), [key, result, size]);
  useEffect(() => setResult(""), [cleared]);
  const list = useAuditPage<T>(section, { ...query, result: result || undefined, page, page_size: size });
  const signedOut = list.error instanceof ApiError && list.error.status === 401;
  useEffect(() => {
    if (signedOut) onEnded();
  }, [signedOut]); // eslint-disable-line react-hooks/exhaustive-deps
  const words = RESULT_WORDS[section];
  const active = [filters.person.trim() && "person", filters.table.trim() && "table", filters.file.trim() && "file", (filters.from || filters.to) && "date", filters.q.trim() && "q"].filter(
    Boolean,
  ) as string[];

  if (list.isLoading) return <Loading what="this list" />;
  if (signedOut) return null;
  if (list.error || !list.data) return <Failure error={list.error} what="this list" />;
  const d = list.data;
  const first = (d.page - 1) * d.page_size + 1;
  const last = first + d.rows.length - 1;
  const ignored = active.filter((a) => !d.can_filter.includes(a));
  const narrowed = active.length > 0 || Boolean(result) || Boolean(code);
  return (
    <div data-testid={`${testid}-list`} className={list.isPlaceholderData ? "opacity-60" : ""}>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <span data-testid={`${testid}-count`} className="text-slate-700">
          {list.isPlaceholderData ? "Updating" : narrowed ? `${plural(d.total, "line")} match` : plural(d.total, "line")}
        </span>
        {words && (
          <label className="flex items-center gap-2 text-slate-700">
            Result
            <select data-testid={`${testid}-result`} className="rounded border border-slate-400 px-2 py-1 text-sm" value={result} onChange={(e) => setResult(e.target.value)}>
              <option value="">All</option>
              {d.results.map((r) => (
                <option key={r} value={r}>
                  {words[r] ?? r}
                </option>
              ))}
            </select>
          </label>
        )}
        {ignored.length > 0 && (
          <span data-testid={`${testid}-ignored`} className="text-xs text-slate-600">
            This list cannot be narrowed by {ignored.map((a) => FILTER_WORDS[a]).join(" or ")}.
          </span>
        )}
      </div>
      {d.total === 0 ? (
        narrowed ? (
          <p data-testid={`${testid}-none`} className="rounded border border-slate-200 bg-white p-3 text-sm text-slate-700">
            No lines match. Change or clear the filters to see more.
          </p>
        ) : (
          <Empty what={empty} />
        )
      ) : (
        <>
          {children(d.rows)}
          <div className="mt-2 flex flex-wrap items-center gap-3 text-sm text-slate-700">
            <span data-testid={`${testid}-range`}>
              Showing {first} to {last} of {d.total}
            </span>
            <button
              type="button"
              data-testid={`${testid}-prev`}
              disabled={d.page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="rounded border border-slate-500 px-2 py-1 text-xs text-slate-900 disabled:opacity-40"
            >
              Previous
            </button>
            <button
              type="button"
              data-testid={`${testid}-next`}
              disabled={d.page >= d.pages}
              onClick={() => setPage((p) => Math.min(d.pages, p + 1))}
              className="rounded border border-slate-500 px-2 py-1 text-xs text-slate-900 disabled:opacity-40"
            >
              Next
            </button>
            <span className="text-xs text-slate-600">
              Page {d.page} of {d.pages}
            </span>
          </div>
        </>
      )}
    </div>
  );
}

/** Says which key the keys and erased lists are narrowed to, and gives the way back. */
function CodeChip({ code, onClear }: { code: string; onClear: () => void }) {
  if (!code) return null;
  return (
    <div data-testid="audit-code-chip" className="mb-2 flex flex-wrap items-center gap-2 rounded border border-sky-200 bg-sky-50 px-3 py-1.5 text-sm text-slate-800">
      <span>
        Showing only the key <span className="font-mono text-xs">{code}</span>
      </span>
      <button type="button" data-testid="clear-code" onClick={onClear} className="text-sky-700 underline">
        Show every key
      </button>
    </div>
  );
}

function Result({ allowed, note }: { allowed: boolean; note?: string | null }) {
  return (
    <>
      {allowed ? (
        <span className="rounded bg-teal-100 px-1.5 py-0.5 text-xs font-medium text-teal-900">Opened</span>
      ) : (
        <span className="rounded bg-red-100 px-1.5 py-0.5 text-xs font-medium text-red-900">Refused</span>
      )}
      {note && <div className="mt-1 text-xs text-slate-600">{note}</div>}
    </>
  );
}

export function LockingAudit() {
  const { principal } = useIdentity();
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [ended, setEnded] = useState(false);
  const location = useLocation();
  const [cleared, setCleared] = useState(0);
  const [size, setSize] = useState(25);
  /** A person code selected in a list: every line recorded for that key, in the keys list and the list of people who were erased. */
  const [code, setCode] = useState("");
  const follow = (c: string) => setCode(c);
  const may = principal?.roles.some((r) => r === "privacy_auditor" || r === "dpo") ?? false;

  if (!may) {
    return (
      <div>
        <Section level="page" title="Locked data audit">
          <p className="text-sm text-slate-700">This page is for the privacy auditor and the data protection officer.</p>
        </Section>
      </div>
    );
  }

  if (ended) {
    return (
      <div data-testid="locking-audit">
        <Section level="page" title="Locked data audit">
          <p data-testid="audit-signed-out" role="alert" className="max-w-xl rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
            Your sign-in has ended.{" "}
            <Link to="/auth/login" state={{ from: location }} className="font-medium underline">
              Sign in again
            </Link>{" "}
            to see the audit.
          </p>
        </Section>
      </div>
    );
  }

  return (
    <div data-testid="locking-audit">
      <Section
        level="page"
        title="Locked data audit"
        description="How locked columns and locked files were used in this organisation: who looked, who was refused, which permissions were given, and what was done to each column. No person's name and no value from any table appears here. Files are named, because this page is for the privacy auditor and the data protection officer only. Opening this page, and each search on it, is itself recorded. Every other decision on the platform is in Access decisions."
      >
        <Contents />
        <FilterBar applied={filters} onChange={setFilters} onClear={() => {
            setCleared((n) => n + 1);
            setCode("");
          }} size={size} onSize={setSize} />

        <Group title="Locked columns">
          <Block
            id="audit-section-column-reads"
            title="Who read locked columns, and who was refused"
            about="Each time somebody asked to read the real values in a locked column. Read means the values were shown. Refused means nothing was shown, and the reason is given. What for is the reason the person stated."
          >
            <PagedList<Evidence["reads"][number]> section="reads" testid="audit-reads" filters={filters} cleared={cleared} onEnded={() => setEnded(true)} size={size} empty="reads of locked columns yet">
              {(rows) => (
                <Table testid="audit-reads" head={["When", "Who", "Table", "Result", "What for"]}>
                  {rows.map((r, i) => (
                    <tr key={i} className="border-b border-slate-100 align-top">
                      <td className="py-2 pr-4">{when(r.at)}</td>
                      <td className="py-2 pr-4">{r.principal_label}</td>
                      <td className="py-2 pr-4">{r.table ?? ""}</td>
                      <td className="py-2 pr-4">
                        {r.allowed ? (
                          <span className="rounded bg-teal-100 px-1.5 py-0.5 text-xs font-medium text-teal-900">Read</span>
                        ) : (
                          <span className="rounded bg-red-100 px-1.5 py-0.5 text-xs font-medium text-red-900">Refused</span>
                        )}
                        {!r.allowed && r.reasons[0] && <div className="mt-1 text-xs text-slate-600">{r.reasons[0]}</div>}
                      </td>
                      <td className="py-2 pr-4 text-xs text-slate-600">
                        {r.purpose.replace(/^read locked columns:? ?/, "") || (r.allowed ? "as the owning department's approver" : "asked without a lease")}
                      </td>
                    </tr>
                  ))}
                </Table>
              )}
            </PagedList>
          </Block>

          <Block
            id="audit-section-column-leases"
            title="Leases for locked rows"
            about="A lease is a permission, given by an approver of the department that owns the table, to read a few rows of locked columns for a few days. Each request is listed, with who decided it and whether it is still in force."
          >
            <PagedList<Evidence["leases"][number]> section="column_leases" testid="audit-leases" filters={filters} cleared={cleared} onEnded={() => setEnded(true)} size={size} empty="leases yet">
              {(rows) => (
                <Table testid="audit-leases" head={["Asked", "Who", "Table", "What for", "Rows and days", "State"]}>
                  {rows.map((l) => (
                    <tr key={l.id} className="border-b border-slate-100 align-top">
                      <td className="py-2 pr-4">{when(l.requested_at)}</td>
                      <td className="py-2 pr-4">{l.principal_label ?? l.principal}</td>
                      <td className="py-2 pr-4">{l.dataset_name ? `${l.dataset_name}, version ${l.dataset_version}` : ""}</td>
                      <td className="py-2 pr-4">{l.purpose}</td>
                      <td className="py-2 pr-4">
                        {plural(l.row_limit, "row")} for {plural(l.days, "day")}
                      </td>
                      <td className="py-2 pr-4">
                        <Lines lines={rowLeaseLines(l)} />
                      </td>
                    </tr>
                  ))}
                </Table>
              )}
            </PagedList>
          </Block>

          <Block
            id="audit-section-treatments"
            title="How each table's columns were treated"
            about="Every table is sealed under a privacy contract that says, for each column, what kind of information it holds and what was done to it. Each block below names the tables sealed under one contract, then lists its columns. Locked columns can be read only with a lease. The latest contracts are shown first."
          >
            <PagedList<Evidence["contracts"][number]> section="contracts" testid="audit-contracts" filters={filters} cleared={cleared} onEnded={() => setEnded(true)} empty="tables sealed under a privacy contract yet">
              {(rows) => (
                <>
                  {rows.map((c) => (
                    <div key={c.schema_id} className="mb-4" data-testid="audit-contract">
                      <p className="text-sm font-medium text-slate-800" data-testid="audit-contract-tables">
                        {c.table_count === 0
                          ? "No table has been sealed under this contract yet"
                          : `${c.tables.slice(0, 2).join("; ")}${c.table_count > 2 ? ` and ${plural(c.table_count - 2, "other table")}` : ""}`}
                      </p>
                      <p className="mb-1 text-xs text-slate-600">
                        Contract {c.name}, version {c.version}
                      </p>
                      <Table testid="audit-treatments" head={["Column", "What it is", "What was done to it"]}>
                        {c.treatments.map((t) => (
                          <tr key={t.name} className="border-b border-slate-100">
                            <td className="py-1.5 pr-4 font-mono text-xs">{t.name}</td>
                            <td className="py-1.5 pr-4">{CLASSIFICATION_WORDS[t.classification]}</td>
                            <td className="py-1.5 pr-4">{TREATMENT_WORDS[t.treatment]}</td>
                          </tr>
                        ))}
                      </Table>
                    </div>
                  ))}
                </>
              )}
            </PagedList>
          </Block>

          <Block
            id="audit-section-small-groups"
            title="Small groups"
            about="When only a few people share the same readable details, such as postcode and age band, those details alone can point to one of them. For each table this shows how many people sit in groups smaller than the limit, and whether the owner accepted that or will change the contract."
          >
            <PagedList<Evidence["group_sizes"][number]> section="small_groups" testid="audit-group-sizes" filters={filters} cleared={cleared} onEnded={() => setEnded(true)} size={size} empty="group-size findings yet">
              {(rows) => (
                <Table testid="audit-group-sizes" head={["Table", "Counted", "People", "In small groups", "Decision"]}>
                  {rows.map((g) => (
                    <tr key={g.dataset_version_id} className="border-b border-slate-100 align-top">
                      <td className="py-2 pr-4">{g.table ?? ""}</td>
                      <td className="py-2 pr-4">{g.columns.length ? g.columns.join(", ") : "nothing to count"}</td>
                      <td className="py-2 pr-4">{g.people}</td>
                      <td className="py-2 pr-4">
                        {g.people_in_small_groups} (smaller than {g.threshold})
                      </td>
                      <td className="py-2 pr-4">
                        {g.decision === "accepted" ? "Risk accepted" : g.decision === "will_change" ? "Contract will change" : "Not decided"}
                        {g.decided_by_label ? ` by ${g.decided_by_label}` : ""}
                        {g.decided_at ? ` on ${when(g.decided_at)}` : ""}
                      </td>
                    </tr>
                  ))}
                </Table>
              )}
            </PagedList>
          </Block>
        </Group>

        <Group title="Locked files">
          <Block
            id="audit-section-file-reads"
            title="Who opened locked files, and who was refused"
            about="Each time a locked file was opened, or somebody was refused. Each line is one file, named here for the privacy auditor and the data protection officer. To see everyone who opened one file, put part of its name in File above. Result says why the platform allowed it. A refusal recorded before refusals named the file shows Not recorded."
          >
            <PagedList<Evidence["file_reads"][number]> section="file_reads" testid="audit-file-reads" filters={filters} cleared={cleared} onEnded={() => setEnded(true)} size={size} empty="openings of locked files yet">
              {(rows) => (
                <Table testid="audit-file-reads" head={["When", "Who", "Table", "File", "Where", "Result"]}>
                  {rows.map((f, i) => (
                    <tr key={i} className="border-b border-slate-100 align-top">
                      <td className="py-2 pr-4">{when(f.at)}</td>
                      <td className="py-2 pr-4">{f.principal_label}</td>
                      <td className="py-2 pr-4">{f.table ?? ""}</td>
                      <td className="py-2 pr-4" data-testid="audit-file-name">
                        {f.file ?? <span className="text-slate-500">Not recorded</span>}
                      </td>
                      <td className="py-2 pr-4">{f.via === "notebook" ? "In a notebook" : f.via ? "In the page" : <span className="text-slate-500">Not recorded</span>}</td>
                      <td className="py-2 pr-4">
                        <Result allowed={f.allowed} note={f.allowed ? (f.basis ? BASIS_WORDS[f.basis] ?? f.basis : null) : f.reason} />
                      </td>
                    </tr>
                  ))}
                </Table>
              )}
            </PagedList>
          </Block>

          <Block
            id="audit-section-file-leases"
            title="Leases for locked files"
            about="A lease is a permission, given by an approver of the department that owns the files, to open a few of them in the page or in a notebook for a few hours. Each request is listed, with who decided it and whether it is still in force. Put part of a file name in File above to see the leases that named that file."
          >
            <PagedList<Evidence["file_leases"][number]> section="file_leases" testid="audit-file-leases" filters={filters} cleared={cleared} onEnded={() => setEnded(true)} size={size} empty="leases for locked files yet">
              {(rows) => (
                <Table testid="audit-file-leases" head={["Asked", "Who", "Table", "What for", "Files and hours", "Where", "State"]}>
                  {rows.map((l) => (
                    <tr key={l.id} className="border-b border-slate-100 align-top">
                      <td className="py-2 pr-4">{when(l.requested_at)}</td>
                      <td className="py-2 pr-4">{l.principal_label}</td>
                      <td className="py-2 pr-4">{l.dataset_name ? `${l.dataset_name}, version ${l.dataset_version}` : ""}</td>
                      <td className="py-2 pr-4">{l.purpose}</td>
                      <td className="py-2 pr-4">
                        {plural(l.granted_count ?? l.requested_count, "file")} for {plural(l.granted_hours ?? l.hours, "hour")}
                      </td>
                      <td className="py-2 pr-4">{l.kind === "notebook" ? "In a notebook" : "In the page"}</td>
                      <td className="py-2 pr-4">
                        <Lines lines={fileLeaseLines(l)} />
                      </td>
                    </tr>
                  ))}
                </Table>
              )}
            </PagedList>
          </Block>
        </Group>

        <Group title="Keys and erasure">
          <Block
            id="audit-section-keys"
            title="Keys"
            about="Each person's locked values and files are locked under a key of their own. It is made when they first appear, opened when their locked values or files are read, and destroyed when they are erased. A person code stands in for the person and means nothing on its own, but it names one key. Select a person code to see everything recorded for that key: when it was made, each time it was opened and why, and when it was destroyed, here and in the list of people who were erased."
          >
            <CodeChip code={code} onClear={() => setCode("")} />
            <PagedList<Evidence["key_uses"][number]> section="keys" testid="audit-keys" filters={filters} cleared={cleared} onEnded={() => setEnded(true)} size={size} code={code} empty="key uses yet">
              {(rows) => (
                <Table testid="audit-keys" head={["When", "What happened", "By", "Person code"]}>
                  {rows.map((k, i) => (
                    <tr key={i} className="border-b border-slate-100">
                      <td className="py-1.5 pr-4">{when(k.at)}</td>
                      <td className="py-1.5 pr-4">
                        <div>{ACTION_WORDS[k.action] ?? k.action}</div>
                        {k.reason && <div className="text-xs text-slate-600">{k.reason}</div>}
                        {k.table && <div className="text-xs text-slate-600">Table: {k.table}</div>}
                        {k.file && <div className="text-xs text-slate-600">File: {k.file}</div>}
                      </td>
                      <td className="py-1.5 pr-4">{k.actor_label}</td>
                      <td className="py-1.5 pr-4">
                        {k.person_code && (
                          <button type="button" data-testid="follow-code" title="Show everything recorded for this key" onClick={() => follow(k.person_code!)} className="font-mono text-xs text-sky-700 underline">
                            {k.person_code}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </Table>
              )}
            </PagedList>
          </Block>

          <Block
            id="audit-section-erasures"
            title="People who were erased"
            about="People whose keys were destroyed, so that nobody can read their locked values or files again. Each is shown only by person code, with who asked for it."
          >
            <CodeChip code={code} onClear={() => setCode("")} />
            <PagedList<Evidence["erasures"][number]> section="erasures" testid="audit-erasures" filters={filters} cleared={cleared} onEnded={() => setEnded(true)} size={size} code={code} empty="erasures yet">
              {(rows) => (
                <Table testid="audit-erasures" head={["When", "Person code", "Asked by"]}>
                  {rows.map((e, i) => (
                    <tr key={i} className="border-b border-slate-100">
                      <td className="py-1.5 pr-4">{when(e.at)}</td>
                      <td className="py-1.5 pr-4">
                        <button type="button" data-testid="follow-code" title="Show everything recorded for this key" onClick={() => follow(e.record_id.replace(/^person:/, ""))} className="font-mono text-xs text-sky-700 underline">
                          {e.record_id.replace(/^person:/, "")}
                        </button>
                      </td>
                      <td className="py-1.5 pr-4">{e.requested_by_label}</td>
                    </tr>
                  ))}
                </Table>
              )}
            </PagedList>
          </Block>
        </Group>
      </Section>
    </div>
  );
}
