"""Keep the verification history, and render it as one searchable page.

Two jobs, deliberately in one file because they share the record's shape:

    python verify/report.py --add run.json   append a run, then re-render
    python verify/report.py --add -          the same, reading stdin
    python verify/report.py --add-after x.json   append what was checked after a run's cleanup, then re-render
    python verify/report.py                  re-render from what is already there

The history is verify/history/runs.jsonl, one run per line, append only. The
page is verify/history/index.html, rewritten in full each time. Both are
gitignored: they are this machine's record of its own runs, not source.

run_all.py produces the run object. It runs inside the munitas-api container
where /verify is read-only, so it cannot append here itself; run-verification.ps1
carries the JSON across.

A run is recorded before its cleanup, because cleanup deletes data and nothing may be deleted that no page can account for. What is checked
after the cleanup (that no test tenants were left behind) therefore cannot be in the run's own line, and the file is append-only, so it goes in a
second line of its own: {"kind": "after_run", "run_started_at": <the run's started_at>, "results": [...]}. The loader attaches it to that run, and
the page shows it beside the run's other results. The run's own line is never rewritten.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
# MUNITAS_VERIFY_HISTORY_DIR exists so a check can exercise this against a folder of its own, not this machine's real record.
HISTORY_DIR = Path(os.environ["MUNITAS_VERIFY_HISTORY_DIR"]) if os.environ.get("MUNITAS_VERIFY_HISTORY_DIR") else HERE / "history"
RUNS_PATH = HISTORY_DIR / "runs.jsonl"
PAGE_PATH = HISTORY_DIR / "index.html"


def load_runs() -> list[dict]:
    if not RUNS_PATH.exists():
        return []
    runs, afters = [], []
    for line in RUNS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            # One corrupt line must not cost the whole history. Say so rather
            # than dropping it silently, which would look like a clean read.
            print(f"skipping unreadable history line: {line[:80]}", file=sys.stderr)
            continue
        (afters if record.get("kind") == "after_run" else runs).append(record)
    runs.sort(key=lambda r: r.get("started_at", ""))
    by_start = {r.get("started_at"): r for r in runs}
    for after in afters:
        run = by_start.get(after.get("run_started_at"))
        if run is None:
            # Said, not dropped: a line that names a run the file does not hold is a mistake somebody should see.
            print(f"skipping an after-run line for a run that is not in the history: {str(after.get('run_started_at'))[:40]}", file=sys.stderr)
            continue
        run.setdefault("after_results", []).extend(after.get("results", []))
    return runs


def append_run(source: str) -> None:
    # utf-8-sig, not utf-8: Windows PowerShell writes a byte order mark that
    # json.loads refuses, and this file is usually written by PowerShell.
    # The codec is a no-op when there is no mark.
    raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8-sig")
    run = json.loads(raw.lstrip("﻿"))
    if "results" not in run or "started_at" not in run:
        raise SystemExit("that JSON is not a run: it needs 'started_at' and 'results'")
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with RUNS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(run, separators=(",", ":")) + "\n")


def append_after_run(source: str) -> None:
    """Append what was checked after a run's cleanup, tied to the run by its started_at. Refused when no such run is recorded."""
    raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8-sig")
    record = json.loads(raw.lstrip("﻿"))
    if "run_started_at" not in record or not record.get("results"):
        raise SystemExit("that JSON is not an after-run record: it needs 'run_started_at' and 'results'")
    if not any(r.get("started_at") == record["run_started_at"] for r in load_runs()):
        raise SystemExit(f"no recorded run started at {record['run_started_at']}, so there is nothing to attach this to")
    record["kind"] = "after_run"
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with RUNS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def flatten(runs: list[dict]) -> list[dict]:
    rows = []
    for run in runs:
        # The suite's own results, then whatever was checked after its cleanup. Both belong to the run, so both carry its start time.
        phases = (("suite", run.get("results", [])), ("after", run.get("after_results", [])))
        for phase, result in ((phase, result) for phase, results in phases for result in results):
            rows.append(
                {
                    "phase": phase,
                    "detail": result.get("detail", ""),
                    "at": run.get("started_at", ""),
                    "host": run.get("host", ""),
                    "check": result.get("check", ""),
                    "label": result.get("label", ""),
                    "script": result.get("script", ""),
                    "status": result.get("status", ""),
                    # Absent on runs recorded before skips were counted, and
                    # null where a script reported no counts: both stay None
                    # rather than becoming a made-up zero.
                    "skipped": result.get("skipped"),
                    "seconds": result.get("seconds", 0),
                }
            )
    return rows


