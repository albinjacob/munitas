r"""Write docs/internal/dev/api-callers.html: every API route, who calls it, and how it knows who is calling.

    .venv\Scripts\python.exe docs\tools\generate_api_callers.py

One row per route and method. The columns say, for each kind of caller (the console, the platform's workers, the agent runtime,
the scripts, the verification checks, and anything else), whether the repository calls that route, and then how the route
authenticates its caller and what decides whether that caller may.

Generated, not written, so it cannot quietly disagree with the code:

  * The routes, and how each one authenticates, come from the running API's own route table: which FastAPI dependencies it carries,
    which headers its handler reads. The authorization column lists the policy decisions (`opa.may_*`) and the organisation and
    identity checks its handler source calls. Needs the stack up (it asks the API container).
  * The callers come from scanning the repository for each route's path, and for the HTTP verb near it. A tick means the path and
    the verb were found together; a half tick means the path was found and the verb could not be told, so the route's other
    methods on the same path may be the ones called. It is a scan of the source, so a path built from pieces at run time is not
    seen, and a tick is evidence that a caller exists and not that it ran.

The page is self-contained and opens from the filesystem. It lives under docs/internal, which is never published.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent
ROOT = DOCS.parent
OUT = DOCS / "internal" / "dev" / "api-callers.html"
CONTAINER = "munitas-munitas-api-1"
DISTRO = "Ubuntu-20.04"

# ---------------------------------------------------------------------------------------------- what the API says about itself

INTROSPECT = r'''
import inspect, json, re, sys
sys.path.insert(0, "/app")
import warnings
warnings.filterwarnings("ignore")
from app.main import app
from fastapi.routing import APIRoute

def deps(dependant):
    names = []
    for d in dependant.dependencies:
        names.append(getattr(d.call, "__name__", str(d.call)))
        names += deps(d)
    return names

out = []
for r in app.routes:
    if not isinstance(r, APIRoute):
        continue
    fn = r.endpoint
    try:
        source = inspect.getsource(fn)
        module = inspect.getsourcefile(fn).replace("/app/", "")
    except Exception:
        source, module = "", "?"
    doc = inspect.getdoc(fn) or ""
    first = re.split(r"(?<=[.!?])\s", doc.strip().replace("\n", " "), maxsplit=1)[0] if doc else ""
    for method in sorted(r.methods - {"HEAD"}):
        out.append({
            "method": method, "path": r.path, "module": module, "handler": fn.__name__, "summary": first[:240],
            "dependencies": sorted(set(deps(r.dependant))),
            "headers": sorted(p.name for p in r.dependant.header_params),
            "policy": sorted(set(re.findall(r"opa\.(may_[a-z_]+|evaluate)\(", source))),
            "must_be": "auth.must_be(" in source,
            "mine": "_mine(" in source or "_own_warehouse(" in source,
            "role_in_code": sorted(set(re.findall(r"\"([a-z_]+)\" in (?:session|identity|caller)\[\"roles\"\]", source))),
        })
print("@@ROUTES@@" + json.dumps(out))
'''


def routes_from_api() -> list[dict]:
    done = subprocess.run(["wsl.exe", "-d", DISTRO, "--", "docker", "exec", "-i", CONTAINER, "python", "-"],
                          input=INTROSPECT, capture_output=True, text=True)
    marker = "@@ROUTES@@"
    for line in done.stdout.splitlines():
        if line.startswith(marker):
            return json.loads(line[len(marker):])
    raise SystemExit("could not read the route table from the API container. Is the stack up?\n" + (done.stderr or done.stdout)[-600:])


# ---------------------------------------------------------------------------------------------- how each route authenticates

OPEN_ON_PURPOSE = {
    ("GET", "/health"): "None, on purpose: liveness for monitors and the start-up scripts",
    ("GET", "/legal-exports/signing-key"): "None, on purpose: the public half of the key that signs a legal export",
    ("GET", "/legal-exports/download/{token}"): "The token in the path is the credential (a few uses, expires)",
    ("POST", "/iceberg/v1/oauth/tokens"): "None, on purpose: the same refusal for everybody, reads and writes nothing",
    ("GET", "/policy/roles"): "None, on purpose: reference material, readable before signing in",
}
SPECIAL = {
    ("POST", "/credentials"): "Worker token, or a task credential naming the principal, or the signed-in person asking as themselves",
    ("POST", "/storage-keys/pipeline"): "A task credential (its own organisation's key only), or the worker token with the organisation named",
    ("POST", "/dataset-versions"): "Worker token only",
}
DEPENDENCY_LABEL = [
    ("catalog_principal", "Catalog bearer token (issued to a signed-in person, expires, can be revoked)"),
    ("_claim", "Table job credential (signed, for one job)"),
    ("worker_only", "Worker token only"),
    ("_worker", "Worker token only"),
    ("person_or_worker", "Signed-in person, or the worker token"),
    ("organisation_scope", "Signed-in person, worker token, or a run credential, limited to one organisation"),
    ("current_session_while_closing", "Signed-in person (also while their organisation is closing down)"),
    ("current_session", "Signed-in person"),
]


def authentication(route: dict) -> tuple[str, str]:
    """(label, kind) where kind is a short class used for filtering."""
    key = (route["method"], route["path"])
    if key in OPEN_ON_PURPOSE:
        return OPEN_ON_PURPOSE[key], "open"
    if key in SPECIAL:
        return SPECIAL[key], "worker" if "Worker token only" == SPECIAL[key] else "mixed"
    found = [label for name, label in DEPENDENCY_LABEL if name in route["dependencies"]]
    seen: list[str] = []
    for label in found:
        if label not in seen:
            seen.append(label)
    if seen:
        kinds = {"Worker token only": "worker", "Table job credential (signed, for one job)": "job"}
        first = seen[0]
        kind = kinds.get(first, "catalog" if first.startswith("Catalog") else "person" if first.startswith("Signed-in person (") or first == "Signed-in person"
                         else "mixed")
        return "; and ".join(seen), kind
    if {"x_worker_token", "x_task_credential"} & set(route["headers"]):
        return "Worker token or a task credential, checked in the handler", "mixed"
    return "NONE: no authentication check found", "none"


def authorization(route: dict) -> str:
    parts: list[str] = []
    if route["policy"]:
        parts.append("Policy decision: " + ", ".join(p for p in route["policy"]))
    if "organisation_scope" in route["dependencies"]:
        parts.append("Another organisation's records are not found")
    if route["must_be"]:
        parts.append("Acts in the caller's own organisation, and only as themselves")
    elif route["mine"]:
        parts.append("Limited to the caller's own organisation")
    if route["role_in_code"]:
        parts.append("Role checked in code: " + ", ".join(route["role_in_code"]))
    if "catalog_principal" in route["dependencies"]:
        parts.append("The reading rules of the person the token names")
    return "; ".join(parts) or "None beyond who is calling"


# ---------------------------------------------------------------------------------------------- who calls what

CALLERS = [
    ("console", "Console", ["web/src"], {".ts", ".tsx"}),
    ("worker", "Workers", ["worker", "platform/tablejob"], {".py"}),
    ("agent", "Agent runtime and test agents", ["agent", "test-agents"], {".py"}),
    ("scripts", "Scripts", ["scripts", "start-dev.ps1", "stop-dev.ps1", "run-verification.ps1", "wsl-docker.ps1", "ports_config.py"], {".py", ".ps1", ".sh"}),
    ("checks", "Checks", ["verify", "web/tests", "web/walkthroughs"], {".py", ".ts"}),
    ("other", "Other", ["docs/tools", "docker-compose.yml", "infra"], {".py", ".yml", ".yaml", ".sh", ".json"}),
]
SKIP_PARTS = {"node_modules", ".venv", "__pycache__", "dist", "test-results", "playwright-report"}
# A path is also seen inside the console's generated API types and the published reference; those describe the API, they do not call it.
SKIP_FILES = {"openapi.json", "api-reference.html", "generate_api_callers.py"}


def files_of(entries: list[str], suffixes: set[str]) -> list[Path]:
    out: list[Path] = []
    for entry in entries:
        base = ROOT / entry
        if base.is_file():
            out.append(base)
        elif base.is_dir():
            out += [p for p in base.rglob("*") if p.is_file() and p.suffix in suffixes and not (set(p.parts) & SKIP_PARTS)
                    and p.name not in SKIP_FILES]
    return out


PARAM = r"(?:\{[^}/]*\}|\$\{[^}]*\}|[A-Za-z0-9_\-.:]+)"


def path_regex(path: str) -> tuple[re.Pattern, int]:
    """A pattern that finds this route's path written in source, and how many fixed characters it has (its specificity)."""
    pieces = re.split(r"(\{[^}]+\})", path)
    pattern, fixed = "", 0
    for piece in pieces:
        if piece.startswith("{") and piece.endswith("}"):
            pattern += r".+" if ":path" in piece else PARAM
        else:
            pattern += re.escape(piece)
            fixed += len(piece)
    return re.compile(pattern + r"(?=[\"'`?#\s,)\]}]|$|\\n)"), fixed


