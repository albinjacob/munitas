/**
 * Starting a de-identification run, and watching one.
 *
 * Kept apart from `ingest.ts` for the reason the API keeps `pipeline.py` apart
 * from `ingest.py`: that file is the one place the console sends bytes, and
 * starting a run sends none. It names a version that already exists and asks a
 * worker to go and read it.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

export interface PipelineStep {
  action: string;
  status: "running" | "succeeded" | "failed";
  started_at: string;
  ended_at: string | null;
  output_version: string | null;
}

export interface PipelineRun {
  id: string;
  dataset: string;
  pipeline_kind: string;
  /** The registered pipeline's own name, for a 'dag' run. Null for the two built-in kinds. */
  pipeline_name: string | null;
  workflow_id: string;
  /** How the run ended, from the platform's own record. 'running' until it does. */
  status: "running" | "succeeded" | "failed" | "cancelled" | "terminated" | "timed_out" | "unknown";
  /** Why it did not succeed, when it did not. */
  error: string | null;
  /** Set when a run still recorded as running could not be checked with the job runner. */
  status_error: string | null;
  started_at: string;
  ended_at: string | null;
  triggered_by: string | null;
  triggered_by_label: string | null;
  started_from: string;
  source_version_id: string | null;
  steps: PipelineStep[];
  gate_decision: {
    id: string;
    state: string;
    recommendation: string | null;
    recommendation_reason: string | null;
    to_class: string;
    metrics: Record<string, number | string>;
  } | null;
}

export interface StartedRun {
  pipeline_run_id: string;
  workflow_id: string;
  status: "running";
  records: number;
}

/**
 * Starting the pipeline against a version somebody already sealed.
 *
 * Returns as soon as the run has started, not when it finishes, the same shape
 * `useFetchHuggingFace` uses: the work takes minutes on a GPU and has no
 * business holding a browser tab. The caller navigates to the run screen, which
 * polls `usePipelineRun`.
 *
 * Every way this can be refused arrives as `ApiError` with a `reasons` list,
 * and every one of them is decided before a workflow exists, so a refusal has
 * started nothing that needs cleaning up.
 */
export function useStartDeidentification() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      datasetId,
      versionId,
      pipelineKind,
      pipelineVersionId,
    }: {
      datasetId: string;
      versionId: string;
      /** Which workflow runs. Left out, the API runs 'deidentify' as before. */
      pipelineKind?: string;
      /** Required only when pipelineKind is 'dag': which sealed pipeline_version runs. */
      pipelineVersionId?: string;
    }) =>
      api.post<StartedRun>(
        `/datasets/${datasetId}/versions/${versionId}/deidentify`,
        pipelineKind
          ? {
              pipeline_kind: pipelineKind,
              ...(pipelineVersionId ? { pipeline_version_id: pipelineVersionId } : {}),
            }
          : undefined,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["summary"] });
    },
  });
}

/**
 * One run, polled while it is still going.
 *
 * The status comes from the job runner on every read rather than from a stored
 * column, so a worker that dies mid-run stops reporting `RUNNING` instead of
 * saying it forever. Polling stops on its own once the run is not running, so
 * a finished run left open in a tab costs nothing.
 */
export function usePipelineRun(pipelineRunId: string | undefined) {
  return useQuery({
    queryKey: ["pipeline-run", pipelineRunId],
    queryFn: () => api.get<PipelineRun>(`/pipeline-runs/${pipelineRunId}`),
    enabled: Boolean(pipelineRunId),
    refetchInterval: (query) => {
      const run = query.state.data;
      if (!run) return 3000;
      // Polled until the record says it ended. A run the job runner could not
      // be asked about stays 'running', because "we cannot see it" is not
      // "it stopped", and the run may well still be going.
      return run.status === "running" ? 3000 : false;
    },
  });
}
