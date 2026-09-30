// Host-published ports, for Node-context files that aren't bundled by Vite:
// Playwright configs and every test/spec file under web/tests/ and
// web/walkthroughs/. web/src/config/ports.ts is the separate, browser-safe
// equivalent for code Vite actually bundles into the console -- that one
// can rely on import.meta.env, which does not exist here.
//
// Read via fs rather than a JSON import attribute (`with { type: "json" }`)
// on purpose: Playwright's own TS transform is what actually loads this
// file, not tsc or Vite, and fs + JSON.parse works the same way regardless
// of which import-attributes syntax that transform does or doesn't support.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

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

export const PORTS: Ports = JSON.parse(
  readFileSync(path.join(REPO_ROOT, "config.json"), "utf-8"),
);

export const API_BASE = `http://localhost:${PORTS.munitas_api_http}`;
export const KRATOS_PUBLIC_URL = `http://localhost:${PORTS.kratos_public}`;
export const CONSOLE_URL = `http://localhost:${PORTS.console_dev}`;
