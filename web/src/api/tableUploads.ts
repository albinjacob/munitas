/**
 * Bringing in a table in parts. A table too large for one request is opened as an upload, sent a part at a time, and finished; a worker then
 * checks and writes it, and the status says how far it has got and how it ended, with every problem found when the file was refused.
 */

import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export interface TableUploadOptions {
  delimiter?: string;
  encoding?: string;
  header?: boolean;
  null_values?: string[];
  /** For a workbook: the sheet to read. The first sheet when it is left out. */
  sheet?: string;
}

export interface TableUpload {
  job_id: string;
  /** pending (waiting for its file or a worker), running, sealed, refused or expired. */
  status: "pending" | "running" | "sealed" | "refused" | "expired";
  version: number;
  version_id: string | null;
  file_name: string;
  size_bytes: number;
  part_bytes: number;
  parts: number;
  received: number[];
  missing: number[];
  /** True while the file is still being sent. */
  open: boolean;
  ready: boolean;
  outcome: string | null;
  reason: string | null;
  problems: string[];
  progress: { phase?: string | null; rows?: number | null } | null;
  brought_in_by: string | null;
  /** True for a table about people: its identifying columns are locked as it is written, and the file that was sent is not kept. */
  about_people: boolean;
  /** For a sealed table about people: how many different people it holds, how many sit in a group smaller than the threshold, and the threshold. */
  people: number | null;
  people_in_small_groups: number | null;
  group_threshold: number | null;
  finished_at: string | null;
  waiting_seconds: number | null;
  stalled: boolean;
}

export const openTableUpload = (datasetId: string, body: { file_name: string; size_bytes: number; contract: string; options: TableUploadOptions }) =>
  api.post<TableUpload>(`/datasets/${datasetId}/table-uploads`, body);

export const tableUploadStatus = (jobId: string) => api.get<TableUpload>(`/table-uploads/${jobId}`);

export const sendTablePart = (jobId: string, part: number, bytes: Blob) =>
  api.putBytes<{ part_number: number; received: number[]; missing: number[] }>(`/table-uploads/${jobId}/parts/${part}`, bytes);

export const finishTableUpload = (jobId: string) => api.post<TableUpload>(`/table-uploads/${jobId}/finish`);

export const cancelTableUpload = (jobId: string) => api.post<TableUpload>(`/table-uploads/${jobId}/cancel`);

const FINAL = ["sealed", "refused", "expired"];

/** The status of an upload that has been finished, asked again every couple of seconds until it has ended. */
export function useTableUploadStatus(jobId: string | null) {
  return useQuery({
    queryKey: ["table-upload", jobId],
    queryFn: () => tableUploadStatus(jobId as string),
    enabled: !!jobId,
    refetchInterval: (query) => (query.state.data && FINAL.includes(query.state.data.status) ? false : 2000),
  });
}
