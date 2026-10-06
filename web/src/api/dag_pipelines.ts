/**
 * Operator-registered pipelines: a DAG of steps someone brings themselves,
 * versioned and sealed the same way an agent's code is. Separate from
 * pipeline.ts, which starts a run against a version that already exists;
 * this is where a pipeline (and a sealed version of it) comes to exist.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, API_BASE, ApiError } from "./client";
import { useIdentity } from "../identity/IdentityContext";

export interface PipelineStepConfig {
  name: string;
  kind: string;
  depends_on?: string[];
  block?: string;
  script?: string;
  /** A script step that is given the run's recordings, read through the platform. Its version is approved by a second person first. */
  recordings?: boolean;
  /** A script step whose files, written through the platform, become a version that waits for review. Its version is approved first as well. */
  writes_files?: boolean;
  /** The memory and the memory-backed scratch space the step's container gets, in megabytes, when its pipeline file asked for more than the default. */
  memory_mb?: number;
  scratch_mb?: number;
}

export interface PipelineVersion {
  id: string;
  version: number;
  dag_config: { name: string; steps: PipelineStepConfig[] };
  config_hash: string;
  created_at: string;
  uploaded_by_label: string | null;
  /** The script steps that are given the recordings, and those that write files. When there are any, a second person approves the version before a run may start. */
  steps_given_recordings: string[];
  steps_writing_files: string[];
  needs_approval: boolean;
  approved: boolean;
  approved_by_label: string | null;
  approved_at: string | null;
}

export interface PipelineRow {
  id: string;
  name: string;
  department_name: string | null;
  registered_by_label: string | null;
  created_at: string;
  version_count: number;
  latest_version: number | null;
  latest_version_id: string | null;
}

export interface PipelineDetail extends PipelineRow {
  tenant_id: string;
  versions: PipelineVersion[];
}

function useTenant(): string | undefined {
  return useIdentity().tenant;
}

export interface PipelinePage {
  pipelines: PipelineRow[];
  shown: number;
  total: number;
  limit: number;
  offset: number;
}

export function usePipelines(limit = 15, offset = 0) {
  // Scoped server-side to the caller's own tenant now (see
  // dag_pipelines.py's list_pipelines). `tenant` still keys the cache and
  // gates the query on identity being resolved.
  const tenant = useTenant();
  return useQuery({
    queryKey: ["pipelines", tenant, limit, offset],
    queryFn: () => api.get<PipelinePage>("/pipelines", { limit, offset }),
    enabled: Boolean(tenant),
  });
}

export function usePipeline(pipelineId: string | undefined) {
  return useQuery({
    queryKey: ["pipeline", pipelineId],
    queryFn: () => api.get<PipelineDetail>(`/pipelines/${pipelineId}`),
    enabled: Boolean(pipelineId),
  });
}

export function useRegisterPipeline() {
  const tenant = useTenant();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; departmentId: string; registeredBy: string }) =>
      api.post<{ id: string; name: string }>("/pipelines/register", {
        tenant_id: tenant, name: body.name, department_id: body.departmentId,
        registered_by: body.registeredBy,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pipelines"] });
    },
  });
}

/** Approving a version, or taking the approval back. The platform decides who may and says why when it refuses. */
export function useDecidePipelineVersion(pipelineId: string, action: "approve" | "withdraw-approval") {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { versionId: string; note: string }) =>
      api.post<{ approved: boolean }>(`/pipelines/${pipelineId}/versions/${body.versionId}/${action}`, { note: body.note }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pipeline", pipelineId] });
    },
  });
}

export function useUploadPipelineVersion(pipelineId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { configFile: File; scripts: File; registeredBy: string }) => {
      const form = new FormData();
      form.append("config_file", body.configFile);
      form.append("scripts", body.scripts);
      form.append("registered_by", body.registeredBy);
      const response = await fetch(
        `${API_BASE}/pipelines/${pipelineId}/versions/upload`,
        // The platform registers the version as the signed-in person, so the session goes with it.
        { method: "POST", body: form, credentials: "include" },
      );
      const text = await response.text();
      const parsed = text ? JSON.parse(text) : null;
      if (!response.ok) throw new ApiError(response.status, parsed);
      return parsed as { id: string; version: number; step_count: number };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pipeline", pipelineId] });
      queryClient.invalidateQueries({ queryKey: ["pipelines"] });
    },
  });
}
