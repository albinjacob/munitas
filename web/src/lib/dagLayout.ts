/**
 * Where to draw the steps of a pipeline, and the arrows between them.
 *
 * Steps are placed in columns, left to right: a step sits one column to the right of the furthest step it waits for, so every arrow points
 * rightwards. Inside a column the steps are ordered to keep arrows from crossing. Every arrow is a path of horizontal and vertical segments
 * meeting at right angles:
 *   - the arrows leaving one step share one vertical run, so a split reads as one source, and every branch turns at the same place;
 *   - arrows into one step arrive at different points on its left edge, and arrive from different vertical runs, so heads never collide;
 *   - an arrow that skips columns runs under the whole diagram rather than through the steps in between;
 *   - where an arrow crosses another arrow it hops over it with a small arc, so the two stay distinguishable.
 * The arrowhead stops ARROW_GAP short of the step it points at.
 */

export const NODE_W = 224;
export const NODE_H = 84;
export const ROW_GAP = 60;
export const PAD = 24;
export const ARROW_GAP = 10;
const MIN_COL_GAP = 80;
const LANE_START = 22;
const LANE_STEP = 12;
const HOP = 5;

export interface DagStepIn {
  name: string;
  depends_on?: string[];
}

export interface DagNode {
  name: string;
  layer: number;
  row: number;
  x: number;
  y: number;
}

export interface DagEdge {
  from: string;
  to: string;
  /** The corners of the arrow, from the step it leaves to where its head ends. */
  points: [number, number][];
  /** The same path as an SVG `d`, with an arc where it hops over another arrow. */
  d: string;
  hops: number;
}

export interface DagLayout {
  width: number;
  height: number;
  nodes: DagNode[];
  edges: DagEdge[];
}

type Seg = { x1: number; y1: number; x2: number; y2: number; edge: number };

