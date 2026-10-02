r"""Build docs/public/walkthroughs/index.html, the page that introduces Munitas once and links every walkthrough.

Run it after the walkthrough builders, because the step counts are read from the pages they wrote:

    .venv\Scripts\python.exe docs\tools\build_walkthrough_index.py

The list of walkthroughs, their order and their one-paragraph summaries live in
walkthrough_kit.SERIES, so a new walkthrough is added in one place and appears here, in the
previous and next links of every page, and under its organisation.
"""
from __future__ import annotations

import re
from datetime import date

from walkthrough_kit import CSS, ORG_TITLES, PUBLIC, SERIES, topbar

NL = chr(10)

ORG_BLURB = {
    "health": "A hospital group. Departments such as Cardiology and Radiology each own some of its data, and a "
              "custodian in each department decides who may read it.",
    "finance": "A company that handles card payments. Fraud Operations and Risk and Compliance are separate "
               "departments with their own custodians.",
    "harbour": "A small clinic that is closing down. One department, Patient Records, holds its letters, and two "
               "platform administrators handle the legal hold that keeps them.",
}


def steps_in(slug: str) -> int:
    page = PUBLIC / f"{slug}-walkthrough.html"
    return len(re.findall(r'<div class="step" id="s\d+">', page.read_text(encoding="utf-8")))


def tile(entry: dict, number: int) -> str:
    steps = steps_in(entry["slug"])
    minutes = max(3, round(steps * 0.8))
    return (f'    <a class="tile" href="{entry["slug"]}-walkthrough.html">\n'
            f'      <span class="tag">{number}. {ORG_TITLES[entry["org"]]}</span>\n'
            f'      <h3>{entry["title"]}</h3>\n'
            f'      <p>{entry["summary"]}</p>\n'
            f'      <p class="meta">You will see: {entry["you_see"]}.</p>\n'
            f'      <p class="meta">{steps} steps, about {minutes} minutes. <span class="open">Open &rarr;</span></p>\n'
            f'    </a>')


def groups() -> str:
    out = []
    for org, title in ORG_TITLES.items():
        entries = [e for e in SERIES if e["org"] == org]
        tiles = "\n".join(tile(e, i) for i, e in enumerate(entries, 1))
        out.append(f'<section class="group" id="{org}">\n  <h2>{title}</h2>\n  <p>{ORG_BLURB[org]}</p>\n'
                   f'  <div class="tiles">\n{tiles}\n  </div>\n</section>')
    return "\n".join(out)


def render() -> str:
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
  <p class="lede">Munitas is a governance platform. It decides who may read which piece of data, for what
  purpose and for how long, and it keeps a permanent record of every decision. Each walkthrough below follows
  one real story, step by step, with real screens from the Munitas console.</p>
</header>
<div class="wrap">
<section class="before">
  <h2>How the walkthroughs work</h2>
  <ul>
    <li>Three example organisations appear, a hospital group, a card payments company and a small clinic that is
    closing. Every name of a person, patient and card holder in them is invented, and each organisation sees only
    its own data.</li>
    <li>Every step names where it happens, shows the evidence, and then says what to look for in it. Each page
    starts with the words that it uses, so a page can be read on its own.</li>
    <li>The screens are captured live from a running Munitas, and the Python steps show the exact commands that
    were typed and what came back. Nothing is mocked.</li>
    <li>New here? Start with the first walkthrough under Health organisation. The other walkthroughs tell
    different stories with different people, so they can be read in any order.</li>
  </ul>
</section>
{groups()}
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
    """Keep the list of walkthroughs on the feature tour in step with SERIES."""
    page = PUBLIC / "feature-walkthrough.html"
    text = page.read_text(encoding="utf-8")
    start, end = "<!-- walkthrough-list:start -->", "<!-- walkthrough-list:end -->"
    a, b = text.index(start) + len(start), text.index(end)
    columns = []
    for org, title in ORG_TITLES.items():
        items = NL.join(f'      <li><a href="{e["slug"]}-walkthrough.html">{e["title"]}</a></li>'
                        for e in SERIES if e["org"] == org)
        columns.append(NL.join(["    <div>", f"      <h3>{title}</h3>", "      <ul>", items, "      </ul>", "    </div>"]))
    block = NL.join(["", '  <div class="walk-list">', NL.join(columns), "  </div>",
                     '  <p class="measure" style="margin-top:1.25rem"><a href="index.html">'
                     "Open the walkthrough index &rarr;</a></p>", "  "])
    page.write_text(text[:a] + block + text[b:], encoding="utf-8")
    print(f"updated the walkthrough list in {page.name}")


if __name__ == "__main__":
    out = PUBLIC / "index.html"
    out.write_text(render(), encoding="utf-8")
    print(f"wrote {out.name} ({out.stat().st_size // 1024} KB, {len(SERIES)} walkthroughs)")
    update_feature_page()
