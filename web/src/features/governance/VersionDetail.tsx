/**
 * One dataset version, in full.
 *
 * This is the load-bearing screen. Everything else in the console is navigation
 * to get here, because this is where the platform's central claim is either
 * visible or it is not: a class is a visibility mask, so promoting a version
 * appends a row and adds a grant, and moves no bytes.
 *
 * Three facts have to sit next to each other for that to land:
 *   - the class it was sealed at, which never changes
 *   - the class it currently holds, which comes from the transition log
 *   - the storage prefix, which is identical either side of a promotion
 *
 * Showing only the current class would hide the mechanism and leave the reader
 * assuming the data was copied somewhere.
 */

import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useIdentity } from "../../identity/IdentityContext";
import {
  useAccessPreview,
  useLeaseRequests,
  useLineage,
  useTransitions,
  useVersion,
} from "../../api/queries";
import { ApiError } from "../../api/client";
import { useStartDeidentification } from "../../api/pipeline";
import { usePipelines } from "../../api/dag_pipelines";
import { ClassBadge } from "../../components/ClassBadge";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { byOn, when } from "../../lib/when";
import { CopyableHash } from "../../components/CopyableHash";
import { AccessPanel } from "./AccessPanel";
import { LockedColumnsSection } from "../locking/LockedColumns";
import { LockedFilesSection } from "../locking/LockedFiles";
import { RecordFilesSection } from "../locking/RecordFiles";
import { FirstRows } from "./FirstRows";
import { useVersionHolds, useVersionTable } from "../../api/queries";
import { size } from "../../lib/size";
import { PORTS } from "../../config/ports";

const TEMPORAL = `http://localhost:${PORTS.temporal_ui}`;
const MLFLOW = `http://localhost:${PORTS.mlflow}`;

