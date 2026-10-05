/** TanStack Query hooks, one per thing the console reads. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { useIdentity } from "../identity/IdentityContext";
import type {
  VersionTable,
  AccessDecision,
  AccessPreview,
  ActionRun,
  DatasetDetail,
  DatasetRow,
  Lease,
  LeaseRequest,
  Lineage,
  Summary,
  Transition,
  VersionRow,
} from "./types";

/**
 * The organisation every list below is scoped to.
 *
 * Taken from the identity seam rather than passed in by each screen. A screen
 * that forgot would show one organisation's data to another's, and the ones
 * that forget are the ones added later by somebody who did not know the rule.
 *
 * Undefined before anybody has chosen, which leaves the request unscoped. That
 * only happens on screens reachable without a principal, and there are none
 * that list tenant data.
 */
function useTenant(): string | undefined {
  return useIdentity().tenant;
}

export interface PolicyRoles {
  class_order: Record<string, number>;
  role_floor: Record<string, number>;
  approver_roles: string[];
}

/**
 * The class floors and approving roles, as the policy defines them.
 *
 * Shared so no screen keeps its own copy. Whether somebody may approve is a
 * policy question, and a component answering it from a local constant is a
 * second answer that can drift from the first.
 */
export function usePolicyRoles() {
  return useQuery({
    queryKey: ["policy-roles"],
    queryFn: () => api.get<PolicyRoles>("/policy/roles"),
    staleTime: 5 * 60_000,
  });
}

export function useSummary() {
  const tenant = useTenant();
  return useQuery({
    queryKey: ["summary", tenant],
    queryFn: () => api.get<Summary>("/summary", { tenant_id: tenant }),
    // Short, because the counts change whenever a pipeline runs and a stale
    // denial count is the one number nobody should be reading out of date.
    refetchInterval: 15_000,
  });
}

export interface DatasetFilters {
  q?: string;
  widest_class?: string;
  modality?: string;
  has_versions?: boolean;
  limit?: number;
  offset?: number;
}

export interface DatasetPage {
  datasets: DatasetRow[];
  shown: number;
  total: number;
  limit: number;
  offset: number;
}

/**
 * Datasets, filtered on the server.
 *
 * The response carries `shown` and `total` so the console can say what it is
 * hiding. A list that silently stops at a limit teaches people the platform
 * holds less than it does.
 */
export function useDatasets(filters: DatasetFilters = {}) {
  const tenant = useTenant();
  return useQuery({
    queryKey: ["datasets", tenant, filters],
    queryFn: () => api.get<DatasetPage>("/datasets", { ...filters, tenant_id: tenant }),
  });
}

/**
 * One dataset's own record, for a screen that needs to act on a dataset
 * that already exists rather than list many of them: resuming ingestion
 * into one that was registered and left, mainly.
 */
export function useDataset(datasetId: string | undefined) {
  const tenant = useTenant();
  return useQuery({
    queryKey: ["dataset", tenant, datasetId],
    queryFn: () => api.get<DatasetDetail>(`/datasets/${datasetId}`, { tenant_id: tenant }),
    enabled: Boolean(datasetId),
  });
}

export function useDatasetVersions(datasetId: string | undefined) {
  const tenant = useTenant();
  return useQuery({
    queryKey: ["dataset-versions", tenant, datasetId],
    queryFn: () =>
      api.get<VersionRow[]>(`/datasets/${datasetId}/versions`, {
        tenant_id: tenant,
      }),
    enabled: Boolean(datasetId),
  });
}

export function useVersions(currentClass?: string) {
  const tenant = useTenant();
  return useQuery({
    queryKey: ["versions", tenant, currentClass ?? "all"],
    queryFn: () =>
      api.get<VersionRow[]>("/dataset-versions", {
        current_class: currentClass,
        tenant_id: tenant,
      }),
  });
}

/**
 * One version, its history and its origin.
 *
 * All three carry the tenant, so a link to another organisation's version is
 * not found rather than rendered. Deep links are the one route into this
 * console that no list filtered on the way in, which made them the way to see
 * something the rest of the console had already decided you should not.
 */
export function useVersion(versionId: string | undefined) {
  const tenant = useTenant();
  return useQuery({
    queryKey: ["version", tenant, versionId],
    queryFn: () =>
      api.get<VersionRow>(`/dataset-versions/${versionId}`, {
        tenant_id: tenant,
      }),
    enabled: Boolean(versionId),
  });
}

