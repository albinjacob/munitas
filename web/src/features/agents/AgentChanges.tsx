/**
 * Agent changes: what each new version of an agent changes against the one before it, and who has approved it.
 *
 * A version that widens what an agent can do is approved by an approver of the department that owns the agent, and also by the AI risk owner where the agent&rsquo;s level asks
 * for a second approver. A version that only narrows needs nobody. Every row says whether the person looking may give an approval still to be given.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useChangeApprovals, useDecideChange, useDecideFirstSubmission, useRiskLevels, type ChangeApproval } from "../../api/agentOversight";
import { Empty, Failure, Loading, Section } from "../../components/states";
import { notify } from "../../components/toast";
import { byOn } from "../../lib/when";
import { sentence } from "../../lib/text";
import { Reasons } from "../locking/LockedColumns";

const STATE_WORDS: Record<ChangeApproval["state"], string> = {
  not_needed: "Needs no approval",
  approved_in_advance: "Approved in advance",
  waiting: "Waiting for approval",
  approved: "Approved",
  refused: "Refused",
};

const STATE_TONE: Record<ChangeApproval["state"], string> = {
  not_needed: "bg-slate-100 text-slate-700",
  approved_in_advance: "bg-green-100 text-green-900",
  waiting: "bg-amber-100 text-amber-900",
  approved: "bg-green-100 text-green-900",
  refused: "bg-red-100 text-red-900",
};

/** Who gives each part. The officer's part is given by the roles the policy names, in the words the server builds from it. */
const partWords = (approval: ChangeApproval, part: "department" | "officer") => (part === "department" ? "the department that owns the agent" : approval.officer_words);

