/**
 * The state the role tests and the walkthrough capture both start from.
 *
 * Shared because both act on the same organisation and the same role: a
 * capture run that left a grant behind would otherwise put a second identical
 * row in front of the next test, and the two would be indistinguishable.
 */

import { bearerFor } from "./auth-helpers";
import { API_BASE } from "../config/ports";

export const API = API_BASE;

export const ASKER = "canary-researcher";
export const CUSTODIAN = "canary-custodian";
export const ADMIN = "canary-admin";

/** The same three in the walkthrough organisation, for the capture that makes the pictures. Tests never use these: they run in the test organisation. */
export const WALKTHROUGH = { ASKER: "sam-researcher", CUSTODIAN: "cust-hartley", ADMIN: "ops-priya" };

/** Rendered as written, because the label table has no entry for it. */
export const WANTED = "deid_reviewer";

/**
 * Withdraw every grant of this role and refuse every ask for it.
 *
 * Run before as well as after. A run that fails halfway leaves a grant
 * behind, and the next run would then act on two rows that look identical:
 * the state a test starts from has to be built, not assumed.
 */
export async function clearTheRole(custodian: string = CUSTODIAN): Promise<void> {
  const headers = {
    ...(await bearerFor(custodian)),
    "content-type": "application/json",
  };
  const roles = await fetch(`${API}/people/roles`, { headers }).then((r) => r.json());

  for (const grant of roles.held ?? []) {
    if (grant.role !== WANTED) continue;
    await fetch(`${API}/people/roles/${grant.id}/attest`, {
      method: "POST",
      headers,
      body: JSON.stringify({ still_needed: false }),
    });
  }
  for (const ask of roles.pending ?? []) {
    if (ask.role !== WANTED) continue;
    await fetch(`${API}/people/role-requests/${ask.id}/decide`, {
      method: "POST",
      headers,
      body: JSON.stringify({ outcome: "reject", reason: "clearing test state" }),
    });
  }
}

