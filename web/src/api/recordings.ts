/**
 * Recordings locked per patient: an upload sent in parts that can each be sent again, and the seal that closes it.
 *
 * Each recording belongs to one patient and is locked under that patient's key as it arrives. The platform keeps only the patient's person code,
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

export interface SealedRecordings {
  id: string;
  version: number;
  records: number;
  people: number;
}

export interface RecordingsSummary {
  version_id: string;
  recordings: number;
  patients: number;
  erased_recordings: number;
}

export const openRecordingUpload = (datasetId: string, body: { file_name: string; size_bytes: number; patient_kind: string; patient_value: string }) =>
  api.post<UploadSession>(`/datasets/${datasetId}/recording-uploads`, body);

export const recordingUploadStatus = (uploadId: string) => api.get<UploadSession>(`/recording-uploads/${uploadId}`);

export const sendRecordingPart = (uploadId: string, part: number, bytes: Blob) =>
  api.putBytes<{ part_number: number; received: number[]; missing: number[] }>(`/recording-uploads/${uploadId}/parts/${part}`, bytes);

export const completeRecordingUpload = (uploadId: string) => api.post<FinishedUpload>(`/recording-uploads/${uploadId}/complete`);

export const abortRecordingUpload = (uploadId: string) => api.delete<UploadSession>(`/recording-uploads/${uploadId}`);

/** Close the upload: every locked recording becomes one sealed version. A refusal seals nothing and lists every problem. */
export function useSealRecordings(datasetId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<SealedRecordings>(`/datasets/${datasetId}/seal-recordings`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["versions"] });
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
}

/** What a sealed version says about its recordings: counts only. Null when the version holds no locked recordings. */
export function useRecordingsSummary(versionId: string | undefined) {
  return useQuery({
    queryKey: ["recordings-summary", versionId],
    queryFn: async () => {
      try {
        return await api.get<RecordingsSummary>(`/dataset-versions/${versionId}/recordings`);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(versionId),
    retry: false,
  });
}
