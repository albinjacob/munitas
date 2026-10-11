r"""Build the public system architecture diagram from one description.

One list of places, boxes and connectors below produces all three files, so they cannot disagree:

  docs/public/diagrams/system-architecture.mmd     Mermaid, the diffable text
  docs/public/diagrams/system-architecture.drawio  for anyone who wants to rearrange it in draw.io
  docs/public/diagrams/system-architecture.html    the page, with the diagram drawn inline as SVG (nothing is loaded from outside)

Run it after changing a service, a network or a connector:

    .venv\Scripts\python.exe docs\tools\build_system_architecture_diagram.py

It then checks the drawing against the project's diagram rules by arithmetic on the coordinates, prints pass or fail for each rule, and exits with 1 if any rule
fails. Nothing is written when a rule fails.

The words in the boxes are the ones the architecture page uses. Colours come from the project palette: blue for a service the platform runs, red for a place
with restricted reach, teal for the edge of a network.
"""

from __future__ import annotations

import html
import re
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "docs" / "public" / "diagrams"

# ---------------------------------------------------------------------------------------------------------------------------------------------------
# The description. Coordinates are canvas pixels. Boxes are x, y, width, height.
# ---------------------------------------------------------------------------------------------------------------------------------------------------

W, H = 1192, 990

BLUE = {"fill": "#E1EEFC", "stroke": "#1A5FA8"}
RED = {"fill": "#FAEBEB", "stroke": "#C9564A"}
ZONE = {"fill": "#DDEEF0", "stroke": "#2E7D8A"}
LINE_BLUE, LINE_RED = "#1A5FA8", "#B03030"

ZONES = {
    "platform": ("Platform network", (40, 130, 768, 410)),
    "step_net": ("Step network", (904, 130, 248, 410)),
    "agent_net": ("Agent network, no route out", (216, 620, 248, 140)),
    "no_net": ("No network connection", (560, 620, 248, 140)),
}

# id: (title, subtitle, box, colour, zone)
NODES = {
    "console": ("Console", "where a person works", (584, 24, 200, 60), BLUE, None),
    "sign_in": ("Sign-in", "knows who each person is", (64, 170, 200, 60), BLUE, "platform"),
    "policy": ("Policy engine", "decides every request", (324, 170, 200, 60), BLUE, "platform"),
    "api": ("API", "every request passes here", (584, 170, 200, 60), BLUE, "platform"),
    "database": ("Database", "records every decision", (64, 310, 200, 60), BLUE, "platform"),
    "storage": ("Object storage", "where the files live", (324, 310, 200, 60), BLUE, "platform"),
    "workflow": ("Workflow engine", "keeps long jobs running", (584, 310, 200, 60), BLUE, "platform"),
    "table_worker": ("Table worker", "writes the large tables", (324, 450, 200, 60), BLUE, "platform"),
    "worker": ("Worker", "runs pipelines and agents", (584, 450, 200, 60), BLUE, "platform"),
    "gateway": ("Step gateway", "forwards only step routes", (928, 170, 200, 60), BLUE, "step_net"),
    "custom": ("Custom pipeline step", "an organisation's own code", (928, 450, 200, 60), RED, "step_net"),
    "agent": ("Sandboxed agent run", "every tool call is checked", (240, 665, 200, 60), RED, "agent_net"),
    "derivation": ("Derivation runner", "runs one query, no network", (584, 665, 200, 60), RED, "no_net"),
}

