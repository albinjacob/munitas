/**
 * Files locked per patient: an upload sent in parts that can each be sent again, and the seal that closes it.
 *
 * Each file belongs to one patient and is locked under that patient's key as it arrives. The platform keeps only the patient's person code,
 * never the identifier that found them.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "./client";

export interface UploadSession {
  id: string;
  file_name: string;
  size_bytes: number;
  status: "open" | "complete" | "aborted" | "expired";
  /** How many bytes of the file each part holds, except the last. */
  part_bytes: number;
  parts: number;
  received: number[];
  missing: number[];
  expires_at: string;
}

export interface FinishedUpload {
  id: string;
  source_id: string;
  file_name: string;
  stored_bytes: number;
  facts: { audio_duration_seconds?: number; audio_sample_rate?: number; truth_has_hazard?: boolean };
}

export interface SealedFiles {
  id: string;
  version: number;
  records: number;
  people: number;
}

export interface FilesSummary {
  version_id: string;
  files: number;
  people: number;
  erased_files: number;
}

export const openFileUpload = (datasetId: string, body: { file_name: string; size_bytes: number; person_kind: string; person_value: string }) =>
  api.post<UploadSession>(`/datasets/${datasetId}/file-uploads`, body);

export const fileUploadStatus = (uploadId: string) => api.get<UploadSession>(`/file-uploads/${uploadId}`);

export const sendFilePart = (uploadId: string, part: number, bytes: Blob) =>
  api.putBytes<{ part_number: number; received: number[]; missing: number[] }>(`/file-uploads/${uploadId}/parts/${part}`, bytes);

export const completeFileUpload = (uploadId: string) => api.post<FinishedUpload>(`/file-uploads/${uploadId}/complete`);

export const abortFileUpload = (uploadId: string) => api.delete<UploadSession>(`/file-uploads/${uploadId}`);

/** Close the upload: every locked file becomes one sealed version. A refusal seals nothing and lists every problem. */
export function useSealFiles(datasetId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<SealedFiles>(`/datasets/${datasetId}/seal-files`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["versions"] });
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
}

/** What a sealed version says about its files: counts only. Null when the version holds no locked files. */
export function useFilesSummary(versionId: string | undefined) {
  return useQuery({
    queryKey: ["files-summary", versionId],
    queryFn: async () => {
      try {
        return await api.get<FilesSummary>(`/dataset-versions/${versionId}/files`);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(versionId),
    retry: false,
  });
}
