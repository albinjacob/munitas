r"""Render docs/public/reference/roles.html: what each role can and cannot do.

Two sources, kept apart on purpose:

  * `roles_reference.json` holds what a person writes: what each power means in plain words, and the lines that say what a role cannot do.
  * `platform/policy/role_words.rego` holds the names of the roles, and `platform/policy/access.rego` holds who has each power.

`verify/v189_roles_documented.py` compares them and fails when they disagree, and when this page is not exactly what this generator makes today. A role or a power added
in the policy has to be written in the catalogue before the page can be regenerated. Regenerate after changing either:

    .venv\Scripts\python.exe docs\tools\generate_roles_reference.py
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_settings_reference import CSS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
CATALOGUE = ROOT / "docs" / "tools" / "roles_reference.json"
ROLE_WORDS = ROOT / "platform" / "policy" / "role_words.rego"
ACCESS = ROOT / "platform" / "policy" / "access.rego"
PAGE = ROOT / "docs" / "public" / "reference" / "roles.html"

PUBLISHED_FLOOR = 4   # role_floor in access.rego: the level every person role reads without a lease


def e(text: str) -> str:
    return html.escape(text, quote=False)


def load() -> dict:
    return json.loads(CATALOGUE.read_text(encoding="utf-8"))


def role_words() -> tuple[dict[str, str], list[str]]:
    """The label of every role, and the roles people hold in the order the policy lists them."""
    text = ROLE_WORDS.read_text(encoding="utf-8")
    labels = dict(re.findall(r'^\t"([a-z_]+)": \{"label": "([^"]+)"', text, re.M))
    people = re.findall(r'^\t"([a-z_]+)": \{"label": "[^"]+", "one": [^}]*"person": true\}', text, re.M)
    order = re.search(r"^order := \[(.*?)\]", text, re.M | re.S)
    ordered = re.findall(r'"([a-z_]+)"', order.group(1)) if order else []
    # Every role a person can hold, the ones in the policy's `order` first and in that order. A person role missing from `order` still counts, so that it cannot go undocumented.
    return labels, [r for r in ordered if r in people] + [r for r in people if r not in ordered]


def listed_order() -> list[str]:
    """The roles named in the policy's `order` list: the roles that sentences about several roles are built from."""
    order = re.search(r"^order := \[(.*?)\]", ROLE_WORDS.read_text(encoding="utf-8"), re.M | re.S)
    return re.findall(r'"([a-z_]+)"', order.group(1)) if order else []


def role_sets() -> dict[str, list[str]]:
    """Every `*_roles` set in access.rego and its members: a set written on one line, and one made from others (`a | b`, or another set under a new name)."""
    text = ACCESS.read_text(encoding="utf-8")
    sets = {m.group(1): re.findall(r'"([a-z_]+)"', m.group(2)) for m in re.finditer(r"^([a-z_]+_roles) := \{([^}]*)\}\s*$", text, re.M)}
    derived = {m.group(1): [x.strip() for x in m.group(2).split("|")] for m in re.finditer(r"^([a-z_]+_roles) := ([a-z_]+_roles(?: \| [a-z_]+_roles)*)\s*$", text, re.M)}
    for _ in range(len(derived) + 1):   # a derived set may name another derived set
        for name, parts in derived.items():
            if name not in sets and all(p in sets for p in parts):
                sets[name] = sorted({role for p in parts for role in sets[p]}, key=lambda r: [r for r in sets[parts[0]] + sum((sets[p] for p in parts[1:]), [])].index(r))
    return sets


def floors() -> dict[str, int]:
    block = re.search(r"role_floor := \{(.*?)\n\}", ACCESS.read_text(encoding="utf-8"), re.S).group(1)
    return {k: int(v) for k, v in re.findall(r'"([a-z_]+)": (\d)', block)}


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def render_page() -> str:
    catalogue = load()
    labels, people = role_words()
    powers = {p["id"]: p["text"] for p in catalogue["powers"]}
    floor = floors()
    parts: dict[str, list[str]] = {"people": [], "platform staff": []}
    toc: list[str] = []
    for role in people:
        if role not in catalogue["roles"]:
            raise SystemExit(f"{role} is a role a person can hold and has no entry in docs/tools/roles_reference.json: write what it can and cannot do first")
        entry = catalogue["roles"][role]
        if floor.get(role) != PUBLISHED_FLOOR:
            raise SystemExit(f"{role} does not read published data without a lease in the policy; the page's sentence about it has to change first")
        label = labels[role]
        can = [powers[p] for p in entry["powers"]] + list(entry["also"])
        can_html = "".join(f"<li>{e(t.rstrip('.'))}.</li>" for t in can) if can else "<li>No power of its own beyond reading published data.</li>"
        cannot_html = "".join(f"<li>{e(c['text'].rstrip('.'))}.</li>" for c in entry["cannot"])
        parts[entry["audience"]].append(
            f'<section class="setting" id="{slug(label)}"><h3>{e(label)}</h3><p>{e(entry["summary"])}</p>'
            f'<dl><dt>Reads without asking</dt><dd>Published data only. Anything more sensitive needs a lease that somebody else approves.</dd></dl>'
            f'<h4>Can</h4><ul>{can_html}</ul><h4>Cannot</h4><ul>{cannot_html}</ul></section>')
        toc.append(f'<li><a href="#{slug(label)}">{e(label)}</a></li>')
    body = ""
    for audience, title, intro in (
        ("people", "Roles held by people", "The staff of an organisation that uses Munitas."),
        ("platform staff", "Roles held by platform staff", "The people who run and support the platform itself. They hold no more standing access to an organisation's data than any other role."),
    ):
        body += f'<h2 id="{slug(title)}">{e(title)}</h2><p class="lead">{e(intro)}</p>{"".join(parts[audience])}'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Roles reference</title><style>{CSS}
h4{{margin:12px 0 2px;font-size:14px;color:var(--faint);font-weight:600}}.setting ul{{margin:4px 0 8px;padding-left:20px}}.setting li{{margin:3px 0}}</style></head>
<body><main>
<h1>Roles reference</h1>
<p class="lead">What each role can and cannot do. {len(people)} roles.</p>
<div class="how">
<h2 id="how" style="margin-top:14px">How to read this page</h2>
<p><strong>The same everywhere.</strong> The roles and what they may do are the same in every organisation. An organisation chooses who holds each role, and a person can hold more than one.</p>
<p><strong>What a role cannot do.</strong> Where a role cannot do something, that is usually on purpose: the person who asks for something is never the only check on it, and whoever
approves an act cannot afterwards give independent assurance over it.</p>
<p><strong>Reading data.</strong> Every role held by a person reads only published data without asking. More sensitive data is requested through a lease, which a person who is not the asker approves.</p>
</div>
<nav class="toc" aria-label="Roles"><strong>Roles</strong><ul>{"".join(toc)}</ul></nav>
{body}
<footer>This page is generated from the platform's own rules, so it cannot say something the platform does not do. An automatic check fails when the page and the rules disagree.</footer>
</main></body></html>
"""


def main() -> int:
    PAGE.write_text(render_page(), encoding="utf-8", newline="\n")
    print(f"wrote {PAGE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
