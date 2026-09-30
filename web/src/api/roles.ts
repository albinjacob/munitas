/**
 * Human-readable names for policy roles.
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
 * The authoritative list of roles is the policy engine, reachable through
 * `GET /policy/roles`. This only decides what to call them on screen, so a role
 * missing from this table renders as its identifier rather than as nothing.
 */

const ROLE_LABEL: Record<string, string> = {
  data_custodian: "Data custodian",
  dpo: "Data protection officer",
  notebook_explore: "Researcher",
  pipeline_operator: "Data engineer",
  platform_admin: "Platform administrator",
  hybridops: "Support and reliability",
  pipeline_action: "Pipeline action",
  annotation_tool: "Annotation tool",
  training_job: "Training job",
  model_eval: "Model evaluation",
  agent_runtime: "Agent runtime",
  analyst: "Analyst",
  deid_reviewer: "De-identification reviewer",
  network_architect: "Network architect",
};

export function roleLabel(role: string): string {
  return ROLE_LABEL[role] ?? role;
}
