/**
 * One pipeline, and its full version history.
 *
 * The version list is the point of this screen: each row is content-
 * addressed (config_hash) and sealed on creation, the same guarantee
 * AgentDetail.tsx shows for an agent's code. "Which steps actually ran"
 * should be answerable by reading a row here, not by asking whoever
 * uploaded it. Running a version happens elsewhere, from the sealed
 * dataset version's own page, not from here.
 */

import { useState } from "react";
import { ApiError } from "../../api/client";
import { CODE_ROLES, useHoldsRole } from "../../identity/mayDo";
import { useParams } from "react-router-dom";
import { useDecidePipelineVersion, usePipeline, type PipelineVersion } from "../../api/dag_pipelines";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { UploadPipelineVersion } from "./UploadPipelineVersion";
import { when } from "../../lib/when";
import { PipelineDag } from "./PipelineDag";

const APPROVER_ROLES = ["data_custodian"];


const STAGE_LABEL: Record<PipelineVersion["stage"], string> = { development: "In development", production: "In production", retired: "Retired" };
const STAGE_STYLE: Record<PipelineVersion["stage"], string> = {
  development: "bg-amber-100 text-amber-900",
  production: "bg-green-100 text-green-900",
  retired: "bg-slate-200 text-slate-700",
};

/** Where a version is in its life, and what moves it on. Moving it to production is a second person's decision when its steps read the files or write files. */
function Lifecycle({ pipelineId, department, version }: { pipelineId: string; department: string | null; version: PipelineVersion }) {
  const mayDecide = useHoldsRole(APPROVER_ROLES);
  const mayRetire = useHoldsRole([...APPROVER_ROLES, ...CODE_ROLES]);
  const promote = useDecidePipelineVersion(pipelineId, "promote");
  const demote = useDecidePipelineVersion(pipelineId, "demote");
  const retire = useDecidePipelineVersion(pipelineId, "retire");
  const [sure, setSure] = useState(false);
  const failed = [promote.error, demote.error, retire.error].find(Boolean);
  const reading = version.steps_given_files;
  const writing = version.steps_writing_files;
  const named = (steps: string[]) => `${steps.length > 1 ? "Steps" : "Step"} ${steps.join(", ")}`;
  // What the version's steps do, in the words of the person who decides whether it may be used: what they read and what they make.
  const made = "which become a new version that a reviewer checks before anyone can use it.";
  const same = reading.length > 0 && reading.join() === writing.join();
  const what = same
    ? `${named(reading)} ${reading.length > 1 ? "read the locked files and write" : "reads the locked files and writes"} files, ${made}`
    : [
        reading.length ? `${named(reading)} ${reading.length > 1 ? "read" : "reads"} the locked files.` : "",
        writing.length ? `${named(writing)} ${writing.length > 1 ? "write" : "writes"} files, ${made}` : "",
      ].filter(Boolean).join(" ");
  const box = version.stage === "development" ? "border-amber-200 bg-amber-50" : version.stage === "retired" ? "border-slate-200 bg-slate-50" : "border-green-200 bg-green-50";
  const secondary = "btn btn-secondary btn-sm mt-2";
  return (
    <div data-testid={`pipeline-lifecycle-${version.version}`} className={`mt-3 rounded border p-3 text-sm text-slate-800 ${box}`}>
      {version.stage === "retired" ? (
        <p>
          Retired by {version.retired_by_label ?? "a person"} on {when(version.retired_at)}
          {version.retired_note ? `: ${version.retired_note}` : "."} This version can no longer be run. Run a later version.
        </p>
      ) : version.stage === "development" ? (
        <>
          <p>
            {what} This version is in development, so it runs only on development data: files declared synthetic, or a sample drawn for you, which you ask for on the
            page of the files. For any other data, an approver of {department ?? "the owning department"} other than{" "}
            {version.uploaded_by_label ?? "the person who uploaded it"} checks it and moves it to production first.
          </p>
          {mayDecide && (
            <button
              type="button"
              className="btn btn-primary btn-sm mr-2 mt-2"
              disabled={promote.isPending}
              onClick={() => promote.mutate({ versionId: version.id, note: "" })}
            >
              Move to production
            </button>
          )}
        </>
      ) : (
        <>
          <p>
            {version.needs_promotion
              ? `Moved to production by ${version.promoted_by_label ?? "an approver"} on ${when(version.promoted_at)}. ${what}`
              : "Every step of this version is one the platform provides, so it is in production."}
          </p>
          {version.needs_promotion && mayDecide && (
            <button type="button" className={`${secondary} mr-2`} disabled={demote.isPending} onClick={() => demote.mutate({ versionId: version.id, note: "" })}>
              Return to development
            </button>
          )}
        </>
      )}
      {version.stage !== "retired" && mayRetire && !sure && (
        <button type="button" className={secondary} onClick={() => setSure(true)}>
          Retire this version
        </button>
      )}
      {version.stage !== "retired" && mayRetire && sure && (
        <span className="mt-2 inline-flex items-center gap-2 text-xs">
          Retiring is final. A retired version is replaced by a later one, never brought back.
          <button
            type="button"
            className="btn btn-danger-outline"
            disabled={retire.isPending}
            onClick={() => retire.mutate({ versionId: version.id, note: "" }, { onSettled: () => setSure(false) })}
          >
            Retire v{version.version}
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => setSure(false)}>
            Cancel
          </button>
        </span>
      )}
      {failed instanceof ApiError && (
        <p role="alert" className="mt-2 text-xs text-red-800">
          {failed.reasons.join(" ") || "That was not accepted."}
        </p>
      )}
    </div>
  );
}