# id: (from, to, colour, points). Points run from the edge of the first box to the point where the arrowhead's tip sits, which is 10 px short of the second box.
EDGES = {
    "console_api": ("console", "api", LINE_BLUE, [(684, 84), (684, 160)]),
    "console_sign_in": ("console", "sign_in", LINE_BLUE, [(584, 54), (164, 54), (164, 160)]),
    "api_policy": ("api", "policy", LINE_BLUE, [(584, 200), (534, 200)]),
    "gateway_api": ("gateway", "api", LINE_BLUE, [(928, 200), (794, 200)]),
    "api_database": ("api", "database", LINE_BLUE, [(684, 230), (684, 270), (164, 270), (164, 300)]),
    "api_storage": ("api", "storage", LINE_BLUE, [(684, 230), (684, 270), (424, 270), (424, 300)]),
    "api_workflow": ("api", "workflow", LINE_BLUE, [(684, 230), (684, 300)]),
    "workflow_worker": ("workflow", "worker", LINE_BLUE, [(684, 370), (684, 440)]),
    "workflow_table_worker": ("workflow", "table_worker", LINE_BLUE, [(684, 370), (684, 410), (424, 410), (424, 440)]),
    "worker_agent": ("worker", "agent", LINE_RED, [(684, 510), (684, 590), (340, 590), (340, 655)]),
    "worker_derivation": ("worker", "derivation", LINE_RED, [(684, 510), (684, 655)]),
    "worker_custom": ("worker", "custom", LINE_RED, [(784, 480), (918, 480)]),
    "custom_gateway": ("custom", "gateway", LINE_RED, [(1028, 450), (1028, 240)]),
}

# The legend is part of the drawing so that it travels with a screenshot. Swatches first, then the one abbreviation in two aligned columns.
LEGEND_Y = 800
LEGEND_SWATCHES = [
    ("box", BLUE, "A service the platform runs"),
    ("box", RED, "A place with restricted reach: it can reach only what an arrow shows"),
    ("zone", ZONE, "The edge of a network"),
    ("line", LINE_BLUE, "A request, from the one that starts it to the one that answers"),
    ("line", LINE_RED, "A connection into or out of a restricted place"),
]
LEGEND_ABBREVIATIONS = [("API", "Application Programming Interface, the platform's single control plane")]

TITLE = "System architecture"
SUMMARY = (
    "A diagram of the services that make up Munitas and the networks they sit on. A person signs in through the Console, and every request passes through the "
    "API, which asks the policy engine. The API reaches the database, object storage and the workflow engine. The workflow engine starts the worker and the "
    "table worker. The worker starts sandboxed agent runs, derivation runners and custom pipeline steps, which sit on networks with restricted reach. A custom "
    "pipeline step reaches the platform only through the step gateway."
)


def box_of(node: str) -> tuple[int, int, int, int]:
    return NODES[node][2]


def centre(box: tuple[int, int, int, int]) -> tuple[float, float]:
    x, y, w, h = box
    return x + w / 2, y + h / 2


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# Checks, as arithmetic on the coordinates above.
# ---------------------------------------------------------------------------------------------------------------------------------------------------

CHAR = 0.56  # average glyph width as a fraction of the font size, with some margin


def segments(points):
    return list(zip(points, points[1:]))


def seg_box_hit(seg, box, pad=0):
    (x1, y1), (x2, y2) = seg
    x, y, w, h = box
    lo_x, hi_x = min(x1, x2), max(x1, x2)
    lo_y, hi_y = min(y1, y2), max(y1, y2)
    return hi_x > x - pad and lo_x < x + w + pad and hi_y > y - pad and lo_y < y + h + pad


