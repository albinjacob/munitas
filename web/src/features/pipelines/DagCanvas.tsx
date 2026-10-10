/**
 * A graph of steps, drawn: a box for each step, an arrow from a step to each step that waits for it, and a legend under it.
 *
 * Where everything goes is worked out by `lib/dagLayout.ts`. This draws it, and says so when the graph is wider than the card it is in: the right
 * edge fades while more is hidden, and a sentence says to scroll sideways. What goes inside a box, and what its colour means, is the caller's: the
 * pipeline page colours boxes by the kind of step, the run page by how far the step has got.
 */

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { NODE_H, NODE_W, layoutDag } from "../../lib/dagLayout";

export interface CanvasNode {
  /** The classes that colour the box: its border and fill. */
  box: string;
  /** What the box says. */
  body: ReactNode;
  /** The whole of it as plain text, for the tooltip. */
  title: string;
  testid?: string;
}

export interface LegendRow {
  swatch: string;
  name: string;
  meaning: string;
}

export function DagCanvas({
  steps,
  node,
  legend,
  ariaLabel,
  testid = "pipeline-dag",
  after,
}: {
  steps: { name: string; depends_on?: string[] }[];
  node: (name: string) => CanvasNode;
  legend: LegendRow[];
  ariaLabel: string;
  testid?: string;
  /** Anything that belongs under the graph and above its legend, such as a plain list of the same steps for a screen reader. */
  after?: ReactNode;
}) {
  const layout = useMemo(() => layoutDag(steps), [steps]);
  const markerId = useMemo(() => `dag-arrow-${Math.random().toString(36).slice(2, 8)}`, []);

  const scroller = useRef<HTMLDivElement>(null);
  const [wider, setWider] = useState(false);
  const [atEnd, setAtEnd] = useState(true);
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const measure = () => {
      setWider(el.scrollWidth > el.clientWidth + 1);
      setAtEnd(el.scrollLeft + el.clientWidth >= el.scrollWidth - 1);
    };
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    el.addEventListener("scroll", measure, { passive: true });
    return () => {
      watch.disconnect();
      el.removeEventListener("scroll", measure);
    };
  }, [layout]);

  return (
    <figure data-testid={testid} className="mt-3 rounded border border-slate-200 bg-white p-4">
      <div className="relative">
        <div ref={scroller} data-testid="dag-scroller" role="region" tabIndex={0} aria-label={ariaLabel} className="overflow-x-auto">
          <svg
            role="img"
            aria-label={ariaLabel}
            viewBox={`0 0 ${layout.width} ${layout.height}`}
            width="100%"
            style={{ minWidth: Math.round(layout.width * 0.75), maxWidth: layout.width, height: "auto" }}
          >
            <defs>
              <marker id={markerId} viewBox="0 0 8 8" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto" markerUnits="userSpaceOnUse">
                <path d="M0,0 L8,4 L0,8 z" fill="#475569" />
              </marker>
            </defs>
            {layout.edges.map((e) => (
              <path
                key={`${e.from}>${e.to}`}
                data-testid={`dag-edge-${e.from}-${e.to}`}
                d={e.d}
                fill="none"
                stroke="#475569"
                strokeWidth={1.5}
                markerEnd={`url(#${markerId})`}
              />
            ))}
            {layout.nodes.map((n) => {
              const it = node(n.name);
              return (
                <foreignObject key={n.name} x={n.x} y={n.y} width={NODE_W} height={NODE_H}>
                  <div
                    data-testid={it.testid ?? `dag-step-${n.name}`}
                    title={it.title}
                    className={`flex h-full flex-col justify-center overflow-hidden rounded border-2 px-6 ${it.box}`}
                  >
                    {it.body}
                  </div>
                </foreignObject>
              );
            })}
          </svg>
        </div>
        {wider && !atEnd && (
          <div aria-hidden="true" data-testid="dag-more" className="pointer-events-none absolute inset-y-0 right-0 w-12 bg-gradient-to-l from-white to-transparent" />
        )}
      </div>
      {wider && (
        <p data-testid="dag-scroll-hint" className="mt-2 text-xs text-slate-500">
          This graph is wider than the screen. Scroll sideways to see the rest of it.
        </p>
      )}

      <figcaption className="mt-4 border-t border-slate-100 pt-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Legend</div>
        <table data-testid="dag-legend" className="mt-2 text-xs text-slate-700">
          <tbody>
            {legend.map((row) => (
              <tr key={row.name}>
                <td className="py-1 pr-3 align-middle">
                  <span className={`block h-4 w-8 rounded border-2 ${row.swatch}`} />
                </td>
                <td className="py-1 pr-4 align-middle font-medium">{row.name}</td>
                <td className="py-1 align-middle text-slate-600">{row.meaning}</td>
              </tr>
            ))}
            <tr>
              <td className="py-1 pr-3 align-middle">
                <svg width="32" height="16" aria-hidden="true">
                  <defs>
                    <marker id={`${markerId}-key`} viewBox="0 0 8 8" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto" markerUnits="userSpaceOnUse">
                      <path d="M0,0 L8,4 L0,8 z" fill="#475569" />
                    </marker>
                  </defs>
                  <path d="M2,8 L30,8" stroke="#475569" strokeWidth={1.5} fill="none" markerEnd={`url(#${markerId}-key)`} />
                </svg>
              </td>
              <td className="py-1 pr-4 align-middle font-medium">Arrow</td>
              <td className="py-1 align-middle text-slate-600">Points from a step to the step that waits for it.</td>
            </tr>
          </tbody>
        </table>
      </figcaption>
      {after}
    </figure>
  );
}
