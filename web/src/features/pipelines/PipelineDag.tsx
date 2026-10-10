/**
 * The steps of one pipeline version as a graph: a box for each step, an arrow from a step to each step that waits for it.
 *
 * Drawing and placing are shared (`DagCanvas.tsx`, `lib/dagLayout.ts`). Here the boxes are coloured by what kind of step they are, and a plain list
 * of the same steps follows for anybody reading the page without seeing the graph.
 */

import type { PipelineStepConfig } from "../../api/dag_pipelines";
import { CLASS_LABEL, type VisibilityClass } from "../../api/types";
import { words } from "../../lib/text";
import { DagCanvas, type CanvasNode, type LegendRow } from "./DagCanvas";

type Kind = "block" | "script" | "gate";

const KINDS: Record<Kind, { box: string; name: string; meaning: string }> = {
  block: { box: "border-teal-400 bg-teal-50", name: "Built-in step", meaning: "One of the platform's own steps." },
  script: { box: "border-indigo-400 bg-indigo-50", name: "Your script", meaning: "Code uploaded with this version, run by the platform in an isolated container." },
  gate: { box: "border-amber-500 bg-amber-50", name: "Decision", meaning: "A person decides here whether the result is released to more people." },
};

function kindOf(step: PipelineStepConfig): Kind {
  return step.kind === "script" ? "script" : step.kind === "gate" ? "gate" : "block";
}

function subtitle(step: PipelineStepConfig): string {
  if (step.kind === "script") return step.script ?? "A script";
  if (step.kind === "gate") {
    const to = step.to_class as VisibilityClass | undefined;
    return to ? `Release to: ${CLASS_LABEL[to] ?? to}` : "A person decides";
  }
  return step.block ? words(step.block) : "A built-in step";
}

function extras(step: PipelineStepConfig): string[] {
  const out: string[] = [];
  if (step.reads_files) out.push("Reads the files");
  if (step.writes_files) out.push("Writes files");
  if (step.memory_mb) out.push(`${step.memory_mb} MB of memory`);
  if (step.scratch_mb) out.push(`${step.scratch_mb} MB of scratch space`);
  return out;
}

export function PipelineDag({ steps }: { steps: PipelineStepConfig[] }) {
  const byName = new Map(steps.map((s) => [s.name, s]));
  const present = (["block", "script", "gate"] as Kind[]).filter((k) => steps.some((s) => kindOf(s) === k));
  const legend: LegendRow[] = present.map((k) => ({ swatch: KINDS[k].box, name: KINDS[k].name, meaning: KINDS[k].meaning }));

  const node = (name: string): CanvasNode => {
    const step = byName.get(name)!;
    const more = extras(step);
    return {
      box: KINDS[kindOf(step)].box,
      title: `${name}: ${subtitle(step)}${more.length ? `. ${more.join(", ")}` : ""}`,
      body: (
        <>
          <div className="truncate text-sm font-semibold text-slate-900">{name}</div>
          <div className="truncate text-xs text-slate-700">{subtitle(step)}</div>
          {more.length > 0 && <div className="mt-0.5 line-clamp-2 text-xs leading-tight text-slate-500">{more.join(" · ")}</div>}
        </>
      ),
    };
  };

  return (
    <DagCanvas
      steps={steps}
      node={node}
      legend={legend}
      ariaLabel={`The steps of this version and which step waits for which: ${steps.map((s) => s.name).join(", ")}.`}
      after={
        <ol className="sr-only">
          {steps.map((step) => (
            <li key={step.name}>
              {step.name} ({step.kind}
              {step.block ? `: ${step.block}` : ""}
              {step.script ? `: ${step.script}` : ""}
              {step.reads_files ? ", reads the files" : ""}
              {step.writes_files ? ", writes files" : ""}
              {step.memory_mb ? `, ${step.memory_mb} MB of memory` : ""}
              {step.scratch_mb ? `, ${step.scratch_mb} MB of scratch space` : ""})
              {step.depends_on?.length ? ` waits for ${step.depends_on.join(", ")}` : ""}
            </li>
          ))}
        </ol>
      }
    />
  );
}
