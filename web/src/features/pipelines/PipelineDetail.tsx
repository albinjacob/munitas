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

import { CODE_ROLES, useHoldsRole } from "../../identity/mayDo";
import { useParams } from "react-router-dom";
import { usePipeline } from "../../api/dag_pipelines";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { UploadPipelineVersion } from "./UploadPipelineVersion";

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
                </div>
                <ol className="mt-2 space-y-1 text-xs text-slate-600">
                  {v.dag_config.steps.map((step) => (
                    <li key={step.name}>
                      <span className="font-medium text-slate-800">{step.name}</span>{" "}
                      <span className="text-slate-500">
                        ({step.kind}
                        {step.block ? `: ${step.block}` : ""}
                        {step.script ? `: ${step.script}` : ""})
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
