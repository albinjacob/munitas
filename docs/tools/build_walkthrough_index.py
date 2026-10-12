r"""Build the walkthrough index and one page for each organisation in docs/public/walkthroughs/.

Run it after the walkthrough builders, because the step counts are read from the pages they wrote:

    .venv\Scripts\python.exe docs\tools\build_walkthrough_index.py

It writes index.html, which introduces Munitas once and links to one page for each organisation, and health_walkthrough.html,
finance_walkthrough.html and harbour_walkthrough.html, which list that organisation's walkthroughs one under another.

The list of walkthroughs, their order and their one-paragraph summaries live in
walkthrough_kit.SERIES, so a new walkthrough is added in one place and appears on its organisation's page, in the
previous and next links of that organisation's pages, and in the tour's list. An entry with a "stage" is listed under that
stage's heading.
"""
from __future__ import annotations

import re
from datetime import date

from walkthrough_kit import CSS, ORG_TITLES, PUBLIC, SERIES, STAGES, org_page, topbar

NL = chr(10)

ORG_BLURB = {
    "health": "A hospital group. Departments such as Cardiology and Radiology each own some of its data, and a "
              "custodian in each department decides who may read it.",
    "finance": "A company that handles card payments. Fraud Operations and Risk and Compliance are separate "
               "departments with their own custodians.",
    "harbour": "A small clinic that is closing down. One department, Patient Records, holds its letters, and two "
               "platform administrators handle the legal hold that keeps them.",
}

# One line on each organisation's page, under its description, about how to read its walkthroughs.
ORG_HOW_TO_READ = {
    "health": "The walkthroughs follow one research programme in the order it unfolds, with the same people and the same data throughout.",
    "finance": "",
    "harbour": "",
}


def steps_in(slug: str) -> int:
    page = PUBLIC / f"{slug}-walkthrough.html"
    return len(re.findall(r'<div class="step" id="s\d+">', page.read_text(encoding="utf-8")))


def minutes_for(steps: int) -> int:
    return max(3, round(steps * 0.8))


def tile(entry: dict, number: int) -> str:
    steps = steps_in(entry["slug"])
    return (f'    <a class="tile" id="w-{entry["slug"]}" href="{entry["slug"]}-walkthrough.html">\n'
            f'      <span class="tag">{number}. {ORG_TITLES[entry["org"]]}</span>\n'
            f'      <h3>{entry["title"]}</h3>\n'
            f'      <p>{entry["summary"]}</p>\n'
            f'      <p class="meta">You will see: {entry["you_see"]}.</p>\n'
            f'      <p class="meta">{steps} steps, about {minutes_for(steps)} minutes. <span class="open">Open &rarr;</span></p>\n'
            f'    </a>')


def org_entries(org: str) -> list:
    return [e for e in SERIES if e["org"] == org]


def stage_id(stage: str) -> str:
    return "stage-" + re.sub(r"[^a-z0-9]+", "-", stage.lower()).strip("-")


def org_contents(org: str) -> str:
    """Quick navigation: every heading and title jumps to its place on the page, where the tile opens the walkthrough."""
    groups, order = {}, []
    for number, entry in enumerate(org_entries(org), 1):
        key = entry.get("stage")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((number, entry))

    def items(pairs: list) -> str:
        return NL.join(f'        <li><a href="#w-{e["slug"]}"><span class="quicknav-n">{n}.</span>{e["title"]}</a></li>'
                       for n, e in pairs)

    if order == [None]:
        return ('<nav class="quicknav" aria-label="Quick navigation">' + NL + "  <h2>Quick navigation</h2>" + NL + "  <ul>" + NL
                + items(groups[None]) + NL + "  </ul>" + NL + "</nav>")
    blocks = [f'    <li><a class="quicknav-stage" href="#{stage_id(k)}">{k}</a>' + NL + "      <ul>" + NL + items(groups[k]) + NL
              + "      </ul>" + NL + "    </li>" for k in order]
    return ('<nav class="quicknav" aria-label="Quick navigation">' + NL + "  <h2>Quick navigation</h2>" + NL
            + f'  <ol class="quicknav-stages{" quicknav-few" if len(order) < 3 else ""}">' + NL + NL.join(blocks) + NL + "  </ol>" + NL + "</nav>")


def org_list(org: str) -> str:
    """One organisation's walkthroughs, under the heading of the stage each belongs to, one under another."""
    parts, tiles, stage = [], [], None
    for number, entry in enumerate(org_entries(org), 1):
        if entry.get("stage") != stage:
            if tiles:
                parts.append('  <div class="tiles">\n' + "\n".join(tiles) + "\n  </div>")
            tiles, stage = [], entry.get("stage")
            if stage:
                parts.append(f'  <h3 class="stage" id="{stage_id(stage)}">{stage}</h3>\n  <p>{STAGES[stage]}</p>')
        tiles.append(tile(entry, number))
    parts.append('  <div class="tiles">\n' + "\n".join(tiles) + "\n  </div>")
    return f'<section class="group" id="{org}">\n' + "\n".join(parts) + "\n</section>"