function Decide({ approval, part }: { approval: ChangeApproval; part: "department" | "officer" }) {
  const decide = useDecideChange();
  const [reason, setReason] = useState("");
  const can = approval.can_decide[part];
  if (!can) return null;
  if (!can.allowed) {
    return (
      <p className="text-sm text-slate-600" data-testid={`cannot-${part}-${approval.version}`}>
        {can.reasons.length ? sentence(can.reasons[0]) : `You may not give the approval of ${partWords(approval, part)}.`}
      </p>
    );
  }
  const send = (decision: "approved" | "refused") =>
    decide.mutate({ approvalId: approval.id, part, decision, reason, agentId: approval.agent_id }, { onSuccess: () => { notify(decision === "approved" ? "Approved." : "Refused."); setReason(""); } });
  return (
    <div className="space-y-2 rounded border border-slate-200 p-3" data-testid={`decide-${part}-${approval.version}`}>
      <label className="flex max-w-xl flex-col gap-1 text-sm">
        Your reason, for the approval of {partWords(approval, part)}
        <textarea rows={2} aria-label={`Reason for ${part}`} data-testid={`decide-reason-${part}-${approval.version}`} value={reason} onChange={(e) => setReason(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      {decide.error ? <div role="alert" className="text-sm text-red-800"><Reasons error={decide.error} fallback="The decision could not be recorded." /></div> : null}
      <div className="flex gap-3">
        <button type="button" data-testid={`approve-${part}-${approval.version}`} disabled={decide.isPending || !reason.trim()} onClick={() => send("approved")} className="btn btn-primary">
          Approve
        </button>
        <button type="button" data-testid={`refuse-${part}-${approval.version}`} disabled={decide.isPending || !reason.trim()} onClick={() => send("refused")} className="btn btn-danger-outline">
          Refuse
        </button>
      </div>
    </div>
  );
}

/** The officer's one decision on a first version: the level proposed with it and the version itself, together. */
function FirstSubmissionDecide({ approval }: { approval: ChangeApproval }) {
  const levels = useRiskLevels();
  const decide = useDecideFirstSubmission();
  const refuse = useDecideChange();
  const proposal = approval.first_submission?.proposal ?? null;
  const [levelKey, setLevelKey] = useState(proposal?.level_key ?? "");
  const [affected, setAffected] = useState("");
  const [reason, setReason] = useState("");
  const can = approval.can_decide.officer;
  if (!can) return null;
  if (!can.allowed) {
    return (
      <p className="text-sm text-slate-600" data-testid={`cannot-officer-${approval.version}`}>
        {can.reasons.length ? sentence(can.reasons[0]) : `You may not give the decision of ${approval.officer_words}.`}
      </p>
    );
  }
  const options = levels.data?.current.levels ?? [];
  const chosen = levelKey || options[0]?.key || "";
  return (
    <div className="space-y-3 rounded border border-slate-200 p-3" data-testid={`first-submission-${approval.version}`}>
      <p className="text-sm font-medium">This is the agent&rsquo;s first version. Its risk level is decided with it, in one decision.</p>
      {proposal ? (
        <div className="text-sm" data-testid={`first-proposal-${approval.version}`}>
          <p>
            Level proposed: <span className="font-medium">{proposal.level_name}</span>, {byOn(proposal.recorded_by_label ?? proposal.recorded_by, proposal.recorded_at)}.
          </p>
          <p><span className="text-slate-500">Affected: </span>{sentence(proposal.affected)}</p>
          <p><span className="text-slate-500">Reason: </span>{sentence(proposal.reasoning)}</p>
        </div>
      ) : (
        <p className="text-sm text-slate-700" data-testid={`first-no-proposal-${approval.version}`}>No level was proposed with this agent. Choose one, and say who is affected.</p>
      )}
      <label className="flex max-w-xs flex-col gap-1 text-sm">
        Level to confirm
        <select data-testid={`first-level-${approval.version}`} value={chosen} onChange={(e) => setLevelKey(e.target.value)} className="rounded border border-slate-400 px-2 py-1">
          {options.map((l) => (
            <option key={l.key} value={l.key}>{l.name}</option>
          ))}
        </select>
      </label>
      {!proposal && (
        <label className="flex max-w-xl flex-col gap-1 text-sm">
          Who is affected by what the agent produces
          <textarea rows={2} data-testid={`first-affected-${approval.version}`} value={affected} onChange={(e) => setAffected(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
        </label>
      )}
      <label className="flex max-w-xl flex-col gap-1 text-sm">
        Your reason, for the level and for the version
        <textarea rows={2} data-testid={`first-reason-${approval.version}`} value={reason} onChange={(e) => setReason(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      {decide.error ? <div role="alert" className="text-sm text-red-800"><Reasons error={decide.error} fallback="The decision could not be recorded." /></div> : null}
      <div className="flex gap-3">
        <button
          type="button"
          data-testid={`approve-first-${approval.version}`}
          disabled={decide.isPending || !reason.trim() || !chosen || (!proposal && !affected.trim())}
          onClick={() => decide.mutate({ approvalId: approval.id, agentId: approval.agent_id, level_key: chosen, reasoning: reason, affected: affected || undefined }, { onSuccess: () => notify("Level confirmed and version approved.") })}
          className="btn btn-primary"
        >
          Confirm the level and approve this version
        </button>
        <button
          type="button"
          data-testid={`refuse-first-${approval.version}`}
          disabled={refuse.isPending || !reason.trim()}
          onClick={() => refuse.mutate({ approvalId: approval.id, part: "officer", decision: "refused", reason, agentId: approval.agent_id }, { onSuccess: () => notify("Refused.") })}
          className="btn btn-danger-outline"
        >
          Refuse
        </button>
      </div>
    </div>
  );
}

export function ApprovalCard({ approval, showAgent, enforce }: { approval: ChangeApproval; showAgent: boolean; enforce: boolean }) {
  const advisory = approval.state === "waiting" && !enforce;
  return (
    <div className="rounded border border-slate-200 p-4" data-testid={`change-${approval.agent_name ?? "agent"}-${approval.version}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-base font-semibold text-slate-900">
          {showAgent && approval.agent_name ? (
            <>
              <Link to={`/agents/${approval.agent_id}`} className="text-sky-700 underline">{approval.agent_name}</Link>,{" "}
            </>
          ) : null}
          version {approval.version}
        </h3>
        <span className={`rounded px-2 py-0.5 text-xs font-medium ${advisory ? "bg-slate-100 text-slate-700" : STATE_TONE[approval.state]}`} data-testid={`change-state-${approval.version}`}>{advisory ? "Would need approval" : STATE_WORDS[approval.state]}</span>
      </div>
      <p className="text-sm text-slate-600">
        {approval.previous_version ? `Compared with version ${approval.previous_version}.` : "The first version, compared with nothing."} Registered {byOn(approval.submitted_by_label ?? approval.submitted_by, approval.created_at)}
        {approval.department_name ? `, for ${approval.department_name}` : ""}.
      </p>
      {approval.changes.length === 0 ? (
        <p className="mt-2 text-sm text-slate-700">Nothing about what this version may do changed.</p>
      ) : (
        <ul className="mt-2 space-y-1 text-sm" data-testid={`change-list-${approval.version}`}>
          {approval.changes.map((c, i) => (
            <li key={i} className="flex gap-2">
              <span className={`mt-0.5 h-fit shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${c.widens ? "bg-amber-100 text-amber-900" : "bg-slate-100 text-slate-700"}`}>{c.widens ? "Widens" : "Narrows"}</span>
              <span>
                <span className="font-medium">{c.label}.</span> {c.what}
                {c.covered_by && (
                  <span className="mt-1 block text-slate-700" data-testid={`covered-${c.covered_by.tool}`}>
                    <span className="font-medium">On the approved tools list.</span> {c.covered_by.tool} is on the list (version {c.covered_by.list_version}, {byOn(c.covered_by.set_by_label, c.covered_by.set_at)}), approved up to {c.covered_by.ceiling_name}
                    {c.covered_by.tier === 1 ? ", so nobody is asked." : " with the approval of the department alone, so the officer is not asked."}{" "}
                    <Link to="/approved-tools" className="text-sky-700 underline">See the list</Link>.
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      {approval.state === "waiting" && (
        <p className="mt-2 text-sm text-slate-700" data-testid={`waiting-for-${approval.version}`}>
          {advisory
            ? `The organisation does not hold agents to its risk levels yet, so nothing waits for this. Once it does, it needs ${approval.waiting_for.join(" and ")}.`
            : `Waiting for ${approval.waiting_for.join(" and ")}.`}
        </p>
      )}
      {approval.decisions.length > 0 && (
        <ul className="mt-2 space-y-1 text-sm" data-testid={`decisions-${approval.version}`}>
          {approval.decisions.map((d) => (
            <li key={d.part}>
              <span className="font-medium">{d.decision === "approved" ? "Approved" : "Refused"}</span> for {d.decided_as} {byOn(d.decided_by_label ?? d.decided_by, d.decided_at)}.{" "}
              <span className="text-slate-500">Reason: </span>
              {sentence(d.reason)}
            </li>
          ))}
        </ul>
      )}
      {approval.state === "waiting" && (
        <div className="mt-3 space-y-3">
          <Decide approval={approval} part="department" />
          {approval.needs_officer && (approval.first_submission ? <FirstSubmissionDecide approval={approval} /> : <Decide approval={approval} part="officer" />)}
        </div>
      )}
    </div>
  );
}

const FILTERS: { key: string | undefined; label: string }[] = [
  { key: "waiting", label: "Waiting" },
  { key: undefined, label: "All" },
  { key: "approved", label: "Approved" },
  { key: "approved_in_advance", label: "Approved in advance" },
  { key: "refused", label: "Refused" },
  { key: "not_needed", label: "Needing no approval" },
];

export function AgentChanges() {
  const [filter, setFilter] = useState<string | undefined>("waiting");
  const data = useChangeApprovals(filter);
  return (
    <Section
      level="page"
      title="Agent changes"
      description="What each new version of an agent changes against the one before it. A version that widens what the agent can do needs the approval of the department that owns it, and of the AI risk owner where the agent's level asks for it."
    >
      <div className="mb-4 flex flex-wrap gap-2" role="tablist" aria-label="Show">
        {FILTERS.map((f) => (
          <button
            key={f.label}
            type="button"
            role="tab"
            aria-selected={filter === f.key}
            data-testid={`changes-filter-${f.key ?? "all"}`}
            onClick={() => setFilter(f.key)}
            className={`btn btn-sm ${filter === f.key ? "btn-selected" : "btn-secondary"}`}
          >
            {f.label}
          </button>
        ))}
      </div>
      {data.isLoading ? (
        <Loading what="the changes" />
      ) : data.error ? (
        <Failure error={data.error} what="the changes" />
      ) : data.data!.approvals.length === 0 ? (
        <Empty what="changes" hint={filter === "waiting" ? "No version waits for an approval." : "No version matches."} />
      ) : (
        <div className="space-y-4" data-testid="changes-list">
          {data.data!.approvals.map((a) => (
            <ApprovalCard key={a.id} approval={a} showAgent enforce={data.data!.enforce} />
          ))}
        </div>
      )}
    </Section>
  );
}
