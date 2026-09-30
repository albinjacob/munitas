/**
 * Agent egress approvals: which hosts a version may call, before it can be
 * deployed.
 *
 * A network tool like `fetch_url` is only as safe as the network it happens
 * to run on. This is the governance half of making it safe by construction
 * instead: a version names every host it may call, and a `network_architect`
 * signs off on that exact list before the version can be deployed at all
 * (see `docs/internal/diagrams/agent-egress-allowlist`). The runtime check itself
 * (resolving each call and confirming it stays inside the approved list) is
 * separate, later work; this screen and the deploy gate are what exist today.
 */

import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { Pagination } from "../../components/Pagination";
import { notify } from "../../components/toast";
import { useIdentity } from "../../identity/IdentityContext";
import {
  useDecideEgress,
  useEgressApproval,
  useEgressApprovals,
  type EgressApprovalDetail,
  type EgressApprovalRow,
} from "../../api/egress";

const STATE_STYLE: Record<EgressApprovalRow["state"], string> = {
  pending: "bg-amber-100 text-amber-900",
  approved: "bg-green-100 text-green-900",
  refused: "bg-red-100 text-red-900",
};

const STATE_WORD: Record<EgressApprovalRow["state"], string> = {
  pending: "Waiting for a decision",
  approved: "Approved",
  refused: "Refused",
};