VERBS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def verb_near(text: str, start: int, end: int) -> str | None:
    """The HTTP verb written closest before a path (api.post(, httpx.post(, "POST",) or in the options after it (method: "POST")."""
    before = text[max(0, start - 140):start]
    best, best_at = None, -1
    for verb in VERBS:
        for pattern in (rf"\.{verb.lower()}\b", rf"\b{verb.lower()}\(", rf"[\"']{verb}[\"']", rf"-X\s+{verb}\b", rf"\b{verb}\s*$", rf"\bmethod\s*[:=]\s*[\"']{verb}[\"']"):
            for m in re.finditer(pattern, before):
                if m.start() > best_at:
                    best, best_at = verb, m.start()
    if best:
        return best
    after = text[end:end + 160]
    m = re.search(r"\bmethod\s*[:=]\s*[\"']([A-Za-z]+)[\"']", after)
    return m.group(1).upper() if m and m.group(1).upper() in VERBS else None


def scan(routes: list[dict]) -> dict[tuple[str, str], dict[str, dict]]:
    """{(method, path): {caller: {"exact": [files], "path_only": [files]}}}"""
    compiled = [(r, *path_regex(r["path"])) for r in routes]
    by_path: dict[str, list[dict]] = {}
    for r in routes:
        by_path.setdefault(r["path"], []).append(r)
    result: dict[tuple[str, str], dict[str, dict]] = {}
    for key, _label, entries, suffixes in CALLERS:
        for file in files_of(entries, suffixes):
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = file.relative_to(ROOT).as_posix()
            hits: list[tuple[int, int, int, str]] = []  # start, end, fixed, path
            for route, pattern, fixed in compiled:
                if route["method"] != by_path[route["path"]][0]["method"]:
                    continue  # one pass per distinct path
                for m in pattern.finditer(text):
                    hits.append((m.start(), m.end(), fixed, route["path"]))
            # A span claimed by several paths (/datasets/{id} also fits /datasets/register) belongs to the most specific one.
            hits.sort(key=lambda h: (h[0], -h[2]))
            kept: list[tuple[int, int, int, str]] = []
            for h in hits:
                if any(k[0] == h[0] or (k[0] <= h[0] < k[1]) and k[2] >= h[2] for k in kept):
                    if any(k[0] == h[0] and k[2] >= h[2] for k in kept) or any(k[0] <= h[0] < k[1] and k[2] > h[2] for k in kept):
                        continue
                kept.append(h)
            for start, end, _fixed, path in kept:
                verb = verb_near(text, start, end)
                if verb is None and key == "console":
                    verb = "GET"  # the console's own helper reads with a bare api(path); every write names its verb
                for route in by_path[path]:
                    slot = result.setdefault((route["method"], route["path"]), {}).setdefault(key, {"exact": set(), "path_only": set()})
                    if verb == route["method"]:
                        slot["exact"].add(rel)
                    elif verb is None:
                        slot["path_only"].add(rel)
    return result


