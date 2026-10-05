/**
 * Which of the signed-in person's roles allow an action, so a screen offers only what the platform will accept.
 *
 * These mirror the policy engine's decisions (`intake_decision` and `code_registration_decision` in platform/policy/access.rego). They decide
 * what is shown, never what is permitted: the platform refuses a person who holds none of these roles whatever the screen offers, and says why.
 */

import { useIdentity } from "./IdentityContext";

/** Bringing data in: registering a dataset, putting files into one, fetching one, sealing it. A data engineer, or a data custodian. */
export const INTAKE_ROLES = ["pipeline_operator", "data_custodian"];

/** Registering an agent or a pipeline, or a version of either. A data engineer. */
export const CODE_ROLES = ["pipeline_operator"];

export function useHoldsRole(roles: string[]): boolean {
  const { principal } = useIdentity();
  return principal?.roles.some((role) => roles.includes(role)) ?? false;
}
