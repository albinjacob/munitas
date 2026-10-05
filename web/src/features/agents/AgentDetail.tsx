/**
 * One agent, and its full version history.
 *
 * The version list is the point of this screen: each row is content-
 * addressed (`code_hash`, `content_hash`), sealed on creation, and
 * immutable, the same guarantee `VersionDetail.tsx` shows for a dataset
 * version. "Which version is actually running" should be answerable by
 * reading a row here, not by asking whoever deployed it.
 */

import { CODE_ROLES, useHoldsRole } from "../../identity/mayDo";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  useAccessPreflight,
  useAgent,
  useAgentRuns,
  useApproveRun,
  useDeployAgent,
  useStartAgentRun,
} from "../../api/agents";
import { useVersions } from "../../api/queries";
import { useIdentity } from "../../identity/IdentityContext";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { CopyableHash } from "../../components/CopyableHash";
import { notify } from "../../components/toast";
import { UploadAgentVersion } from "./UploadAgentVersion";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm">{children}</dd>
    </div>
  );
}

export function AgentDetail() {
  const { agentId } = useParams();
  const { principal } = useIdentity();
  const mayRegisterCode = useHoldsRole(CODE_ROLES);
  const agent = useAgent(agentId);
  const runs = useAgentRuns(agentId);
  const deploy = useDeployAgent(agentId!);
  const startRun = useStartAgentRun(agentId!);
  const approveRun = useApproveRun();
  const versions = useVersions();
  const [runPurpose, setRunPurpose] = useState("");
  const [runTarget, setRunTarget] = useState("");
  const preflight = useAccessPreflight(agentId!, runTarget);
  const needsAccess = preflight.data?.needs_access_request ?? false;
  // A dataset with no owning department has nobody entitled to grant access to
  // it, and the API refuses the request rather than parking a run behind a
  // decision no one can make. Saying so here, and not offering the button, is
  // the difference between a clear dead end and an error after the click.
  const noApprover = needsAccess && !preflight.data?.approver_label;

  if (agent.isLoading) return <Loading what="this agent" />;
  if (agent.error) return <Failure error={agent.error} what="this agent" />;
  if (!agent.data) return null;

  const a = agent.data;
  const activeVersionId = a.active_version?.agent_version_id;

  return (
    <>
      <Section level="page" title={a.name} description={a.purpose}>
        <dl className="grid grid-cols-1 gap-4 rounded border border-slate-200 bg-white p-4 md:grid-cols-3">
          <Field label="Owned by">{a.department_name ?? "no department"}</Field>
          <Field label="Runtime identity (Principal)">
            <CopyableHash value={a.principal_id} />
          </Field>
          <Field label="Registered by">{a.registered_by_label ?? a.principal_id}</Field>
        </dl>
      </Section>

      <Section
        title="Versions"
        description="Sealed on registration and immutable from then on. Each is content-addressed, not just described."
      >
        {!a.versions.length ? (
          <Empty
            what="versions"
            hint="Upload a project below, or register one from its own checkout: python -m agent.register_version --agent-id "
          />
        ) : (
          <ol data-testid="agent-versions" className="space-y-3">
            {a.versions.map((v) => (
              <li
                key={v.id}
                data-testid={`agent-version-${v.version}`}
                className="rounded border border-slate-200 bg-white p-4"
              >
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium">v{v.version}</span>
                  {v.id === activeVersionId && (
                    <span className="rounded bg-teal-100 px-1.5 py-0.5 text-xs font-medium text-teal-900">
                      active
                    </span>
                  )}
                  <span className="text-slate-400">&middot;</span>
                  <span className="text-slate-600">{v.model_id}</span>
                  <span
                    data-testid={`agent-version-mode-${v.version}`}
                    className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                      v.sandboxed
                        ? "bg-indigo-100 text-indigo-900"
                        : "bg-slate-100 text-slate-600"
                    }`}
                  >
                    {v.sandboxed ? `sandboxed · ${v.entrypoint}` : "declared only"}
                  </span>
                  {v.requested_hosts.length > 0 && (
                    <span
                      data-testid={`egress-state-${v.version}`}
                      className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                        v.egress_state === "approved"
                          ? "bg-green-100 text-green-900"
                          : v.egress_state === "refused"
                            ? "bg-red-100 text-red-900"
                            : "bg-amber-100 text-amber-900"
                      }`}
                    >
                      {v.egress_state === "approved"
                        ? "egress approved"
                        : v.egress_state === "refused"
                          ? "egress refused"
                          : "egress pending"}
                    </span>
                  )}
                  <span className="ml-auto text-xs text-slate-400">
                    {new Date(v.created_at).toLocaleString()}
                  </span>
                  {v.id !== activeVersionId && principal && (
                    <button
                      type="button"
                      data-testid={`deploy-version-${v.version}`}
                      disabled={deploy.isPending || v.egress_state === "pending" || v.egress_state === "refused"}
                      title={
                        v.egress_state === "pending"
                          ? "This version's requested hosts have not been approved yet"
                          : v.egress_state === "refused"
                            ? "This version's requested hosts were refused"
                            : undefined
                      }
                      onClick={() =>
                        deploy.mutate(
                          { agent_version_id: v.id },
                          { onSuccess: () => notify(`v${v.version} deployed.`) },
                        )
                      }
                      className="ml-2 rounded bg-indigo-500 hover:bg-indigo-800 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                    >
                      {deploy.isPending ? "Deploying" : "Deploy"}
                    </button>
                  )}
                </div>
                <dl className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
                  <Field label="Code hash">
                    <CopyableHash value={v.code_hash} />
                  </Field>
                  <Field label="Source path">
                    <code className="break-all font-mono text-xs">{v.source_path}</code>
                  </Field>
                  <Field label="Runtime environment">
                    <CopyableHash value={v.image_digest} />
                  </Field>
                  <Field label="Content hash">
                    <CopyableHash value={v.content_hash} />
                  </Field>
                  <Field label="Tools it may call">
                    {v.tool_scope.length ? (
                      <span className="font-mono text-xs">{v.tool_scope.join(", ")}</span>
                    ) : (
                      <span className="text-slate-400">none declared</span>
                    )}
                  </Field>
                  <Field label="Registered by">{v.registered_by_label ?? "unknown"}</Field>
                  {v.requested_hosts.length > 0 && (
                    <Field label="Hosts it may call">
                      <span className="font-mono text-xs">{v.requested_hosts.join(", ")}</span>
                      {v.egress_approval_id && (
                        <Link
                          to={`/egress-approvals/${v.egress_approval_id}`}
                          className="ml-2 text-blue-700 hover:underline"
                        >
                          view request
                        </Link>
                      )}
                    </Field>
                  )}
                </dl>
              </li>
            ))}
          </ol>
        )}
        {mayRegisterCode && (
          <div className="mt-4">
            <UploadAgentVersion agentId={agentId!} />
          </div>
        )}
      </Section>

      <Section
        title="Runs"
        description="Each run is pinned to the version that actually executed, even if a different one is deployed afterward."
        actions={
          principal && activeVersionId ? (
            <div className="flex flex-col items-end gap-2">
              <div className="flex items-center gap-2">
                <input
                  data-testid="run-purpose"
                  value={runPurpose}
                  onChange={(e) => setRunPurpose(e.target.value)}
                  placeholder="What this run is for"
                  className="rounded border border-slate-300 px-2 py-1 text-sm"
                />
                <select
                  data-testid="run-target"
                  value={runTarget}
                  onChange={(e) => setRunTarget(e.target.value)}
                  className="rounded border border-slate-300 px-2 py-1 text-sm"
                >
                  <option value="">No dataset</option>
                  {versions.data?.map((v) => (
                    <option key={v.dataset_version_id} value={v.dataset_version_id}>
                      {v.dataset_name ?? v.dataset_id} v{v.version}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  data-testid="start-run"
                  disabled={!runPurpose.trim() || startRun.isPending || noApprover}
                  onClick={() => {
                    startRun.mutate({
                      purpose: runPurpose.trim(),
                      dataset_version_id: runTarget || undefined,
                      // Only ever true once the notice below has said who will
                      // be asked. Requesting access in somebody's name without
                      // showing them first is a surprise, not a shortcut.
                      request_access: needsAccess,
                    });
                    setRunPurpose("");
                  }}
                  className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                >
                  {startRun.isPending
                    ? "Starting"
                    : noApprover
                      ? "Nobody can grant this"
                      : needsAccess
                        ? "Request access and start"
                        : "Start a run"}
                </button>
              </div>
              {noApprover ? (
                <p
                  data-testid="access-notice"
                  className="max-w-md text-right text-xs text-amber-800"
                >
                  This agent cannot read that dataset
                  {preflight.data?.visibility_class
                    ? ` (${preflight.data.visibility_class})`
                    : ""}
                  , and the dataset belongs to no department, so there is
                  nobody who can grant it access. Give the dataset an owning
                  department first, or pick a different one.
                </p>
              ) : needsAccess ? (
                <p
                  data-testid="access-notice"
                  className="max-w-md text-right text-xs text-amber-800"
                >
                  This agent cannot read that dataset on its own
                  {preflight.data?.visibility_class
                    ? ` (${preflight.data.visibility_class})`
                    : ""}
                  . Starting will ask{" "}
                  {preflight.data?.approver_label}
                  {preflight.data?.department_name
                    ? `, who looks after ${preflight.data.department_name},`
                    : ""}{" "}
                  to grant it access, in your name. The run waits until they decide.
                </p>
              ) : null}
            </div>
          ) : undefined
        }
      >
        {!activeVersionId ? (
          <Empty what="deployed versions" hint="Deploy one above before starting a run." />
        ) : runs.isLoading ? (
          <Loading what="runs" />
        ) : runs.error ? (
          <Failure error={runs.error} what="runs" />
        ) : !runs.data?.length ? (
          <Empty what="runs" hint="Nobody has started one yet." />
        ) : (
          <ul data-testid="agent-runs" className="space-y-2">
            {runs.data.map((r) => (
              <li key={r.id} className="rounded border border-slate-200 bg-white p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                      r.status === "succeeded"
                        ? "bg-teal-100 text-teal-900"
                        : r.status === "failed"
                          ? "bg-red-200 text-red-900"
                          : r.status === "halted"
                            ? "bg-amber-100 text-amber-900"
                            : r.status === "awaiting_approval"
                              ? "bg-violet-100 text-violet-900"
                              : r.status === "awaiting_access"
                                ? "bg-orange-100 text-orange-900"
                                : r.status === "awaiting_activation"
                                  ? "bg-lime-100 text-lime-900"
                                  : "bg-sky-100 text-sky-900"
                    }`}
                  >
                    {r.status === "awaiting_approval"
                      ? "waiting for sign-off"
                      : r.status === "awaiting_access"
                        ? "waiting for data access"
                        : r.status === "awaiting_activation"
                          ? "access taking effect"
                          : r.status}
                  </span>
                  <span>v{r.agent_version}</span>
                  {r.execution_mode === "sandboxed" && (
                    <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-xs font-medium text-indigo-900">
                      sandboxed
                    </span>
                  )}
                  <span className="text-slate-600">{r.purpose}</span>
                  <span className="ml-auto text-xs text-slate-400">
                    {r.tool_calls} tool call{r.tool_calls === 1 ? "" : "s"}
                  </span>
                  {r.status === "awaiting_approval" &&
                    principal &&
                    (principal.id === r.requested_by ? (
                      <span className="text-xs text-slate-500">
                        Someone else has to approve this
                      </span>
                    ) : (
                      <button
                        type="button"
                        data-testid={`approve-run-${r.id}`}
                        disabled={approveRun.isPending}
                        onClick={() => approveRun.mutate({ run_id: r.id })}
                        className="rounded bg-indigo-500 hover:bg-indigo-800 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                      >
                        {approveRun.isPending ? "Approving" : "Approve"}
                      </button>
                    ))}
                </div>
                {r.approved_by && (
                  <p className="mt-1 text-xs text-slate-500">
                    Approved by {r.approved_by}
                    {r.approved_at ? ` on ${new Date(r.approved_at).toLocaleString()}` : ""}
                  </p>
                )}
                {r.status === "awaiting_access" && (
                  <p className="mt-1 text-xs text-orange-800">
                    Waiting for a custodian to grant this agent access to the
                    dataset. It starts on its own once they do.
                  </p>
                )}
                {r.status === "awaiting_activation" && (
                  <p
                    data-testid={`run-activation-${r.id}`}
                    className="mt-1 text-xs text-lime-800"
                  >
                    Approved; waiting for storage access to take effect. The
                    run continues on its own once it has.
                  </p>
                )}
                {r.execution_mode === "sandboxed" && r.findings?.length > 0 ? (
                  // A sandboxed agent's output is whatever JSON its own code
                  // printed: the fixed document/priority/reason shape below
                  // is specific to the platform's own built-in graph, and
                  // fitting arbitrary output into it would show blank
                  // fields rather than what the code actually reported.
                  <pre
                    data-testid={`run-findings-${r.id}`}
                    className="mt-2 overflow-x-auto rounded bg-slate-50 p-2 font-mono text-xs text-slate-700"
                  >
                    {JSON.stringify(r.findings, null, 2)}
                  </pre>
                ) : r.findings?.length > 0 && (
                  <ul data-testid={`run-findings-${r.id}`} className="mt-2 space-y-1">
                    {r.findings.map((f, i) => (
                      <li key={f.document ?? i} className="flex items-baseline gap-2 text-xs">
                        <span
                          className={`rounded px-1.5 py-0.5 font-medium ${
                            f.priority === "high"
                              ? "bg-red-100 text-red-900"
                              : f.priority === "low"
                                ? "bg-slate-100 text-slate-700"
                                : "bg-sky-100 text-sky-900"
                          }`}
                        >
                          {f.priority}
                        </span>
                        <span className="font-mono text-slate-500">{f.document}</span>
                        <span className="text-slate-600">{f.reason}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {r.halted_reason && <p className="mt-1 text-xs text-amber-800">{r.halted_reason}</p>}
                {r.error && <p className="mt-1 text-xs text-red-800">{r.error}</p>}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  );
}