def run_checks() -> list[tuple[str, bool, list[str]]]:
    results: list[tuple[str, bool, list[str]]] = []

    def record(name, problems):
        results.append((name, not problems, problems))

    # Containment: every box inside its place, and every place inside the canvas.
    problems = []
    for nid, (_t, _s, (x, y, w, h), _c, zone) in NODES.items():
        if zone:
            zx, zy, zw, zh = ZONES[zone][1]
            if not (x >= zx + 24 and y >= zy + 24 and x + w <= zx + zw - 24 and y + h <= zy + zh - 24):
                problems.append(f"{nid} is within 24 px of, or outside, {zone}")
        if not (x >= 0 and y >= 0 and x + w <= W and y + h <= H):
            problems.append(f"{nid} is outside the canvas")
    for zid, (_t, (x, y, w, h)) in ZONES.items():
        if not (x >= 0 and y >= 0 and x + w <= W and y + h <= H):
            problems.append(f"{zid} is outside the canvas")
    legend_bottom = LEGEND_Y + 14 + 26 * (len(LEGEND_SWATCHES) + len(LEGEND_ABBREVIATIONS)) + 4
    if legend_bottom > H:
        problems.append(f"the legend ends at {legend_bottom}, below the canvas ({H})")
    record("Containment: every box sits inside its network with 24 px of padding, and the legend inside the canvas", problems)

    # Spacing between boxes.
    problems = []
    for a, b in combinations(NODES, 2):
        ax, ay, aw, ah = box_of(a)
        bx, by, bw, bh = box_of(b)
        gap_x = max(bx - (ax + aw), ax - (bx + bw))
        gap_y = max(by - (ay + ah), ay - (by + bh))
        if max(gap_x, gap_y) < 60:
            problems.append(f"{a} and {b} are {max(gap_x, gap_y)} px apart")
    for nid, (_t, _s, (_x, _y, _w, h), _c, _z) in NODES.items():
        if h < 56:
            problems.append(f"{nid} is {h} px tall")
    record("Spacing: at least 60 px between boxes, and two-line boxes at least 56 px tall", problems)

    # Text fits its box.
    problems = []
    for nid, (title, sub, (_x, _y, w, _h), _c, _z) in NODES.items():
        need = max(len(title) * 13 * CHAR + 4, len(sub) * 11 * CHAR)
        if need > w - 24:
            problems.append(f"{nid}: text needs about {need:.0f} px, box allows {w - 24}")
    for zid, (title, (_x, _y, w, _h)) in ZONES.items():
        if len(title) * 12 * CHAR > w - 32:
            problems.append(f"zone {zid}: its title needs about {len(title) * 12 * CHAR:.0f} px, the zone allows {w - 32}")
    record("Text: nothing overflows its box", problems)

    # Orthogonal routing, run lengths, arrowhead clearance.
    problems = []
    for eid, (a, b, _c, pts) in EDGES.items():
        for (x1, y1), (x2, y2) in segments(pts):
            if x1 != x2 and y1 != y2:
                problems.append(f"{eid} has a diagonal segment")
        segs = segments(pts)
        if len(segs) > 1 or True:
            first, last = segs[0], segs[-1]
            for name, s in (("first", first), ("last", last)):
                length = abs(s[0][0] - s[1][0]) + abs(s[0][1] - s[1][1])
                if length < 20:
                    problems.append(f"{eid}: its {name} segment is only {length} px")
    record("Routing: every segment is horizontal or vertical, and the first and last runs are at least 20 px", problems)

    # Each connector leaves its source from the centre of one edge and arrives at the centre of one edge of its target, with a 10 px gap.
    problems = []
    for eid, (a, b, _c, pts) in EDGES.items():
        ax, ay, aw, ah = box_of(a)
        bx, by, bw, bh = box_of(b)
        sx, sy = pts[0]
        ex, ey = pts[-1]
        cx, cy = centre(box_of(a))
        leaves = (
            (sy == ay + ah and sx == cx) or (sy == ay and sx == cx) or (sx == ax + aw and sy == cy) or (sx == ax and sy == cy)
        )
        if not leaves:
            problems.append(f"{eid} does not leave {a} from the centre of an edge")
        tx, ty = centre(box_of(b))
        arrives = (
            (ex == tx and ey == by - 10) or (ex == tx and ey == by + bh + 10) or (ey == ty and ex == bx - 10) or (ey == ty and ex == bx + bw + 10)
        )
        if not arrives:
            problems.append(f"{eid} does not arrive at the centre of an edge of {b}, 10 px short")
    record("Placement: each connector leaves and arrives at the centre of an edge, with a 10 px gap before the box", problems)

    # Connectors may share a trunk from one source, nothing else may overlap or sit close in parallel.
    problems = []
    for (ea, (a1, b1, _c1, p1)), (eb, (a2, b2, _c2, p2)) in combinations(EDGES.items(), 2):
        for (s1, s2) in ((x, y) for x in segments(p1) for y in segments(p2)):
            (x1, y1), (x2, y2) = s1
            (u1, v1), (u2, v2) = s2
            both_h = y1 == y2 and v1 == v2
            both_v = x1 == x2 and u1 == u2
            if both_h:
                overlap = min(max(x1, x2), max(u1, u2)) - max(min(x1, x2), min(u1, u2))
                if overlap > 0 and abs(y1 - v1) < 20 and not (a1 == a2 and y1 == v1):
                    problems.append(f"{ea} and {eb} run {abs(y1 - v1)} px apart over {overlap} px")
            if both_v:
                overlap = min(max(y1, y2), max(v1, v2)) - max(min(y1, y2), min(v1, v2))
                if overlap > 0 and abs(x1 - u1) < 20 and not (a1 == a2 and x1 == u1):
                    problems.append(f"{ea} and {eb} run {abs(x1 - u1)} px apart over {overlap} px")
    record("Parallel runs: two connectors from different sources never run closer than 20 px", problems)

    # Crossings.
    problems = []
    for (ea, (_a1, _b1, _c1, p1)), (eb, (_a2, _b2, _c2, p2)) in combinations(EDGES.items(), 2):
        for s1 in segments(p1):
            for s2 in segments(p2):
                (x1, y1), (x2, y2) = s1
                (u1, v1), (u2, v2) = s2
                for (hs, vs) in ((s1, s2), (s2, s1)):
                    (hx1, hy1), (hx2, hy2) = hs
                    (vx1, vy1), (vx2, vy2) = vs
                    if hy1 == hy2 and vx1 == vx2:
                        if min(hx1, hx2) <= vx1 <= max(hx1, hx2) and min(vy1, vy2) <= hy1 <= max(vy1, vy2):
                            problems.append(f"{ea} and {eb} meet at ({vx1}, {hy1})")
    record("Crossings: no two connectors cross or touch except along a shared trunk from one source", [p for p in problems if not _shared_source(p)])

    # A connector never passes through a box it does not connect, and no arrowhead is near an unrelated box.
    problems = []
    for eid, (a, b, _c, pts) in EDGES.items():
        for nid in NODES:
            if nid in (a, b):
                continue
            for s in segments(pts):
                if seg_box_hit(s, box_of(nid)):
                    problems.append(f"{eid} passes through {nid}")
        tip = pts[-1]
        for nid in NODES:
            if nid == b:
                continue
            x, y, w, h = box_of(nid)
            dx = max(x - tip[0], 0, tip[0] - (x + w))
            dy = max(y - tip[1], 0, tip[1] - (y + h))
            if (dx * dx + dy * dy) ** 0.5 < 8:
                problems.append(f"the tip of {eid} is within 8 px of {nid}")
        for zid, (_t, (x, y, w, h)) in ZONES.items():
            for (bx_, by_) in ((x, None), (x + w, None), (None, y), (None, y + h)):
                if bx_ is not None and y <= tip[1] <= y + h and abs(tip[0] - bx_) < 8:
                    problems.append(f"the tip of {eid} is within 8 px of the edge of {zid}")
                if by_ is not None and x <= tip[0] <= x + w and abs(tip[1] - by_) < 8:
                    problems.append(f"the tip of {eid} is within 8 px of the edge of {zid}")
    for (ea, (_a1, _b1, _c1, p1)), (eb, (_a2, _b2, _c2, p2)) in combinations(EDGES.items(), 2):
        d = ((p1[-1][0] - p2[-1][0]) ** 2 + (p1[-1][1] - p2[-1][1]) ** 2) ** 0.5
        if d < 24:
            problems.append(f"the tips of {ea} and {eb} are {d:.0f} px apart")
    record("Arrowheads: none points at, or sits within 8 px of, a box it does not name; tips are 24 px apart", problems)

    # Text and lines: no connector passes through any text.
    problems = []
    labels = []
    for nid, (title, sub, (x, y, w, h), _c, _z) in NODES.items():
        labels.append((nid, (x + w / 2 - len(sub) * 11 * CHAR / 2, y + 12, len(sub) * 11 * CHAR, 34)))
    for zid, (title, (x, y, w, h)) in ZONES.items():
        labels.append((f"title of {zid}", (x + 24, y + h - 26, len(title) * 12 * CHAR, 16)))
    for eid, (_a, _b, _c, pts) in EDGES.items():
        for lid, tb in labels:
            if lid in (_a, _b):
                continue
            for s in segments(pts):
                if seg_box_hit(s, tb):
                    problems.append(f"{eid} passes through {lid}")
    for (la, ta), (lb, tb) in combinations(labels, 2):
        if ta[0] < tb[0] + tb[2] and tb[0] < ta[0] + ta[2] and ta[1] < tb[1] + tb[3] and tb[1] < ta[1] + ta[3]:
            problems.append(f"the text of {la} overlaps the text of {lb}")
    record("Text and lines: no connector passes through text, and no two texts overlap", problems)

    # Arrowheads: one marker per connector, and the other counts agree.
    svg = build_svg()
    paths = len(re.findall(r'<path class="edge"', svg))
    markers = len(re.findall(r'<path class="edge"[^>]*marker-end="url\(#', svg))
    problems = [] if paths == markers == len(EDGES) else [f"{len(EDGES)} connectors, {paths} drawn, {markers} with an arrowhead"]
    record("Arrowheads: every connector is drawn and has one", problems)
    return results


