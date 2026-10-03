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
    try:
        mod = sys.modules[fn.__module__]
        funcs = {n: f for n, f in vars(mod).items() if inspect.isfunction(f) and f.__module__ == fn.__module__}
        seen, frontier = {fn.__name__}, [source]
        for _ in range(2):
            nxt = []
            for text in frontier:
                for name in set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\(", text)):
                    if name in funcs and name not in seen:
                        seen.add(name)
                        try:
                            nxt.append(inspect.getsource(funcs[name]))
                        except Exception:
                            pass
            source += "\n".join(nxt)
            frontier = nxt
    except Exception:
        pass
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
    ("GET", "/health"): "Nobody needs to sign in. It only says whether the platform is running, and returns no organisation's data.",
    ("GET", "/legal-exports/signing-key"): "Nobody needs to sign in. It returns the public half of the key that signs a legal export, which anyone who receives an export needs in order to check it.",
    ("GET", "/legal-exports/download/{token}"): "Nobody needs to sign in. The secret token inside the web address is the proof. It works a few times and then expires.",
    ("POST", "/iceberg/v1/oauth/tokens"): "Nobody needs to sign in. It gives every caller the same refusal and reads and writes nothing.",
    ("GET", "/policy/roles"): "Nobody needs to sign in. It lists what each role is and may do, so the console's roles page can be read before signing in.",
}
SPECIAL = {
    ("POST", "/credentials"): "The worker token, or a task credential for the same principal that is asking, or a signed-in person asking for themselves",
    ("POST", "/storage-keys/pipeline"): "A task credential (it gets its own organisation's key only), or the worker token with the organisation named",
    ("POST", "/dataset-versions"): "Worker token only. No person can call it, not even an administrator.",
}
# (dependency name, what it means in plain words, kind used by the filter)
DEPENDENCY_LABEL = [
    ("catalog_principal", "A catalog token. It is issued to a signed-in person, expires, and can be revoked.", "catalog"),
    ("_claim", "A table-job credential, signed by the platform and valid for one job only", "job"),
    ("worker_only", "Worker token only. No person can call it, not even an administrator.", "worker"),
    ("_worker", "Worker token only. No person can call it, not even an administrator.", "worker"),
    ("person_or_worker", "A signed-in person, or the worker token", "mixed"),
    ("organisation_scope", "A signed-in person, the worker token, or a task credential. What comes back is limited to one organisation.", "mixed"),
    ("current_session_while_closing", "A signed-in person (this also works while their organisation is closing down)", "person"),
    ("current_session", "A signed-in person", "person"),
]


def authentication(route: dict) -> tuple[str, str]:
    """(plain wording, kind) where kind is a short class used for filtering."""
    key = (route["method"], route["path"])
    if key in OPEN_ON_PURPOSE:
        return OPEN_ON_PURPOSE[key], "open"
    if key in SPECIAL:
        text = SPECIAL[key]
        return text, "worker" if text.startswith("Worker token only") else "mixed"
    found = [(label, kind) for name, label, kind in DEPENDENCY_LABEL if name in route["dependencies"]]
    seen: list[str] = []
    for label, _kind in found:
        if label not in seen:
            seen.append(label)
    if seen:
        return " Also: ".join(seen), found[0][1]
    if {"x_worker_token", "x_task_credential"} & set(route["headers"]):
        return "The worker token or a task credential, checked inside the route", "mixed"
    return "NONE: no check found. This must be fixed.", "none"


