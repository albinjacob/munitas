/**
 * The de-identification gate: what the run measured, and what to do about it.
 *
 * The pipeline used to promote on its own. Promotion is the act that widens
 * who can see clinical data and it cannot be undone, so it now waits for
 * somebody qualified to look at the evidence.
 *
 * The evidence is the point of the screen. A count of leaks cannot tell a
 * partial postcode from a patient's full name beside their address, and those
 * are not the same decision, so the identifiers are shown rather than
 * summarised. That means this page displays unredacted personal data on
 * purpose, which is why the queue shows only a count and the detail is a
 * separate, deliberate click.
 */

import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { Pagination } from "../../components/Pagination";
import { notify } from "../../components/toast";
import { useIdentity } from "../../identity/IdentityContext";
import {
  useDecideGate,
  useGateDecision,
  useGateDecisions,
  useReview,
  type GateDetail,
  type GateLeak,
  type GateRun,
  type GateRow,
} from "../../api/gates";
import { CLASS_LABEL, type VisibilityClass } from "../../api/types";
import { joined, joinedMax } from "../../lib/joined";
import { when } from "../../lib/when";
import { sentence, words } from "../../lib/text";
import { RecordFilesSection } from "../locking/RecordFiles";
import { ChooseStandard, JudgedBy, KindResults } from "./StandardJudgement";

/** The class in the middle of a sentence, where a capital would read as a shout. */
const classWord = (c: VisibilityClass) => CLASS_LABEL[c].toLowerCase();

const STATE_STYLE: Record<GateRow["state"], string> = {
  pending: "bg-amber-100 text-amber-900",
  promoted: "bg-green-100 text-green-900",
  refused: "bg-red-100 text-red-900",
};

const STATE_WORD: Record<GateRow["state"], string> = {
  pending: "Waiting for a decision",
  promoted: "Released",
  refused: "Held back",
};

