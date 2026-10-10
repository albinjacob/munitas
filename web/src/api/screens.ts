/**
 * Which pages the signed-in person may open, and which of them the menu lists. The platform answers from its policy; the console keeps no list of its own.
 */

import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export interface Screen {
  path: string;
  label: string;
  /** The person may open the page. */
  open: boolean;
  /** The menu lists the page for this person. */
  menu: boolean;
  /** Who the page is for, in words, for the sentence shown to everybody else. */
  who: string;
}

export function useScreens(enabled = true) {
  return useQuery({
    queryKey: ["screens"],
    enabled,
    staleTime: 60_000,
    queryFn: async () => (await api.get<{ screens: Record<string, Screen> }>("/screens")).screens,
  });
}
