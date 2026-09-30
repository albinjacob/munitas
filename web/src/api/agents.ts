/**
 * Agents: a first-class, versioned resource, separate from `queries.ts`'s
 * `Service` list.
 *
 * A `Service` (pipeline action, annotation tool, training job, agent
 * runtime) is fixed platform infrastructure nobody registers. An agent here
 * is a resource an end user creates and is accountable for, the same way
 * they are for a dataset: named, owned by a department, versioned, and
 * content-addressed rather than trusted on description alone.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, API_BASE, ApiError } from "./client";
import { useIdentity } from "../identity/IdentityContext";

export interface AgentVersion {
  id: string;
  version: number;
  code_hash: string;
  source_path: string;
  image_digest: string;
  model_id: string;
  tool_scope: string[];
  content_hash: string;
  sealed: boolean;
  created_at: string;
  registered_by_label: string | null;
  // True only for a version registered through the upload endpoint
  // (uploadAgentVersion below): the platform actually executes this one,
  // sandboxed, rather than merely describing it. See `entrypoint` for what
  // it runs.
  sandboxed: boolean;
  entrypoint: string;
  // Empty for most versions: only non-empty when this version's code calls
  // something outside the boundary. Non-empty means `egress_state` below is
  // meaningful and deploy is refused until it reads "approved".
  requested_hosts: string[];
  egress_approval_id: string | null;
  egress_state: "pending" | "approved" | "refused" | null;
}

export interface AgentRow {
  id: string;
  name: string;
  purpose: string;
  principal_id: string;
  created_at: string;
  department_name: string | null;
  registered_by_label: string | null;
  version_count: number;
  latest_version: number | null;
  latest_code_hash: string | null;
}

export interface ActiveVersion {
  agent_version_id: string;
  deployed_at: string;
}

export interface AgentDetail extends AgentRow {
  tenant_id: string;
  versions: AgentVersion[];
  active_version: ActiveVersion | null;
}

export interface RunFinding {
  document: string | null;
  priority: string;
  reason: string;
  ranked_by?: string;
}

/**
 * What the platform would do if this agent were pointed at this dataset.
 *
 * Asked before starting rather than after, so the person pressing the button
 * knows whether it will run or ask somebody for access on the agent's behalf.
 */
export interface AccessPreflight {
  dataset_version_id: string;
  visibility_class: string;
  allowed: boolean;
  reasons: string[];
  needs_access_request: boolean;
  approver_label: string | null;
  department_name: string | null;
  dataset_name: string | null;
}

export interface AgentRun {
  id: string;
  agent_version_id: string;
  agent_version: number;
  status:
    | "running"
    | "awaiting_access"
    | "awaiting_approval"
    | "awaiting_activation"
    | "succeeded"
    | "failed"
    | "halted";
  purpose: string;
  requested_by: string;
  requested_by_label: string | null;
  tool_calls: number;
  halted_reason: string | null;
  error: string | null;
  started_at: string;
  ended_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  lease_request_id: string | null;
  findings: RunFinding[];
  execution_mode: "native" | "sandboxed";
}

function useTenant(): string | undefined {
  return useIdentity().tenant;
}

export interface AgentFilters {
  q?: string;
  limit?: number;
  offset?: number;
}

export interface AgentPage {
  agents: AgentRow[];
  shown: number;
  total: number;
  limit: number;
  offset: number;
}

/**
 * Registered agents, with the total for real page controls.
 *
 * Used to return a bare array; the pagination pass across the whole
 * console (`/datasets`, `/agents`, `/action-runs`, `/egress-approvals`,
 * `/gate-decisions`, `/access-decisions`) put it in the same
 * `{items, shown, total, limit, offset}` shape as the rest, so one
 * `<Pagination>` component can front all of them.
 */
export function useAgents(filters: AgentFilters = {}) {
  const tenant = useTenant();
  return useQuery({
    queryKey: ["agents", tenant, filters],
    queryFn: () =>
      api.get<AgentPage>("/agents", {
        q: filters.q || undefined,
        limit: filters.limit,
        offset: filters.offset,
      }),
  });
}

