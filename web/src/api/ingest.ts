/**
 * Bringing data in.
 *
 * Kept apart from `queries.ts` for the reason the API keeps `ingest.py` apart
 * from `read_models.py`: this is the only place in the console that sends
 * bytes. Everything else here moves identifiers, decisions and a sensitivity
 * claim, never file contents, so a reviewer asking "does the console ever
 * upload anything itself" has one file to check.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, API_BASE, ApiError } from "./client";
import { useIdentity } from "../identity/IdentityContext";
import type { VisibilityClass } from "./types";

export interface RegisteredDataset {
  id: string;
  declared_class: VisibilityClass;
  declaration_basis: "verified_source" | "asserted" | null;
  needs_confirmation: boolean;
  custodian: string | null;
  note: string;
}

/**
 * Registering exposes nothing, so it needs nobody's approval. It does need an
 * owning department, because that is who becomes accountable from the moment
 * the dataset exists.
 */
export function useRegisterDataset() {
  const tenant = useIdentity().tenant;
  return useMutation({
    mutationFn: (body: {
      name: string;
      department_id: string;
      registered_by: string;
      provenance: "external_public" | "external_licensed" | "internal_regulated";
      declared_class: VisibilityClass;
      modality: string[];
    }) =>
      api.post<RegisteredDataset>("/datasets/register", {
        ...body,
        tenant_id: tenant,
      }),
  });
}

/**
 * One file. `FormData`, not JSON, because this is the one endpoint that
 * carries bytes rather than a decision, so it does not go through `api.post`,
 * which always sends `content-type: application/json`.
 */
export function useUploadFile() {
  return useMutation({
    mutationFn: async ({
      datasetId,
      file,
    }: {
      datasetId: string;
      file: File;
    }) => {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch(
        `${API_BASE}/datasets/${datasetId}/files`,
        // Sent with the session: this is a person acting, and the platform acts as the signed-in person.
        { method: "POST", body: form, credentials: "include" },
      );
      const text = await response.text();
      const body = text ? JSON.parse(text) : null;
      if (!response.ok) throw new ApiError(response.status, body);
      // The derived keys are present only for a file the platform recognised
      // and read: duration and sample rate for audio, the hazard flag for an
      // answer key. Optional here because most uploads are neither.
      return body as {
        source_id: string;
        filename: string;
        bytes: number;
        checksum: string;
        audio_duration_seconds?: number;
        audio_sample_rate?: number;
        truth_has_hazard?: boolean;
      };
    },
  });
}

/**
 * Starting a fetch from a public (or gated, with a connected account)
 * HuggingFace dataset repo.
 *
 * Returns as soon as the job starts, not when it finishes: the platform is
 * still the one making the request (so the resulting claim can become
 * `verified_source` rather than `asserted`), but the request itself runs
 * in the background, in a process separate from the one serving this call.
 * A large or slow repo used to hold this request open until it either
 * finished or took the whole API down with it; now the caller polls
 * `useHuggingFaceFetchJobs` for progress instead.
 */
export function useFetchHuggingFace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      datasetId,
      fetchedBy,
      repoId,
      revision,
      path,
    }: {
      datasetId: string;
      fetchedBy: string;
      repoId: string;
      revision: string;
      path: string;
    }) =>
      api.post<{ job_id: string; status: "running" }>(
        `/datasets/${datasetId}/fetch-huggingface`,
        { fetched_by: fetchedBy, repo_id: repoId, revision, path },
      ),
    onSuccess: (_result, { datasetId }) => {
      queryClient.invalidateQueries({ queryKey: ["huggingface-fetch-jobs", datasetId] });
    },
  });
}

export interface HuggingFaceFetchJob {
  id: string;
  repo_id: string;
  revision: string;
  path: string;
  status: "running" | "succeeded" | "failed" | "cancelled";
  files_total: number | null;
  files_done: number;
  bytes_total: number | null;
  bytes_done: number;
  error: string | null;
  started_at: string;
  ended_at: string | null;
  fetched_by: string;
  fetched_by_label: string | null;
}

/**
 * A dataset's HuggingFace fetch jobs, most recent first. Polled while any
 * of them is still running, so "ingestion in progress" is something the
 * console can show without holding a request open, and something that
 * survives a page reload because it is read from the job row rather than
 * remembered by a component.
 */
export function useHuggingFaceFetchJobs(datasetId: string | undefined) {
  return useQuery({
    queryKey: ["huggingface-fetch-jobs", datasetId],
    queryFn: () => api.get<HuggingFaceFetchJob[]>(`/datasets/${datasetId}/huggingface-fetch-jobs`),
    enabled: Boolean(datasetId),
    refetchInterval: (query) => {
      const jobs = query.state.data;
      return jobs?.some((j) => j.status === "running") ? 2000 : false;
    },
  });
}

