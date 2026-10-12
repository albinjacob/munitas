r"""Build the public layered architecture diagram from one description.

A layered picture: who works with Munitas, where every request is decided, where code runs, and where files live, with one band across all of them for the record
of every decision. It has no connectors on purpose. It says what sits in which layer and which places have restricted reach, and it does not say how data travels.

One description below produces:

  docs/public/diagrams/architecture-layers.mmd     Mermaid, the diffable text
  docs/public/diagrams/architecture-layers.drawio  for anyone who wants to rearrange it in draw.io
  docs/public/diagrams/architecture-layers.svg     the picture as a file, shown on the architecture page
  the picture on docs/public/walkthroughs/feature-walkthrough.html, between the diagram markers, with its short description

It reuses the checked drawing code in build_system_architecture_diagram.py, runs every rule from that file on this picture, prints pass or fail for each, and writes
nothing if one fails.

    .venv\Scripts\python.exe docs\tools\build_layers_diagram.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_system_architecture_diagram as g  # noqa: E402

ROOT = g.ROOT
OUT = ROOT / "docs" / "public" / "diagrams"
B, R = g.BLUE, g.RED

BOX_W, BOX_H, GAP = 200, 72, 60
BAND_X, BAND_W = 40, 1028
BAND_H = 150
BAND_TOPS = {"people": 24, "governance": 210, "processing": 396, "storage": 582}
CROSS = (1108, 24, 248, 708)


def row(count: int) -> list[int]:
    """The x of each box when `count` boxes sit centred in a band."""
    inner = BAND_W - 48
    total = count * BOX_W + (count - 1) * GAP
    first = BAND_X + 24 + (inner - total) // 2
    return [first + i * (BOX_W + GAP) for i in range(count)]


# band id: (title, [(box id, title, subtitle, colour)])
BANDS = {
    "people": ("People and tools", [
        ("console", "Console", "where a person works", B),
        ("notebooks", "Notebooks", "Python and client library", B),
    ]),
    "governance": ("Governance", [
        ("sign_in", "Sign-in", "knows who each person is", B),
        ("policy", "Policy engine", "decides every request", B),
        ("api", "API", "every request passes here", B),
    ]),
    "processing": ("Processing", [
        ("jobs", "Pipelines and jobs", "long jobs run here", B),
        ("custom", "Custom pipeline steps", "an organisation's own code", R),
        ("derivation", "Derivation queries", "run with no network", R),
        ("agents", "Sandboxed AI agents", "every tool call is checked", R),
    ]),
    "storage": ("Storage", [
        ("files", "Object storage", "one bucket per organisation", B),
        ("tables", "Tables", "readable with open tools", B),
        ("keys", "Person keys", "one key per person", B),
    ]),
}

W, H = 1396, 960
LEGEND_Y = 770
LEGEND = [
    ("box", B, "A service the platform runs"),
    ("box", R, "A place with restricted reach: it has no route to the internet"),
    ("box", g.ZONE, "A layer"),
]
ABBREVIATIONS = [("API", "Application Programming Interface, the one place every request goes through")]

TITLE = "Munitas in layers"
SUMMARY = (
    "A layered picture of Munitas. People work through the Console and notebooks. Governance decides every request: sign-in, the policy engine and the API. "
    "Processing runs pipelines and jobs, and three kinds of code that are not the platform's own, in places with restricted reach. Storage holds files, tables "
    "and the keys for locked data. One record of every decision runs across all the layers."
)
LEDE = (
    "Munitas is arranged in layers. People work through the Console or a notebook. Every request passes the governance layer: sign-in establishes who the "
    "person is, the API receives the request, and the policy engine decides it. Work that runs code happens in the processing layer, and the three kinds of code "
    "that are not the platform's own, an organisation's custom pipeline steps, derivation queries and sandboxed AI agents, run in places with restricted reach. "
    "Files and tables live in storage, one bucket per organisation, with a key for each person whose data is locked. A record of every decision runs across all of the layers."
)
NOTE = "The picture is simplified. It shows the layers and the places with restricted reach, not every service or every connection."


def describe() -> None:
    """Load this description into the shared drawing code."""
    g.W, g.H, g.LEGEND_Y = W, H, LEGEND_Y
    g.ZONES.clear()
    g.NODES.clear()
    g.EDGES.clear()
    g.EXPLAIN.clear()
    for band, (title, boxes) in BANDS.items():
        top = BAND_TOPS[band]
        g.ZONES[band] = (title, (BAND_X, top, BAND_W, BAND_H))
        for x, (nid, box_title, sub, colour) in zip(row(len(boxes)), boxes):
            g.NODES[nid] = (box_title, sub, (x, top + 24, BOX_W, BOX_H), colour, band)
    cx, cy, cw, ch = CROSS
    g.ZONES["record"] = ("Across every layer", CROSS)
    g.NODES["record"] = ("Record of decisions", "and the reason for each", (cx + 24, cy + ch // 2 - BOX_H // 2 - 15, BOX_W, BOX_H), B, "record")
    g.LEGEND_SWATCHES[:] = LEGEND
    g.LEGEND_ABBREVIATIONS[:] = ABBREVIATIONS
    g.TITLE, g.SUMMARY = TITLE, SUMMARY


def picture() -> str:
    svg = g.build_svg()
    # The bands are layers, not networks, so they are drawn with a solid edge.
    return svg.replace(' stroke-dasharray="6 4"', "")


def diagram_block(svg: str) -> str:
    return f'<!-- diagram:start -->\n  <div class="diagram-wrap">\n{svg}\n  </div>\n  <!-- diagram:end -->'


def update_tour(svg: str) -> None:
    page = ROOT / "docs" / "public" / "walkthroughs" / "feature-walkthrough.html"
    raw = page.read_bytes().decode("utf-8")
    text = raw.replace("\r\n", "\n")
    text = re.sub(r"<!-- diagram:start -->.*?<!-- diagram:end -->", lambda m: diagram_block(svg), text, flags=re.S)
    section = re.search(r'<section id="architecture">.*?</section>', text, re.S).group(0)
    new = section
    new = re.sub(r'<h2 class="measure">.*?</h2>', '<h2 class="measure">Four layers, and one record across all of them</h2>', new, count=1, flags=re.S)
    new = re.sub(r'(<h2 class="measure">.*?</h2>\s*)<p class="measure">.*?</p>', lambda m: f'{m.group(1)}<p class="measure">\n    {LEDE}\n  </p>', new, count=1, flags=re.S)
    new = re.sub(r'<p class="measure" style="font-size:0\.88rem; color:var\(--ink-faint\);">.*?</p>',
                 f'<p class="measure" style="font-size:0.88rem; color:var(--ink-faint);">\n    {NOTE}\n  </p>', new, count=1, flags=re.S)
    text = text.replace(section, new)
    page.write_bytes((text.replace("\n", "\r\n") if "\r\n" in raw else text).encode("utf-8"))
    print("wrote", page.relative_to(ROOT))


def main() -> int:
    describe()
    results = g.run_checks()
    failed = [r for r in results if not r[1]]
    for name, ok, problems in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        for p in problems:
            print("        " + p)
    print(f"{len(results) - len(failed)} of {len(results)} rules pass; {len(g.NODES)} boxes, {len(g.ZONES)} layers")
    if failed:
        print("Nothing written.")
        return 1
    svg = picture()
    for path, text in ((OUT / "architecture-layers.mmd", g.build_mermaid()), (OUT / "architecture-layers.drawio", g.build_drawio()), (OUT / "architecture-layers.svg", svg)):
        path.write_text(text, encoding="utf-8", newline="\n")
        print("wrote", path.relative_to(ROOT))
    update_tour(svg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
