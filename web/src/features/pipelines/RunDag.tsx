/**
 * The steps of one run as a graph, each box showing how far that step has got.
 *
 * The same drawing as the pipeline's own page (`DagCanvas.tsx`), coloured by status instead of by kind: a step that has not started has no record
 * yet, and is drawn as waiting rather than left out, so a step that is slow is never mistaken for one that does not exist.
 */

import { Link } from "react-router-dom";
import type { PipelineStep } from "../../api/pipeline";
import { DagCanvas, type CanvasNode, type LegendRow } from "../pipelines/DagCanvas";

export interface RunGraphStep {
  /** The step's own name, which is what its record is kept under. */
  action: string;
  /** What it is called on this page. */
  label: string;
  depends_on: string[];
}

const STATUS = {
  waiting: { box: "border-slate-300 bg-white", word: "Not started", meaning: "No record of this step yet." },
  running: { box: "border-sky-500 bg-sky-50", word: "Working", meaning: "The step is running now." },
  succeeded: { box: "border-green-500 bg-green-50", word: "Done", meaning: "The step finished." },
  failed: { box: "border-red-500 bg-red-50", word: "Failed", meaning: "The step stopped with an error." },
} as const;

function took(step: PipelineStep): string {
  if (!step.ended_at) return "";
  const seconds = (new Date(step.ended_at).getTime() - new Date(step.started_at).getTime()) / 1000;
  return seconds < 90 ? `${seconds.toFixed(0)}s` : `${(seconds / 60).toFixed(1)} min`;
}

export function RunDag({ graph, records }: { graph: RunGraphStep[]; records: Map<string, PipelineStep> }) {
  const byName = new Map(graph.map((g) => [g.action, g]));
  const shown = new Set<keyof typeof STATUS>();
  const stateOf = (name: string): keyof typeof STATUS => records.get(name)?.status ?? "waiting";
  graph.forEach((g) => shown.add(stateOf(g.action)));
  const legend: LegendRow[] = (Object.keys(STATUS) as (keyof typeof STATUS)[])
    .filter((k) => shown.has(k))
    .map((k) => ({ swatch: STATUS[k].box, name: STATUS[k].word, meaning: STATUS[k].meaning }));

  const node = (name: string): CanvasNode => {
    const g = byName.get(name)!;
    const record = records.get(name);
    const state = stateOf(name);
    const line = record ? [STATUS[state].word, took(record)].filter(Boolean).join(" · ") : STATUS.waiting.word;
    return {
      box: STATUS[state].box,
      testid: `pipeline-step-${record ? record.action : "waiting"}`,
      title: `${g.label}: ${line}`,
      body: (
        <>
          <div className="truncate text-sm font-semibold text-slate-900">{g.label}</div>
          <div className="truncate text-xs text-slate-700">{line}</div>
          {record?.output_version && (
            <Link
              to={`/versions/${record.output_version}`}
              data-testid={`pipeline-step-made-${record.action}`}
              className="mt-0.5 truncate text-xs text-sky-700 underline"
            >
              Open the version it made
            </Link>
          )}
        </>
      ),
    };
  };

  return (
    <DagCanvas
      steps={graph.map((g) => ({ name: g.action, depends_on: g.depends_on }))}
      node={node}
      legend={legend}
      testid="run-dag"
      ariaLabel={`The steps of this run and how far each has got: ${graph.map((g) => `${g.label}, ${STATUS[stateOf(g.action)].word}`).join("; ")}.`}
    />
  );
}