# What each policy engine decision means, in the words of the person using the console.
DECISION_MEANING = {
    "evaluate": "May this person read this version of this dataset? This is the main access decision.",
    "may_approve": "May this person approve a request for extra, time-limited access to a dataset?",
    "may_decide_gate": "May this person approve or reject a pipeline's human sign-off step?",
    "may_start_pipeline": "May this person start this pipeline?",
    "may_approve_egress_hosts": "May this person approve the internet hosts an agent version is allowed to contact?",
    "may_request_role": "May this person ask for this role?",
    "may_approve_role": "May this person approve someone else's request for a role?",
    "may_attest_role": "May this person formally confirm that someone holds a role?",
    "may_see_housekeeping": "May this person see the storage housekeeping page?",
    "may_free_storage": "May this person free up storage?",
    "may_retire": "May this person start retiring an organisation?",
    "may_cancel_retirement": "May this person cancel an organisation's retirement?",
    "may_place_hold": "May this person place a legal hold on an organisation?",
    "may_decide_hold": "May this person approve or reject a legal hold?",
    "may_release_hold": "May this person release a legal hold?",
    "may_see_lifecycle": "May this person see an organisation's retirement and hold status?",
    "may_set_table_worker": "May this person give an organisation its own table worker?",
    "may_request_export": "May this person ask for a legal export?",
    "may_approve_export": "May this person approve a legal export?",
    "may_confirm_export": "May this person confirm a legal export?",
    "may_link_export": "May this person create the download link for a legal export?",
    "may_read_passphrase": "May this person read the passphrase of a legal export?",
    "may_export": "May this person export a dataset's data?",
}


