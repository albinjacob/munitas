/**
 * The limits an organisation sets on what people may ask for: who may ask to read rows of locked columns, the most rows and days, and the most files and hours to
 * open locked files in the page and in a notebook. The page only reads them, and asks what would happen to a request of a given size.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "./client";

export interface AccessRule {
  key: string;
  group: string;
  label: string;
  about: string;
  kind: "number" | "roles";
  unit: string | null;
  value: number | string[];
  default: number | string[];
  maximum: number | null;
  minimum: number | null;
  allowed: string[];
  /** "default" until the organisation has set it. */
  source: "default" | "organisation";
  set_by: string | null;
  set_at: string | null;
  reason: string | null;
}

export interface AccessRules {
  rules: AccessRule[];
  capabilities: Record<string, string>;
  roles: string[];
  role_words: Record<string, string>;
}

export interface WhatWouldHappen {
  allowed: boolean;
  reasons: string[];
}

export function useAccessRules() {
  return useQuery({ queryKey: ["access-rules"], queryFn: () => api.get<AccessRules>("/access-rules") });
}

export function useWhatWouldHappen() {
  return useMutation({
    mutationFn: (v: { capability: string; role: string; size: number; length: number }) => api.post<WhatWouldHappen>("/access-rules/what-would-happen", v),
  });
}
