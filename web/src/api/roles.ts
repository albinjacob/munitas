/**
 * What the console calls each role.
 *
 * Deliberately outside `src/identity/`. That directory is the identity seam,
 * and U7 forbids anything outside it from importing its internals so that
 * swapping the fake for real authentication stays a one-directory change.
 *
 * A lookup table of display names is not identity. It was briefly in
 * `identity/principals.ts` and the seam test caught it, which is the test doing
 * exactly its job: the rule is worth more than the convenience of leaving it
 * where it first landed.
 *
 * The names are not written here. They come from the policy's role table (platform/policy/role_words.rego) through `GET /policy/roles`, which needs no
 * session, and are read once before the console draws anything. A role the table does not know, or a platform that cannot be reached, shows the role's
 * identifier rather than nothing.
 */

import { api } from "./client";

interface RoleWords {
  role_words: Record<string, { label: string }>;
}

let labels: Record<string, string> = {};

export async function loadRoleLabels(): Promise<void> {
  try {
    const roles = await api.get<RoleWords>("/policy/roles");
    labels = Object.fromEntries(Object.entries(roles.role_words).map(([role, words]) => [role, words.label]));
  } catch {
    labels = {};
  }
}

export function roleLabel(role: string): string {
  return labels[role] ?? role;
}