def authorization(route: dict) -> str:
    parts: list[str] = []
    if route["policy"]:
        parts.append("The policy engine (OPA) decides: " + " ".join(DECISION_MEANING.get(p, p) for p in route["policy"]))
    if "organisation_scope" in route["dependencies"]:
        parts.append("Records of another organisation are reported as not found.")
    if route["must_be"]:
        parts.append("The caller acts only in their own organisation and only as themselves. A different name in the request is refused.")
    elif route["mine"]:
        parts.append("Limited to the caller's own organisation.")
    if route["role_in_code"]:
        parts.append("The route's own code requires the role: " + ", ".join(route["role_in_code"]) + ".")
    if "catalog_principal" in route["dependencies"]:
        parts.append("Each table read is decided by the policy engine (OPA) as the person the token names.")
    if parts:
        return " ".join(parts)
    if (route["method"], route["path"]) in OPEN_ON_PURPOSE:
        return "Nothing further. The route is public by design."
    return "No further rule found in the route's code. Anyone who passes the check on the left may use it."


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
header { padding:20px 24px 8px; } h1 { margin:0 0 4px; font-size:20px; } p.lead { margin:4px 0; color:var(--faint); }
.bar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; padding:10px 24px; position:sticky; top:0; background:var(--bg); z-index:3; border-bottom:1px solid var(--line); }
.bar input, .bar select { font:inherit; padding:5px 8px; border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--ink); }
.bar input { min-width:260px; } .count { color:var(--faint); }
.summary { display:flex; flex-wrap:wrap; gap:8px; padding:10px 24px; }
.chip { border:1px solid var(--line); border-radius:999px; padding:2px 10px; background:var(--card); font-size:12px; }
.wrap { padding:0 24px 40px; }
table { border-collapse:collapse; width:100%; min-width:1250px; background:var(--card); }
th, td { border:1px solid var(--line); padding:6px 8px; vertical-align:top; text-align:left; }
th { background:var(--head); position:sticky; top:var(--bar, 48px); z-index:2; font-size:12px; text-transform:uppercase; letter-spacing:.03em; }
td.c { text-align:center; width:74px; } .tick { color:var(--tick); font-weight:700; font-size:16px; } .half { color:var(--half); font-size:16px; }
.m { font:600 11px/1 ui-monospace,Menlo,Consolas,monospace; padding:3px 6px; border-radius:4px; background:var(--head); border:1px solid var(--line); }
.p { font:12px ui-monospace,Menlo,Consolas,monospace; word-break:break-all; } .sum { color:var(--faint); font-size:12px; margin-top:2px; }
.auth { font-weight:600; } .auth.open { color:var(--open); } .auth.person { color:var(--person); } .auth.worker { color:var(--worker); }
.auth.mixed { color:var(--mixed); } .auth.catalog { color:var(--catalog); } .auth.job { color:var(--job); } .auth.none { color:var(--none); }
tr.group td { background:var(--head); font-weight:700; letter-spacing:.02em; } tr.hidden { display:none; }
.legend { padding:0 24px 8px; color:var(--faint); font-size:13px; } .legend span { margin-right:18px; }
.az { color:var(--faint); font-size:12.5px; }
.explain { padding:0 24px 8px; } .explain h2 { font-size:17px; margin:18px 0 6px; } .explain h3 { font-size:14px; margin:14px 0 4px; }
.explain pre { background:var(--head); border:1px solid var(--line); border-radius:6px; padding:10px 12px; overflow-x:auto; font:12.5px/1.45 ui-monospace,Menlo,Consolas,monospace; }
.index { padding:4px 24px 8px; } .index h2 { font-size:17px; margin:12px 0 6px; } .index ol, .index ul { margin:4px 0; }
.index a, .areas a { color:var(--person); text-decoration:none; } .index a:hover { text-decoration:underline; }
.areas { margin:6px 0 0; line-height:1.9; } .areas a { margin-right:12px; font:12.5px ui-monospace,Menlo,Consolas,monospace; } .areas b { margin-right:10px; }
h2[id], h3[id], tr.group[id] { scroll-margin-top:calc(var(--bar, 54px) + 8px); } tr.group[id] { scroll-margin-top:calc(var(--bar, 54px) + 110px); }
.explain th { position:static; } .explain table.small { min-width:0; width:100%; } .legend b { margin-right:14px; color:var(--ink); }
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
const bar = document.querySelector('.bar');
function fit() { document.documentElement.style.setProperty('--bar', bar.offsetHeight + 'px'); }
fit(); window.addEventListener('resize', fit);
document.querySelectorAll('a[href^="#"]').forEach(l => l.addEventListener('click', e => {
  const t = document.getElementById(l.getAttribute('href').slice(1)); if (!t) return;
  e.preventDefault(); t.scrollIntoView({ behavior: 'smooth', block: 'start' });
}));
"""


def cell(found: dict | None) -> tuple[str, bool]:
    if not found or not (found["exact"] or found["path_only"]):
        return '<td class="c"></td>', False
    files = sorted(found["exact"]) + sorted(found["path_only"])
    tip = html.escape("\n".join(files[:8]) + (f"\n... and {len(files) - 8} more" if len(files) > 8 else ""))
    if found["exact"]:
        return f'<td class="c" title="{tip}"><span class="tick">&#10003;</span></td>', True
    return f'<td class="c" title="{tip}"><span class="half">&#189;</span></td>', True


def rego_rule(text: str, name: str, brace: str = "{", close: str = "}") -> str:
    """The first block of the policy file that defines `name`, copied as written so the page cannot drift from the rules."""
    m = re.search(rf"^{name}\b[^\n]*{re.escape(brace)}\s*$", text, re.M)
    if not m:
        raise SystemExit(f"rule {name} not found in access.rego")
    end = text.index("\n" + close, m.end())
    return text[m.start():end + 1 + len(close)]


def build(routes: list[dict], seen: dict) -> str:
    rows_html: list[str] = []
    counts: dict[str, int] = {}
    caller_totals = {k: 0 for k, *_ in CALLERS}
    area_of = lambda p: "/" + (p.strip("/").split("/")[0] if p.strip("/") else "")  # noqa: E731
    ordered = sorted(routes, key=lambda r: (area_of(r["path"]), r["path"], r["method"]))
    last_area = None
    area_ids: list[str] = []
    for r in ordered:
        label, kind = authentication(r)
        if kind == "open":
            continue  # listed in its own section above the table
        counts[kind] = counts.get(kind, 0) + 1
        if area_of(r["path"]) != last_area:
            last_area = area_of(r["path"])
            area_ids.append(last_area)
            rows_html.append(f'<tr class="group" id="area{len(area_ids)}"><td colspan="{5 + len(CALLERS)}">{html.escape(last_area)}</td></tr>')
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
    names = {"person": "a signed-in person", "worker": "worker token only", "mixed": "more than one way", "open": "public, no sign-in",
             "catalog": "catalog token", "job": "table-job credential", "none": "no check at all"}
    chips = "".join(f'<span class="chip">{html.escape(names[k])}: <b>{v}</b></span>' for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
    chips += "".join(f'<span class="chip">called by {html.escape(lbl.lower())}: <b>{caller_totals[k]}</b></span>' for k, lbl, *_ in CALLERS)
    options = "".join(f'<option value="{k}">{html.escape(names[k])}</option>' for k in names if k != "open")
    who = "".join(f'<option value="{k}">{html.escape(lbl)}</option>' for k, lbl, *_ in CALLERS)
    heads = "".join(f"<th>{html.escape(lbl)}</th>" for _k, lbl, *_ in CALLERS)
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rego = (ROOT / "platform" / "policy" / "access.rego").read_text(encoding="utf-8")
    read_rule = html.escape(rego_rule(rego, "role_reaches") + "\n" + rego_rule(rego, "baseline_ok"))
    actor_line = re.search(r"^lifecycle_actor_roles\b[^\n]*$", rego, re.M).group(0)
    table_rule = html.escape(actor_line + "\n\n" + rego_rule(rego, "may_set_table_worker"))
    public = [r for r in routes if authentication(r)[1] == "open"]
    public_rows = "".join(
        f'<tr><td><span class="m">{r["method"]}</span></td><td class="p">{html.escape(r["path"])}</td><td>{html.escape(authentication(r)[0])}</td></tr>'
        for r in sorted(public, key=lambda r: r["path"]))
    unchecked = counts.get("none", 0)
    used: dict[str, list[str]] = {}
    for r in routes:
        for d in r["policy"]:
            used.setdefault(d, []).append(f'{r["method"]} {r["path"]}')
    decision_rows = "".join(
        f'<tr><td class="p">{html.escape(d)}</td><td>{html.escape(DECISION_MEANING.get(d, ""))}</td>'
        f'<td class="p">{"<br>".join(html.escape(x) for x in sorted(set(where)))}</td></tr>'
        for d, where in sorted(used.items()))
    areas = " ".join(f'<a href="#area{i}">{html.escape(a)}</a>' for i, a in enumerate(area_ids, 1))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>API callers</title><style>{STYLE}</style></head><body>
<header><h1>Every API route: who calls it, and who may</h1>
<p class="lead">{len(routes)} routes (a route is a web address plus a method such as GET or POST), {len(public)} public and {len(routes) - len(public)} needing a sign-in or a credential, read from the running API on {when}.
For each route this page shows which parts of the repository call it, how the platform learns who is calling, and what else decides whether that caller may go ahead.
Internal: this page lives under docs/internal and is never published. <a href="services.html">Services</a> &middot; <a href="logins.html">Logins</a></p></header>
<nav class="index"><h2>Contents</h2>
<ol>
<li><a href="#checked">How a request is checked</a>
<ul><li><a href="#example-read">Example 1: who may read a dataset version</a></li>
<li><a href="#example-worker">Example 2: who may give an organisation its own table worker</a></li>
<li><a href="#decisions">Every policy decision the routes ask for</a></li></ul></li>
<li><a href="#public">Routes anyone can call without signing in</a></li>
<li><a href="#protected">Routes that need a sign-in or a credential</a>
<div class="areas"><b>Jump to an area:</b> {areas}</div></li>
</ol></nav>
<section class="explain">
<h2 id="checked">How a request is checked</h2>
<p>Every request passes up to three checks, in this order. The table at the bottom has one column for the first check and one for the other two.</p>
<ol>
<li><b>Who is calling?</b> (the column "Who may call it, and how the platform knows"). A signed-in person is recognised by a session cookie or a bearer token. The platform's own workers send the worker token. A task such as an agent run or a table job carries a credential signed by the platform for that task alone.</li>
<li><b>Which organisation are they in?</b> The route compares the organisation named in the request with the organisation of the caller. A mismatch is refused, and another organisation's records are reported as not found.</li>
<li><b>Does the policy allow it?</b> For many actions the route does not decide by itself. It collects the facts (who is asking, their roles, the dataset, its class) and sends them to <b>OPA</b> (Open Policy Agent), a separate policy engine that holds all the rules in one file, <code>platform/policy/access.rego</code>. OPA answers allow or deny with reasons, and the platform records the answer. If OPA cannot be reached, the request is denied.</li>
</ol>
<p>The column "What else decides whether they may" names the OPA decision when a route asks one. A route that shows none asks OPA nothing itself. Reading a dataset's data goes through <code>POST /credentials</code>, where OPA decides every time.</p>
<h3 id="example-read">Example 1: who may read a dataset version</h3>
<p>Two rules must both hold. First, the person must be in the same organisation as the dataset and must state a purpose (<code>baseline_ok</code>). Second, the dataset's class must be open enough for one of their roles (<code>role_reaches</code>). The classes run from most to least sensitive: RAW, UNDER_REVIEW, OPEN_FOR_ANNOTATION, OPEN_FOR_TRAINING, PUBLISHED. Each role has a floor, the most sensitive class it may read, and every human role has the floor PUBLISHED. So a person reads only published data by default. Anything more sensitive needs a lease, which is extra access that a second person approved for a limited time.</p>
<pre>{read_rule}</pre>
<h3 id="example-worker">Example 2: who may give an organisation its own table worker</h3>
<p>Only a platform administrator. The route <code>PUT /tenants/{{organisation_id}}/table-worker</code> sends the caller's roles to OPA, and OPA allows it only if one of them is in the list below.</p>
<pre>{table_rule}</pre>
<h3 id="decisions">Every policy decision the routes ask for</h3>
<table class="small"><thead><tr><th>OPA decision</th><th>The question it answers</th><th>Routes that ask it</th></tr></thead><tbody>{decision_rows}</tbody></table>
</section>
<section class="explain">
<h2 id="public">Routes anyone can call without signing in</h2>
<p>These {len(public)} routes need no sign-in, on purpose. Every other route has a check, and a route with no check at all would show as "no check at all" below: there are <b>{unchecked}</b> today.
The verification suite fails if that number is ever above zero (check U113).</p>
<table class="small"><thead><tr><th>Method</th><th>Route</th><th>Why it is public</th></tr></thead><tbody>{public_rows}</tbody></table>
</section>
<section class="explain"><h2 id="protected">Routes that need a sign-in or a credential</h2>
<p>The other {len(routes) - len(public)} routes. The table says who may call each one and how the platform knows, what else decides whether they may (including the OPA question when there is one), and which parts of the repository call it.</p></section>
<div class="legend"><b>Legend</b>
<span><span class="tick">&#10003;</span> a file of this kind calls this route (both the web address and the method were found)</span>
<span><span class="half">&#189;</span> a file of this kind mentions this web address, but the method could not be read, so it may call a different method on the same address</span>
<span>Hover over a tick to see the files.</span></div>
<div class="bar"><input id="q" type="search" placeholder="Filter by address, method or wording"><select id="a"><option value="">Any way of proving who is calling</option>{options}</select>
<select id="c"><option value="">Called by anyone</option>{who}</select><span class="count" id="n"></span></div>
<div class="summary">{chips}</div>
<div class="wrap"><table><thead><tr><th>Method</th><th>Route</th><th>Who may call it, and how the platform knows</th><th>What else decides whether they may</th>{heads}</tr></thead>
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