function StateBadge({ state }: { state: EgressApprovalRow["state"] }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium ${STATE_STYLE[state]}`}
      data-testid="egress-state"
    >
      {STATE_WORD[state]}
    </span>
  );
}

const PAGE_SIZE = 15;

/** The queue. */
export function EgressApprovalQueue() {
  const [page, setPage] = useState(1);
  const approvals = useEgressApprovals(undefined, PAGE_SIZE, (page - 1) * PAGE_SIZE);
  // A count of pending across the whole queue, not just this page: with
  // history and pending work mixed into one paginated list, counting
  // `rows.filter(pending)` on the current page alone would undercount the
  // moment there is more than one page, since pending work is not
  // guaranteed to sort onto page 1. `limit: 1` because only `.total` is
  // read from this one; the API's own filtered count already exists for
  // exactly this reason.
  const pendingCount = useEgressApprovals("pending", 1, 0);

  if (approvals.isLoading) return <Loading what="egress approval requests" />;
  if (approvals.error)
    return <Failure error={approvals.error} what="egress approval requests" />;

  const rows = approvals.data?.egress_approvals ?? [];
  const waiting = pendingCount.data?.total ?? 0;

  return (
    <div className="p-6">
      <Section
        level="page"
        title="Egress approvals"
        description="Each request names the external hosts one agent version wants to call. Nothing calls them until this is approved, and the version cannot be deployed until it is."
      >
        {rows.length === 0 ? (
          <Empty
            what="requests to review"
            hint="A request appears here the moment an agent version declares hosts it may call. A version that declares none files nothing."
          />
        ) : (
          <>
            <p className="mb-3 text-sm text-slate-600">
              {waiting === 0
                ? "Nothing is waiting on you. Earlier decisions are kept below."
                : `${waiting} waiting on a decision.`}
            </p>
            <table className="w-full text-sm" data-testid="egress-queue">
              <thead className="border-b border-slate-200 text-left text-slate-500">
                <tr>
                  <th className="py-2 font-medium">Agent</th>
                  <th className="py-2 font-medium">Requested hosts</th>
                  <th className="py-2 font-medium">State</th>
                  <th className="py-2 font-medium">Submitted by</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className="border-b border-slate-100">
                    <td className="py-2">
                      <Link
                        className="font-medium text-blue-700 hover:underline"
                        to={`/egress-approvals/${row.id}`}
                      >
                        {row.agent_name} v{row.version}
                      </Link>
                    </td>
                    <td className="py-2 font-mono text-xs">
                      {row.requested_hosts.join(", ")}
                    </td>
                    <td className="py-2">
                      <StateBadge state={row.state} />
                    </td>
                    <td className="py-2 text-slate-600">
                      {row.submitted_by_label ?? row.submitted_by}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={approvals.data?.total ?? 0}
              onPageChange={setPage}
            />
          </>
        )}
      </Section>
    </div>
  );
}

function Decide({ detail }: { detail: EgressApprovalDetail }) {
  const { principal } = useIdentity();
  const decide = useDecideEgress(detail.id);
  const [reason, setReason] = useState("");

  const submittedIt = detail.submitted_by === principal?.id;
  const mayDecide = principal?.roles.includes("network_architect") ?? false;
  const blocked = submittedIt
    ? "You submitted this version, so somebody else has to decide its requested hosts."
    : !mayDecide
      ? "Approving a version's requested hosts is not part of your role."
      : null;

  if (detail.state !== "pending") {
    return (
      <div className="rounded border border-slate-200 bg-slate-50 p-4 text-sm">
        <p className="font-medium text-slate-900">
          {detail.state === "approved" ? "Approved by " : "Refused by "}
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
      <label className="block text-sm font-medium text-slate-900" htmlFor="egress-reason">
        Why you decided this
      </label>
      <p className="mt-0.5 text-sm text-slate-500">
        Kept with the decision, the same record a lease approval or a
        de-identification result keeps.
      </p>
      <textarea
        id="egress-reason"
        data-testid="egress-reason"
        className="mt-2 w-full rounded border border-slate-300 p-2 text-sm"
        rows={3}
        value={reason}
        disabled={Boolean(blocked)}
        onChange={(e) => setReason(e.target.value)}
      />
      {blocked && (
        <p className="mt-2 text-sm text-amber-800" data-testid="egress-blocked">
          {blocked}
        </p>
      )}
      {decide.error && <div className="mt-3"><Failure error={decide.error} what="the decision" /></div>}
      <div className="mt-3 flex gap-3">
        <button
          type="button"
          data-testid="egress-approve"
          disabled={Boolean(blocked) || !reason.trim() || decide.isPending}
          onClick={() =>
            decide.mutate(
              { outcome: "approve", reason },
              { onSuccess: () => notify("Hosts approved.") },
            )
          }
          className="rounded bg-green-700 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
        >
          {decide.isPending ? "Approving" : "Approve these hosts"}
        </button>
        <button
          type="button"
          data-testid="egress-refuse"
          disabled={Boolean(blocked) || !reason.trim() || decide.isPending}
          onClick={() =>
            decide.mutate(
              { outcome: "refuse", reason },
              { onSuccess: () => notify("Refused.") },
            )
          }
          className="rounded bg-red-700 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
        >
          {decide.isPending ? "Refusing" : "Refuse"}
        </button>
      </div>
    </div>
  );
}

/** One request, with everything needed to decide it. */
export function EgressApprovalDetailScreen() {
  const { approvalId } = useParams<{ approvalId: string }>();
  const detail = useEgressApproval(approvalId);

  if (detail.isLoading) return <Loading what="this request" />;
  if (detail.error) return <Failure error={detail.error} what="this request" />;
  if (!detail.data) return <Failure error={new Error("not found")} what="this request" />;

  const d = detail.data;

  return (
    <div className="p-6">
      <Link className="text-sm text-blue-700 hover:underline" to="/egress-approvals">
        Back to requests
      </Link>

      <div className="mt-2 mb-6 flex items-center gap-3">
        <h1 className="text-xl font-semibold text-slate-900">
          {d.agent_name} v{d.version}
        </h1>
        <StateBadge state={d.state} />
      </div>

      <Section
        title="What this version asked for"
        description="Approving this is what lets the version be deployed; it is not itself a claim that any request has actually been made yet."
      >
        <dl className="grid grid-cols-1 gap-4 text-sm md:grid-cols-2">
          <div>
            <dt className="text-slate-500">Requested hosts</dt>
            <dd className="mt-0.5 font-mono text-xs">
              {d.requested_hosts.join(", ")}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">Model</dt>
            <dd className="mt-0.5 font-medium">{d.model_id}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Tools it may call</dt>
            <dd className="mt-0.5 font-mono text-xs">
              {d.tool_scope.length ? d.tool_scope.join(", ") : "none declared"}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">Submitted by</dt>
            <dd className="mt-0.5 font-medium">
              {d.submitted_by_label ?? d.submitted_by}
            </dd>
          </div>
        </dl>
      </Section>

      <Section title="Your decision">
        <Decide detail={d} />
      </Section>
    </div>
  );
}