export function PipelineDetail() {
  const { pipelineId } = useParams();
  const mayRegisterCode = useHoldsRole(CODE_ROLES);
  const pipeline = usePipeline(pipelineId);

  if (pipeline.isLoading) return <Loading what="this pipeline" />;
  if (pipeline.error) return <Failure error={pipeline.error} what="this pipeline" />;
  if (!pipeline.data) return null;

  const p = pipeline.data;

  return (
    <div className="space-y-6">
      <Section
        level="page"
        title={p.name}
        description={`Owned by ${p.department_name ?? "no department"}. Registered by ${p.registered_by_label ?? "unknown"}. A version in development runs only on development data, a version in production runs on any data the department allows, and a retired version has ended for good.`}
      >
        {!p.versions.length ? (
          <Empty
            what="versions"
            hint="Upload a config and its scripts below to seal the first one."
          />
        ) : (
          <ul data-testid="pipeline-versions" className="space-y-3">
            {p.versions.map((v) => (
              <li
                key={v.id}
                data-testid={`pipeline-version-${v.version}`}
                className="rounded border border-slate-200 bg-white p-4"
              >
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium">v{v.version}</span>
                  <span className="text-slate-400">&middot;</span>
                  <code className="font-mono text-xs text-slate-500">
                    {v.config_hash.slice(0, 12)}
                  </code>
                  <span data-testid={`pipeline-stage-${v.version}`} className={`rounded px-2 py-0.5 text-xs font-medium ${STAGE_STYLE[v.stage]}`}>
                    {STAGE_LABEL[v.stage]}
                  </span>
                  {v.uploaded_by_label && <span className="text-xs text-slate-500">uploaded by {v.uploaded_by_label}{v.created_at ? ` on ${when(v.created_at)}` : ""}</span>}
                </div>
                <PipelineDag steps={v.dag_config.steps} />
                <Lifecycle pipelineId={p.id} department={p.department_name} version={v} />
              </li>
            ))}
          </ul>
        )}
      </Section>

      {mayRegisterCode && (
        <Section
          title="Upload the next version"
          description="Sealed on arrival. There is no editing a version in place, only registering the next one."
        >
          <UploadPipelineVersion pipelineId={p.id} />
        </Section>
      )}
    </div>
  );
}