# ---------------------------------------------------------------------------------------------- the page

STYLE = """
:root { --bg:#f7f8fa; --ink:#1b2430; --faint:#5d6b7a; --line:#d9dee5; --card:#ffffff; --head:#eef1f5; --tick:#17803d; --half:#9a6b00;
        --none:#b42318; --open:#9a6b00; --person:#0b5cad; --worker:#6b3fa0; --mixed:#0b6b6b; --catalog:#4a5a1a; --job:#a03f6b; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#12161c; --ink:#e6eaf0; --faint:#9aa7b6; --line:#2a323c; --card:#1a2028;
        --head:#222a34; --tick:#4ade80; --half:#fbbf24; --none:#f87171; --open:#fbbf24; --person:#60a5fa; --worker:#c4a1f0; --mixed:#5eead4;
        --catalog:#bef264; --job:#f0a1c4; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
header { padding:20px 24px 8px; } h1 { margin:0 0 4px; font-size:20px; } p.lead { margin:4px 0; color:var(--faint); max-width:110ch; }
.bar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; padding:10px 24px; position:sticky; top:0; background:var(--bg); z-index:3; border-bottom:1px solid var(--line); }
.bar input, .bar select { font:inherit; padding:5px 8px; border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--ink); }
.bar input { min-width:260px; } .count { color:var(--faint); }
.summary { display:flex; flex-wrap:wrap; gap:8px; padding:10px 24px; }
.chip { border:1px solid var(--line); border-radius:999px; padding:2px 10px; background:var(--card); font-size:12px; }
.wrap { padding:0 24px 40px; overflow-x:auto; }
table { border-collapse:collapse; width:100%; min-width:1250px; background:var(--card); }
th, td { border:1px solid var(--line); padding:6px 8px; vertical-align:top; text-align:left; }
th { background:var(--head); position:sticky; top:48px; z-index:2; font-size:12px; text-transform:uppercase; letter-spacing:.03em; }
td.c { text-align:center; width:74px; } .tick { color:var(--tick); font-weight:700; font-size:16px; } .half { color:var(--half); font-size:16px; }
.m { font:600 11px/1 ui-monospace,Menlo,Consolas,monospace; padding:3px 6px; border-radius:4px; background:var(--head); border:1px solid var(--line); }
.p { font:12px ui-monospace,Menlo,Consolas,monospace; word-break:break-all; } .sum { color:var(--faint); font-size:12px; margin-top:2px; }
.auth { font-weight:600; } .auth.open { color:var(--open); } .auth.person { color:var(--person); } .auth.worker { color:var(--worker); }
.auth.mixed { color:var(--mixed); } .auth.catalog { color:var(--catalog); } .auth.job { color:var(--job); } .auth.none { color:var(--none); }
tr.group td { background:var(--head); font-weight:700; letter-spacing:.02em; } tr.hidden { display:none; }
.legend { padding:0 24px 8px; color:var(--faint); font-size:13px; } .legend span { margin-right:18px; }
.az { color:var(--faint); font-size:12.5px; }
"""