def summarise(rows: list[dict]) -> list[dict]:
    """One row per check: how it stands, and when it last went wrong."""
    by_check: dict[str, list[dict]] = {}
    for row in rows:
        by_check.setdefault(row["check"], []).append(row)

    summary = []
    for check, entries in by_check.items():
        entries.sort(key=lambda r: r["at"])
        failures = [e for e in entries if e["status"] == "fail"]
        durations = [e["seconds"] for e in entries if isinstance(e["seconds"], (int, float))]
        summary.append(
            {
                "check": check,
                "label": entries[-1]["label"],
                "script": entries[-1]["script"],
                "latest": entries[-1]["status"],
                "latest_skipped": entries[-1]["skipped"],
                "latest_at": entries[-1]["at"],
                "runs": len(entries),
                "fails": len(failures),
                "last_failed_at": failures[-1]["at"] if failures else "",
                "median_seconds": round(statistics.median(durations), 2) if durations else 0,
                "max_seconds": round(max(durations), 2) if durations else 0,
                "total_seconds": round(sum(durations), 2) if durations else 0,
            }
        )
    summary.sort(key=lambda s: (-s["median_seconds"], s["check"]))
    return summary


def render(runs: list[dict]) -> str:
    rows = flatten(runs)
    summary = summarise(rows)
    payload = json.dumps(
        {"runs": runs, "rows": rows, "summary": summary},
        separators=(",", ":"),
    ).replace("</", "<\\/")
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return PAGE_TEMPLATE.replace("__PAYLOAD__", payload).replace("__GENERATED__", generated)


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Munitas verification history</title>
<style>
  :root {
    --bg: #ffffff; --fg: #14171a; --muted: #5b6570; --line: #e2e6ea;
    --panel: #f6f8fa; --pass: #1a7f47; --pass-bg: #e6f4ec;
    --fail: #b3261e; --fail-bg: #fbeae9; --accent: #2b5fd9;
    --skip: #8a5a00; --skip-bg: #fdf0d5;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #14171a; --fg: #e8ecef; --muted: #98a2ad; --line: #2b3238;
      --panel: #1c2126; --pass: #4ec27f; --pass-bg: #16301f;
      --fail: #f2837c; --fail-bg: #331a19; --accent: #7aa2f7;
      --skip: #e0b34f; --skip-bg: #3a2f14;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px; background: var(--bg); color: var(--fg);
    font: 14px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }
  h1 { font-size: 20px; margin: 0 0 4px; }
  h2 { font-size: 15px; margin: 32px 0 8px; }
  .sub { color: var(--muted); margin: 0 0 20px; }
  .cards { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 8px; }
  .card {
    background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
    padding: 12px 16px; min-width: 132px;
  }
  .card .k { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
  .card .v { font-size: 22px; font-weight: 600; margin-top: 2px; }
  .controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 12px 0; }
  input, select, button {
    font: inherit; color: var(--fg); background: var(--bg);
    border: 1px solid var(--line); border-radius: 6px; padding: 6px 9px;
  }
  input:focus, select:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
  button { cursor: pointer; background: var(--panel); }
  label.f { color: var(--muted); font-size: 12px; display: flex; align-items: center; gap: 5px; }
  .wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
  table { border-collapse: collapse; width: 100%; min-width: 720px; }
  th, td { text-align: left; padding: 7px 12px; border-bottom: 1px solid var(--line); white-space: nowrap; }
  th { background: var(--panel); font-weight: 600; cursor: pointer; user-select: none; position: sticky; top: 0; }
  th span.dir { color: var(--muted); font-weight: 400; }
  tbody tr:last-child td { border-bottom: none; }
  td.wide { white-space: normal; min-width: 260px; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 99px; font-size: 12px; font-weight: 600; }
  .pill.pass { color: var(--pass); background: var(--pass-bg); }
  .pill.fail { color: var(--fail); background: var(--fail-bg); }
  .pill.skip { color: var(--skip); background: var(--skip-bg); }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .never { color: var(--muted); }
  .empty { padding: 24px; text-align: center; color: var(--muted); }
  .detail { color: var(--muted); font-size: 12px; margin-top: 2px; }
  .after { color: var(--muted); font-size: 12px; }
  footer { margin-top: 32px; color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<h1>Munitas verification history</h1>
<p class="sub">Every run of <code>run-verification.ps1</code>, newest first. Times are this machine's local time. Generated __GENERATED__.</p>

<div class="cards" id="cards"></div>

<h2>By check</h2>
<p class="sub">Sorted slowest first. Click a heading to sort by it.</p>
<div class="wrap">
  <table id="summary">
    <thead><tr>
      <th data-k="check">Check</th>
      <th data-k="label">What it proves</th>
      <th data-k="latest">Latest</th>
      <th data-k="last_failed_at">Last failed</th>
      <th data-k="runs" class="num">Runs</th>
      <th data-k="fails" class="num">Fails</th>
      <th data-k="median_seconds" class="num">Median s</th>
      <th data-k="max_seconds" class="num">Slowest s</th>
    </tr></thead>
    <tbody></tbody>
  </table>
</div>

<h2>Every result</h2>
<div class="controls">
  <input id="q" type="search" placeholder="Search check, wording or script" size="32">
  <select id="status">
    <option value="">Any result</option>
    <option value="fail">Failures only</option>
    <option value="pass">Passes only</option>
    <option value="skip">Skipped only</option>
  </select>
  <label class="f">From <input id="from" type="date"></label>
  <label class="f">To <input id="to" type="date"></label>
  <button id="clear">Clear</button>
  <span class="f" id="count"></span>
</div>
<div class="wrap">
  <table id="detail">
    <thead><tr>
      <th data-k="at">When</th>
      <th data-k="check">Check</th>
      <th data-k="label">What it proves</th>
      <th data-k="status">Result</th>
      <th data-k="seconds" class="num">Seconds</th>
      <th data-k="script">Script</th>
    </tr></thead>
    <tbody></tbody>
  </table>
</div>

<footer>Written by verify/report.py. Re-run the suite to update it.</footer>

<script type="application/json" id="data">__PAYLOAD__</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);