export function useVersionTable(versionId: string | undefined) {
  return useQuery({
    queryKey: ["version-table", versionId],
    queryFn: () => api.get<VersionTable>(`/dataset-versions/${versionId}/table`),
    enabled: Boolean(versionId),
  });
}

export function useTransitions(versionId: string | undefined) {
  const tenant = useTenant();
  return useQuery({
    queryKey: ["transitions", tenant, versionId],
    queryFn: () =>
      api.get<Transition[]>(`/dataset-versions/${versionId}/transitions`, {
        tenant_id: tenant,
      }),
    enabled: Boolean(versionId),
  });
}

export function useLineage(versionId: string | undefined) {
  const tenant = useTenant();
  return useQuery({
    queryKey: ["lineage", tenant, versionId],
    queryFn: () =>
      api.get<Lineage>(`/lineage/${versionId}`, { tenant_id: tenant }),
    enabled: Boolean(versionId),
  });
}

export interface DecisionPage {
  decisions: AccessDecision[];
  shown: number;
  total: number;
  limit: number;
  offset: number;
}

export interface DecisionFilters {
  principal?: string;
  allowed?: boolean;
  resource?: "dataset" | "agent";
  limit?: number;
  offset?: number;
}

export function useDecisions(filters: DecisionFilters = {}) {
  // Scoped server-side to the caller's own tenant now (see main.py's
  // list_decisions), not by a query parameter this hook used to send.
  // `tenant` still keys the cache, so switching identity still refetches.
  // `allowed`/`resource` are filtered server-side too, for the same
  // reason limit/offset are: a page fetches 15 rows, and filtering fewer
  // than that in the browser afterward would silently miss whatever
  // didn't happen to be on this page.
  const tenant = useTenant();
  return useQuery({
    queryKey: ["decisions", tenant, filters],
    queryFn: () => api.get<DecisionPage>("/access-decisions", { ...filters }),
  });
}

/**
 * Requests, optionally narrowed to the ones a custodian can actually decide.
 *
 * The narrowing is not cosmetic. Without it a custodian's queue listed every
 * pending request in the platform under a heading naming their own department,
 * including requests for data owned by somebody else and for data owned by
 * nobody at all.
 */
export interface LeaseRequestPage {
  lease_requests: LeaseRequest[];
  shown: number;
  total: number;
  limit: number;
  offset: number;
}

export function useLeaseRequests(
  state?: string,
  custodian?: string,
  options: {
    principal?: string;
    excludePending?: boolean;
    limit?: number;
    offset?: number;
  } = {},
) {
  const tenant = useTenant();
  // 500 (the API's own ceiling) by default: most callers want "everything
  // relevant", not one page of it -- a pending queue or a version's own
  // "have I already asked" check silently missing an entry past page 1
  // would be a real functional bug, not a display nicety. Only a caller
  // that actually renders pages (CustodianHome's own history list) passes
  // a real `limit`/`offset`.
  const { principal, excludePending = false, limit = 500, offset = 0 } = options;
  return useQuery({
    queryKey: [
      "lease-requests", tenant, state ?? "all", custodian ?? "anyone",
      principal ?? "anyone", excludePending, limit, offset,
    ],
    queryFn: () =>
      api.get<LeaseRequestPage>("/lease-requests", {
        state,
        custodian,
        principal,
        exclude_pending: excludePending,
        limit,
        offset,
      }),
  });
}

/**
 * Granting or refusing a request.
 *
 * One hook for both, because they are the same decision with two outcomes and
 * splitting them makes it easy to build a screen where only one is reachable.
 *
 * The refusal from the policy engine is surfaced rather than swallowed. A
 * custodian who tries to decide something outside their department should be
 * told which custodian owns it, not shown a silent failure.
 */
export function useDecideRequest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      action,
      reason,
      pattern,
    }: {
      id: string;
      action: "approve" | "reject";
      reason?: string;
      /** Custodian's own choice at approval time. Omitted means "strict". */
      pattern?: "strict" | "simple";
    }) =>
      action === "approve"
        ? api.post(`/leases/requests/${id}/approve`, pattern ? { pattern } : undefined)
        : api.post(`/leases/requests/${id}/reject`, { reason: reason ?? "" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["lease-requests"] });
      queryClient.invalidateQueries({ queryKey: ["summary"] });
      // An approval creates a lease, and the custodian's own record of what
      // they have decided reads that lease to say whether the access is still
      // open. Without this the decision appeared instantly and its consequence
      // was a refresh behind.
      queryClient.invalidateQueries({ queryKey: ["leases"] });
      queryClient.invalidateQueries({ queryKey: ["access-preview"] });
    },
  });
}

