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
  type GateDetail,
  type GateLeak,
  type GateRow,
} from "../../api/gates";
import { CLASS_LABEL, type VisibilityClass } from "../../api/types";

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

  if (!principal) return <Loading what="your identity" />;
  if (decisions.isLoading) return <Loading what="gate decisions" />;
  if (decisions.error)
    return <Failure error={decisions.error} what="gate decisions" />;

  const rows = decisions.data?.gate_decisions ?? [];
  const waiting = pendingCount.data?.total ?? 0;

  return (
    <div className="p-6">
      <Section
        level="page"
        title="De-identification results"
        description="Each run measured how much identifying detail its redaction removed. Nothing is released until somebody decides it is safe enough."
      >
        {rows.length === 0 ? (
          <Empty
            what="results to review"
            hint="A result appears here when a de-identification run finishes. Runs you started yourself are not listed, because somebody else has to clear them."
          />
        ) : (
          <>
            <p className="mb-3 text-sm text-slate-600">
              {waiting === 0
                ? "Nothing is waiting on you. Earlier decisions are kept below."
                : `${waiting} waiting on a decision.`}
            </p>
            <table className="w-full text-sm" data-testid="gate-queue">
              <thead className="border-b border-slate-200 text-left text-slate-500">
                <tr>
                  <th className="py-2 font-medium">Dataset</th>
                  <th className="py-2 font-medium">Would move to</th>
                  <th className="py-2 font-medium">Measured</th>
                  <th className="py-2 font-medium">Left in the clear</th>
                  <th className="py-2 font-medium">State</th>
                  <th className="py-2 font-medium">Ran for</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className="border-b border-slate-100">
                    <td className="py-2">
                      <Link
                        className="font-medium text-blue-700 hover:underline"
                        to={`/gates/${row.id}`}
                      >
                        {row.dataset_name} v{row.version}
                      </Link>
                    </td>
                    <td className="py-2">
                      {CLASS_LABEL[row.visibility_class]} to{" "}
                      {classWord(row.to_class)}
                    </td>
                    <td className="py-2">
                      {percent(row.metrics?.recall_effective)} removed
                    </td>
                    <td className="py-2">
                      {row.leak_count === 0
                        ? "nothing"
                        : `${row.leak_count} to look at`}
                    </td>
                    <td className="py-2">
                      <StateBadge state={row.state} />
                    </td>
                    <td className="py-2 text-slate-600">
                      {row.triggered_by_label ?? "a schedule"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={decisions.data?.total ?? 0}
              onPageChange={setPage}
            />
          </>
        )}
      </Section>
    </div>
  );
}

