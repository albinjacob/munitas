/**
 * The pieces every home is made of, so the eight homes read as one family: a welcome, a row of tiles that each open the thing they count,
 * and panels that hold a short table with a link to everything.
 *
 * They use the console's own styles (shell.css), the same ones the Datasets page is built from.
 */

import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Ico } from "../../components/Ico";

export function Hero({ name, where, sub, action }: { name: string; where?: string; sub: string; action?: ReactNode }) {
  return (
    <div className="ds-hero">
      <div>
        <h1 data-testid="home-welcome">
          Welcome, {name}
          {where && <span style={{ marginLeft: 10, fontSize: 14, fontWeight: 400, color: "var(--text2)" }}>{where}</span>}
        </h1>
        <p>{sub}</p>
      </div>
      {action && <div className="ds-actions">{action}</div>}
    </div>
  );
}

export function Tiles({ three, children }: { three?: boolean; children: ReactNode }) {
  return <div className={`ds-stats${three ? " three" : ""}`}>{children}</div>;
}

/** One number that opens the page it counts. `attention` turns it amber when somebody has to act. */
export function Tile({
  to,
  tone,
  icon,
  value,
  name,
  hint,
  attention,
  testid,
}: {
  to?: string;
  tone: "blue" | "green" | "red" | "indigo";
  icon: string;
  value: number | null | undefined;
  name: string;
  hint?: string;
  attention?: boolean;
  testid?: string;
}) {
  const body = (
    <>
      <div className="top">
        <span className={`ds-ico ${tone}`}>
          <Ico name={icon} />
        </span>
      </div>
      <div className="val">{value === null || value === undefined ? "…" : value.toLocaleString()}</div>
      <div className="name">{name}</div>
      {hint && <div className="hint">{hint}</div>}
    </>
  );
  const cls = `ds-card ds-stat${attention ? " attention" : ""}`;
  return to ? (
    <Link to={to} className={cls} data-testid={testid}>
      {body}
    </Link>
  ) : (
    <div className={cls} data-testid={testid}>
      {body}
    </div>
  );
}

/** A titled card that holds one short table, or a sentence when there is nothing to show. */
export function Panel({
  title,
  sub,
  to,
  toLabel,
  children,
  testid,
  padded,
}: {
  title: string;
  sub?: string;
  to?: string;
  toLabel?: string;
  children: ReactNode;
  testid?: string;
  /** For a list of cards or a paragraph rather than a table, which runs edge to edge. */
  padded?: boolean;
}) {
  return (
    <section className="ds-card ds-tablecard ds-panel" data-testid={testid}>
      <div className="ds-pad" style={{ paddingBottom: 12 }}>
        <div className="ds-cardhead" style={{ marginBottom: 0 }}>
          <div>
            <h2>{title}</h2>
            {sub && <p>{sub}</p>}
          </div>
          {to && <Link to={to}>{toLabel ?? "See all"}</Link>}
        </div>
      </div>
      {padded ? <div className="ds-pad" style={{ paddingTop: 0 }}>{children}</div> : children}
    </section>
  );
}

export function Nothing({ children }: { children: ReactNode }) {
  return <div className="ds-note">{children}</div>;
}

export interface TableRow {
  key: string;
  cells: ReactNode[];
  attrs?: Record<string, string | boolean>;
}

/** A heading that is an empty string is the column of buttons at the end of the row. */
export function Rows({ head, rows, testid }: { head: string[]; rows: TableRow[]; testid?: string }) {
  return (
    <table className="ds-table" data-testid={testid}>
      <thead>
        <tr>
          {head.map((h, i) => (
            <th key={i} style={h === "" ? { width: 1 } : undefined}>
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key} {...r.attrs}>
            {r.cells.map((c, i) => (
              <td key={i} className={head[i] === "" ? "act" : undefined}>
                {c}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Pill({ tone, children }: { tone: "ok" | "wait" | "stop" | "none"; children: ReactNode }) {
  return <span className={`ds-pill ${tone}`}>{children}</span>;
}

/** A dataset or result by name with its version underneath, so the long name is not the only thing on the line. */
export function Named({ name, version, to }: { name: string; version?: number | string | null; to?: string }) {
  return (
    <>
      {to ? (
        <Link to={to} className="mono" style={{ color: "inherit" }}>
          {name}
        </Link>
      ) : (
        <span className="mono">{name}</span>
      )}
      {version !== undefined && version !== null && <span className="sub">Version {version}</span>}
    </>
  );
}

export function Go({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="ds-btn">
      {children}
    </Link>
  );
}

export function count(n: number, one: string, many: string) {
  return n === 1 ? `1 ${one}` : `${n} ${many}`;
}
