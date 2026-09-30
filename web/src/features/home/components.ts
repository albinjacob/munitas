/**
 * The parts of the running system.
 *
 * Kept as data so U14 can check it against docker-compose.yml and fail when a
 * service is added without appearing here. A map that drifts from the system it
 * describes is worse than no map, because it is believed.
 *
 * `service` matches the Compose service name exactly. `url` is present only
 * where the component has a interface of its own worth opening, because a link
 * to a port that answers with JSON is a link nobody should follow twice.
 */

import { PORTS } from "../../config/ports";

export interface Component {
  service: string;
  label: string;
  purpose: string;
  url?: string;
  /** What it would mean for this one to be down. */
  ifDown: string;
}

export const COMPONENTS: Component[] = [
  {
    service: "munitas-api",
    label: "Control plane",
    purpose:
      "Decides access, seals dataset versions, records every decision. Everything else in this list is reached through it or reports to it.",
    url: `http://localhost:${PORTS.munitas_api_http}/docs`,
    ifDown: "Nothing can be granted, sealed or audited.",
  },
  {
    service: "postgres",
    label: "Metadata and lineage",
    purpose:
      "Holds dataset versions, class transitions, leases and the audit log. Immutability and separation of duties are enforced here, in constraints, not in code.",
    ifDown: "The whole platform stops. This is the system of record.",
  },
  {
    service: "seaweedfs",
    label: "Object storage",
    purpose:
      "Holds audio, transcripts and redacted output. Credentials are scoped to a version prefix, which is how a class becomes a boundary rather than a label.",
    url: `http://localhost:${PORTS.seaweedfs_filer}`,
    ifDown: "Data cannot be read or written, but the record of it survives.",
  },
  {
    service: "opa",
    label: "Policy engine",
    purpose:
      "Answers whether a principal may read a version, and who may approve a request. The rules are testable on their own, without the platform running.",
    ifDown: "Access requests fail closed. Nothing is granted.",
  },
  {
    service: "temporal",
    label: "Workflow engine",
    purpose:
      "Runs the de-identification pipeline durably, so a crash resumes at the failed step rather than the beginning.",
    url: `http://localhost:${PORTS.temporal_ui}`,
    ifDown: "No new pipeline runs. Existing data is unaffected.",
  },
  {
    service: "mlflow",
    label: "Score cards",
    purpose:
      "Stores what each verification run measured. A promotion points here for its evidence, so a gate decision can be checked rather than trusted.",
    url: `http://localhost:${PORTS.mlflow}`,
    ifDown: "Promotions lose their evidence trail.",
  },
  {
    service: "jaeger",
    label: "Traces",
    purpose:
      "Shows what an agent did, step by step, with the visibility class of the data each span touched.",
    url: `http://localhost:${PORTS.jaeger_ui}`,
    ifDown: "Agent behaviour becomes unobservable.",
  },
  {
    service: "otel-collector",
    label: "Telemetry collector",
    purpose:
      "Receives spans and stamps any that arrive without a visibility class, so a span that forgets shows up rather than passing silently.",
    ifDown: "Traces stop reaching Jaeger.",
  },
  {
    service: "label-studio",
    label: "Annotation tool",
    purpose:
      "Where a person corrects detected spans. It holds the credential on their behalf, which is how humans stay off the data plane.",
    url: `http://localhost:${PORTS.label_studio}`,
    ifDown: "Human review stops. The pipeline still runs.",
  },
  {
    service: "temporal-ui",
    label: "Workflow history",
    purpose:
      "The interface onto Temporal. Linked to rather than rebuilt, because a second view of the same history is a second version of the truth.",
    url: `http://localhost:${PORTS.temporal_ui}`,
    ifDown: "Workflow history is still there, just harder to read.",
  },
  {
    service: "kratos",
    label: "Real login",
    purpose:
      "Verifies a person's session and resolves it to a directory row (GET /auth/whoami). Every decision a person makes in the console needs one.",
    ifDown: "Nobody can sign in. Whoever is already signed in stays signed in until their session expires.",
  },
  {
    service: "kratos-migrate",
    label: "Login database setup",
    purpose:
      "Applies Kratos's own schema to its database, once, before Kratos starts. Not a running service: it exits as soon as it succeeds, the same shape as a database migration in any other stack.",
    ifDown: "Already ran. Only matters again on a fresh database, where Kratos itself will not start until it has.",
  },
  {
    service: "worker-lite",
    label: "Quickstart worker",
    purpose:
      "A lighter pipeline worker for a fresh install with no GPU next to it (the `quickstart` Compose profile, not the default one). The Windows/WSL2 path this platform is normally developed on already runs its own worker on the host and never starts this one.",
    ifDown: "No effect on a normal install. A quickstart install loses pipeline runs until it is back.",
  },
];
