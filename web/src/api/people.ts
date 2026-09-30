/**
 * Turning a principal id into something a person would say.
 *
 * The audit log, the custodian's queue and the lease list all store ids:
 * `sam-researcher`, `cust-hartley`, `health-pipeline`. Those read as identifiers
 * because that is what they are, and a screen showing one to a custodian
 * deciding whether to release recordings is asking them to translate.
 *
 * The id looking like a name and a role is a coincidence of how the
 * demonstration organisation was seeded. A real deployment might register
 * `u-40912`, so nothing here parses the id: the label and the roles are
 * separate columns in `directory` and this reads both.
 *
 * Not in `src/identity/`. Naming somebody is not deciding who is acting, and
 * U7 keeps that directory to the one question it answers. This reads the
 * directory through the seam's public value, which is the supported way.
 */

import { useCallback } from "react";
import { useIdentity } from "../identity/IdentityContext";
import { roleLabel } from "./roles";

export interface People {
  /** `"Sam (Researcher)"`. For anywhere the role is context the reader needs. */
  name: (id: string | null | undefined) => string;
  /**
   * `"Sam"`, with no role.
   *
   * The audit log uses this one. Each decision row records the roles the
   * principal held **at the time it was decided**, and that is the answer an
   * auditor wants; overlaying today's roles on a two-year-old decision would
   * quietly rewrite what was true when it was made.
   */
  label: (id: string | null | undefined) => string;
}

/**
 * Both fall back to the id itself when it does not resolve.
 *
 * That matters. Workloads and principals from other organisations are not in
 * the list this reads, and an unresolved id is better shown raw than blank: a
 * row naming nobody is worse than one naming something you have to look up.
 */
export function usePeople(): People {
  const { everyone } = useIdentity();

  const label = useCallback(
    (id: string | null | undefined) => {
      if (!id) return "somebody";
      return everyone.find((p) => p.id === id)?.label ?? id;
    },
    [everyone],
  );

  const name = useCallback(
    (id: string | null | undefined) => {
      if (!id) return "somebody";
      const person = everyone.find((p) => p.id === id);
      if (!person) return id;
      const role = person.roles.map(roleLabel)[0];
      return role ? `${person.label} (${role})` : person.label;
    },
    [everyone],
  );

  return { name, label };
}
