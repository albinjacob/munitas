r"""Render the settings documentation: `.env.example` and docs/public/reference/configuration.html.

Two sources, kept apart on purpose:

  * `settings_reference.json` holds what a person writes: what each setting means, its unit, which group it belongs to.
  * `settings_scan.py` reads what the code does: every setting it reads, the default each has, which program reads it.

Everything factual on the page (the defaults, who reads a setting, the port numbers from config.json) comes from the second, so it cannot be typed wrongly,
and `verify/v154_settings_documented.py` fails when the two sources disagree or when either output is out of date. Regenerate after changing a setting:

    .venv\Scripts\python.exe docs\tools\generate_settings_reference.py
"""
from __future__ import annotations

import html
import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings_scan  # noqa: E402

ROOT = settings_scan.ROOT
CATALOGUE = ROOT / "docs" / "tools" / "settings_reference.json"
ENV_EXAMPLE = ROOT / ".env.example"
PAGE = ROOT / "docs" / "public" / "reference" / "configuration.html"
COMPONENTS = (("platform/api/", "the API"), ("platform/tablejob/", "the table worker"), ("worker/", "the worker"), ("docker-compose.yml", "Docker Compose"))


def load() -> dict:
    return json.loads(CATALOGUE.read_text(encoding="utf-8"))


def kind_of(entry: dict, catalogue: dict) -> str:
    return entry.get("kind") or next(g["kind"] for g in catalogue["groups"] if g["id"] == entry["group"])


def human_bytes(n: int) -> str:
    for size, unit in ((1024 ** 3, "GiB"), (1024 ** 2, "MiB"), (1024, "KiB")):
        if n >= size and n % size == 0:
            return f"{n // size} {unit}"
    return f"{n} bytes"


def human_seconds(n: int) -> str:
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if n >= size and n % size == 0:
            count = n // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return ""


def defaults_of(scanned: dict) -> list[str]:
    """The distinct, plain defaults the code gives a setting. Empty when it has none or the code works it out."""
    seen: list[str] = []
    for value in scanned.get("defaults", []):
        if value not in ("", "(computed)", "None") and value not in seen:
            seen.append(value)
    return seen


def counted(value: str, unit: str) -> str:
    """"1 volume", "3 uses": the unit agrees with the number."""
    return f"{value} {unit[:-1] if value == '1' and unit.endswith('s') else unit}"


def is_phrase(text: str) -> bool:
    """A sentence or phrase, which ends with a full stop, as opposed to a value such as a path or an address, which must not."""
    return " " in text.strip() and text.rstrip()[-1].isalpha() or text.rstrip().endswith(".")


def default_text(entry: dict, scanned: dict, kind: str) -> str:
    """What the page says the default is, from the code, with the unit."""
    plain = defaults_of(scanned)
    if kind == "secret" and plain:
        return "A development value is used when it is not set. Set your own for any shared or production deployment."
    if entry.get("shown"):
        return entry["shown"]
    if not plain:
        return "Not set."
    value, unit = plain[0], entry.get("unit")
    if len(plain) > 1:
        return " or ".join(plain)
    if unit == "bytes" and value.isdigit():
        return value + (f" bytes ({human_bytes(int(value))})" if int(value) else " bytes")
    if unit == "seconds" and value.isdigit():
        human = human_seconds(int(value))
        return f"{value} seconds" + (f" ({human})" if human else "")
    if unit and value.isdigit():
        return counted(value, unit)
    return value


def readers(scanned: dict) -> list[str]:
    found = []
    for prefix, label in COMPONENTS:
        if any(path.startswith(prefix) for path in scanned.get("read_in", [])) and label not in found:
            found.append(label)
    return found


def merged() -> tuple[dict, dict]:
    catalogue = load()
    scan = settings_scan.scan()
    return catalogue, scan


# ------------------------------------------------------------------------------------------------------------------ .env.example
def wrap(text: str, width: int = 116) -> list[str]:
    return [f"# {line}" for line in textwrap.wrap(text, width=width - 2)] or ["#"]


