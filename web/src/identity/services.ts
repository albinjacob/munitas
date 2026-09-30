/**
 * The workloads. Described, never impersonated.
 *
 * Nobody logs in as a training job, so these are not in the chooser. They appear
 * in the administrator's view showing what each has actually been doing, which
 * is better than acting as one: the pipeline and the agent already run for real
 * and leave audit rows, so an administrator reads evidence rather than a
 * simulation.
 *
 * Their floors come from the same table as everyone else's, and only these have
 * a floor below published. That is the whole asymmetry of the system:
 * work happens inside the boundary, people wait outside it.
 */

import type { VisibilityClass } from "../api/types";

export interface Service {
  id: string;
  label: string;
  role: string;
  /** The most sensitive class it may read without a lease. */
  floor: VisibilityClass;
  description: string;
}

export const SERVICES: Service[] = [
  {
    id: "health-pipeline",
    label: "Pipeline action",
    role: "pipeline_action",
    floor: "RAW",
    description:
      "Transcribes, detects and redacts. The only role that reaches raw data, because it works inside the boundary and produces the thing everyone else waits for.",
  },
  {
    id: "annotation-tool",
    label: "Annotation tool",
    role: "annotation_tool",
    floor: "OPEN_FOR_ANNOTATION",
    description:
      "Holds the credential on a reviewer's behalf, so a person corrects spans without ever holding a data-plane key themselves.",
  },
  {
    id: "svc-trainer",
    label: "Training job",
    role: "training_job",
    floor: "OPEN_FOR_TRAINING",
    description:
      "Trains on de-identified data. Anything more sensitive needs a lease a custodian approved.",
  },
  {
    id: "agent-triage-1",
    label: "Triage agent",
    role: "agent_runtime",
    floor: "PUBLISHED",
    description:
      "Ranks records for human review. Reads nothing below published, has no route to the network, and every tool call goes through a policy decision.",
  },
];
