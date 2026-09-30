// Host-published ports, read from the repo's single config.json at build
// time (Vite inlines JSON imports, so this adds nothing to the runtime
// bundle beyond the numbers themselves). This is the browser-safe half of
// the port setup: it has no access to environment variables the way the
// Python side does, so a VITE_* env var (set in web/.env or the shell)
// always wins when present, and config.json only supplies the default --
// same two-layer shape as web/src/api/client.ts and
// web/src/identity/kratos.ts already used before this module existed.
import ports from "../../../config.json";

export interface Ports {
  postgres: number;
  seaweedfs_s3: number;
  seaweedfs_filer: number;
  seaweedfs_master: number;
  temporal: number;
  temporal_ui: number;
  opa: number;
  mlflow: number;
  jaeger_ui: number;
  jaeger_otlp_grpc: number;
  jaeger_otlp_http: number;
  otel_collector_http: number;
  label_studio: number;
  munitas_api_http: number;
  munitas_api_https: number;
  kratos_public: number;
  kratos_admin: number;
  console_dev: number;
  console_preview: number;
}

// config.json also carries "_comment" for whoever opens the file directly;
// everything else on it is a Ports field, so this cast is accurate rather
// than a loose escape hatch.
export const PORTS = ports as unknown as Ports;

export const API_BASE =
  import.meta.env.VITE_API_BASE ?? `http://localhost:${PORTS.munitas_api_http}`;

export const KRATOS_PUBLIC_URL =
  import.meta.env.VITE_KRATOS_PUBLIC_BASE ?? `http://localhost:${PORTS.kratos_public}`;
