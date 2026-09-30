/**
 * One de-identification run, while it is happening.
 *
 * The person who starts a run cannot see the worker's terminal, so without this
 * screen the only honest thing the console could say is "started". The run takes
 * minutes on a GPU, and a run that is merely slow looks exactly like one that
 * died, which is the distinction this page exists to make.
 */

import { Link, useParams } from "react-router-dom";
import { Failure, Loading, Section } from "../../components/states";
import { CopyableHash } from "../../components/CopyableHash";
import { usePipelineRun, type PipelineStep } from "../../api/pipeline";

/**
 * The steps a run takes, in order, named for the person watching rather than
 * for the activity that does the work.
 *
 * Held here rather than derived from what came back, because a step that has
 * not started yet has no row to render, and an absent row looks exactly like a
 * slow one. Scoring is not in this list: it opens no run of its own, and what
 * it produced is the gate decision shown underneath.
 *
 * Keyed by pipeline_kind, since a run started against a different workflow
 * has a different, usually shorter, list. count_records has no dataset_action
 * rows at all, so it renders no steps here; its one activity finishes fast
 * enough that "Steps" would be empty for longer than it would be useful.
 */
const STEPS_BY_KIND: Record<string, { action: string; label: string }[]> = {
  deidentify: [
    { action: "transcribe", label: "Transcribe the recordings" },
    { action: "detect", label: "Find the identifiers" },
    { action: "handoff", label: "Hand off for annotation" },
    { action: "redact", label: "Remove the identifiers" },
  ],
  count_records: [],
};

const STATUS_WORD: Record<PipelineStep["status"], string> = {
  running: "Working",
  succeeded: "Done",
  failed: "Failed",
};

const STATUS_STYLE: Record<PipelineStep["status"], string> = {
  running: "bg-sky-100 text-sky-900",
  succeeded: "bg-green-100 text-green-900",
  failed: "bg-red-100 text-red-900",
};

/** What this page calls the run, in its heading. Matches STEPS_BY_KIND's
 * keys; a kind with no entry here falls back to its own name rather than
 * claiming a step this run never took. */
const HEADING_BY_KIND: Record<string, string> = {
  deidentify: "De-identifying",
  count_records: "Counting records in",
};

function took(step: PipelineStep): string {
  if (!step.ended_at) return "";
  const seconds =
    (new Date(step.ended_at).getTime() - new Date(step.started_at).getTime()) / 1000;
  if (seconds < 90) return `${seconds.toFixed(0)}s`;
  return `${(seconds / 60).toFixed(1)} min`;
}

function StepRow({
  label,
  step,
}: {
  label: string;
  step: PipelineStep | undefined;
}) {
  return (
    <li
      className="flex items-center justify-between gap-4 border-b border-slate-100 py-2 last:border-b-0"
      data-testid={`pipeline-step-${step ? step.action : "waiting"}`}
    >
      <span className="text-sm text-slate-800">{label}</span>
      <span className="flex items-center gap-3">
        {step && <span className="text-xs text-slate-500">{took(step)}</span>}
        <span
          className={`rounded px-2 py-0.5 text-xs font-medium ${
            step ? STATUS_STYLE[step.status] : "bg-slate-100 text-slate-600"
          }`}
        >
          {step ? STATUS_WORD[step.status] : "Not started"}
        </span>
      </span>
    </li>
  );
}

