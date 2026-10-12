/**
 * The Munitas client library, as this platform offers it for download: its version, where the file is, and the checksum of that file.
 *
 * Open to anyone who can reach the platform (a `pip` install cannot sign in), so this page needs no session for it. `available` is false when the platform was
 * built without the library.
 */

import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export interface ClientLibrary {
  available: boolean;
  name?: string;
  version?: string;
  filename?: string;
  /** Where the file is, relative to the platform's address. */
  path?: string;
  sha256?: string;
  size?: number;
  requires_python?: string;
  requires?: string[];
  extras?: Record<string, string[]>;
}

export function useClientLibrary() {
  return useQuery({
    queryKey: ["client-library"],
    queryFn: () => api.get<ClientLibrary>("/client"),
    staleTime: 5 * 60_000,
  });
}