SCRIPT = """
const rows = [...document.querySelectorAll('tr[data-row]')], groups = [...document.querySelectorAll('tr.group')];
const q = document.getElementById('q'), a = document.getElementById('a'), c = document.getElementById('c'), n = document.getElementById('n');
function apply() {
  const t = q.value.trim().toLowerCase(), kind = a.value, who = c.value; let shown = 0;
  rows.forEach(r => {
    const ok = (!t || r.dataset.text.includes(t)) && (!kind || r.dataset.kind === kind) && (!who || r.dataset['has' + who] === '1');
    r.classList.toggle('hidden', !ok); if (ok) shown++;
  });
  groups.forEach(g => { let el = g.nextElementSibling, any = false; while (el && !el.classList.contains('group')) { if (!el.classList.contains('hidden')) any = true; el = el.nextElementSibling; } g.classList.toggle('hidden', !any); });
  n.textContent = shown + ' of ' + rows.length + ' routes';
}
[q, a, c].forEach(e => e.addEventListener('input', apply)); apply();
"""


def cell(found: dict | None) -> tuple[str, bool]:
    if not found or not (found["exact"] or found["path_only"]):
        return '<td class="c"></td>', False
    files = sorted(found["exact"]) + sorted(found["path_only"])
    tip = html.escape("\n".join(files[:8]) + (f"\n... and {len(files) - 8} more" if len(files) > 8 else ""))
    if found["exact"]:
        return f'<td class="c" title="{tip}"><span class="tick">&#10003;</span></td>', True
    return f'<td class="c" title="{tip}"><span class="half">&#189;</span></td>', True