export function PipelineRun() {
  const { pipelineRunId } = useParams();
  const { data: run, isLoading, error } = usePipelineRun(pipelineRunId);

  if (isLoading) return <Loading what="this run" />;
  if (error) return <Failure error={error} what="this run" />;
  if (!run) return <Failure error={error} what="this run" />;

  const running = run.status === "running";
  const stoppedAs: Record<string, string> = {
    failed: "it failed",
    cancelled: "it was cancelled",
    terminated: "it was stopped from outside",
    timed_out: "it ran out of time",
  };
  const byAction = new Map(run.steps.map((s) => [s.action, s]));
  // A DAG's step list is not fixed the way the built-in kinds' are: it
  // depends on which pipeline ran. Built directly from what the run
  // actually reports (already populated for 'dag' by pipeline_step_run)
  // rather than a static lookup, using the step's own name as its label
  // since nothing else knows what to call an operator's own step.
  const steps =
    run.pipeline_kind === "dag"
      ? run.steps.map((s) => ({ action: s.action, label: s.action }))
      : (STEPS_BY_KIND[run.pipeline_kind] ?? []);
  const heading =
    run.pipeline_kind === "dag" && run.pipeline_name
      ? `Running ${run.pipeline_name} against`
      : (HEADING_BY_KIND[run.pipeline_kind] ?? run.pipeline_kind);

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">
          {heading} {run.dataset}
        </h1>
        <p className="mt-1 text-sm text-slate-600" data-testid="pipeline-run-status">
          {running && "Running now. This page updates itself."}
          {run.status === "succeeded" &&
            "Finished. Nothing has been released yet: a reviewer decides that."}
          {stoppedAs[run.status] && `Stopped without finishing: ${stoppedAs[run.status]}.`}
          {run.status === "unknown" && "Ended, but how it ended was not recorded."}
        </p>
        {run.error && run.status !== "succeeded" && (
          <p className="mt-1 text-xs text-red-800" data-testid="pipeline-run-error">
            {run.error}
          </p>
        )}
        {run.status_error && (
          <p className="mt-1 text-xs text-amber-800" role="alert">
            {run.status_error}. The steps below come from the platform's own records and are accurate.
          </p>
        )}
      </div>

      <Section title="Where this run came from">
        <dl className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-slate-500">Started by</dt>
            <dd className="text-slate-900">
              {run.triggered_by_label ?? run.triggered_by ?? "not recorded"}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">Started at</dt>
            <dd className="text-slate-900">
              {new Date(run.started_at).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">Reading</dt>
            <dd className="text-slate-900">
              {run.source_version_id ? (
                <Link
                  className="text-sky-700 underline"
                  to={`/versions/${run.source_version_id}`}
                >
                  the sealed version it began from
                </Link>
              ) : (
                "a corpus on the worker's own disk"
              )}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">For diagnosis</dt>
            <dd className="text-slate-700"><CopyableHash value={run.workflow_id} /></dd>
          </div>
        </dl>
      </Section>

      {steps.length > 0 && (
        <Section title="Steps">
          <ul>
            {steps.map((s) => (
              <StepRow key={s.action} label={s.label} step={byAction.get(s.action)} />
            ))}
          </ul>
        </Section>
      )}

      <Section title="What happens next">
        {run.gate_decision ? (
          <div className="space-y-2 text-sm" data-testid="pipeline-gate">
            <p className="text-slate-800">
              Scoring is done. A reviewer has to decide whether this may be
              released, and it cannot be whoever started the run.
            </p>
            <p className="text-slate-700">
              The measurement suggests{" "}
              <span className="font-medium">
                {run.gate_decision.recommendation ?? "no verdict"}
              </span>
              {run.gate_decision.recommendation_reason
                ? `: ${run.gate_decision.recommendation_reason}`
                : "."}
            </p>
            <Link
              className="inline-block text-sky-700 underline"
              to={`/gates/${run.gate_decision.id}`}
            >
              Open the decision
            </Link>
          </div>
        ) : running ? (
          <p className="text-sm text-slate-600">
            Scoring happens after the last step. Nothing has been decided, and
            nothing has been released.
          </p>
        ) : stoppedAs[run.status] ? (
          <p className="text-sm text-slate-600">
            The run stopped before it could be scored, so there is nothing to
            decide. Nothing was released.
          </p>
        ) : (
          <p className="text-sm text-slate-600">
            No decision was recorded for this run.
          </p>
        )}
      </Section>
    </div>
  );
}