export function useAgent(agentId: string | undefined) {
  const tenant = useTenant();
  return useQuery({
    queryKey: ["agent", tenant, agentId],
    queryFn: () => api.get<AgentDetail>(`/agents/${agentId}`, { tenant_id: tenant }),
    enabled: Boolean(agentId),
  });
}

export function useAgentRuns(agentId: string | undefined) {
  const tenant = useTenant();
  return useQuery({
    queryKey: ["agent-runs", tenant, agentId],
    queryFn: () => api.get<AgentRun[]>(`/agents/${agentId}/runs`, { tenant_id: tenant }),
    enabled: Boolean(agentId),
    // A run waiting for its access to take effect carries on by itself, so
    // it is polled too, or the screen would keep showing it waiting.
    refetchInterval: (query) =>
      query.state.data?.some((r) => r.status === "running") ? 2000
        : query.state.data?.some((r) => r.status === "awaiting_activation") ? 5000
        : false,
  });
}

export function useDeployAgent(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { agent_version_id: string; note?: string }) =>
      api.post(`/agents/${agentId}/deploy`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent"] });
    },
  });
}

/**
 * Ask whether this agent may read this dataset, before offering to start.
 *
 * Only runs once a dataset is chosen. Without one there is nothing to check,
 * and asking anyway would put a spurious warning in front of somebody who has
 * not made the choice the warning is about.
 */
export function useAccessPreflight(agentId: string, datasetVersionId: string) {
  return useQuery({
    queryKey: ["agent-preflight", agentId, datasetVersionId],
    queryFn: () =>
      api.get<AccessPreflight>(`/agents/${agentId}/access-preflight`, {
        dataset_version_id: datasetVersionId,
      }),
    enabled: Boolean(agentId && datasetVersionId),
  });
}

export function useStartAgentRun(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      purpose: string;
      dataset_version_id?: string;
      request_access?: boolean;
    }) => api.post<{ run_id: string; status: string }>(`/agents/${agentId}/runs`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
    },
  });
}

/**
 * Let a run that stopped at its approval gate carry on.
 *
 * Run-scoped rather than agent-scoped in the URL, matching
 * `GET /agents/runs/{id}`: a run id identifies a run without needing to be
 * told which agent it belongs to. The API refuses a self-approval; the
 * console disables the button in that case so nobody meets that refusal by
 * surprise.
 */
export function useApproveRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { run_id: string }) =>
      api.post<{ run_id: string; status: string }>(
        `/agents/runs/${body.run_id}/approve`,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
    },
  });
}

export interface RegisteredAgent {
  id: string;
  name: string;
}

export interface UploadedAgentVersion {
  id: string;
  agent_id: string;
  version: number;
  code_hash: string;
  content_hash: string;
  entrypoint: string;
  bytes: number;
  sealed: boolean;
}

/**
 * Upload a project's own code as this agent's next version. `FormData`, not
 * JSON, for the same reason `useUploadFile` above is: this carries bytes,
 * not a decision, so it does not go through `api.post`. The one other place
 * the console sends bytes at all.
 */
export function useUploadAgentVersion(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      zip: File;
      modelId: string;
      toolScope: string;
      requestedHosts: string;
      registeredBy: string;
    }) => {
      const form = new FormData();
      form.append("zip", body.zip);
      form.append("model_id", body.modelId);
      form.append("tool_scope", body.toolScope);
      form.append("requested_hosts", body.requestedHosts);
      form.append("registered_by", body.registeredBy);
      const response = await fetch(`${API_BASE}/agents/${agentId}/versions/upload`, {
        method: "POST",
        body: form,
      });
      const text = await response.text();
      const parsed = text ? JSON.parse(text) : null;
      if (!response.ok) throw new ApiError(response.status, parsed);
      return parsed as UploadedAgentVersion;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent"] });
    },
  });
}

export function useRegisterAgent() {
  const tenant = useTenant();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      department_id: string;
      registered_by: string;
      purpose: string;
    }) =>
      api.post<RegisteredAgent>("/agents/register", {
        ...body,
        tenant_id: tenant,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}