function LeakTable({ leaks }: { leaks: GateLeak[] }) {
  if (leaks.length === 0) {
    return (
      <Empty
        what="identifying detail left in the clear"
        hint="The run removed everything the answer key contains."
      />
    );
  }
  return (
    <table className="w-full text-sm" data-testid="gate-leaks">
      <thead className="border-b border-slate-200 text-left text-slate-500">
        <tr>
          <th className="py-2 font-medium">Kind</th>
          <th className="py-2 font-medium">What it was</th>
          <th className="py-2 font-medium">What survived</th>
          <th className="py-2 font-medium">Removed</th>
          <th className="py-2 font-medium">Where</th>
        </tr>
      </thead>
      <tbody>
        {leaks.map((leak) => (
          <tr
            key={`${leak.record_id}-${leak.span_start}`}
            className="border-b border-slate-100"
          >
            <td className="py-2">
              {leak.entity}
              {leak.direct && (
                <span className="ml-2 rounded bg-red-100 px-1.5 py-0.5 text-xs font-medium text-red-900">
                  names a person directly
                </span>
              )}
            </td>
            <td className="py-2 font-mono text-xs">{leak.identifier}</td>
            <td className="py-2 font-mono text-xs text-red-800">
              {leak.left_in_the_clear || "the whole thing"}
            </td>
            <td className="py-2">{percent(leak.coverage)}</td>
            <td className="py-2 text-slate-500">
              {leak.record_id}, characters {leak.span_start} to {leak.span_end}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Decide({ detail }: { detail: GateDetail }) {
  const { principal } = useIdentity();
  const decide = useDecideGate(detail.id);
  const [reason, setReason] = useState("");

  const startedIt = detail.triggered_by === principal?.id;
  const mayDecide = principal?.roles.includes("deid_reviewer") ?? false;
  const blocked = startedIt
    ? "You started this run, so somebody else has to clear it."
    : !mayDecide
      ? "Clearing a de-identification result is not part of your role."
      : null;

  if (detail.state !== "pending") {
    return (
      <div className="rounded border border-slate-200 bg-slate-50 p-4 text-sm">
        <p className="font-medium text-slate-900">
          {detail.state === "promoted"
            ? "Released by "
            : "Held back by "}
          {detail.decided_by_label ?? detail.decided_by}.
        </p>
        {detail.decision_reason && (
          <p className="mt-1 text-slate-700">{detail.decision_reason}</p>
        )}
      </div>
    );
  }

  return (
    <div className="rounded border border-slate-200 p-4">
      <label className="block text-sm font-medium text-slate-900" htmlFor="reason">
        Why you decided this
      </label>
      <p className="mt-0.5 text-sm text-slate-500">
        Kept with the decision. Whoever reads this later will not have the
        result in front of them.
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
      {decide.error && <div className="mt-3"><Failure error={decide.error} what="the decision" /></div>}
      <div className="mt-3 flex gap-3">
        <button
          type="button"
          data-testid="gate-promote"
          disabled={Boolean(blocked) || !reason.trim() || decide.isPending}
          onClick={() =>
            decide.mutate(
              { outcome: "promote", reason },
              { onSuccess: () => notify(`Released to ${classWord(detail.to_class)}.`) },
            )
          }
          className="rounded bg-green-700 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
        >
          {decide.isPending ? "Releasing" : `Release to ${classWord(detail.to_class)}`}
        </button>
        <button
          type="button"
          data-testid="gate-refuse"
          disabled={Boolean(blocked) || !reason.trim() || decide.isPending}
          onClick={() =>
            decide.mutate(
              { outcome: "refuse", reason },
              { onSuccess: () => notify("Held back.") },
            )
          }
          className="rounded bg-red-700 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
        >
          {decide.isPending ? "Holding back" : "Hold it back"}
        </button>
      </div>
    </div>
  );
}

/** One decision, with everything needed to make it. */
export function GateDetailScreen() {
  const { decisionId } = useParams<{ decisionId: string }>();
  const detail = useGateDecision(decisionId);

  if (detail.isLoading) return <Loading what="this result" />;
  if (detail.error) return <Failure error={detail.error} what="this result" />;
  if (!detail.data) return <Failure error={new Error("not found")} what="this result" />;

  const d = detail.data;

  return (
    <div className="p-6">
      <Link className="text-sm text-blue-700 hover:underline" to="/gates">
        Back to results
      </Link>

      <div className="mt-2 mb-6 flex items-center gap-3">
        <h1 className="text-xl font-semibold text-slate-900">
          {d.dataset_name} v{d.version}
        </h1>
        <StateBadge state={d.state} />
      </div>

      <Section
        title="What the run measured"
        description={`Releasing this moves it from ${classWord(d.visibility_class)} to ${classWord(d.to_class)}, which cannot be undone.`}
      >
        <dl className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
          <div>
            <dt className="text-slate-500">Identifying detail removed</dt>
            <dd className="mt-0.5 font-medium">
              {percent(d.metrics?.recall_effective)}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">Left in the clear</dt>
            <dd className="mt-0.5 font-medium">
              {d.metrics?.leaks_total ?? d.leaks.length}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">Naming a person directly</dt>
            <dd className="mt-0.5 font-medium">{d.metrics?.leaks_direct ?? 0}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Started by</dt>
            <dd className="mt-0.5 font-medium">
              {d.triggered_by_label ?? "a schedule"}
            </dd>
          </div>
        </dl>

        <div
          className={`mt-4 rounded border p-3 text-sm ${
            d.recommendation === "pass"
              ? "border-green-300 bg-green-50 text-green-900"
              : "border-amber-300 bg-amber-50 text-amber-900"
          }`}
          data-testid="gate-recommendation"
        >
          <p className="font-medium">
            {d.recommendation === "pass"
              ? "The measurement clears the agreed bar."
              : "The measurement does not clear the agreed bar."}
          </p>
          <p className="mt-1">{d.recommendation_reason}</p>
        </div>
      </Section>

      <Section
        title="What was left in the clear"
        description="Judge how bad each one is. A fragment and a full name both count as one."
      >
        <LeakTable leaks={d.leaks} />
      </Section>

      {d.steps.length > 0 && (
        <Section
          title="The run that produced this"
          description="Every step of the same run, in order."
        >
          <ul className="text-sm" data-testid="gate-steps">
            {d.steps.map((step) => (
              <li
                key={step.id}
                className="flex justify-between border-b border-slate-100 py-1.5"
              >
                <span>{step.action_name}</span>
                <span className="text-slate-500">{step.status}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Your decision">
        <Decide detail={d} />
      </Section>
    </div>
  );
}
