/**
 * Real page controls, not a "show more" that only ever grows the same list.
 *
 * "Show more" (raising a limit and re-fetching everything up to it) was
 * this console's first attempt at a long list, on `Datasets.tsx` and
 * `Agents.tsx`. It never actually shortens anything: the page you're
 * looking at just gets longer forever, so finding something specific means
 * ever more scrolling the longer the platform has been used. A fixed page
 * size or reachable pages you can jump between actually bounds how much
 * is on screen at once, which is the point.
 *
 * Deliberately dumb: it knows nothing about what it is paging. `page` is
 * 1-based (matches how a person reads "page 3 of 12", not how an array
 * indexes), and the caller owns that state and the offset math
 * (`(page - 1) * pageSize`) it implies -- this component only renders
 * controls and reports which page was picked.
 */

const WINDOW = 1;

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  if (totalPages <= 1) return null;

  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  // Always first, last, the current page, and one neighbour each side;
  // everything else collapses into a single "…" rather than a page-number
  // wall nobody actually clicks through one at a time.
  const pages: (number | "…")[] = [];
  for (let p = 1; p <= totalPages; p++) {
    if (
      p === 1 ||
      p === totalPages ||
      (p >= page - WINDOW && p <= page + WINDOW)
    ) {
      pages.push(p);
    } else if (pages[pages.length - 1] !== "…") {
      pages.push("…");
    }
  }

  return (
    <nav
      aria-label="Pagination"
      data-testid="pagination"
      className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm"
    >
      <span className="text-slate-500">
        Showing {from}&ndash;{to} of {total}
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          data-testid="pagination-prev"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          className="rounded border border-slate-300 px-2.5 py-1 text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Previous
        </button>
        {pages.map((p, i) =>
          p === "…" ? (
            <span key={`ellipsis-${i}`} className="px-1.5 text-slate-400">
              &hellip;
            </span>
          ) : (
            <button
              key={p}
              type="button"
              data-testid={`pagination-page-${p}`}
              aria-current={p === page ? "page" : undefined}
              onClick={() => onPageChange(p)}
              className={`min-w-[2rem] rounded px-2.5 py-1 ${
                p === page
                  ? "bg-indigo-500 font-medium text-white"
                  : "text-slate-700 hover:bg-slate-100"
              }`}
            >
              {p}
            </button>
          ),
        )}
        <button
          type="button"
          data-testid="pagination-next"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
          className="rounded border border-slate-300 px-2.5 py-1 text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </nav>
  );
}
