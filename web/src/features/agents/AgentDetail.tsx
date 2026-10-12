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
import { Fragment, useState } from "react";
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
import { AgentOversightPanel } from "./AgentOversightPanel";
import { RunOutput } from "./RunOutput";
import { useAgentChangeApprovals, useRefuseRun } from "../../api/agentOversight";
import { Reasons } from "../locking/LockedColumns";
import { when } from "../../lib/when";

/** Refuse what a run produced while it waits for approval. Asks for the reason first, because a refusal is kept with who made it and why. */
function RefuseRun({ runId, agentId }: { runId: string; agentId: string }) {
  const refuse = useRefuseRun();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  if (!open) {
    return (
      <button type="button" data-testid={`refuse-run-${runId}`} onClick={() => setOpen(true)} className="btn btn-danger-outline btn-sm ml-2">
        Refuse
      </button>
    );
  }
  return (
    <span className="ml-2 inline-flex flex-col items-end gap-1 text-left" data-testid={`refuse-run-form-${runId}`}>
      <textarea rows={2} aria-label="Why it is refused" data-testid={`refuse-run-reason-${runId}`} value={reason} onChange={(e) => setReason(e.target.value)} className="w-56 rounded border border-slate-400 px-2 py-1 text-xs" />
      <span className="flex gap-2">
        <button type="button" data-testid={`refuse-run-send-${runId}`} disabled={refuse.isPending || !reason.trim()} onClick={() => refuse.mutate({ runId, reason, agentId }, { onSuccess: () => notify("The run was refused.") })} className="btn btn-danger btn-sm">
          Refuse this run
        </button>
        <button type="button" onClick={() => setOpen(false)} className="btn btn-secondary btn-sm">Cancel</button>
      </span>
    </span>
  );
}

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
  const changes = useAgentChangeApprovals(agentId);
  /** Why a version cannot be deployed yet because of its approval, said before the button is pressed; empty when nothing stands in the way. */
  const heldBy = (versionId: string): string => {
    if (!changes.data?.enforce) return "";
    const found = changes.data.approvals.find((x) => x.agent_version_id === versionId);
    if (!found) return "";
    if (found.state === "refused") return "This version widens what the agent can do, and the widening was refused.";
    return found.state === "waiting" ? `This version widens what the agent can do. It waits for ${found.waiting_for.join(" and ")}.` : "";
  };
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
  // Where the only way in would give the agent nothing it can read, asking is pointless and the platform refuses it: said before the click, like the dead end above.
  const cannotBeGranted = needsAccess ? preflight.data?.cannot_be_granted ?? [] : [];
  const deadEnd = noApprover || cannotBeGranted.length > 0;

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
        description="Each version is sealed on registration and cannot be changed afterwards. Its fingerprint identifies exactly what was registered."
      >
        {!a.versions.length ? (
          <Empty
            what="versions"
            hint="Upload a project below, or register one from its own checkout: python -m agent.register_version --agent-id "
          />
        ) : (
          <div className="overflow-x-auto rounded border border-slate-200 bg-white">
            {deploy.error ? <div role="alert" className="border-b border-slate-200 px-4 py-2 text-sm text-red-800"><Reasons error={deploy.error} fallback="The version could not be deployed." /></div> : null}
            <table data-testid="agent-versions" className="w-full text-sm">
              <thead className="whitespace-nowrap bg-slate-50 text-left text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-4 py-2">Version</th>
                  <th className="px-2">Mode</th>
                  <th className="px-2">Registered</th>
                  <th className="px-2">Fingerprints</th>
                  <th className="px-2">Source path</th>
                  <th className="px-2">What it may call</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {a.versions.map((v) => (
                  <tr
                    key={v.id}
                    data-testid={`agent-version-${v.version}`}
                    className="border-t border-slate-100 align-top transition-colors hover:bg-slate-50"
                  >
                    <td className="px-4 py-2">
                      <span className="font-medium">v{v.version}</span>
                      {v.id === activeVersionId && (
                        <span className="ml-2 rounded bg-teal-100 px-1.5 py-0.5 text-xs font-medium text-teal-900">
                          active
                        </span>
                      )}
                      <span className="block text-xs text-slate-500">{v.model_id}</span>
                    </td>
                    <td className="px-2 py-2">
                      <span
                        data-testid={`agent-version-mode-${v.version}`}
                        className={`block w-fit rounded px-1.5 py-0.5 text-xs font-medium ${
                          v.sandboxed ? "bg-indigo-100 text-indigo-900" : "bg-slate-100 text-slate-600"
                        }`}
                      >
                        {v.sandboxed ? `sandboxed · ${v.entrypoint}` : "declared only"}
                      </span>
                      {v.requested_hosts.length > 0 && (
                        <span
                          data-testid={`egress-state-${v.version}`}
                          className={`mt-1 block w-fit rounded px-1.5 py-0.5 text-xs font-medium ${
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
                    </td>
                    <td className="px-2 py-2 text-xs text-slate-600">
                      {when(v.created_at)}
                      <span className="block text-slate-500">by {v.registered_by_label ?? "unknown"}</span>
                    </td>
                    <td className="space-y-1 px-2 py-2 text-xs">
                      <div className="flex items-center gap-2"><span className="w-14 shrink-0 text-slate-500">Code</span><CopyableHash value={v.code_hash} /></div>
                      <div className="flex items-center gap-2"><span className="w-14 shrink-0 text-slate-500">Content</span><CopyableHash value={v.content_hash} /></div>
                      <div className="flex items-center gap-2"><span className="w-14 shrink-0 text-slate-500">Runtime</span><CopyableHash value={v.image_digest} /></div>
                    </td>
                    <td className="px-2 py-2">
                      <code className="break-all font-mono text-xs">{v.source_path}</code>
                    </td>
                    <td className="px-2 py-2 text-xs">
                      <span className="text-slate-500">Tools: </span>
                      {v.tool_scope.length ? (
                        <span className="font-mono">{v.tool_scope.join(", ")}</span>
                      ) : v.tools_declared ? (
                        <span data-testid={`tools-none-${v.version}`}>uses no tools</span>
                      ) : (
                        <span className="text-amber-800" data-testid={`tools-not-declared-${v.version}`}>not declared</span>
                      )}
                      {v.model_hosting && <span className="mt-1 block text-slate-500">Model runs {v.model_hosting === "outside" ? "outside the platform" : "in the platform"}</span>}
                      {v.sandboxed && (
                        <span className="mt-1 block text-slate-500" data-testid={`data-access-${v.version}`}>
                          {v.data_access === "copy" ? "Given a copy of the data a run is started on" : "Given no data"}
                        </span>
                      )}
                      {v.requested_hosts.length > 0 && (
                        <span className="mt-1 block">
                          <span className="text-slate-500">Hosts: </span>
                          <span className="font-mono">{v.requested_hosts.join(", ")}</span>
                          {v.egress_approval_id && (
                            <Link to={`/egress-approvals/${v.egress_approval_id}`} className="ml-2 text-blue-700 hover:underline">
                              view request
                            </Link>
                          )}
                        </span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-right">
                      {v.id !== activeVersionId && principal && (
                        <button
                          type="button"
                          data-testid={`deploy-version-${v.version}`}
                          disabled={deploy.isPending || v.egress_state === "pending" || v.egress_state === "refused" || Boolean(heldBy(v.id))}
                          title={
                            v.egress_state === "pending"
                              ? "This version's requested hosts have not been approved yet"
                              : v.egress_state === "refused"
                                ? "This version's requested hosts were refused"
                                : heldBy(v.id) || undefined
                          }
                          onClick={() =>
                            deploy.mutate(
                              { agent_version_id: v.id },
                              { onSuccess: () => notify(`v${v.version} deployed.`) },
                            )
                          }
                          className="btn btn-primary btn-sm"
                        >
                          {deploy.isPending ? "Deploying" : "Deploy"}
                        </button>
                      )}
                      {v.id !== activeVersionId && principal && heldBy(v.id) && (
                        <span data-testid={`held-${v.version}`} className="ml-auto mt-1 block max-w-[16rem] whitespace-normal text-right text-xs text-amber-800">{heldBy(v.id)}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
                  disabled={!runPurpose.trim() || startRun.isPending || deadEnd}
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
                  className="btn btn-primary shrink-0 whitespace-nowrap"
                >
                  {startRun.isPending
                    ? "Starting"
                    : cannotBeGranted.length > 0
                      ? "Nothing readable to grant"
                      : noApprover
                        ? "Nobody can grant this"
                        : needsAccess
                        ? "Request access and start"
                        : "Start a run"}
                </button>
              </div>
              {cannotBeGranted.length > 0 ? (
                <p
                  data-testid="access-notice"
                  className="max-w-md text-right text-xs text-amber-800"
                >
                  This agent cannot read that dataset on its own, and asking for access would not help: {cannotBeGranted.join("; ")}. Pick a different dataset.
                </p>
              ) : noApprover ? (
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
          <div className="overflow-x-auto rounded border border-slate-200 bg-white">
            <table data-testid="agent-runs" className="w-full text-sm">
              <thead className="whitespace-nowrap bg-slate-50 text-left text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-2">Version</th>
                  <th className="px-2">Purpose</th>
                  <th className="px-2">Tool calls</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {runs.data.map((r) => {
                  const hasFindings = r.findings?.length > 0;
                  const hasDetail =
                    Boolean(r.approved_by) ||
                    r.status === "awaiting_access" ||
                    r.status === "awaiting_activation" ||
                    hasFindings ||
                    Boolean(r.halted_reason) ||
                    Boolean(r.error);
                  return (
                    <Fragment key={r.id}>
                      <tr data-testid="agent-run" className="border-t border-slate-100 align-top transition-colors hover:bg-slate-50">
                        <td className="whitespace-nowrap px-4 py-2">
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
                        </td>
                        <td className="whitespace-nowrap px-2 py-2">
                          v{r.agent_version}
                          {r.execution_mode === "sandboxed" && (
                            <span className="ml-2 rounded bg-indigo-100 px-1.5 py-0.5 text-xs font-medium text-indigo-900">sandboxed</span>
                          )}
                        </td>
                        <td className="px-2 py-2 text-slate-600">{r.purpose}</td>
                        <td className="whitespace-nowrap px-2 py-2 text-xs text-slate-500">
                          {r.tool_calls} tool call{r.tool_calls === 1 ? "" : "s"}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-right">
                          {r.status === "awaiting_approval" &&
                            principal &&
                            (principal.id === r.requested_by ? (
                              <span className="text-xs text-slate-500">Someone else has to approve this</span>
                            ) : (
                              <button
                                type="button"
                                data-testid={`approve-run-${r.id}`}
                                disabled={approveRun.isPending}
                                onClick={() => approveRun.mutate({ run_id: r.id })}
                                className="btn btn-primary btn-sm"
                              >
                                {approveRun.isPending ? "Approving" : "Approve"}
                              </button>
                            ))}
                          {r.status === "awaiting_approval" && principal && principal.id !== r.requested_by && <RefuseRun runId={r.id} agentId={agentId!} />}
                        </td>
                      </tr>
                      {hasDetail && (
                        <tr>
                          <td colSpan={5} className="px-4 pb-3 pt-0">
                            {r.approved_by && (
                              <p className="text-xs text-slate-500">
                                Approved by {r.approved_by_label ?? r.approved_by}
                                {r.approved_at ? ` on ${when(r.approved_at)}` : ""}
                              </p>
                            )}
                            {r.status === "awaiting_access" && (
                              <p className="mt-1 text-xs text-orange-800">
                                Waiting for a custodian to grant this agent access to the dataset. It starts on its own once they do.
                              </p>
                            )}
                            {r.status === "awaiting_activation" && (
                              <p data-testid={`run-activation-${r.id}`} className="mt-1 text-xs text-lime-800">
                                Approved; waiting for storage access to take effect. The run continues on its own once it has.
                              </p>
                            )}
                            {r.execution_mode === "sandboxed" && hasFindings ? (
                              // A sandboxed agent's output is whatever JSON its own code
                              // printed: the fixed document/priority/reason shape below
                              // is specific to the platform's own built-in graph, and
                              // fitting arbitrary output into it would show blank
                              // fields rather than what the code actually reported.
                              <RunOutput run={r} agentId={agentId!} me={principal?.id} />
                            ) : (
                              hasFindings && (
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
                              )
                            )}
                            {r.halted_reason && <p className="mt-1 text-xs text-amber-800">{r.halted_reason}</p>}
                            {r.error && <p className="mt-1 text-xs text-red-800">{r.error}</p>}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      <AgentOversightPanel agentId={agentId!} />
    </>
  );
}
