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
INTERNAL = ROOT / "docs" / "internal" / "diagrams" / "system-architecture"

# ---------------------------------------------------------------------------------------------------------------------------------------------------
# The description. Coordinates are canvas pixels. Boxes are x, y, width, height.
# ---------------------------------------------------------------------------------------------------------------------------------------------------

W, H = 1192, 1080

BLUE = {"fill": "#E1EEFC", "stroke": "#1A5FA8"}
RED = {"fill": "#FAEBEB", "stroke": "#C9564A"}
ZONE = {"fill": "#DDEEF0", "stroke": "#2E7D8A"}
LINE_BLUE, LINE_RED = "#1A5FA8", "#B03030"

ZONES = {
    "platform": ("Platform network", (40, 130, 768, 470)),
    "step_net": ("Step network", (904, 130, 248, 470)),
    "agent_net": ("Agent network, no route out", (216, 680, 248, 140)),
    "no_net": ("No network connection", (560, 680, 248, 140)),
}

# id: (title, subtitle, box, colour, zone)
NODES = {
    "console": ("Console", "where a person works", (584, 24, 200, 60), BLUE, None),
    "sign_in": ("Sign-in", "knows who each person is", (64, 170, 200, 60), BLUE, "platform"),
    "policy": ("Policy engine", "decides every request", (324, 170, 200, 60), BLUE, "platform"),
    "api": ("API", "every request passes here", (584, 170, 200, 60), BLUE, "platform"),
    "database": ("Database", "records every decision", (64, 340, 200, 60), BLUE, "platform"),
    "storage": ("Object storage", "where the files live", (324, 340, 200, 60), BLUE, "platform"),
    "workflow": ("Workflow engine", "keeps long jobs running", (584, 340, 200, 60), BLUE, "platform"),
    "table_worker": ("Table worker", "writes the large tables", (324, 510, 200, 60), BLUE, "platform"),
    "worker": ("Worker", "runs pipelines and agents", (584, 510, 200, 60), BLUE, "platform"),
    "gateway": ("Step gateway", "forwards only step routes", (928, 170, 200, 60), BLUE, "step_net"),
    "custom": ("Custom pipeline step", "an organisation's own code", (928, 510, 200, 60), RED, "step_net"),
    "agent": ("Sandboxed agent run", "every tool call is checked", (240, 725, 200, 60), RED, "agent_net"),
    "derivation": ("Derivation runner", "runs one query, no network", (584, 725, 200, 60), RED, "no_net"),
}

# id: (from, to, colour, points). Points run from the edge of the first box to the point where the arrowhead's tip sits, which is 10 px short of the second box.
EDGES = {
    "console_api": ("console", "api", LINE_BLUE, [(684, 84), (684, 160)]),
    "console_sign_in": ("console", "sign_in", LINE_BLUE, [(584, 54), (164, 54), (164, 160)]),
    "api_policy": ("api", "policy", LINE_BLUE, [(584, 200), (534, 200)]),
    "gateway_api": ("gateway", "api", LINE_BLUE, [(928, 200), (794, 200)]),
    "api_database": ("api", "database", LINE_BLUE, [(684, 230), (684, 270), (164, 270), (164, 330)]),
    "api_storage": ("api", "storage", LINE_BLUE, [(684, 230), (684, 270), (424, 270), (424, 330)]),
    "api_workflow": ("api", "workflow", LINE_BLUE, [(684, 230), (684, 330)]),
    "workflow_worker": ("workflow", "worker", LINE_BLUE, [(684, 400), (684, 500)]),
    "workflow_table_worker": ("workflow", "table_worker", LINE_BLUE, [(684, 400), (684, 440), (424, 440), (424, 500)]),
    "worker_agent": ("worker", "agent", LINE_RED, [(684, 570), (684, 650), (340, 650), (340, 715)]),
    "worker_derivation": ("worker", "derivation", LINE_RED, [(684, 570), (684, 715)]),
    "worker_custom": ("worker", "custom", LINE_RED, [(784, 540), (918, 540)]),
    "custom_gateway": ("custom", "gateway", LINE_RED, [(1028, 510), (1028, 240)]),
}