def render_env() -> str:
    catalogue, scan = merged()
    out = [
        "# Copy this file to .env and change what you need. Every line that starts with # is optional: the value shown is the default, so removing the #",
        "# (and changing the value) is all it takes. A blank value means \"not set\". A setting written here takes effect for the API when its container is",
        "# recreated, and for the host worker when it is restarted: run .\\start-dev.ps1 (after .\\stop-dev.ps1 for the worker).",
        "#",
        "# Every setting starts with MUNITAS_, in upper case with underscores. This file is generated from docs/tools/settings_reference.json by",
        "# docs/tools/generate_settings_reference.py, so edit that file and regenerate, not this one. The same descriptions are published as the",
        "# Configuration reference, docs/public/reference/configuration.html. The MUNITAS_PORT_* lines that start-dev.ps1 adds to your .env come from",
        "# config.json: change a port there, never here.",
        "",
    ]
    for group in catalogue["groups"]:
        if group["kind"] == "internal":
            continue
        entries = [e for e in catalogue["settings"] if e["group"] == group["id"] and e.get("template", "commented") != "omit"]
        if not entries:
            continue
        out += ["# " + "-" * 114, f"# {group['title']}", "#"] + wrap(group["intro"]) + ["# " + "-" * 114, ""]
        for entry in entries:
            kind = kind_of(entry, catalogue)
            scanned = scan[entry["name"]]
            sentence = entry["meaning"]
            default = default_text(entry, scanned, kind)
            lines = wrap(sentence)
            if entry.get("unit"):
                lines += wrap(f"Unit: {entry['unit']}.")
            lines += wrap("Default: " + (default + "." if is_phrase(default) and not default.endswith(".") else default))
            plain = defaults_of(scanned)
            template = entry.get("template", "commented")
            if template == "active":
                value = entry.get("example") or (plain[0] if plain else "")
                line = f"{entry['name']}={value}"
            else:
                value = entry.get("example") or (plain[0] if len(plain) == 1 and not entry.get("shown") and kind != "secret" else "")
                line = f"# {entry['name']}={value}"
            out += lines + [line, ""]
    return "\n".join(out).rstrip("\n") + "\n"


# ------------------------------------------------------------------------------------------------------------------ the page
CSS = """
:root{--bg:#f6f7fa;--card:#fff;--ink:#1a1d29;--soft:#565c6e;--faint:#8a90a3;--line:#e0e2ea;--accent:#3949e0;--accent-bg:#edeefc;--warn-bg:#fbeee0;--warn:#8a4b00;--ok-bg:#e5f5f1;--ok:#0b6b5d}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#14161f;--card:#1c1f2b;--ink:#e9eaf2;--soft:#a7acc0;--faint:#6f748a;--line:#2b2e3c;--accent:#8b93f7;--accent-bg:#23264a;--warn-bg:#33270f;--warn:#e7b96a;--ok-bg:#103830;--ok:#4fd6bf}}
:root[data-theme="dark"]{--bg:#14161f;--card:#1c1f2b;--ink:#e9eaf2;--soft:#a7acc0;--faint:#6f748a;--line:#2b2e3c;--accent:#8b93f7;--accent-bg:#23264a;--warn-bg:#33270f;--warn:#e7b96a;--ok-bg:#103830;--ok:#4fd6bf}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,"Segoe UI","Inter",system-ui,sans-serif}
main{max-width:1000px;margin:0 auto;padding:32px 20px 80px}h1{font-size:28px;margin:0 0 6px}h2{font-size:21px;margin:44px 0 6px;scroll-margin-top:12px}
p{margin:8px 0}.lead{color:var(--soft);font-size:17px}code,.name{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.92em}
code{background:var(--accent-bg);padding:1px 6px;border-radius:4px}nav.toc{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin:22px 0}
nav.toc ul{columns:2;margin:6px 0 0;padding-left:18px}nav.toc li{margin:2px 0}a{color:var(--accent)}
.how{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:4px 22px 14px}.how h2{margin-top:18px}
.setting{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin:12px 0;scroll-margin-top:12px}
.setting h3{margin:0 0 4px;font-size:16px}.setting .name{font-weight:600;color:var(--ink)}
.badge{display:inline-block;font-size:12px;border-radius:999px;padding:1px 9px;margin-left:6px;vertical-align:1px}.b-secret{background:var(--warn-bg);color:var(--warn)}.b-adv{background:var(--accent-bg);color:var(--accent)}.b-int{background:var(--ok-bg);color:var(--ok)}
dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 16px;margin:8px 0 0;font-size:14.5px}dt{color:var(--faint)}dd{margin:0;color:var(--soft)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:15px}th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line)}th{color:var(--faint);font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:.04em}tr:last-child td{border-bottom:0}
footer{margin-top:48px;color:var(--faint);font-size:14px}@media(max-width:640px){nav.toc ul{columns:1}dl{grid-template-columns:1fr}}
""".strip()


def e(text: str) -> str:
    return html.escape(text, quote=False)


