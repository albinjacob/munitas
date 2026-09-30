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
}

export interface PipelineVersion {
  id: string;
  version: number;
  dag_config: { name: string; steps: PipelineStepConfig[] };
  config_hash: string;
  created_at: string;
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
        { method: "POST", body: form },
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