# The legend is part of the drawing so that it travels with a screenshot. Swatches first, then the one abbreviation in two aligned columns.
LEGEND_Y = 860
LEGEND_SWATCHES = [
    ("box", BLUE, "A service the platform runs"),
    ("box", RED, "A place with restricted reach: it can reach only what an arrow shows"),
    ("zone", ZONE, "The edge of a network"),
    ("line", LINE_BLUE, "A request, from the one that starts it to the one that answers"),
    ("line", LINE_RED, "A connection into or out of a restricted place"),
    ("badge", LINE_BLUE, "The number of a connector: what it does is written under the picture"),
]
LEGEND_ABBREVIATIONS = [("API", "Application Programming Interface, the platform's single control plane")]

# One line for each connector, in the order the connectors are numbered (the order of EDGES). They are written for a reader who has never seen the platform.
EXPLAIN = {
    "console_api": "The Console sends everything a person does to the API as a request.",
    "console_sign_in": "The Console asks Sign-in who the person is, and Sign-in issues the session that every request carries.",
    "api_policy": "The API asks the policy engine whether each request is allowed, and the engine answers with a reason.",
    "gateway_api": "The step gateway passes a custom step's requests to the API. It forwards only the step routes and answers 404 to everything else.",
    "api_database": "The API records every decision, and the history behind it, in the database.",
    "api_storage": "The API creates a key in object storage for one task at a time, so a task can open only the files it was allowed.",
    "api_workflow": "The API starts a long job, such as a pipeline run, in the workflow engine.",
    "workflow_worker": "The workflow engine hands the job to the worker and keeps it going if the worker stops, so the job can resume.",
    "workflow_table_worker": "The workflow engine hands the writing of a large table to the table worker.",
    "worker_agent": "The worker starts a sandboxed agent run on a network with no route to the internet.",
    "worker_derivation": "The worker starts the derivation runner, which runs one query with no network connection.",
    "worker_custom": "The worker starts a custom pipeline step, which is an organisation's own code, on a network of its own.",
    "custom_gateway": "The custom step reaches the platform only through the step gateway.",
}

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


BADGE_R = 10


def _dist_point_segment(p, seg):
    (x1, y1), (x2, y2) = seg
    px, py = p
    dx, dy = x2 - x1, y2 - y1
    u = 0 if dx == dy == 0 else max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return ((px - (x1 + u * dx)) ** 2 + (py - (y1 + u * dy)) ** 2) ** 0.5


def _dist_point_box(p, box):
    x, y, w, h = box
    dx = max(x - p[0], 0, p[0] - (x + w))
    dy = max(y - p[1], 0, p[1] - (y + h))
    return (dx * dx + dy * dy) ** 0.5