def render_org(org: str) -> str:
    title = ORG_TITLES[org]
    how = f'\n  <p class="lede" style="margin-top:0.9em">{ORG_HOW_TO_READ[org]}</p>' if ORG_HOW_TO_READ[org] else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} walkthroughs: Munitas</title>
{CSS}
</head>
<body>
{topbar("index")}
<header class="hero">
  <p class="crumbs"><a href="index.html">All walkthroughs</a> / {title}</p>
  <div class="eyebrow">Munitas &middot; Walkthroughs</div>
  <h1>{title}</h1>
  <p class="lede">{ORG_BLURB[org]}</p>{how}
</header>
<div class="wrap">
{org_contents(org)}
{org_list(org)}
</div>
<footer class="page-foot">
  <p><a href="index.html">All walkthroughs</a> &middot; <a href="feature-walkthrough.html">Feature tour</a> &middot; Last updated {date.today().isoformat()}.</p>
</footer>
</body>
</html>
"""


def org_card(org: str) -> str:
    entries = org_entries(org)
    total = sum(steps_in(e["slug"]) for e in entries)
    minutes = sum(minutes_for(steps_in(e["slug"])) for e in entries)   # the sum of the tiles' own figures, so the two always agree
    return (f'    <a class="tile" href="{org_page(org)}">\n'
            f'      <h3>{ORG_TITLES[org]}</h3>\n'
            f'      <p>{ORG_BLURB[org]}</p>\n'
            f'      <p class="meta">{len(entries)} walkthroughs, {total} steps, about {minutes} minutes in all. '
            f'<span class="open">Open &rarr;</span></p>\n'
            f'    </a>')


def render() -> str:
    cards = "\n".join(org_card(org) for org in ORG_TITLES)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Munitas walkthroughs</title>
{CSS}
</head>
<body>
{topbar("index")}
<header class="hero">
  <div class="eyebrow">Munitas &middot; Walkthroughs</div>
  <h1>See how Munitas decides who may read which data</h1>
  <p class="lede">Munitas is a governed data platform. It decides who may read which piece of data, for what
  purpose and for how long, and it keeps a permanent record of every decision. Each walkthrough follows
  one real story, step by step, with real screens from the Munitas console.</p>
  <p class="lede" style="margin-top:0.9em"><em>Munitas</em> is built from the Latin <em>munire</em>, &ldquo;to fortify or protect&rdquo;, in the pattern of
  <em>veritas</em> and <em>libertas</em>: the state of being protected. It names what the platform is for.
  Datasets, AI agents and the record of what happened to them are protected by guarantees that cannot be
  quietly bypassed.</p>
</header>
<div class="wrap">
<section class="before">
  <h2>How the walkthroughs work</h2>
  <ul>
    <li>Three separate example organisations appear: a hospital group (Health), a company that handles card payments
    (Finance) and a small clinic that is closing down (Harbour Clinic). Health is the full story, and the other two are
    shorter. Every name of a person, patient and card holder is invented, and the people of an organisation see only that
    organisation's data.</li>
    <li>Every step names the screen it happens on, shows what was captured, and then says what to look for in it. Click
    any picture to enlarge it.</li>
    <li>Each page starts with the words it uses, so a page can be read on its own. The Health pages also follow one story,
    with the same people and the same data throughout.</li>
    <li>The screens are captured live from a running Munitas, and where a step shows Python, it shows the commands that
    were typed and what came back. Nothing is mocked.</li>
    <li>New here? Open the Health organisation. Its walkthroughs follow one research programme in the order it unfolds.</li>
  </ul>
</section>
<section class="group">
  <h2>Choose an organisation</h2>
  <div class="tiles">
{cards}
  </div>
</section>
<section class="before">
  <h2>Looking for the platform tour?</h2>
  <p style="margin:0;color:var(--ink-soft)">The <a href="feature-walkthrough.html">feature tour</a> describes what
  Munitas does and how it is built, in the order a new reader asks about it.</p>
</section>
</div>
<footer class="page-foot">
  <p><a href="feature-walkthrough.html">Feature tour</a> &middot; Last updated {date.today().isoformat()}.</p>
</footer>
</body>
</html>
"""


def update_feature_page() -> None:
    """Keep the short list of organisations, and how many walkthroughs each has, on the feature tour in step with SERIES."""
    page = PUBLIC / "feature-walkthrough.html"
    text = page.read_text(encoding="utf-8")
    start, end = "<!-- walkthrough-list:start -->", "<!-- walkthrough-list:end -->"
    a, b = text.index(start) + len(start), text.index(end)
    items = NL.join(
        f'    <li><a href="{org_page(org)}">{title}</a>: {len(org_entries(org))} walkthroughs</li>'
        for org, title in ORG_TITLES.items())
    block = NL.join(["", '  <ul class="plain">', items, "  </ul>",
                     '  <p class="measure" style="margin-top:1.25rem"><a href="index.html">'
                     "Open the walkthrough index, which also says where to start &rarr;</a></p>", "  "])
    page.write_text(text[:a] + block + text[b:], encoding="utf-8")
    print(f"updated the walkthrough list in {page.name}")


if __name__ == "__main__":
    out = PUBLIC / "index.html"
    out.write_text(render(), encoding="utf-8")
    print(f"wrote {out.name} ({out.stat().st_size // 1024} KB, {len(ORG_TITLES)} organisations)")
    for org in ORG_TITLES:
        page = PUBLIC / org_page(org)
        page.write_text(render_org(org), encoding="utf-8")
        print(f"wrote {page.name} ({page.stat().st_size // 1024} KB, {len(org_entries(org))} walkthroughs)")
    update_feature_page()