def _shared_source(problem: str) -> bool:
    """A meeting between two connectors from the same source along its trunk is not a crossing."""
    m = re.match(r"(\w+) and (\w+) meet at", problem)
    if not m:
        return False
    return EDGES[m.group(1)][0] == EDGES[m.group(2)][0]


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# Output.
# ---------------------------------------------------------------------------------------------------------------------------------------------------

def esc(text: str) -> str:
    return html.escape(text, quote=True)


def build_svg() -> str:
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="diagram-title diagram-desc" '
        'font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Arial, sans-serif">',
        f'<title id="diagram-title">{esc(TITLE)}</title>',
        f'<desc id="diagram-desc">{esc(SUMMARY)}</desc>',
        "<defs>",
        *(
            f'<marker id="arrow-{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto" markerUnits="userSpaceOnUse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{colour}"/></marker>'
            for name, colour in (("blue", LINE_BLUE), ("red", LINE_RED))
        ),
        "</defs>",
        f'<rect x="0" y="0" width="{W}" height="{H}" fill="#F6F4EC"/>',
    ]
    for zid, (title, (x, y, w, h)) in ZONES.items():
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{ZONE["fill"]}" stroke="{ZONE["stroke"]}" stroke-width="1.5" stroke-dasharray="6 4"/>')
        out.append(f'<text x="{x + 24}" y="{y + h - 14}" font-size="12" font-weight="700" fill="#1F5D68">{esc(title)}</text>')
    for nid, (title, sub, (x, y, w, h), colour, _z) in NODES.items():
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{colour["fill"]}" stroke="{colour["stroke"]}" stroke-width="1.5"/>')
        out.append(f'<text x="{x + w / 2}" y="{y + 25}" font-size="13" font-weight="700" text-anchor="middle" fill="#1C2333">{esc(title)}</text>')
        out.append(f'<text x="{x + w / 2}" y="{y + 44}" font-size="11" font-style="italic" text-anchor="middle" fill="#4B5266">{esc(sub)}</text>')
    for eid, (_a, _b, colour, pts) in EDGES.items():
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        name = "blue" if colour == LINE_BLUE else "red"
        out.append(f'<path class="edge" id="{eid}" d="{d}" fill="none" stroke="{colour}" stroke-width="1.5" marker-end="url(#arrow-{name})"/>')

    # Legend, a table drawn in the picture.
    x0 = 40
    out.append(f'<text x="{x0}" y="{LEGEND_Y}" font-size="13" font-weight="700" fill="#1C2333">Legend</text>')
    y = LEGEND_Y + 14
    for kind, colour, label in LEGEND_SWATCHES:
        if kind == "line":
            name = "blue" if colour == LINE_BLUE else "red"
            out.append(f'<path class="legend-line" d="M{x0},{y + 11} L{x0 + 26},{y + 11}" fill="none" stroke="{colour}" stroke-width="1.5" marker-end="url(#arrow-{name})"/>')
        else:
            fill, stroke = (colour["fill"], colour["stroke"])
            dash = ' stroke-dasharray="4 3"' if kind == "zone" else ""
            out.append(f'<rect x="{x0}" y="{y + 3}" width="26" height="16" rx="4" fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash}/>')
        out.append(f'<text x="{x0 + 46}" y="{y + 15}" font-size="12" fill="#1C2333">{esc(label)}</text>')
        y += 26
    for abbreviation, meaning in LEGEND_ABBREVIATIONS:
        out.append(f'<text x="{x0}" y="{y + 15}" font-size="12" font-weight="700" fill="#1C2333">{esc(abbreviation)}</text>')
        out.append(f'<text x="{x0 + 46}" y="{y + 15}" font-size="12" fill="#1C2333">{esc(meaning)}</text>')
        y += 26
    out.append("</svg>")
    return "\n".join(out)