function StateBadge({ state }: { state: GateRow["state"] }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium ${STATE_STYLE[state]}`}
      data-testid="gate-state"
    >
      {STATE_WORD[state]}
    </span>
  );
}

function percent(value: number | string | undefined): string {
  if (value === undefined) return "not measured";
  const n = typeof value === "string" ? Number(value) : value;
  return `${(n * 100).toFixed(1)}%`;
}

const PAGE_SIZE = 15;

/** A note that only restates that nothing was measured, which the page has already said. */
const SAYS_NOTHING_WAS_MEASURED = /^\s*(nothing|no automatic measurement|no measurement|not measured)\b[^.]{0,40}\bmeasur\w*\b[^.]{0,20}\.?\s*$|^\s*nothing measured( it)?\.?\s*$|^\s*not measured\.?\s*$/i;


/** The queue. */
export function GateQueue() {
  const { principal } = useIdentity();
  const [page, setPage] = useState(1);
  const decisions = useGateDecisions(
    principal?.tenant_id, principal?.id, PAGE_SIZE, (page - 1) * PAGE_SIZE,
  );
  // Counted across the whole queue, not just this page -- same technique
  // as EgressApprovals.tsx's own queue, and for the same reason: pending
  // work is not guaranteed to sort onto page 1 once there is history.
  const pendingCount = useGateDecisions(
    principal?.tenant_id, principal?.id, 1, 0, "pending",
  );
  // What this person started and is waiting on. Read-only: they cannot decide their own runs.
  const mine = useGateDecisions(
    principal?.tenant_id, undefined, 50, 0, undefined, principal?.id,
  );

  if (!principal) return <Loading what="your identity" />;
  if (decisions.isLoading) return <Loading what="gate decisions" />;
  if (decisions.error)
    return <Failure error={decisions.error} what="gate decisions" />;

  const rows = decisions.data?.gate_decisions ?? [];
  const waiting = pendingCount.data?.total ?? 0;

  return (
    <div>
      <Section
        level="page"
        title="De-identification results"
        description="Each pipeline run measures how much identifying detail it removed from the data, and how much it left in. The data stays restricted until a reviewer decides, on this page, that it is safe to release to more people."
      >
        {rows.length === 0 ? (
          <Empty
            what="results to review"
            hint="A result appears here when a de-identification run finishes. Results of runs you started are under Your runs, because somebody else has to decide them."
          />
        ) : (
          <>
            <p className="mb-3 text-sm text-slate-600">
              {waiting === 0
                ? "Nothing is waiting on you. Earlier decisions are kept below."
                : `${waiting} waiting on a decision.`}
            </p>
            <div className="data-card"><table className="data-table" data-testid="gate-queue">
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th>Access change</th>
                  <th>Measurement</th>
                  <th>State</th>
                  <th>Started</th>
                  <th>Started by</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className="border-b border-slate-100">
                    <td>
                      <Link
                        className="font-medium text-blue-700 hover:underline"
                        to={`/gates/${row.id}`}
                      >
                        {row.dataset_name} v{row.version}
                      </Link>
                    </td>
                    <td className="whitespace-nowrap">
                      {CLASS_LABEL[row.visibility_class]} &rarr; {CLASS_LABEL[row.to_class]}
                    </td>
                    <td>
                      {row.recommendation === "not_measured" || row.metrics?.recall_effective === undefined ? (
                        <span className="text-slate-500">Not measured</span>
                      ) : (
                        `${percent(row.metrics?.recall_effective)} removed, ${row.leak_count === 0 ? "none left in" : `${row.leak_count} left in`}`
                      )}
                    </td>
                    <td>
                      <StateBadge state={row.state} />
                      {row.decided_at && <div className="mt-1 text-xs text-slate-500" data-testid="gate-decided-at">{when(row.decided_at)}</div>}
                    </td>
                    <td className="whitespace-nowrap text-slate-600" data-testid="gate-started">
                      {when(row.created_at)}
                    </td>
                    <td className="text-slate-600">
                      {row.triggered_by_label ?? "a schedule"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table></div>
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={decisions.data?.total ?? 0}
              onPageChange={setPage}
            />
          </>
        )}
      </Section>

      <div className="mt-8" data-testid="my-runs">
        <Section
          title="Your runs"
          description="Results of runs you started. You cannot decide these yourself, so each one says who it is waiting on, or who decided it. The people it waits on hold the de-identification reviewer role, other than you."
        >
          {mine.isLoading ? (
            <Loading what="your runs" />
          ) : mine.error ? (
            <Failure error={mine.error} what="your runs" />
          ) : (mine.data?.gate_decisions ?? []).length === 0 ? (
            <Empty what="runs of yours" hint="When you start a de-identification run, its result is listed here until somebody decides it." />
          ) : (
            <div className="data-card"><table className="data-table" data-testid="my-runs-table">
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th>Access change</th>
                  <th>State</th>
                  <th>Started</th>
                  <th>Decision</th>
                </tr>
              </thead>
              <tbody>
                {(mine.data?.gate_decisions ?? []).map((row) => (
                  <tr key={row.id} className="border-b border-slate-100" data-testid={`my-run-${row.id}`}>
                    <td>
                      <Link className="font-medium text-blue-700 hover:underline" to={`/gates/${row.id}`}>
                        {row.dataset_name} v{row.version}
                      </Link>
                    </td>
                    <td className="whitespace-nowrap">
                      {CLASS_LABEL[row.visibility_class]} &rarr; {CLASS_LABEL[row.to_class]}
                    </td>
                    <td>
                      <StateBadge state={row.state} />
                    </td>
                    <td className="whitespace-nowrap text-slate-600">{when(row.created_at)}</td>
                    <td className="text-slate-700">
                      <WaitingOn row={row} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
          {(mine.data?.total ?? 0) > (mine.data?.gate_decisions.length ?? 0) && (
            <p className="mt-2 text-xs text-slate-500" data-testid="my-runs-more">
              Showing the {mine.data?.gate_decisions.length} most recent of {mine.data?.total} runs.
            </p>
          )}
        </Section>
      </div>
    </div>
  );
}

/** Who a result waits on, or who decided it. The people are those who hold the reviewing role now, so the line changes when roles do. */
function WaitingOn({ row }: { row: Pick<GateRow, "state" | "waiting_on" | "decided_by_label" | "decided_by" | "decided_at"> }) {
  if (row.state !== "pending") {
    return (
      <span>
        {row.state === "promoted" ? "Released" : "Held back"} by {row.decided_by_label ?? row.decided_by}
        {row.decided_at ? ` on ${when(row.decided_at)}` : ""}
      </span>
    );
  }
  if (row.waiting_on.length === 0) {
    return (
      <span data-testid="waiting-on-nobody" className="text-amber-800">
        Nobody holds the de-identification reviewer role right now, so nobody can decide it. Ask an administrator to give the role to somebody.
      </span>
    );
  }
  return <span data-testid="waiting-on">Waiting on {joinedMax(row.waiting_on.map((p) => p.label), 3)}</span>;
}

function LeakTable({ leaks, none }: { leaks: GateLeak[]; none?: string }) {
  if (leaks.length === 0) {
    return (
      <Empty
        what="identifying detail left in the clear"
        hint={none ?? "The run removed everything the answer key contains."}
      />
    );
  }
  return (
    <div className="data-card"><table className="data-table" data-testid="gate-leaks">
      <thead>
        <tr>
          <th>Kind</th>
          <th>What it was</th>
          <th>What survived</th>
          <th>Removed</th>
          <th>Where</th>
        </tr>
      </thead>
      <tbody>
        {leaks.map((leak) => (
          <tr
            key={`${leak.record_id}-${leak.span_start}`}
            className="border-b border-slate-100"
          >
            <td>
              {leak.entity}
              {leak.direct && (
                <span className="ml-2 rounded bg-red-100 px-1.5 py-0.5 text-xs font-medium text-red-900">
                  names a person directly
                </span>
              )}
            </td>
            <td className="font-mono text-xs">{leak.identifier}</td>
            <td className="font-mono text-xs text-red-800">
              {leak.left_in_the_clear || "the whole thing"}
            </td>
            <td>{percent(leak.coverage)}</td>
            <td className="text-slate-500">
              {leak.record_id}, characters {leak.span_start} to {leak.span_end}
            </td>
          </tr>
        ))}
      </tbody>
    </table></div>
  );
}

/** The files the run made, with what the platform checked on each, and the files to open to judge the result. */
function ReviewSection({ detail }: { detail: GateDetail }) {
  const review = useReview(detail.id);
  if (review.isLoading || review.error || !review.data || review.data.files.length === 0) return null;
  const r = review.data;
  const failures = r.files.flatMap((f) => f.checks.filter((c) => !c.ok).map((c) => ({ file: f.record_id, check: c })));
  return (
    <>
      <Section
        title="What the platform checked"
        description="Before anyone looked, the platform checked every file the run made: that each is what its name says, that none is empty or far larger than the file it was made from, and that none is a copy of that original."
      >
        <div data-testid="review-checks" className="space-y-2 text-sm">
          <p className="text-slate-900">
            {r.files.length} {r.files.length === 1 ? "file" : "files"}.{" "}
            {failures.length === 0 ? "Every file passed every check." : `${failures.length} ${failures.length === 1 ? "check" : "checks"} failed.`}
          </p>
          {failures.length > 0 && (
            <ul className="space-y-1" data-testid="review-failures">
              {failures.map((f) => (
                <li key={`${f.file}-${f.check.check}`} className="rounded border border-red-200 bg-red-50 p-2 text-red-900">
                  <span className="font-mono text-xs">{f.file}</span> {f.check.detail}.
                  {f.check.blocking && <span className="ml-2 rounded bg-red-100 px-1.5 py-0.5 text-xs font-medium">stops a release</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      </Section>
      {r.may_review && (
        <RecordFilesSection
          versionId={r.version_id}
          title="Look at the files yourself"
          description={`These are the files the run made, not the files it was given. Open some of them to judge the result: you may open up to ${r.limit}, until it is decided. A file marked Locked is stored scrambled under the key of the person it is about, and the platform unscrambles it for you here.`}
        />
      )}
    </>
  );
}

function Decide({ detail, standardId }: { detail: GateDetail; standardId: string | null }) {
  const { principal } = useIdentity();
  const decide = useDecideGate(detail.id);
  // Asked under the standard chosen above, so what stops a release is what the decision itself will be checked against.
  const review = useReview(detail.id, standardId);
  const [reason, setReason] = useState("");
  // What stops a release now, from the platform: the same answer the decision itself is checked against, so this page cannot offer what is refused.
  const stoppers = detail.state === "pending" ? (review.data?.blockers ?? []) : [];

  const startedIt = detail.triggered_by === principal?.id;
  // What the platform says, not a guess from the roles on this screen: it is the same rule the decision itself is checked against.
  const names = detail.waiting_on.length ? joined(detail.waiting_on.map((p) => p.label)) : null;
  const waitingLine = names
    ? ` It is waiting on ${names}.`
    : " Nobody holds the de-identification reviewer role right now, so ask an administrator to give it to somebody.";
  const blocked =
    detail.state !== "pending" || detail.can_decide
      ? null
      : startedIt
        ? `You started this run, so somebody else has to decide it.${waitingLine}`
        : `Deciding a de-identification result is not part of your role.${waitingLine}`;

  if (detail.state !== "pending") {
    return (
      <div className="rounded border border-slate-200 bg-slate-50 p-4 text-sm">
        <p className="font-medium text-slate-900">
          {detail.state === "promoted"
            ? "Released by "
            : "Held back by "}
          {detail.decided_by_label ?? detail.decided_by}
          {detail.decided_at ? ` on ${when(detail.decided_at)}` : ""}.
        </p>
        {detail.decision_reason && (
          <p className="mt-1 text-slate-700" data-testid="gate-decision-reason">
            <span className="text-slate-500">Reason: </span>
            {sentence(detail.decision_reason)}
          </p>
        )}
        {review.data?.recorded && (
          <p className="mt-1 text-slate-600" data-testid="gate-rested">
            It rested on {review.data.recorded.files} {review.data.recorded.files === 1 ? "file" : "files"} checked by the platform and{" "}
            {review.data.recorded.opened.length} opened by the reviewer.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="rounded border border-slate-200 p-4">
      <label className="block text-sm font-medium text-slate-900" htmlFor="reason">
        Your reason
      </label>
      <p className="mt-0.5 text-sm text-slate-500">
        Write why before you decide. It is kept with the decision, because whoever reads it later will not have the result in front of them.
      </p>
      <textarea
        id="reason"
        data-testid="gate-reason"
        className="mt-2 w-full rounded border border-slate-300 p-2 text-sm"
        rows={3}
        value={reason}
        disabled={Boolean(blocked)}
        onChange={(e) => setReason(e.target.value)}
      />
      {blocked && (
        <p className="mt-2 text-sm text-amber-800" data-testid="gate-blocked">
          {blocked}
        </p>
      )}
      {!blocked && stoppers.length > 0 && (
        <div className="mt-2 text-sm text-amber-800" data-testid="gate-stoppers">
          <p className="font-medium">You cannot release this yet:</p>
          <ul className="ml-5 list-disc">
            {stoppers.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
          <p className="mt-1">You can still hold it back.</p>
        </div>
      )}
      {!blocked && stoppers.length === 0 && !reason.trim() && detail.state === "pending" && (
        <p className="mt-2 text-sm text-slate-600" data-testid="gate-needs-reason">
          Write your reason above to enable the two buttons.
        </p>
      )}
      {decide.error && <div className="mt-3"><Failure error={decide.error} what="the decision" /></div>}
      <div className="mt-3 flex gap-3">
        <button
          type="button"
          data-testid="gate-promote"
          disabled={Boolean(blocked) || stoppers.length > 0 || !reason.trim() || decide.isPending}
          onClick={() =>
            decide.mutate(
              { outcome: "promote", reason, standardId: standardId },
              { onSuccess: () => notify(`Released to ${classWord(detail.to_class)}.`) },
            )
          }
          className="btn btn-primary"
        >
          {decide.isPending ? "Releasing" : `Release to ${classWord(detail.to_class)}`}
        </button>
        <button
          type="button"
          data-testid="gate-refuse"
          disabled={Boolean(blocked) || !reason.trim() || decide.isPending}
          onClick={() =>
            decide.mutate(
              { outcome: "refuse", reason, standardId: standardId },
              { onSuccess: () => notify("Held back.") },
            )
          }
          className="btn btn-danger-outline"
        >
          {decide.isPending ? "Holding back" : "Hold it back"}
        </button>
      </div>
    </div>
  );
}

/** How long something took, in the units a person would say. */
function took(start: string, end: string | null): string {
  if (!end) return "still running";
  const seconds = Math.max(0, (new Date(end).getTime() - new Date(start).getTime()) / 1000);
  if (seconds < 1) return "under a second";
  if (seconds < 90) return `${Math.round(seconds)} seconds`;
  const minutes = seconds / 60;
  return minutes < 90 ? `${Math.round(minutes)} minutes` : `${(minutes / 60).toFixed(1)} hours`;
}

/** Where a result came from: the pipeline and its version, the run, who started it, and what it was given. */
function RunFacts({ run }: { run: GateRun }) {
  return (
    <dl className="grid grid-cols-1 gap-x-8 gap-y-3 text-sm md:grid-cols-2" data-testid="gate-run">
      <div>
        <dt className="text-slate-500">Pipeline</dt>
        <dd className="mt-0.5">
          {run.pipeline_id && run.pipeline_name ? (
            <>
              <Link className="font-medium text-blue-700 hover:underline" to={`/pipelines/${run.pipeline_id}`}>
                {run.pipeline_name}, version {run.pipeline_version}
              </Link>
              <div className="text-xs text-slate-500">
                Uploaded by {run.uploaded_by_label ?? "somebody"}
                {run.uploaded_at ? ` on ${when(run.uploaded_at)}` : ""}
              </div>
              {run.approved_by_label && (
                <div className="text-xs text-slate-500" data-testid="gate-run-approval">
                  Approved to read files by {run.approved_by_label} on {when(run.approved_at)}
                </div>
              )}
            </>
          ) : (
            <span className="font-medium">A built-in pipeline</span>
          )}
        </dd>
      </div>
      <div>
        <dt className="text-slate-500">Run</dt>
        <dd className="mt-0.5">
          <Link className="font-medium text-blue-700 hover:underline" to={`/pipeline-runs/${run.id}`} data-testid="gate-run-link">
            Open the run
          </Link>
          <div className="text-xs text-slate-500">
            Started <span data-testid="gate-detail-started">{when(run.started_at)}</span>
            {run.ended_at ? `, finished ${when(run.ended_at)} (${took(run.started_at, run.ended_at)})` : ", not finished"}
          </div>
        </dd>
      </div>
      <div>
        <dt className="text-slate-500">Started by</dt>
        <dd className="mt-0.5">
          <span className="font-medium">{run.triggered_by_label ?? "a schedule"}</span>
          <div className="text-xs text-slate-500">{run.trigger_kind === "scheduled" ? "On a schedule" : run.trigger_kind === "manual" ? "By hand" : words(run.trigger_kind ?? "unknown")}</div>
        </dd>
      </div>
      <div>
        <dt className="text-slate-500">Input dataset</dt>
        <dd className="mt-0.5">
          {run.source_version_id && run.source_dataset_name ? (
            <Link className="font-medium text-blue-700 hover:underline" to={`/versions/${run.source_version_id}`}>
              {run.source_dataset_name}, version {run.source_version}
            </Link>
          ) : (
            <span className="text-slate-500">Not recorded</span>
          )}
        </dd>
      </div>
    </dl>
  );
}

/** One decision, with everything needed to make it. */
export function GateDetailScreen() {
  const { decisionId } = useParams<{ decisionId: string }>();
  const detail = useGateDecision(decisionId);
  // The same query the review section reads, so the page knows whether there are files to look at without asking twice.
  const review = useReview(decisionId);
  const reviewFiles = review.data?.files.length ?? 0;
  // The de-identification standard the person has chosen to judge this result by. Empty is the platform's built-in standard.
  const [standardId, setStandardId] = useState<string | null>(null);

  if (detail.isLoading) return <Loading what="this result" />;
  if (detail.error) return <Failure error={detail.error} what="this result" />;
  if (!detail.data) return <Failure error={new Error("not found")} what="this result" />;

  const d = detail.data;
  // What a practice run found is kept apart from what was found in this batch, so one is never read as the other.
  const notMeasured = d.recommendation === "not_measured";
  // Whether the result carries counts for each kind of identifier (a standard can judge it), and whether the run measured the share removed at all (a pipeline's own check does not).
  const counted = Boolean((d.metrics as unknown as { per_entity?: unknown }).per_entity);
  const hasFigures = d.metrics?.recall_effective !== undefined;
  const batchLeaks = d.leaks.filter((leak) => !leak.practice);
  const practiceLeaks = d.leaks.filter((leak) => leak.practice);

  return (
    <div>
      <Link className="text-sm text-blue-700 hover:underline" to="/gates">
        Back to results
      </Link>

      <div className="mt-2 mb-6 flex items-center gap-3">
        <h1 className="text-xl font-semibold text-slate-900">
          {d.dataset_name} v{d.version}
        </h1>
        <StateBadge state={d.state} />
      </div>
      {/* What is being decided, said once and before the evidence, in the tense of where the result stands. */}
      <p className="-mt-3 mb-1 max-w-3xl text-sm text-slate-700" data-testid="gate-lead">
        {d.state === "pending"
          ? `You are deciding whether to release this. Releasing moves it from ${classWord(d.visibility_class)} to ${classWord(d.to_class)}, which cannot be undone. Holding it back leaves it where it is.`
          : d.state === "promoted"
            ? `This was released and is now ${classWord(d.to_class)}.`
            : `This was held back and stays ${classWord(d.visibility_class)}.`}
      </p>
      <p className="mb-6 text-sm">
        <Link className="text-blue-700 hover:underline" to={`/versions/${d.dataset_version_id}`} data-testid="gate-version-link">
          Open this version
        </Link>
        <span className="text-slate-500"> to see its record, its history and every file it holds.</span>
      </p>

      <Section
        title={notMeasured ? "No automatic measurement" : hasFigures ? "What the run measured" : "What the pipeline's own check found"}
        description={notMeasured || !hasFigures ? undefined : "How much identifying detail the run removed from the data, and how much it left in."}
      >
        {notMeasured ? (
          // Nothing was counted, so there are no figures to show: one statement of what that means for the reader, the pipeline's own note about it,
          // and who started the run. Said once here and not again further down the page.
          <div className="space-y-2 text-sm">
            <p className="text-slate-900" data-testid="gate-not-measured">
              How much identifying detail this run removed was not counted. That is neither a pass nor a fail.{" "}
              {d.state === "pending"
                ? `The decision rests with you as the reviewer. ${reviewFiles > 0 ? "Use the platform's checks and the files below to reach it." : "Use what is shown below to reach it."}`
                : "The decision rested on the reviewer's own review, recorded below."}
            </p>
            {/* The pipeline's own note is shown when it says something the statement above does not: a note that only says nothing was measured is that statement again. */}
            {d.recommendation_reason?.trim() && !SAYS_NOTHING_WAS_MEASURED.test(d.recommendation_reason) && (
              <p className="text-slate-700" data-testid="gate-recommendation">
                The pipeline&rsquo;s note: {sentence(d.recommendation_reason)}
              </p>
            )}
          </div>
        ) : (
          <>
            {hasFigures && (
              <dl className="grid grid-cols-2 gap-4 text-sm md:grid-cols-3">
                <div>
                  <dt className="text-slate-500">Identifying detail removed</dt>
                  <dd className="mt-0.5 font-medium">{percent(d.metrics?.recall_effective)}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Left in the clear</dt>
                  <dd className="mt-0.5 font-medium">{d.metrics?.leaks_total ?? batchLeaks.length}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Naming a person directly</dt>
                  <dd className="mt-0.5 font-medium">{d.metrics?.leaks_direct ?? 0}</dd>
                </div>
              </dl>
            )}

            <div className={hasFigures ? "mt-4" : ""}>
              {d.state === "pending" && counted ? (
                <ChooseStandard detail={d} standardId={standardId} onChange={setStandardId} />
              ) : d.standard_name ? (
                <JudgedBy detail={d} />
              ) : (
                <>
                  <div
                    className={`rounded border p-3 text-sm ${
                      d.recommendation === "pass" ? "border-green-300 bg-green-50 text-green-900" : "border-amber-300 bg-amber-50 text-amber-900"
                    }`}
                    data-testid="gate-recommendation"
                  >
                    <p className="font-medium">
                      {hasFigures
                        ? d.recommendation === "pass" ? "The measurement cleared the standard the run was given." : "The measurement did not clear the standard the run was given."
                        : d.recommendation === "pass" ? "The pipeline's own check passed." : "The pipeline's own check did not pass."}
                    </p>
                    <p className="mt-1">{sentence(d.recommendation_reason)}</p>
                  </div>
                  <KindResults detail={d} />
                </>
              )}
            </div>
          </>
        )}
      </Section>

      {notMeasured && d.metrics?.practice_ran !== undefined && (
        <Section
          title="What a practice run measured"
          description="The same models, on files made for the purpose whose contents are known. This says what the method does. It says nothing about this batch."
        >
          {d.metrics?.practice_ran === 1 ? (
            <div data-testid="gate-practice">
              <dl className="grid grid-cols-2 gap-4 text-sm md:grid-cols-5">
                <div>
                  <dt className="text-slate-500">Files in the practice run</dt>
                  <dd className="mt-0.5 font-medium">{d.metrics?.practice_records}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Identifying detail removed</dt>
                  <dd className="mt-0.5 font-medium">
                    {percent(d.metrics?.practice_recall_effective)}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">Left in the clear</dt>
                  <dd className="mt-0.5 font-medium">{d.metrics?.practice_leaks_total}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Naming a person directly</dt>
                  <dd className="mt-0.5 font-medium">{d.metrics?.practice_leaks_direct}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Meets the Platform default standard</dt>
                  <dd className="mt-0.5 font-medium">
                    {d.metrics?.practice_cleared_bar === 1 ? "Yes" : "No"}
                  </dd>
                </div>
              </dl>
              <div className="mt-4">
                <LeakTable
                  leaks={practiceLeaks}
                  none="Nothing identifying was left in the clear in the practice run."
                />
              </div>
            </div>
          ) : (
            <p className="text-sm text-slate-700" data-testid="gate-practice">
              No practice run could be made, so there is no evidence about the method either.
            </p>
          )}
        </Section>
      )}

      <ReviewSection detail={d} />

      {!notMeasured && (hasFigures || batchLeaks.length > 0) && (
        <Section
          title="What was left in the clear"
          description="Judge how bad each one is. A fragment and a full name both count as one."
        >
          <LeakTable leaks={batchLeaks} />
        </Section>
      )}

      {(d.run || d.steps.length > 0 || d.triggered_by_label) && (
        <Section
          title="The run that produced this"
          description="Which pipeline ran, what it took as input, who started it, and what each step did."
        >
          {d.run && <RunFacts run={d.run} />}
          {!d.run && d.triggered_by_label && (
            <p className="text-sm text-slate-600">
              Started by <span className="font-medium text-slate-900">{d.triggered_by_label}</span> on {when(d.created_at)}. No run is recorded for it.
            </p>
          )}
          {d.steps.length > 0 && (
            <div className="data-card mt-4"><table className="data-table" data-testid="gate-steps">
              <thead>
                <tr>
                  <th>Step</th>
                  <th>Result</th>
                  <th>Started</th>
                  <th>Took</th>
                </tr>
              </thead>
              <tbody>
                {d.steps.map((step) => (
                  <tr key={step.id} className="border-b border-slate-100">
                    <td>{words(step.step_name ?? step.action_name)}</td>
                    <td className="text-slate-600">{words(step.status)}</td>
                    <td className="whitespace-nowrap text-slate-600">{when(step.started_at)}</td>
                    <td className="text-slate-600">{took(step.started_at, step.ended_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </Section>
      )}

      <Section title={d.state === "pending" ? "Your decision" : "The decision"}>
        <Decide detail={d} standardId={standardId} />
      </Section>
    </div>
  );
}
