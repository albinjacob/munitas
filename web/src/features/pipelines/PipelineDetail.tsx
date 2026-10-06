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

import { ApiError } from "../../api/client";
import { CODE_ROLES, useHoldsRole } from "../../identity/mayDo";
import { useParams } from "react-router-dom";
import { useDecidePipelineVersion, usePipeline, type PipelineVersion } from "../../api/dag_pipelines";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { UploadPipelineVersion } from "./UploadPipelineVersion";

const APPROVER_ROLES = ["data_custodian"];

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
}

/** What a version that gives a step the recordings needs: a second person's approval, or the record that it has one. */
function Approval({ pipelineId, department, version }: { pipelineId: string; department: string | null; version: PipelineVersion }) {
  const mayApprove = useHoldsRole(APPROVER_ROLES);
  const approve = useDecidePipelineVersion(pipelineId, "approve");
  const withdraw = useDecidePipelineVersion(pipelineId, "withdraw-approval");
  const failed = [approve.error, withdraw.error].find(Boolean);
  const reading = version.steps_given_recordings;
  const writing = version.steps_writing_files;
  const named = (steps: string[]) => `${steps.length > 1 ? "steps" : "step"} ${steps.join(", ")}`;
  // What the version's steps are allowed to do, in the words of the person who has to approve it: the sound is read by code the platform did not write,
  // and what such code writes becomes a version that waits for review.
  const what = [
    reading.length ? `The ${named(reading)} ${reading.length > 1 ? "are" : "is"} given the recordings, so code that the platform did not write reads the sound.` : "",
    writing.length ? `The ${named(writing)} ${writing.length > 1 ? "write" : "writes"} files, which become a version that waits for a person's review.` : "",
  ].filter(Boolean).join(" ");
  return (
    <div data-testid={`pipeline-approval-${version.version}`} className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-slate-800">
      {version.approved ? (
        <>
          <p>
            Approved by {version.approved_by_label ?? "an approver"} on {when(version.approved_at)}. {what}
          </p>
          {mayApprove && (
            <button
              type="button"
              className="mt-2 rounded border border-slate-300 bg-white px-3 py-1 text-xs hover:bg-slate-50"
              disabled={withdraw.isPending}
              onClick={() => withdraw.mutate({ versionId: version.id, note: "" })}
            >
              Take the approval back
            </button>
          )}
        </>
      ) : (
        <>
          <p>
            {what} This version cannot run until an approver of {department ?? "the owning department"}, who is not{" "}
            {version.uploaded_by_label ?? "the person who uploaded it"}, approves it.
          </p>
          {mayApprove && (
            <button
              type="button"
              className="mt-2 rounded bg-slate-900 px-3 py-1 text-xs text-white hover:bg-slate-700"
              disabled={approve.isPending}
              onClick={() => approve.mutate({ versionId: version.id, note: "" })}
            >
              Approve this version
            </button>
          )}
        </>
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
        description={`Owned by ${p.department_name ?? "no department"}. Registered by ${p.registered_by_label ?? "unknown"}.`}
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
                  {v.uploaded_by_label && <span className="text-xs text-slate-500">uploaded by {v.uploaded_by_label}</span>}
                </div>
                <ol className="mt-2 space-y-1 text-xs text-slate-600">
                  {v.dag_config.steps.map((step) => (
                    <li key={step.name}>
                      <span className="font-medium text-slate-800">{step.name}</span>{" "}
                      <span className="text-slate-500">
                        ({step.kind}
                        {step.block ? `: ${step.block}` : ""}
                        {step.script ? `: ${step.script}` : ""}
                        {step.recordings ? ", given the recordings" : ""}
                        {step.writes_files ? ", writes files" : ""}
                        {step.memory_mb ? `, ${step.memory_mb} MB of memory` : ""}
                        {step.scratch_mb ? `, ${step.scratch_mb} MB of scratch space` : ""})
                      </span>
                      {step.depends_on?.length ? (
                        <span className="text-slate-400">
                          {" "}
                          &larr; {step.depends_on.join(", ")}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ol>
                {v.needs_approval && <Approval pipelineId={p.id} department={p.department_name} version={v} />}
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
