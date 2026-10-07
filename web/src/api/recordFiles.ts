/**
 * The files of a version, opened through the platform.
 *
 * A person never holds a key or a storage address. Opening a file is a request to the platform, which decides from what it holds why this person may
 * (never from what they say), unlocks a locked file with its patient's key inside the platform, writes the opening to a log, and returns a short handle.
 * The handle is put in the address of the sound player or the text request, and the platform asks everything again on every request, so a sample that
 * ends or a patient who is erased stops what is playing.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { API_BASE, api } from "./client";

export interface RecordFile {
  record_id: string;
  kind: "recording" | "file";
  content_type: string;
  locked: boolean;
  erased: boolean;
  bytes: number;
}

export interface RecordList {
  version_id: string;
  /** Why the platform finds this person may open the files: their own sample, synthetic recordings, what they made from either, or none. */
  basis: "own_sample" | "synthetic" | "own_development_output" | "expired" | "none";
  may_open: boolean;
  reasons: string[];
  total: number;
  records: RecordFile[];
  /** Present for a version made for development: when it ends, and when it ended and its files were deleted. */
  development: { kind: "sample" | "output"; ends_at: string; expired_at: string | null } | null;
}

export interface OpenedRecord {
  record_id: string;
  bytes: number;
  content_type: string;
  locked: boolean;
  handle: string;
  expires_in: number;
}

export function useRecordList(versionId: string | undefined) {
  return useQuery({
    queryKey: ["record-files", versionId],
    queryFn: () => api.get<RecordList>(`/dataset-versions/${versionId}/records`),
    enabled: Boolean(versionId),
    retry: false,
  });
}

export function useOpenRecord(versionId: string) {
  return useMutation({
    mutationFn: (recordId: string) => api.post<OpenedRecord>(`/dataset-versions/${versionId}/records/${encodeURIComponent(recordId)}/open`),
  });
}

/** Where a sound player or a request reads the opened file from. The handle is the only credential the address carries. */
export function streamUrl(handle: string): string {
  return `${API_BASE}/record-stream/${handle}`;
}