def build_mermaid() -> str:
    lines = ["%% Generated by docs/tools/build_system_architecture_diagram.py. Edit the description there, not this file.", "flowchart TB"]
    in_zone: dict[str | None, list[str]] = {}
    for nid, (_t, _s, _b, _c, zone) in NODES.items():
        in_zone.setdefault(zone, []).append(nid)

    def node_line(nid, indent):
        title, sub, _b, _c, _z = NODES[nid]
        return f'{indent}{nid}["{title}<br/><i>{sub}</i>"]'

    for nid in in_zone.get(None, []):
        lines.append(node_line(nid, "  "))
    for zid, (title, _b) in ZONES.items():
        lines.append(f'  subgraph {zid}["{title}"]')
        lines.extend(node_line(nid, "    ") for nid in in_zone.get(zid, []))
        lines.append("  end")
    for eid, (a, b, _c, _p) in EDGES.items():
        lines.append(f"  {a} --> {b}")
    reds = [nid for nid, (_t, _s, _b, c, _z) in NODES.items() if c is RED]
    lines.append(f"  classDef restricted fill:{RED['fill']},stroke:{RED['stroke']}")
    lines.append(f"  classDef service fill:{BLUE['fill']},stroke:{BLUE['stroke']}")
    lines.append(f"  classDef network fill:{ZONE['fill']},stroke:{ZONE['stroke']},stroke-dasharray: 5 5")
    lines.append("  class " + ",".join(nid for nid in NODES if nid not in reds) + " service")
    lines.append("  class " + ",".join(reds) + " restricted")
    lines.append("  class " + ",".join(ZONES) + " network")
    return "\n".join(lines) + "\n"