/**
 * Stopping a running fetch. Returns as soon as Temporal accepts the
 * cancellation, not once it has actually taken effect: `status` stays
 * `running` until the workflow itself records `cancelled`, which is why the
 * caller keeps polling `useHuggingFaceFetchJobs` afterwards instead of
 * assuming this call alone finished the job.
 */
export function useCancelHuggingFaceFetch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ datasetId, jobId }: { datasetId: string; jobId: string }) =>
      api.post<{ job_id: string; status: "cancelling" }>(
        `/datasets/${datasetId}/huggingface-fetch-jobs/${jobId}/cancel`,
      ),
    onSuccess: (_result, { datasetId }) => {
      queryClient.invalidateQueries({ queryKey: ["huggingface-fetch-jobs", datasetId] });
    },
  });
}

export interface HuggingFaceAccount {
  connected: boolean;
  hf_username: string | null;
  connected_at: string | null;
}

/**
 * Whether the given person has their own HuggingFace account connected.
 *
 * Never fetches or exposes the token itself; only the connection state and
 * which HuggingFace account it belongs to.
 */
export function useHuggingFaceAccount(directoryId: string | undefined) {
  return useQuery({
    queryKey: ["huggingface-account", directoryId],
    queryFn: () =>
      api.get<HuggingFaceAccount>(`/directory/${directoryId}/huggingface-token`),
    enabled: Boolean(directoryId),
  });
}

/**
 * Connecting a token. Validated by the platform against HuggingFace before
 * anything is stored, so a typo or an expired token is refused here rather
 * than surfacing later as a confusing fetch failure.
 */
export function useConnectHuggingFace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ directoryId, token }: { directoryId: string; token: string }) =>
      api.post<{ connected: true; hf_username: string }>(
        `/directory/${directoryId}/huggingface-token`,
        { token },
      ),
    onSuccess: (_result, { directoryId }) => {
      queryClient.invalidateQueries({ queryKey: ["huggingface-account", directoryId] });
    },
  });
}

export function useDisconnectHuggingFace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (directoryId: string) =>
      api.delete<{ connected: false }>(`/directory/${directoryId}/huggingface-token`),
    onSuccess: (_result, directoryId) => {
      queryClient.invalidateQueries({ queryKey: ["huggingface-account", directoryId] });
    },
  });
}

/**
 * Closes the upload and creates the first sealed version, through the same
 * path the pipeline uses, so nothing arriving this way skips immutability.
 */
export function useSealDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (datasetId: string) =>
      api.post<{ id: string; version: number; files: number }>(
        `/datasets/${datasetId}/seal`,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
      queryClient.invalidateQueries({ queryKey: ["summary"] });
    },
  });
}

/**
 * Sealing uploads as a version the de-identification pipeline can read.
 *
 * There is no separate check call, and that is deliberate. Sealing cannot be
 * undone, but a refusal seals nothing and carries every problem in `reasons`,
 * so this is both the check and the commit. A second Check button would ask the
 * same question and say the same thing twice.
 */
export function useSealAudio() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (datasetId: string) =>
      api.post<{ id: string; version: number; records: number }>(
        `/datasets/${datasetId}/seal-audio`,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
      queryClient.invalidateQueries({ queryKey: ["summary"] });
    },
  });
}

/**
 * Taking one upload out of the set without erasing that it arrived.
 *
 * For a recording that cannot be read and cannot be replaced. The row stays, so
 * the record of who removed it survives; only its inclusion in a future version
 * goes away.
 */
export function useWithdrawUpload() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ datasetId, sourceId }: { datasetId: string; sourceId: string }) =>
      api.post<{ withdrawn: boolean; locator: string }>(
        `/datasets/${datasetId}/uploads/${sourceId}/withdraw`,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
}

/**
 * Datasets whose sensitivity claim nobody has agreed to yet, narrowed to what
 * one custodian can act on. Only claims need this: the safe default and a
 * verified source need no confirmation.
 */
export function useAwaitingConfirmation(custodian: string | undefined) {
  const tenant = useIdentity().tenant;
  return useQuery({
    queryKey: ["awaiting-confirmation", tenant, custodian],
    queryFn: () =>
      api.get<
        {
          id: string;
          name: string;
          declared_class: VisibilityClass;
          declared_by: string;
          declared_at: string;
          provenance: string;
          department_name: string | null;
          custodian: string | null;
        }[]
      >("/datasets/awaiting-confirmation", {
        tenant_id: tenant,
        custodian,
      }),
    enabled: Boolean(custodian),
  });
}

/**
 * The custodian agreeing with somebody's claim. Refused for anybody who is not
 * the custodian of the owning department, and refused for whoever made the
 * claim, so the platform's separation-of-duties rule holds here too.
 */
export function useConfirmClassification() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      datasetId,
      confirmedBy,
    }: {
      datasetId: string;
      confirmedBy: string;
    }) =>
      api.post(`/datasets/${datasetId}/confirm-classification`, {
        confirmed_by: confirmedBy,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["awaiting-confirmation"] });
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
}