def badge_positions() -> dict:
    """Where each connector's number sits: on a stretch of its own, away from every box, every title and every other connector. None when there is no such place."""
    zone_titles = [(x + 24, y + h - 26, len(title) * 12 * CHAR, 16) for _z, (title, (x, y, w, h)) in ZONES.items()]
    spots: dict = {}
    for eid, (_a, _b, _c, pts) in EDGES.items():
        segs = segments(pts)
        best = None
        for index, ((x1, y1), (x2, y2)) in enumerate(segs):
            length = abs(x2 - x1) + abs(y2 - y1)
            last = index == len(segs) - 1
            lo, hi = 18, length - (30 if last else 18)
            if hi < lo or (best is not None and length <= best[0]):
                continue
            horizontal = y1 == y2
            sign = 1 if (x2 > x1 if horizontal else y2 > y1) else -1
            for d in sorted(range(lo, hi + 1), key=lambda v: abs(v - (lo + hi) / 2)):
                p = (x1 + sign * d, y1) if horizontal else (x1, y1 + sign * d)
                if any(_dist_point_box(p, box_of(n)) < BADGE_R + 8 for n in NODES):
                    continue
                if any(_dist_point_box(p, tb) < BADGE_R + 6 for tb in zone_titles):
                    continue
                if any(_dist_point_segment(p, s) < BADGE_R + 6 for oid, (_x, _y, _cc, opts) in EDGES.items() if oid != eid for s in segments(opts)):
                    continue
                if any(((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5 < 2 * BADGE_R + 8 for q in spots.values() if q):
                    continue
                best = (length, p)
                break
        spots[eid] = best[1] if best else None
    return spots


def connection_title(eid: str) -> str:
    a, b, _c, _p = EDGES[eid]
    return f"{NODES[a][0]} to {NODES[b][0]}"


def connections_html() -> str:
    items = "\n".join(f"    <li><strong>{esc(connection_title(eid))}.</strong> {esc(EXPLAIN[eid])}</li>" for eid in EDGES)
    return f'<ol class="connections" style="margin:1.2rem 0 0; padding-left:1.5rem; line-height:1.6;">\n{items}\n  </ol>'


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
    # Numbers: one badge per connector, clear of everything, and a line of explanation for each.
    problems = [f"{eid} has no clear place for its number" for eid, spot in badge_positions().items() if spot is None]
    problems += [f"{eid} has no explanation" for eid in EDGES if eid not in EXPLAIN]
    problems += [f"{eid} is explained but is not a connector" for eid in EXPLAIN if eid not in EDGES]
    record("Numbers: every connector has a number on a stretch of its own, clear of boxes, titles and other connectors, and a line of explanation", problems)
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

    for number, (eid, spot) in enumerate(badge_positions().items(), 1):
        if spot is None:
            continue
        colour = EDGES[eid][2]
        out.append(f'<circle class="badge" cx="{spot[0]}" cy="{spot[1]}" r="{BADGE_R}" fill="#FFFFFF" stroke="{colour}" stroke-width="1.5"/>')
        out.append(f'<text x="{spot[0]}" y="{spot[1] + 4}" font-size="11" font-weight="700" text-anchor="middle" fill="#1C2333">{number}</text>')

    # Legend, a table drawn in the picture.
    x0 = 40
    out.append(f'<text x="{x0}" y="{LEGEND_Y}" font-size="13" font-weight="700" fill="#1C2333">Legend</text>')
    y = LEGEND_Y + 14
    for kind, colour, label in LEGEND_SWATCHES:
        if kind == "badge":
            out.append(f'<circle cx="{x0 + 13}" cy="{y + 11}" r="9" fill="#FFFFFF" stroke="{colour}" stroke-width="1.5"/>')
            out.append(f'<text x="{x0 + 13}" y="{y + 15}" font-size="11" font-weight="700" text-anchor="middle" fill="#1C2333">1</text>')
        elif kind == "line":
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
    for number, (eid, (a, b, _c, _p)) in enumerate(EDGES.items(), 1):
        lines.append(f"  %% {number}. {EXPLAIN[eid]}")
        lines.append(f"  {a} -->|{number}| {b}")
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
    for number, (eid, (a, b, colour, pts)) in enumerate(EDGES.items(), 1):
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
            f'<mxCell id="e_{eid}" value="{number}" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor={colour};strokeWidth=1.5;endArrow=block;endFill=1;targetPerimeterSpacing=10;'
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
    INTERNAL.mkdir(parents=True, exist_ok=True)
    for path, text in (
        (INTERNAL / "system-architecture.mmd", build_mermaid()),
        (INTERNAL / "system-architecture.drawio", build_drawio()),
        (INTERNAL / "system-architecture.svg", svg),
    ):
        path.write_text(text, encoding="utf-8", newline="\n")
        print("wrote", path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