def build(routes: list[dict], seen: dict) -> str:
    rows_html: list[str] = []
    counts: dict[str, int] = {}
    caller_totals = {k: 0 for k, *_ in CALLERS}
    area_of = lambda p: "/" + (p.strip("/").split("/")[0] if p.strip("/") else "")  # noqa: E731
    ordered = sorted(routes, key=lambda r: (area_of(r["path"]), r["path"], r["method"]))
    last_area = None
    for r in ordered:
        label, kind = authentication(r)
        counts[kind] = counts.get(kind, 0) + 1
        if area_of(r["path"]) != last_area:
            last_area = area_of(r["path"])
            rows_html.append(f'<tr class="group"><td colspan="{5 + len(CALLERS)}">{html.escape(last_area)}</td></tr>')
        cells, data = [], {}
        for key, *_ in CALLERS:
            td, has = cell(seen.get((r["method"], r["path"]), {}).get(key))
            cells.append(td)
            data[key] = has
            caller_totals[key] += has
        text = " ".join([r["method"], r["path"], r["summary"], label, authorization(r)]).lower()
        attrs = " ".join(f'data-has{k}="{1 if v else 0}"' for k, v in data.items())
        rows_html.append(
            f'<tr data-row data-kind="{kind}" data-text="{html.escape(text)}" {attrs}>'
            f'<td><span class="m">{r["method"]}</span></td>'
            f'<td><div class="p">{html.escape(r["path"])}</div><div class="sum">{html.escape(r["summary"])}</div></td>'
            f'<td class="auth {kind}">{html.escape(label)}</td><td class="az">{html.escape(authorization(r))}</td>'
            + "".join(cells) + "</tr>")
    names = {"person": "signed-in person", "worker": "worker token only", "mixed": "more than one way", "open": "open on purpose",
             "catalog": "catalog token", "job": "job credential", "none": "no authentication"}
    chips = "".join(f'<span class="chip">{html.escape(names[k])}: <b>{v}</b></span>' for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
    chips += "".join(f'<span class="chip">called by {html.escape(lbl.lower())}: <b>{caller_totals[k]}</b></span>' for k, lbl, *_ in CALLERS)
    options = "".join(f'<option value="{k}">{html.escape(names[k])}</option>' for k in names)
    who = "".join(f'<option value="{k}">{html.escape(lbl)}</option>' for k, lbl, *_ in CALLERS)
    heads = "".join(f"<th>{html.escape(lbl)}</th>" for _k, lbl, *_ in CALLERS)
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>API callers</title><style>{STYLE}</style></head><body>
<header><h1>Every API route, who calls it, and how it knows who is calling</h1>
<p class="lead">{len(routes)} routes and methods, read from the running API on {when}. The caller columns come from scanning this repository for the route's path
and the HTTP verb beside it. The last two descriptive columns say how the route authenticates its caller, and what decides whether that caller may.
Internal: this page lives under docs/internal and is never published. <a href="services.html">Services</a> &middot; <a href="logins.html">Logins</a></p></header>
<div class="legend"><span><span class="tick">&#10003;</span> the path and the verb were found together in a file of that kind</span>
<span><span class="half">&#189;</span> the path was found and the verb could not be told, so another method on the same path may be the one called</span>
<span>hover a tick for the files</span></div>
<div class="bar"><input id="q" type="search" placeholder="Filter by path, method, wording"><select id="a"><option value="">Any authentication</option>{options}</select>
<select id="c"><option value="">Called by anyone</option>{who}</select><span class="count" id="n"></span></div>
<div class="summary">{chips}</div>
<div class="wrap"><table><thead><tr><th>Method</th><th>Route</th><th>Authentication: how it knows who is calling</th><th>Authorization: what decides whether they may</th>{heads}</tr></thead>
<tbody>{"".join(rows_html)}</tbody></table></div>
<script>{SCRIPT}</script></body></html>"""


def main() -> int:
    routes = routes_from_api()
    seen = scan(routes)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(routes, seen), encoding="utf-8")
    none = [r for r in routes if authentication(r)[1] == "none"]
    print(f"wrote {OUT.relative_to(ROOT)}: {len(routes)} routes, {len(none)} with no authentication check")
    for r in none:
        print("  NONE:", r["method"], r["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