export function useOrganisation() {
  const tenant = useTenant();
  return useQuery({
    queryKey: ["organisation", tenant],
    queryFn: () =>
      api.get<{
        departments: {
          id: string;
          name: string;
          /** The person the department was made with. Decisions use `approvers`. */
          custodian: string;
          approvers: {
            person_id: string;
            label: string;
            added_at: string;
            /** Set for temporary cover, which ends by itself. */
            valid_until: string | null;
            /** False for somebody listed whose Data custodian role has lapsed: listed, but not able to act and not counted. */
            holds_role: boolean;
          }[];
          datasets: number;
        }[];
        datasets_without_a_department: number;
      }>("/organisation", { tenant_id: tenant }),
  });
}

export interface LeasePage {
  leases: Lease[];
  shown: number;
  total: number;
  limit: number;
  offset: number;
}

export function useLeases(
  principal?: string,
  options: { limit?: number; offset?: number } = {},
) {
  const tenant = useTenant();
  // 500 by default, the same reasoning as useLeaseRequests: a caller
  // building a lookup map (CustodianHome, no principal) needs everything,
  // not one page of it. Only ResearcherHome's own paginated "access you
  // have been given" list passes a real limit/offset.
  const { limit = 500, offset = 0 } = options;
  return useQuery({
    queryKey: ["leases", tenant, principal ?? "everyone", limit, offset],
    queryFn: () => api.get<LeasePage>("/leases", { principal, limit, offset }),
  });
}

/**
 * Withdrawing a lease before it would otherwise end.
 *
 * Matters most for a standing one: nothing else ever ends it, so this is the
 * only way it stops. The API checks the same custodian authority approving
 * one does; a caller with no standing over the asset gets a 403, surfaced
 * rather than swallowed, the same as `useDecideRequest`.
 */
export function useRevokeLease() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (leaseId: string) => api.post(`/leases/${leaseId}/revoke`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leases"] });
      queryClient.invalidateQueries({ queryKey: ["access-preview"] });
      queryClient.invalidateQueries({ queryKey: ["summary"] });
    },
  });
}

/**
 * Asking for access to a version.
 *
 * This grants nothing. It puts a request in front of the custodian who owns the
 * data, which is the only route to reading anything above your role's floor.
 * Before this existed the researcher's landing page told them to ask the person
 * answerable for it and gave them no way to do so, which is an instruction with
 * nowhere to go.
 */
export function useRequestAccess() {
  const queryClient = useQueryClient();
  const tenant = useTenant();
  return useMutation({
    mutationFn: (body: {
      principal: string;
      dataset_version_id: string;
      purpose: string;
      justification: string;
      ttl_hours: number;
    }) => api.post("/leases/requests", { ...body, tenant_id: tenant }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["lease-requests"] });
      queryClient.invalidateQueries({ queryKey: ["summary"] });
      queryClient.invalidateQueries({ queryKey: ["access-preview"] });
    },
  });
}

/**
 * Which versions the signed-in person can read, asked of the policy itself.
 *
 * Refreshed on focus and every minute, because access ends on its own when a
 * grant runs out. It is only a preview: reading still goes through the real
 * check, so a stale mark can never let anybody in.
 */
export function useAccessPreview(ids: { datasetIds?: string[]; versionIds?: string[] }) {
  const { principal } = useIdentity();
  const datasetIds = ids.datasetIds ?? [];
  const versionIds = ids.versionIds ?? [];
  return useQuery({
    queryKey: ["access-preview", principal?.id, datasetIds, versionIds],
    queryFn: () =>
      api.post<AccessPreview>("/access-preview", {
        dataset_ids: datasetIds,
        version_ids: versionIds,
      }),
    enabled: Boolean(principal) && datasetIds.length + versionIds.length > 0,
    refetchOnWindowFocus: true,
    refetchInterval: 60_000,
  });
}

export interface ActionRunPage {
  action_runs: ActionRun[];
  shown: number;
  total: number;
  limit: number;
  offset: number;
}

export function useActionRuns(limit?: number, offset?: number) {
  const tenant = useTenant();
  return useQuery({
    queryKey: ["action-runs", tenant, limit, offset],
    queryFn: () => api.get<ActionRunPage>("/action-runs", { limit, offset }),
  });
}