export function layoutDag(steps: DagStepIn[]): DagLayout {
  const names = steps.map((s) => s.name);
  const known = new Set(names);
  const parents = new Map<string, string[]>();
  const children = new Map<string, string[]>();
  for (const n of names) {
    parents.set(n, []);
    children.set(n, []);
  }
  for (const s of steps) {
    for (const d of s.depends_on ?? []) {
      if (!known.has(d) || d === s.name) continue;
      parents.get(s.name)!.push(d);
      children.get(d)!.push(s.name);
    }
  }

  // Column: one more than the furthest step waited for. The count is bounded so a loop, which a valid pipeline never has, cannot hang the page.
  const layer = new Map<string, number>();
  const place = (n: string, depth: number): number => {
    if (layer.has(n)) return layer.get(n)!;
    if (depth > names.length) return 0;
    const l = parents.get(n)!.reduce((m, p) => Math.max(m, place(p, depth + 1) + 1), 0);
    layer.set(n, l);
    return l;
  };
  names.forEach((n) => place(n, 0));
  const layerCount = Math.max(0, ...names.map((n) => layer.get(n)!)) + 1;
  const columns: string[][] = Array.from({ length: layerCount }, () => []);
  names.forEach((n) => columns[layer.get(n)!].push(n));

  // Order inside each column by the average position of the steps it is joined to, a few sweeps each way.
  const rowOf = new Map<string, number>();
  const reindex = () => columns.forEach((c) => c.forEach((n, i) => rowOf.set(n, i)));
  reindex();
  const bary = (n: string, from: Map<string, string[]>) => {
    const rows = from.get(n)!.map((m) => rowOf.get(m)!);
    return rows.length ? rows.reduce((a, b) => a + b, 0) / rows.length : rowOf.get(n)!;
  };
  for (let sweep = 0; sweep < 3; sweep++) {
    for (let l = 1; l < layerCount; l++) {
      columns[l].sort((a, b) => bary(a, parents) - bary(b, parents) || rowOf.get(a)! - rowOf.get(b)!);
      reindex();
    }
    for (let l = layerCount - 2; l >= 0; l--) {
      columns[l].sort((a, b) => bary(a, children) - bary(b, children) || rowOf.get(a)! - rowOf.get(b)!);
      reindex();
    }
  }

  const maxRows = Math.max(1, ...columns.map((c) => c.length));
  const bodyH = maxRows * NODE_H + (maxRows - 1) * ROW_GAP;
  const yOf = new Map<string, number>();
  columns.forEach((c) => {
    const h = c.length * NODE_H + (c.length - 1) * ROW_GAP;
    const top = PAD + (bodyH - h) / 2;
    c.forEach((n, i) => yOf.set(n, top + i * (NODE_H + ROW_GAP)));
  });

  // Every arrow, with the gap (between columns) it leaves through and the one it arrives through.
  interface E { from: string; to: string; ls: number; lt: number; far: boolean }
  const edges: E[] = [];
  for (const s of steps) {
    for (const d of parents.get(s.name)!) {
      edges.push({ from: d, to: s.name, ls: layer.get(d)!, lt: layer.get(s.name)!, far: layer.get(s.name)! - layer.get(d)! > 1 });
    }
  }

  // Arrival points on each step's left edge, ordered by where the arrow starts so they do not cross each other.
  const portY = new Map<E, number>();
  for (const n of names) {
    const into = edges.filter((e) => e.to === n).sort((a, b) => yOf.get(a.from)! - yOf.get(b.from)! || a.ls - b.ls);
    into.forEach((e, i) => portY.set(e, yOf.get(n)! + ((i + 1) * NODE_H) / (into.length + 1)));
  }

  // Vertical runs (lanes) in each gap: one per step that sends arrows through it (shared by all its arrows), one per skipping arrow arriving.
  type Owner = { key: string; y: number };
  const gapOwners: Owner[][] = Array.from({ length: Math.max(0, layerCount - 1) }, () => []);
  for (const n of names) {
    if (children.get(n)!.length) gapOwners[layer.get(n)!].push({ key: `src:${n}`, y: yOf.get(n)! + NODE_H / 2 });
  }
  for (const e of edges) {
    if (e.far) gapOwners[e.lt - 1].push({ key: `arr:${e.from}>${e.to}`, y: portY.get(e)! });
  }
  gapOwners.forEach((o) => o.sort((a, b) => a.y - b.y || a.key.localeCompare(b.key)));
  const gapWidth = gapOwners.map((o) => Math.max(MIN_COL_GAP, LANE_START + Math.max(0, o.length - 1) * LANE_STEP + LANE_STEP + ARROW_GAP + 14));
  const colX: number[] = [];
  let x = PAD;
  for (let l = 0; l < layerCount; l++) {
    colX.push(x);
    x += NODE_W + (gapWidth[l] ?? 0);
  }
  const laneX = (gap: number, key: string) => colX[gap] + NODE_W + LANE_START + gapOwners[gap].findIndex((o) => o.key === key) * LANE_STEP;

  const farEdges = edges.filter((e) => e.far);
  const underY = PAD + bodyH + 28;
  const farIndex = new Map<E, number>();
  farEdges.sort((a, b) => a.ls - b.ls || a.lt - b.lt).forEach((e, i) => farIndex.set(e, i));

  const raw = edges.map((e) => {
    const sx = colX[e.ls] + NODE_W;
    const sy = yOf.get(e.from)! + NODE_H / 2;
    const px = colX[e.lt] - ARROW_GAP;
    const py = portY.get(e)!;
    const pts: [number, number][] = [[sx, sy]];
    if (!e.far) {
      const lx = laneX(e.ls, `src:${e.from}`);
      if (Math.abs(sy - py) < 0.5) pts.push([px, py]);
      else pts.push([lx, sy], [lx, py], [px, py]);
    } else {
      const l1 = laneX(e.ls, `src:${e.from}`);
      const l2 = laneX(e.lt - 1, `arr:${e.from}>${e.to}`);
      const yy = underY + farIndex.get(e)! * LANE_STEP;
      pts.push([l1, sy], [l1, yy], [l2, yy], [l2, py], [px, py]);
    }
    return { e, pts };
  });

  // Hops: where one arrow's horizontal run crosses another arrow's vertical run. Arrows of one step share their first vertical run, which is a
  // join and not a crossing.
  const vertical: Seg[] = [];
  raw.forEach(({ pts }, i) => {
    for (let k = 0; k + 1 < pts.length; k++) {
      const [x1, y1] = pts[k];
      const [x2, y2] = pts[k + 1];
      if (x1 === x2 && y1 !== y2) vertical.push({ x1, y1: Math.min(y1, y2), x2, y2: Math.max(y1, y2), edge: i });
    }
  });
  let totalHops = 0;
  const out: DagEdge[] = raw.map(({ e, pts }, i) => {
    let d = `M${pts[0][0]},${pts[0][1]}`;
    let hops = 0;
    for (let k = 0; k + 1 < pts.length; k++) {
      const [x1, y1] = pts[k];
      const [x2, y2] = pts[k + 1];
      if (y1 === y2 && x1 !== x2) {
        const hits = vertical
          .filter((v) => v.edge !== i && raw[v.edge].e.from !== e.from && v.x1 > Math.min(x1, x2) + HOP + 1 && v.x1 < Math.max(x1, x2) - HOP - 1 && y1 > v.y1 + 1 && y1 < v.y2 - 1)
          .map((v) => v.x1)
          .sort((a, b) => a - b);
        for (const cx of hits) {
          d += ` L${cx - HOP},${y1} A${HOP},${HOP} 0 0 1 ${cx + HOP},${y1}`;
          hops++;
        }
      }
      d += ` L${x2},${y2}`;
    }
    totalHops += hops;
    return { from: e.from, to: e.to, points: pts, d, hops };
  });

  const width = x - (gapWidth[layerCount - 1] ?? 0) + PAD;
  const height = PAD + bodyH + (farEdges.length ? 28 + farEdges.length * LANE_STEP : 0) + PAD;
  const nodes: DagNode[] = names.map((n) => ({ name: n, layer: layer.get(n)!, row: rowOf.get(n)!, x: colX[layer.get(n)!], y: yOf.get(n)! }));
  void totalHops;
  return { width, height, nodes, edges: out };
}