def render_page() -> str:
    catalogue, scan = merged()
    ports = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    toc = [f'<li><a href="#{g["id"]}">{e(g["title"])}</a></li>' for g in catalogue["groups"]] + ['<li><a href="#ports">Ports</a></li>']
    body: list[str] = []
    for group in catalogue["groups"]:
        entries = [x for x in catalogue["settings"] if x["group"] == group["id"]]
        if not entries:
            continue
        body.append(f'<h2 id="{group["id"]}">{e(group["title"])}</h2><p class="lead">{e(group["intro"])}</p>')
        for entry in entries:
            kind = kind_of(entry, catalogue)
            scanned = scan[entry["name"]]
            badges = ""
            if kind == "secret":
                badges += '<span class="badge b-secret">Secret</span>'
            if kind == "internal":
                badges += '<span class="badge b-int">Set by the platform</span>'
            if entry.get("advanced"):
                badges += '<span class="badge b-adv">Advanced</span>'
            default = default_text(entry, scanned, kind)
            facts = [("Default", default + "." if is_phrase(default) and not default.endswith(".") else default)]
            if entry.get("unit"):
                facts.append(("Unit", entry["unit"]))
            who = readers(scanned)
            if who:
                facts.append(("Read by", ", ".join(who)))
            rows = "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in facts)
            body.append(f'<section class="setting" id="{entry["name"]}"><h3><span class="name">{entry["name"]}</span>{badges}</h3>'
                        f'<p>{e(entry["meaning"])}</p><dl>{rows}</dl></section>')
    port_rows = "".join(
        f'<tr><td><span class="name">MUNITAS_PORT_{key.upper()}</span></td><td>{ports[key]}</td><td>{e(label)}</td></tr>'
        for key, label in sorted(catalogue["ports"].items()))
    body.append('<h2 id="ports">Ports</h2><p class="lead">Every port the stack publishes on this machine is set in one place, '
                '<code>config.json</code>, and nowhere else. Change a value there to resolve a conflict with something already using that port, then run '
                '<code>start-dev.ps1</code>: it writes the <code>MUNITAS_PORT_*</code> lines into your <code>.env</code> and regenerates the sign-in service\'s '
                'configuration. Do not edit those lines by hand, because the next start overwrites them.</p>'
                f'<table><thead><tr><th>Setting</th><th>Default port</th><th>What listens on it</th></tr></thead><tbody>{port_rows}</tbody></table>')
    how = """
<div class="how">
<h2 id="how" style="margin-top:14px">How settings work</h2>
<p><strong>Where to write them.</strong> In <code>.env</code>, at the root of the repository. Copy <code>.env.example</code> to <code>.env</code>, which lists every setting with its default
commented out. <code>.env</code> is never committed, so secrets in it stay on your machine.</p>
<p><strong>Who reads it.</strong> Docker Compose passes the whole file to the API. The start script gives the host worker the settings the worker reads, by name, and nothing else from the file:
the worker is built not to hold the storage administrator's secret or the master key, so those never reach it. A setting that is not written anywhere uses the default shown on this page.</p>
<p><strong>After a change.</strong> The API reads its settings once, when it starts. Run <code>start-dev.ps1</code> to recreate the API container with the new values. For the worker, run
<code>stop-dev.ps1</code> and then <code>start-dev.ps1</code>. The worker that runs inside the Linux distribution, and the optional quickstart worker, read the environment they are started with and
use the defaults shown here.</p>
<p><strong>Names.</strong> Every setting starts with <code>MUNITAS_</code> and is written in upper case with underscores, so none of them can collide with a variable that belongs to another program.
Variables that belong to other software, such as the <code>AWS_</code> ones the storage client library reads, are set by the platform and are not listed.</p>
<p><strong>Secrets.</strong> Each one has a development value so that a laptop starts without setup. Those values are published in this repository, so they must be replaced in any shared deployment.
Set <code>MUNITAS_ENV=production</code> and the platform refuses to start until the two that matter most have been replaced.</p>
</div>"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Configuration reference</title><style>{CSS}</style></head>
<body><main>
<h1>Configuration reference</h1>
<p class="lead">Every setting of the platform and its worker: what it means, its unit and its default. {len(catalogue["settings"])} settings and {len(catalogue["ports"])} ports.</p>
{how}
<nav class="toc" aria-label="Sections"><strong>Sections</strong><ul>{"".join(toc)}</ul></nav>
{"".join(body)}
<footer>This page is generated. The meanings are written in <code>docs/tools/settings_reference.json</code>. The defaults, and which program reads each setting, are read from the
code itself by <code>docs/tools/settings_scan.py</code>, and the port numbers come from <code>config.json</code>. An automatic check fails when the page and the code disagree.</footer>
</main></body></html>
"""


def main() -> int:
    ENV_EXAMPLE.write_text(render_env(), encoding="utf-8", newline="\n")
    PAGE.write_text(render_page(), encoding="utf-8", newline="\n")
    print(f"wrote {ENV_EXAMPLE.relative_to(ROOT)} and {PAGE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
