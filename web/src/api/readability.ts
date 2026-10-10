/**
 * What is locked in a version and what permission to read the whole of it is worth, as the platform works it out.
 *
 * The version page shows only the ways of asking that can work for what the version holds. It never works that out itself: a screen that guessed would
 * offer a form the platform then refuses.
 */

import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export interface Readability {
  version_id: string;
  /** files: every file is locked. columns: a table with locked columns. plain: nothing is locked. */
  shape: "files" | "columns" | "plain";
  /** What permission to read the whole version reads. */
  reads: "all" | "readable_columns" | "nothing";
  locked_files: number;
  locked_columns: number;
  readable_columns: number;
  /** One sentence for the person looking, or null when nothing is locked. */
  says: string | null;
}

export function useReadability(versionId: string | undefined) {
  return useQuery({
    queryKey: ["readability", versionId],
    queryFn: () => api.get<Readability>(`/dataset-versions/${versionId}/readability`),
    enabled: Boolean(versionId),
    retry: false,
  });
}