/** Checks a layout against the drawing rules; returns what is wrong, in words. Empty means every rule holds. */
export function checkLayout(l: DagLayout): string[] {
  const bad: string[] = [];
  const box = (n: DagNode) => ({ x1: n.x, y1: n.y, x2: n.x + NODE_W, y2: n.y + NODE_H });
  for (const e of l.edges) {
    for (let k = 0; k + 1 < e.points.length; k++) {
      const [x1, y1] = e.points[k];
      const [x2, y2] = e.points[k + 1];
      if (x1 !== x2 && y1 !== y2) bad.push(`${e.from}>${e.to}: a diagonal segment`);
    }
    const [lx, ly] = e.points[e.points.length - 1];
    const target = l.nodes.find((n) => n.name === e.to)!;
    if (Math.abs(target.x - lx - ARROW_GAP) > 0.01) bad.push(`${e.from}>${e.to}: the head is not ${ARROW_GAP}px from its step`);
    if (ly <= target.y || ly >= target.y + NODE_H) bad.push(`${e.from}>${e.to}: arrives off the step's edge`);
    // no run goes through a step
    for (const n of l.nodes) {
      const b = box(n);
      for (let k = 0; k + 1 < e.points.length; k++) {
        const [x1, y1] = e.points[k];
        const [x2, y2] = e.points[k + 1];
        const sx1 = Math.min(x1, x2), sx2 = Math.max(x1, x2), sy1 = Math.min(y1, y2), sy2 = Math.max(y1, y2);
        if (sx2 > b.x1 && sx1 < b.x2 && sy2 > b.y1 && sy1 < b.y2) bad.push(`${e.from}>${e.to}: runs through ${n.name}`);
      }
    }
  }
  // arrows from different steps must not share a stretch of line
  const segs = l.edges.flatMap((e, i) => e.points.slice(0, -1).map((p, k) => ({ i, e, a: p, b: e.points[k + 1] })));
  for (let i = 0; i < segs.length; i++) {
    for (let j = i + 1; j < segs.length; j++) {
      const s = segs[i], t = segs[j];
      if (s.e.from === t.e.from) continue;
      const sv = s.a[0] === s.b[0], tv = t.a[0] === t.b[0];
      if (sv && tv && s.a[0] === t.a[0]) {
        const lo = Math.max(Math.min(s.a[1], s.b[1]), Math.min(t.a[1], t.b[1]));
        const hi = Math.min(Math.max(s.a[1], s.b[1]), Math.max(t.a[1], t.b[1]));
        if (hi - lo > 0.5) bad.push(`${s.e.from}>${s.e.to} and ${t.e.from}>${t.e.to} share a vertical stretch`);
      }
      if (!sv && !tv && s.a[1] === t.a[1]) {
        const lo = Math.max(Math.min(s.a[0], s.b[0]), Math.min(t.a[0], t.b[0]));
        const hi = Math.min(Math.max(s.a[0], s.b[0]), Math.max(t.a[0], t.b[0]));
        if (hi - lo > 0.5) bad.push(`${s.e.from}>${s.e.to} and ${t.e.from}>${t.e.to} share a horizontal stretch`);
      }
    }
  }
  return bad;
}