def build_drawio() -> str:
    def style_for(colour, extra=""):
        return f"rounded=1;arcSize=8;whiteSpace=wrap;html=1;fillColor={colour['fill']};strokeColor={colour['stroke']};strokeWidth=1.5;fontSize=12;{extra}"

    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    for zid, (title, (x, y, w, h)) in ZONES.items():
        cells.append(
            f'<mxCell id="z_{zid}" value="{esc(title)}" style="rounded=1;arcSize=3;whiteSpace=wrap;html=1;dashed=1;verticalAlign=bottom;align=left;spacingLeft=24;spacingBottom=6;'
            f'fontSize=12;fontStyle=1;fontColor=#1F5D68;fillColor={ZONE["fill"]};strokeColor={ZONE["stroke"]};strokeWidth=1.5;container=1;collapsible=0;" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
        )
    for nid, (title, sub, (x, y, w, h), colour, zone) in NODES.items():
        parent = f"z_{zone}" if zone else "1"
        if zone:
            zx, zy, _zw, _zh = ZONES[zone][1]
            x, y = x - zx, y - zy
        value = esc(f"<b>{title}</b><br><i>{sub}</i>")
        cells.append(
            f'<mxCell id="{nid}" value="{value}" style="{style_for(colour)}" vertex="1" parent="{parent}"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
        )
    for eid, (a, b, colour, pts) in EDGES.items():
        ax, ay, aw, ah = box_of(a)
        bx, by, bw, bh = box_of(b)
        sx, sy = pts[0]
        ex, ey = pts[-1]
        exit_x, exit_y = (sx - ax) / aw, (sy - ay) / ah
        # The tip sits 10 px short of the box, so the entry point is the box edge on the line through the tip.
        if ey == by - 10:
            entry_x, entry_y = (ex - bx) / bw, 0
        elif ey == by + bh + 10:
            entry_x, entry_y = (ex - bx) / bw, 1
        elif ex == bx - 10:
            entry_x, entry_y = 0, (ey - by) / bh
        else:
            entry_x, entry_y = 1, (ey - by) / bh
        waypoints = "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in pts[1:-1])
        cells.append(
            f'<mxCell id="e_{eid}" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor={colour};strokeWidth=1.5;endArrow=block;endFill=1;targetPerimeterSpacing=10;'
            f'exitX={exit_x:g};exitY={exit_y:g};exitDx=0;exitDy=0;entryX={entry_x:g};entryY={entry_y:g};entryDx=0;entryDy=0;" edge="1" parent="1" source="{a}" target="{b}">'
            f'<mxGeometry relative="1" as="geometry"><Array as="points">{waypoints}</Array></mxGeometry></mxCell>'
        )
    body = "\n        ".join(cells)
    return (
        '<mxfile host="app.diagrams.net" version="24.0.0">\n'
        '  <diagram name="System architecture" id="system-architecture">\n'
        f'    <mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{W}" pageHeight="{H}" math="0" shadow="0">\n'
        '      <root>\n        ' + body + "\n      </root>\n    </mxGraphModel>\n  </diagram>\n</mxfile>\n"
    )