const local = iso => {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
};
const dayOf = iso => { const d = new Date(iso); return isNaN(d) ? "" : `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`; };
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
// A pass that skipped some checks says so beside the word, so it cannot be read
// as everything having run. "skipped" is null on runs recorded before skips
// were counted, which shows nothing rather than an invented zero.
const pill = (s, skipped) => {
  const cls = s === "pass" || s === "skip" ? s : "fail";
  const extra = s === "pass" && skipped ? ` &middot; ${skipped} skipped` : "";
  return `<span class="pill ${cls}">${cls}${extra}</span>`;
};

function cards() {
  const runs = DATA.runs;
  const last = runs[runs.length - 1];
  const failing = DATA.summary.filter(s => s.latest === "fail").length;
  const neverFailed = DATA.summary.filter(s => !s.last_failed_at).length;
  const skipping = DATA.summary.filter(s => s.latest === "skip").length;
  // What was checked after the last run's cleanup. A run recorded before that existed says "not checked", not "clean".
  const after = last && (last.after_results || []);
  const afterWord = !last ? "-" : !after.length ? "not checked"
    : after.every(r => r.status === "pass") ? "clean" : "problem: see below";
  const items = [
    ["Runs recorded", runs.length],
    ["Checks tracked", DATA.summary.length],
    ["Failing now", failing],
    ["Skipped entirely now", skipping],
    ["Never failed", neverFailed],
    ["Last run", last ? local(last.started_at) : "none"],
    ["Last run took", last ? `${last.seconds}s` : "-"],
    ["After the last run's cleanup", afterWord],
  ];
  document.getElementById("cards").innerHTML = items
    .map(([k, v]) => `<div class="card"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`)
    .join("");
}