function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm">{children}</dd>
      {hint && <p className="mt-0.5 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

export function VersionDetail() {
  const { versionId } = useParams();
  const navigate = useNavigate();
  const { principal } = useIdentity();
  const version = useVersion(versionId);
  const transitions = useTransitions(versionId);
  const table = useVersionTable(versionId);
  const holds = useVersionHolds(versionId);
  const lineage = useLineage(versionId);
  const access = useAccessPreview({ versionIds: versionId ? [versionId] : [] });
  const myRequests = useLeaseRequests(undefined);
  const startPipeline = useStartDeidentification();
  // The full set, not one paginated page: this feeds a <select>, where
  // hiding anything past page 1 would make a registered pipeline silently
  // unreachable from here rather than merely unlisted on a list screen.
  const pipelines = usePipelines(1000, 0);
  // Which kind is offered is not decided here: this page has no way to know
  // whether the version was sealed with the files contract, and asking
  // would duplicate a check the endpoint already makes. Every kind is
  // offered; starting the wrong one for this version comes back as a
  // stated reason, the same courtesy-gate RegisterDataset.tsx's own button
  // already relies on.
  //
  // A registered pipeline's option value carries its version id too
  // ("dag:<pipeline_version_id>"), since starting one needs both the kind
  // and exactly which sealed version runs, not just the kind's name.
  const [pipelineChoice, setPipelineChoice] = useState("deidentify");

  if (version.isLoading) return <Loading what="this version" />;
  if (version.error) return <Failure error={version.error} what="this version" />;
  if (!version.data) return null;

  const v = version.data;
  const promoted = v.sealed_class !== v.current_class;

  const mine = (myRequests.data?.lease_requests ?? []).filter(
    (r) =>
      r.dataset_version_id === v.dataset_version_id &&
      r.principal === principal?.id,
  );

  // The policy's answer, not a copy of it.
  const mark = access.data?.versions[v.dataset_version_id];

  // The courtesy this button hides behind, same role RegisterDataset.tsx's
  // own start button checks. The endpoint is what actually refuses anyone
  // else; this only decides whether to show the control at all.
  const mayRunPipeline =
    Boolean(principal?.roles.includes("pipeline_operator")) &&
    Boolean(v.sealed) &&
    !v.reclaimed_at;

  // Storage paths, fingerprints and links into the workflow and evidence tools
  // are for the people who run this, not for the people using it.
  const technical =
    principal?.roles.some((r) =>
      ["platform_admin", "hybridops", "pipeline_operator"].includes(r),
    ) ?? false;

  return (
    <>
      <Section
        level="page"
        title={`${v.dataset_name ?? "Version"} v${v.version}`}
        description={promoted
          ? "This has been released for wider use. Nothing about it was rewritten to do that."
          : "This has never been released beyond the access level it was sealed at."}
      >
        {/*
          Said before anything else on the page, because everything below it
          describes data that is no longer there. Finding that out by clicking
          through to an empty download is how people conclude a platform is
          broken, and this is not a fault: it is the space being reclaimed.
        */}
        {v.reclaimed_at && (
          <p
            data-testid="reclaimed-notice"
            className="mb-4 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"
          >
            <strong className="font-semibold">The files are gone.</strong> They
            were deleted on {when(v.reclaimed_at)} to
            free storage. Everything recorded about this version stays: what it
            was, what it was made from, who was allowed to read it and who was
            refused. There is nothing left to download.
          </p>
        )}
        <dl className="grid grid-cols-1 gap-4 rounded border border-slate-200 bg-white p-4 md:grid-cols-3">
          <Field
            label="Access level when sealed"
            hint="Fixed when it was sealed, and never rewritten."
          >
            <span data-testid="sealed-class">
              <ClassBadge value={v.sealed_class} />
            </span>
          </Field>
          <Field
            label="Access level now"
            hint="Follows from every release decision made since."
          >
            <span data-testid="current-class">
              <ClassBadge value={v.current_class} />
            </span>
          </Field>
          <Field label="Sealed">
            <span data-testid="sealed-at">{v.created_at ? when(v.created_at) : "Not recorded"}</span>
          </Field>
          {v.brought_in && (
            <Field label="Brought in" hint="The file as it was sent, and who sent it.">
              <span data-testid="brought-in-by">
                {v.brought_in.file_name}
                {v.brought_in.bytes ? ` (${size(v.brought_in.bytes)})` : ""} {byOn(v.brought_in.who, v.brought_in.at)}
              </span>
            </Field>
          )}
          {/*
            Where the data physically sits, and the hash that fixes its
            contents, are shown to the people who run the platform. For everyone
            else they are noise: a researcher deciding whether to ask for this
            data does not need a storage path, and putting one on screen invites
            them to think it means something to them.
          */}
          {technical && (
            <>
              <Field
                label="Where it is stored"
                hint={promoted
                  ? "Unchanged by the release. The data was never copied."
                  : "Identifies the dataset and version, never the access level."}
              >
                <code data-testid="storage-prefix" className="break-all font-mono text-xs">
                  {v.storage_prefix}
                </code>
              </Field>
              <Field label="Fingerprint" hint="Changes if the contents change.">
                {v.content_hash ? <CopyableHash value={v.content_hash} /> : <span className="text-slate-500">Not recorded</span>}
              </Field>
              {v.brought_in?.sha256 && (
                <Field label="Fingerprint of the file sent" hint="Of the bytes as they arrived, so a later copy can be shown to be the same file.">
                  <CopyableHash value={v.brought_in.sha256} />
                </Field>
              )}
            </>
          )}
        </dl>
      </Section>

      <Section
        title="What it holds"
        description={
          v.reclaimed_at
            ? "What this version held before its files were deleted."
            : "What the platform found in this version's files when it was sealed. Nothing here was declared by whoever uploaded it."
        }
      >
        {holds.isLoading ? (
          <Loading what="what this version holds" />
        ) : holds.error ? (
          <Failure error={holds.error} what="what this version holds" />
        ) : !holds.data || holds.data.by_format.length === 0 ? (
          <Empty what="files" hint="Nothing is stored in this version." />
        ) : (
          <div data-testid="version-holds" className="rounded border border-slate-200 bg-white p-4">
            <dl className="grid grid-cols-2 gap-4 md:grid-cols-5">
              <Field label="Kind">
                <span data-testid="holds-kind">{holds.data.kinds.map((k) => k.charAt(0).toUpperCase() + k.slice(1)).join(" + ")}</span>
                {holds.data.kinds.length > 1 && <span className="block text-xs text-slate-500">Multimodal</span>}
              </Field>
              <Field label="Structure">
                <span data-testid="holds-structure">{holds.data.structure ? holds.data.structure.charAt(0).toUpperCase() + holds.data.structure.slice(1) : ""}</span>
                {holds.data.structure === "mixed" && (
                  <span className="block text-xs text-slate-500">
                    {holds.data.structures.map((x) => x.charAt(0).toUpperCase() + x.slice(1)).join(", ")}
                  </span>
                )}
              </Field>
              <Field label={holds.data.formats.length > 1 ? "Formats" : "Format"}>
                <span data-testid="holds-format-list">{holds.data.formats.map((f) => (f === "table" ? "Table" : f.toUpperCase())).join(", ")}</span>
              </Field>
              {holds.data.table ? (
                <Field label="Rows">
                  <span data-testid="holds-rows" className="tabular-nums">
                    {holds.data.table.present ? (holds.data.table.rows ?? 0).toLocaleString() : "No table was written"}
                  </span>
                </Field>
              ) : (
                <>
                  <Field label="Files">
                    <span data-testid="holds-files" className="tabular-nums">{holds.data.files.toLocaleString()}</span>
                  </Field>
                  <Field label="Size">
                    <span data-testid="holds-size" className="tabular-nums">{size(holds.data.bytes)}</span>
                  </Field>
                </>
              )}
            </dl>
            {holds.data.by_format.length > 1 && (
            <table className="mt-4 w-full text-sm" data-testid="holds-formats">
              <thead className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
                <tr>
                  <th className="py-2 pr-4 font-medium">Format</th>
                  <th className="py-2 pr-4 font-medium">Kind</th>
                  <th className="py-2 pr-4 font-medium">Structure</th>
                  <th className="py-2 pr-4 text-right font-medium">Files</th>
                  <th className="py-2 text-right font-medium">Size</th>
                </tr>
              </thead>
              <tbody>
                {holds.data.by_format.map((row) => (
                  <tr key={`${row.kind}-${row.format}`} className="border-b border-slate-100">
                    <td className="py-1.5 pr-4 font-medium">{row.format === "table" ? "Table" : row.format.toUpperCase()}</td>
                    <td className="py-1.5 pr-4 text-slate-700">{row.kind.charAt(0).toUpperCase() + row.kind.slice(1)}</td>
                    <td className="py-1.5 pr-4 text-slate-700">{row.structure.charAt(0).toUpperCase() + row.structure.slice(1)}</td>
                    <td className="py-1.5 pr-4 text-right tabular-nums">{row.files.toLocaleString()}</td>
                    <td className="py-1.5 text-right tabular-nums">{size(row.bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            )}
            {(holds.data.read_from === "name" || holds.data.read_from === "mixed") && (
              <p data-testid="holds-basis" className="mt-3 text-xs text-slate-500">
                {holds.data.read_from === "name"
                  ? "These were worked out from the file names, because the files could not be read: they are locked to the person each is about, or the version was sealed before the platform read its files."
                  : "Some of these were worked out from the file names, because those files could not be read: they are locked to the person each is about, or the version was sealed before the platform read its files."}
              </p>
            )}
          </div>
        )}
      </Section>

      <Section
        title="Stored as a table"
        description="Whether the rows of this version are also written as a table that a standard tool can read, and that a legal export can filter to the rows for named people."
      >
        {table.isLoading ? (
          <Loading what="the table copy" />
        ) : table.error ? (
          <Failure error={table.error} what="whether this version is stored as a table" />
        ) : table.data!.projected ? (
          <div data-testid="table-copy" className="rounded border border-slate-200 bg-white p-4 text-sm">
            <span className="rounded bg-green-100 px-2 py-0.5 text-xs font-medium text-green-900">Yes, as a table</span>
            <p className="mt-2 text-slate-700">
              The table <code className="font-mono text-xs">{table.data!.table}</code> holds{" "}
              <strong>{table.data!.rows} rows</strong>, written when this version was sealed (snapshot {table.data!.snapshot_id}). A
              standard tool such as DuckDB can read it, and a legal export can hand over only the rows for named people.
            </p>
            <FirstRows versionId={v.dataset_version_id} />
          </div>
        ) : (
          <div data-testid="table-copy" className="rounded border border-slate-200 bg-white p-4 text-sm">
            <span
              className={`rounded px-2 py-0.5 text-xs font-medium ${
                table.data!.outcome === "failed" ? "bg-red-100 text-red-900" :
                table.data!.outcome === "not_requested" ? "bg-slate-200 text-slate-800" : "bg-amber-100 text-amber-900"
              }`}
            >
              {table.data!.outcome === "not_requested" ? "Files, not a table" : "No table copy"}
            </span>
            <p className="mt-2 text-slate-700">{table.data!.reason}</p>
            <p className="mt-1 text-xs text-slate-500">
              A legal export can hand this version over whole, but cannot filter it to the rows for named people.
            </p>
          </div>
        )}
      </Section>

      {mayRunPipeline && (
        <Section
          title="Run a pipeline"
          description="Starts a worker reading this version. Nothing is released until a reviewer decides that separately."
        >
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-xs uppercase tracking-wide text-slate-500">
                Pipeline
              </span>
              <select
                data-testid="pipeline-kind"
                value={pipelineChoice}
                onChange={(event) => setPipelineChoice(event.target.value)}
                className="rounded border border-slate-300 px-2 py-1"
              >
                <option value="deidentify">De-identify these files</option>
                <option value="count_records">
                  Count records (starter pipeline)
                </option>
                {(pipelines.data?.pipelines ?? []).flatMap((p) =>
                  p.latest_version_id
                    ? [
                        <option key={p.id} value={`dag:${p.latest_version_id}`}>
                          {p.name} (v{p.latest_version})
                        </option>,
                      ]
                    : [],
                )}
              </select>
            </label>
            <button
              type="button"
              data-testid="version-pipeline-start"
              disabled={startPipeline.isPending}
              className="rounded bg-sky-700 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              onClick={() => {
                const [kind, pipelineVersionId] = pipelineChoice.startsWith("dag:")
                  ? pipelineChoice.split(":")
                  : [pipelineChoice, undefined];
                startPipeline.mutate(
                  {
                    datasetId: v.dataset_id,
                    versionId: v.dataset_version_id,
                    pipelineKind: kind,
                    pipelineVersionId,
                  },
                  {
                    onSuccess: (started) =>
                      navigate(`/pipeline-runs/${started.pipeline_run_id}`),
                  },
                );
              }}
            >
              {startPipeline.isPending ? "Starting" : "Start"}
            </button>
            <a
              href="/starter-pipeline/starter_pipeline.py"
              download
              className="text-xs text-sky-700 underline"
            >
              Download starter pipeline
            </a>
          </div>
          {startPipeline.error && (
            <div
              data-testid="version-pipeline-refused"
              className="mt-3 rounded border border-red-200 bg-red-50 p-3 text-xs text-red-800"
              role="alert"
            >
              <p className="font-medium">Nothing was started. Fix these first:</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">
                {(startPipeline.error instanceof ApiError &&
                startPipeline.error.reasons.length
                  ? startPipeline.error.reasons
                  : [
                      startPipeline.error instanceof Error
                        ? startPipeline.error.message
                        : "The run could not be started.",
                    ]
                ).map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
          )}
        </Section>
      )}

      {principal && (
        <Section title="Your access">
          <AccessPanel
            versionId={v.dataset_version_id}
            principalId={principal.id}
            mark={mark}
            markFailed={Boolean(access.error)}
            existing={mine}
          />
        </Section>
      )}

      <LockedColumnsSection versionId={v.dataset_version_id} />
      <LockedFilesSection versionId={v.dataset_version_id} />
      <RecordFilesSection versionId={v.dataset_version_id} />

      <Section
        title="Release history"
        description="Every time this was released more widely, who decided, and what they relied on."
      >
        {transitions.isLoading ? (
          <Loading what="the release history" />
        ) : transitions.error ? (
          <Failure error={transitions.error} what="the release history" />
        ) : !transitions.data?.length ? (
          <Empty
            what="releases"
            hint="Nobody has widened access to this."
          />
        ) : (
          <ol className="space-y-3">
            {transitions.data.map((t) => {
              const run = (t.gate_evidence as { mlflow_run?: string })?.mlflow_run;
              return (
                <li
                  key={t.id}
                  className="rounded border border-slate-200 bg-white p-4"
                >
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <ClassBadge value={t.from_class} />
                    <span className="text-slate-400">&rarr;</span>
                    <ClassBadge value={t.to_class} />
                    <span className="ml-2 text-slate-600">
                      by {t.decided_by}{" "}
                      <span className="text-slate-400">({t.decided_by_kind})</span>
                    </span>
                    <span className="ml-auto text-xs text-slate-400">{when(t.at)}</span>
                  </div>
                  <div className="mt-3">
                    <p className="text-xs uppercase tracking-wide text-slate-500">
                      Evidence
                    </p>
                    <pre className="mt-1 overflow-x-auto rounded bg-slate-50 p-2 text-xs">
                      {JSON.stringify(t.gate_evidence, null, 2)}
                    </pre>
                    {run && (
                      <a
                        href={`${MLFLOW}/#/experiments/0/runs/${run}`}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-2 inline-block text-sm text-sky-700 underline"
                      >
                        Open the evidence behind this decision
                      </a>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </Section>

      <Section
        title="Where this came from"
        description="What produced it, from what, and who ran it."
      >
        {lineage.isLoading ? (
          <Loading what="its origin" />
        ) : lineage.error ? (
          <Failure error={lineage.error} what="its origin" />
        ) : !lineage.data ? null : (
          <dl className="grid grid-cols-1 gap-4 rounded border border-slate-200 bg-white p-4 md:grid-cols-2">
            {lineage.data.action_name ? (
              <>
                <Field label="Made by the step">{lineage.data.action_name}</Field>
                <Field label="Run by">{lineage.data.operator ?? "Not recorded"}</Field>
                {lineage.data.started_at && (
                  <Field label="Started">{when(lineage.data.started_at)}</Field>
                )}
                {lineage.data.ended_at && <Field label="Finished">{when(lineage.data.ended_at)}</Field>}
              </>
            ) : (
              <Field label="How it came to be here">
                Brought in directly. A person uploaded it, so no pipeline run made it.
              </Field>
            )}
            {/*
              Which build produced this matters when you are debugging a bad
              run. It means nothing to somebody deciding whether they want the
              data.
            */}
            {technical && (
              <>
                <Field label="Code version" hint="Exactly which build ran.">
                  <CopyableHash value={lineage.data.code_hash ?? "Not recorded"} />
                </Field>
                <Field label="Environment">
                  <CopyableHash value={lineage.data.image_digest ?? "Not recorded"} />
                </Field>
              </>
            )}
            <Field label="Made from">
              {lineage.data.input_versions?.length ? (
                <ul className="space-y-0.5">
                  {lineage.data.input_versions.map((id) => (
                    <li key={id}>
                      <Link to={`/versions/${id}`} className="text-sky-700 underline">
                        <code className="break-all font-mono text-xs">{id}</code>
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="text-slate-500">Nothing: this version is where the trail starts.</span>
              )}
            </Field>
            {technical && (
              <Field label="Workflow engine">
                <a
                  href={`${TEMPORAL}/namespaces/default/workflows`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-sky-700 underline"
                >
                  Open the workflow engine (all runs)
                </a>
              </Field>
            )}
          </dl>
        )}
      </Section>
    </>
  );
}