LEDE = (
    "The services that make up Munitas, grouped by network. Every request into the platform network passes through the API and is checked by the policy engine. "
    "Sandboxed agent runs sit on a separate network with no route out, and every tool call they make is still checked by the same policy engine. A step of an "
    "organisation's own pipeline runs on a network of its own, with no storage and no key, and reaches the platform only through one small gateway."
)
FOOTER = (
    "The same diagram as text: <code>docs/public/diagrams/system-architecture.mmd</code> (Mermaid). To rearrange it, open "
    "<code>docs/public/diagrams/system-architecture.drawio</code> in draw.io."
)


def build_html(existing: str, svg: str) -> str:
    page = existing
    page = re.sub(r'<script src="https://viewer\.diagrams\.net[^\n]*</script>\n', "", page)
    # The legend moved into the picture, so its styles go.
    page = re.sub(r"  h2\.legend-title \{[^\n]*\n", "", page)
    page = re.sub(r"  table\.legend[^\n]*\n", "", page)
    page = re.sub(r"  \.swatch \{[^\n]*\n", "", page)
    page = re.sub(r"  \.diagram svg \{[^\n]*\n", "", page)
    page = re.sub(r"  \.diagram \{.*?\n  \}\n", "  .diagram {\n    /* Always a light plate, whatever the page theme: the drawing uses fixed light fills. */\n"
                  "    background: #f6f4ec; color-scheme: light; border: 1px solid var(--paper-line); border-radius: 10px;\n    padding: 24px; box-shadow: var(--shadow); overflow-x: auto;\n  }\n"
                  "  .diagram svg { display: block; width: 100%; height: auto; }\n", page, flags=re.S)
    page = page.replace(".wrap { max-width: 900px;", ".wrap { max-width: 1240px;")
    block = f'<!-- diagram:start -->\n  <div class="diagram">\n{svg}\n  </div>\n  <!-- diagram:end -->'
    if "<!-- diagram:start -->" in page:
        page = re.sub(r"<!-- diagram:start -->.*?<!-- diagram:end -->", lambda m: block, page, flags=re.S)
    else:
        page = re.sub(r'  <div class="diagram">.*?</table>', lambda m: "  " + block, page, count=1, flags=re.S)
    page = re.sub(r'(<p class="lede">).*?(</p>)', lambda m: f"{m.group(1)}\n    {LEDE}\n  {m.group(2)}", page, count=1, flags=re.S)
    page = re.sub(r"<footer>.*?</footer>", lambda m: f"<footer>\n    {FOOTER}\n  </footer>", page, count=1, flags=re.S)
    return page


def main() -> int:
    results = run_checks()
    failed = [r for r in results if not r[1]]
    for name, ok, problems in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        for p in problems:
            print(f"        {p}")
    print(f"{len(results) - len(failed)} of {len(results)} rules pass; {len(EDGES)} connectors, {len(NODES)} boxes, {len(ZONES)} networks")
    if failed:
        print("Nothing written.")
        return 1
    svg = build_svg()
    page_path = OUT / "system-architecture.html"
    existing = page_path.read_text(encoding="utf-8")
    page = build_html(existing, svg)
    for path, text in (
        (OUT / "system-architecture.mmd", build_mermaid()),
        (OUT / "system-architecture.drawio", build_drawio()),
        (page_path, page),
    ):
        path.write_text(text, encoding="utf-8", newline="\n")
        print("wrote", path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
