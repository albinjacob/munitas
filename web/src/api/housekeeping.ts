/**
 * Storage housekeeping: what is held, what could be freed, what was.
 *
 * Two scopes, and the API decides which one a caller may have rather than
 * this module choosing. Asking without an organisation is the platform-wide
 * question and is refused for anyone but support and reliability or the
 * administrator; asking with one is that organisation's own record and is
 * refused for anyone outside it. Both refusals arrive as a 403 carrying the
 * reason, which the screen shows rather than turning into "something went
 * wrong".
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

/** One organisation's storage, as the platform-wide view sees it. */
export interface TenantStorage {
  tenant_id: string;
  /** How the platform treats it: production, canary, retired or scratch. */
  purpose: string;
  /** Why it exists, in a sentence, or null if nobody wrote one. */
  note: string | null;
  /** Which backends its objects are actually on. Null before it writes. */
  backends: string[] | null;
  bucket: string;
  versions: number;
  reclaimable_versions: number;
  reclaimed_versions: number;
  bytes_reclaimed: number;
}

/** How much of the storage engine's fixed volume pool is spoken for. */
export interface VolumePool {
  /** Null when the storage master could not be reached. */
  used: number | null;
  by_collection: { collection: string; volumes: number }[];
  reason?: string;
}

/** A throwaway organisation the verification suite left behind. */
export interface ProbeWaiting {
  tenant_id: string;
  purpose: string;
  versions: number;
}

/** Whether allowed storage access is taking effect. */
export interface StoragePermissions {
  last_success_at: string | null;
  failing: boolean;
  failing_since: string | null;
  /** False when retrying cannot fix it and somebody has to. */
  retryable: boolean | null;
  reason: string | null;
  /** True when an administrator needs to know. */
  alert: boolean;
  parked_runs: number;
}

/** How many versions are also stored as tables, and where one was meant to be and is not. */
export interface TableCopies {
  projected: number;
  /** An error the platform did not recognise while writing a table. Needs somebody. */
  failed: number;
  /** A stated reason, such as rows that do not fit their contract, a file too large, or files on R2. */
  skipped: number;
  /** Versions of files, which were never meant to be tables. */
  files: number;
  /** Sealed before the platform wrote reasons down, so it cannot say. */
  unrecorded: number;
  alert: boolean;
  lacking: {
    dataset_version_id: string;
    tenant_id: string;
    /** Named only in an organisation's own view. */
    dataset_name?: string;
    version?: number;
    outcome: "failed" | "skipped";
    reason: string;
    noted_at: string;
  }[];
}

export interface PlatformHousekeeping {
  scope: "platform";
  table_copies: TableCopies;
  storage_permissions: StoragePermissions;
  /** Where SeaweedFS keeps its files on the host, or "" if unstated. */
  storage_path: string;
  tenants: TenantStorage[];
  volumes: VolumePool;
  probes_waiting: ProbeWaiting[];
}

/** One deletion, from the organisation whose bytes went. */
export interface ReclaimedRow {
  dataset_name: string;
  version: number;
  bytes_freed: number;
  object_count: number;
  reclaimed_at: string;
  reclaimed_by: string;
  reason: string;
}

export interface TenantHousekeeping {
  scope: "tenant";
  tenant_id: string;
  table_copies: TableCopies;
  reclaimed: ReclaimedRow[];
}

export interface FreedVersion {
  dataset_name: string;
  version: number;
  tenant_id: string;
  bucket: string;
  objects: number;
  bytes: number;
}

export interface ReclaimResult {
  dry_run: boolean;
  versions: number;
  objects: number;
  bytes: number;
  freed: FreedVersion[];
}

export function usePlatformHousekeeping(enabled: boolean) {
  return useQuery({
    queryKey: ["housekeeping", "platform"],
    queryFn: () => api.get<PlatformHousekeeping>("/housekeeping/storage"),
    enabled,
    retry: false,
  });
}

export function useTenantHousekeeping(tenantId: string | undefined) {
  return useQuery({
    queryKey: ["housekeeping", "tenant", tenantId],
    queryFn: () =>
      api.get<TenantHousekeeping>("/housekeeping/storage", {
        tenant_id: tenantId,
      }),
    enabled: Boolean(tenantId),
    retry: false,
  });
}

/**
 * Rehearse or perform a reclaim.
 *
 * `dry_run` is not a convenience. Freeing storage destroys bytes a sealed
 * version can never get back, so the screen shows what would go before it
 * offers to do it, and the caller sends the same request twice: once to see,
 * once to mean it.
 */
export function useReclaim() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      reason: string;
      older_than_days: number;
      tenant_id?: string;
      dry_run: boolean;
    }) => api.post<ReclaimResult>("/housekeeping/reclaim", body),
    onSuccess: (result) => {
      if (result.dry_run) return;
      // Only a real reclaim changes what the screens below show.
      queryClient.invalidateQueries({ queryKey: ["housekeeping"] });
    },
  });
}