function sortable(tableId, rows, draw) {
  let key = null, dir = 1;
  const table = document.getElementById(tableId);
  const paint = () => {
    table.querySelectorAll("th").forEach(th => {
      const s = th.querySelector("span.dir");
      if (s) s.remove();
      if (th.dataset.k === key) th.insertAdjacentHTML("beforeend", `<span class="dir"> ${dir > 0 ? "\\u2191" : "\\u2193"}</span>`);
    });
  };
  table.querySelectorAll("th").forEach(th => th.addEventListener("click", () => {
    const k = th.dataset.k;
    dir = key === k ? -dir : 1;
    key = k;
    rows().sort((a, b) => {
      const x = a[k], y = b[k];
      if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
      return String(x).localeCompare(String(y)) * dir;
    });
    paint();
    draw();
  }));
}

let summaryRows = DATA.summary.slice();
function drawSummary() {
  const body = document.querySelector("#summary tbody");
  if (!summaryRows.length) { body.innerHTML = `<tr><td colspan="8" class="empty">No runs recorded yet.</td></tr>`; return; }
  body.innerHTML = summaryRows.map(s => `<tr>
    <td>${esc(s.check)}</td>
    <td class="wide">${esc(s.label)}</td>
    <td>${pill(s.latest, s.latest_skipped)}</td>
    <td>${s.last_failed_at ? esc(local(s.last_failed_at)) : '<span class="never">never</span>'}</td>
    <td class="num">${s.runs}</td>
    <td class="num">${s.fails}</td>
    <td class="num">${s.median_seconds}</td>
    <td class="num">${s.max_seconds}</td>
  </tr>`).join("");
}

let detailRows = DATA.rows.slice().reverse();
function filtered() {
  const q = document.getElementById("q").value.trim().toLowerCase();
  const st = document.getElementById("status").value;
  const from = document.getElementById("from").value;
  const to = document.getElementById("to").value;
  return detailRows.filter(r => {
    if (st && r.status !== st) return false;
    const day = dayOf(r.at);
    if (from && day < from) return false;
    if (to && day > to) return false;
    if (q && !`${r.check} ${r.label} ${r.script}`.toLowerCase().includes(q)) return false;
    return true;
  });
}
function drawDetail() {
  const rows = filtered();
  const body = document.querySelector("#detail tbody");
  document.getElementById("count").textContent = `${rows.length} of ${detailRows.length} results`;
  if (!rows.length) { body.innerHTML = `<tr><td colspan="6" class="empty">Nothing matches those filters.</td></tr>`; return; }
  body.innerHTML = rows.map(r => `<tr>
    <td>${esc(local(r.at))}</td>
    <td>${esc(r.check)}</td>
    <td class="wide">${esc(r.label)}${r.phase === "after" ? ' <span class="after">(after the cleanup)</span>' : ""}${r.detail ? `<div class="detail">${esc(r.detail)}</div>` : ""}</td>
    <td>${pill(r.status, r.skipped)}</td>
    <td class="num">${r.seconds}</td>
    <td>${esc(r.script)}</td>
  </tr>`).join("");
}

["q", "status", "from", "to"].forEach(id =>
  document.getElementById(id).addEventListener("input", drawDetail));
document.getElementById("clear").addEventListener("click", () => {
  ["q", "from", "to"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("status").value = "";
  drawDetail();
});

sortable("summary", () => summaryRows, drawSummary);
sortable("detail", () => detailRows, drawDetail);
cards();
drawSummary();
drawDetail();
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--add", metavar="RUN_JSON", help="run JSON to append first, or - for stdin")
    parser.add_argument("--add-after", metavar="AFTER_JSON",
                        help="what was checked after a recorded run's cleanup, as a second line tied to that run, or - for stdin")
    args = parser.parse_args()

    if args.add:
        append_run(args.add)
    if args.add_after:
        append_after_run(args.add_after)

    runs = load_runs()
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    PAGE_PATH.write_text(render(runs), encoding="utf-8")
    print(f"{len(runs)} runs in the history. Page written to {PAGE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
