/**
 * What an organisation does to oversee one agent, on the agent's own page: the risk level it is held at, what its versions changed and who approved them, whether it is suspended,
 * incidents reported about it, how the people who review its output have decided, and the evidence pack.
 */

import { useState } from "react";
import {
  useAgentChangeApprovals,
  useAgentOversight,
  useAgentRisk,
  useAssignSponsor,
  useCheckChain,
  useConfirmLevel,
  useMakeEvidencePack,
  useProposeLevel,
  useReconfirm,
  useReportIncident,
  useResume,
  useSponsorship,
  useSuspend,
  type AgentRisk,
} from "../../api/agentOversight";
import { Failure, Loading, Section } from "../../components/states";
import { notify } from "../../components/toast";
import { CODE_ROLES, useHoldsRole } from "../../identity/mayDo";
import { byOn, when } from "../../lib/when";
import { sentence } from "../../lib/text";
import { Reasons } from "../locking/LockedColumns";
import { ApprovalCard } from "./AgentChanges";

function LevelForm({ agentId, risk, mode }: { agentId: string; risk: AgentRisk; mode: "propose" | "confirm" }) {
  const propose = useProposeLevel(agentId);
  const confirm = useConfirmLevel(agentId);
  const levels = risk.levels.levels;
  const start = risk.proposal_waiting?.level_key ?? risk.confirmed?.level_key ?? levels[0]?.key ?? "";
  const [levelKey, setLevelKey] = useState(start);
  const [purpose, setPurpose] = useState(risk.proposal_waiting?.intended_purpose ?? risk.confirmed?.intended_purpose ?? "");
  const [affected, setAffected] = useState(risk.proposal_waiting?.affected ?? risk.confirmed?.affected ?? "");
  const [reasoning, setReasoning] = useState("");
  const mutation = mode === "propose" ? propose : confirm;
  const chosen = levels.find((l) => l.key === levelKey);
  return (
    <form
      className="mt-3 space-y-3 rounded border border-slate-200 p-4 text-sm"
      data-testid={`${mode}-level-form`}
      onSubmit={(e) => {
        e.preventDefault();
        if (mode === "propose") propose.mutate({ level_key: levelKey, intended_purpose: purpose, affected, reasoning }, { onSuccess: () => { notify("Level proposed."); setReasoning(""); } });
        else confirm.mutate({ level_key: levelKey, reasoning, affected: affected || undefined }, { onSuccess: () => { notify("Level confirmed."); setReasoning(""); } });
      }}
    >
      <label className="flex flex-col gap-1">
        Level
        <select data-testid={`${mode}-level`} value={levelKey} onChange={(e) => setLevelKey(e.target.value)} className="w-64 rounded border border-slate-400 px-2 py-1">
          {levels.map((l) => (
            <option key={l.key} value={l.key}>{l.name}</option>
          ))}
        </select>
      </label>
      {chosen && <p className="text-slate-600">{chosen.description}</p>}
      {mode === "propose" && (
        <label className="flex max-w-xl flex-col gap-1">
          What the agent is for
          <textarea data-testid="propose-purpose" rows={2} value={purpose} onChange={(e) => setPurpose(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
        </label>
      )}
      <label className="flex max-w-xl flex-col gap-1">
        Who is affected by what the agent produces
        <textarea data-testid={`${mode}-affected`} rows={2} value={affected} onChange={(e) => setAffected(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      <label className="flex max-w-xl flex-col gap-1">
        Why this level
        <textarea data-testid={`${mode}-reasoning`} rows={2} value={reasoning} onChange={(e) => setReasoning(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      {mutation.error ? <div role="alert" className="text-red-800"><Reasons error={mutation.error} fallback="The level could not be recorded." /></div> : null}
      <button
        type="submit"
        data-testid={`${mode}-level-save`}
        disabled={mutation.isPending || !reasoning.trim() || (mode === "propose" && (!purpose.trim() || !affected.trim()))}
        className="btn btn-primary"
      >
        {mode === "propose" ? "Propose this level" : "Confirm this level"}
      </button>
    </form>
  );
}

function RiskSection({ agentId }: { agentId: string }) {
  const risk = useAgentRisk(agentId);
  const mayPropose = useHoldsRole(CODE_ROLES);
  const [form, setForm] = useState<"propose" | "confirm" | null>(null);
  const [showing, setShowing] = useState(false);
  if (risk.isLoading) return <Loading what="the risk level" />;
  if (risk.error) return <Failure error={risk.error} what="the risk level" />;
  const r = risk.data!;
  return (
    <Section level="section" title="Risk level" description={`The level the organisation holds this agent to. The person who registered the agent proposes it, and ${r.confirm_words} confirms it.`}>
      {r.confirmed ? (
        <div data-testid="risk-confirmed" className="text-sm">
          <p>
            <span className="font-medium">{r.confirmed.level_name}</span>
            {r.confirmed.framework_label && <span className="text-slate-600"> ({r.confirmed.framework_label})</span>}, confirmed {byOn(r.confirmed.recorded_by_label ?? r.confirmed.recorded_by, r.confirmed.recorded_at)}.
          </p>
          <p><span className="text-slate-500">Purpose: </span>{sentence(r.confirmed.intended_purpose)}</p>
          <p><span className="text-slate-500">Affected: </span>{sentence(r.confirmed.affected)}</p>
          <p><span className="text-slate-500">Reason: </span>{sentence(r.confirmed.reasoning)}</p>
          {r.confirmed.blocks && <p className="mt-1 text-red-800">This level does not allow the agent to be deployed or to run.</p>}
        </div>
      ) : (
        <p className="text-sm" data-testid="risk-unclassified">
          No level has been confirmed. Until one is, every approval for this agent treats it as {r.held_at.name}, the strictest level that allows deployment.
          {r.enforce ? " The organisation holds agents to its levels, so this agent cannot start a run yet." : ""}
        </p>
      )}
      {r.proposal_waiting && (
        <p className="mt-2 text-sm" data-testid="risk-proposal">
          Proposed: <span className="font-medium">{r.proposal_waiting.level_name}</span>, {byOn(r.proposal_waiting.recorded_by_label ?? r.proposal_waiting.recorded_by, r.proposal_waiting.recorded_at)}. It waits for {r.confirm_words}.
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-3 text-sm">
        {mayPropose && (
          <button type="button" data-testid="risk-propose-open" onClick={() => setForm(form === "propose" ? null : "propose")} className="btn btn-secondary">
            Propose a level
          </button>
        )}
        {r.may_confirm && (
          <button type="button" data-testid="risk-confirm-open" onClick={() => setForm(form === "confirm" ? null : "confirm")} className="btn btn-secondary">
            Confirm or change the level
          </button>
        )}
        {r.history.length > 0 && (
          <button type="button" data-testid="risk-history-toggle" onClick={() => setShowing(!showing)} className="btn btn-link">
            {showing ? "Hide the history" : `Show all ${r.history.length} proposals and decisions`}
          </button>
        )}
      </div>
      {!r.may_confirm && r.may_confirm_reasons.length > 0 && <p className="mt-2 text-sm text-slate-600" data-testid="risk-cannot-confirm">{sentence(r.may_confirm_reasons[0])}</p>}
      {form && <LevelForm agentId={agentId} risk={r} mode={form} />}
      {showing && (
        <ol className="mt-3 space-y-2 text-sm" data-testid="risk-history">
          {r.history.map((h) => (
            <li key={h.id} className="rounded border border-slate-200 p-3">
              <span className="font-medium">{h.kind === "confirmed" ? "Confirmed" : "Proposed"}: {h.level_name}</span> {byOn(h.recorded_by_label ?? h.recorded_by, h.recorded_at)}.{" "}
              <span className="text-slate-500">Reason: </span>{sentence(h.reasoning)}
            </li>
          ))}
        </ol>
      )}
    </Section>
  );
}

function SponsorSection({ agentId }: { agentId: string }) {
  const data = useSponsorship(agentId);
  const assign = useAssignSponsor(agentId);
  const reconfirm = useReconfirm(agentId);
  const [chosen, setChosen] = useState("");
  const [why, setWhy] = useState("");
  const [note, setNote] = useState("");
  const [showing, setShowing] = useState(false);
  if (data.isLoading) return <Loading what="the sponsor" />;
  if (data.error) return <Failure error={data.error} what="the sponsor" />;
  const s = data.data!;
  const c = s.reconfirm;
  const pick = chosen || s.sponsor?.sponsor_id || s.candidates[0]?.id || "";
  return (
    <Section level="section" title="Sponsor and re-confirmation" description="The sponsor is the person who answers for this agent. They are an approver of the department that owns it. Re-confirming says the agent is still needed and still held at the right level.">
      {s.sponsor ? (
        <p className="text-sm" data-testid="sponsor-current">
          Sponsor: <span className="font-medium">{s.sponsor.sponsor_label ?? s.sponsor.sponsor_id}</span>, assigned {byOn(s.sponsor.assigned_by_label ?? s.sponsor.assigned_by, s.sponsor.assigned_at)}.
        </p>
      ) : (
        <p className="text-sm text-amber-800" data-testid="sponsor-none">This agent has no sponsor yet.</p>
      )}
      {s.sponsor && !s.sponsor_can_act && <p className="mt-1 text-sm text-red-800" data-testid="sponsor-cannot-act">The sponsor can no longer act for this department. Assign another.</p>}
      <p className="mt-2 text-sm" data-testid="reconfirm-clock">
        {c.days === null
          ? "This agent's level does not ask for re-confirmation."
          : `Its level asks for re-confirmation every ${c.days} days. Last confirmed ${c.last_at ? when(c.last_at) : "never"}. Due ${c.due_at ? when(c.due_at) : "when a level is confirmed"}.`}
      </p>
      {c.overdue && <p className="mt-1 text-sm font-medium text-red-800" data-testid="reconfirm-overdue">Re-confirmation is overdue.</p>}
      {!c.overdue && c.due_soon && <p className="mt-1 text-sm text-amber-800" data-testid="reconfirm-soon">Re-confirmation is due soon.</p>}

      {s.may_assign ? (
        <div className="mt-3 max-w-xl space-y-2 text-sm" data-testid="sponsor-form">
          <label className="flex flex-col gap-1">
            Sponsor
            <select data-testid="sponsor-select" value={pick} onChange={(e) => setChosen(e.target.value)} className="w-64 rounded border border-slate-400 px-2 py-1">
              {s.candidates.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            Why this person
            <textarea rows={2} data-testid="sponsor-reason" value={why} onChange={(e) => setWhy(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
          </label>
          {assign.error ? <div role="alert" className="text-red-800"><Reasons error={assign.error} fallback="The sponsor could not be assigned." /></div> : null}
          <button type="button" data-testid="sponsor-assign" disabled={assign.isPending || !why.trim() || !pick} onClick={() => assign.mutate({ sponsor_id: pick, reason: why }, { onSuccess: () => { notify("Sponsor assigned."); setWhy(""); } })} className="btn btn-primary">
            Assign as sponsor
          </button>
        </div>
      ) : (
        <p className="mt-3 text-sm text-slate-600" data-testid="cannot-assign">{s.may_assign_reasons.length ? sentence(s.may_assign_reasons[0]) : "You may not assign a sponsor."}</p>
      )}

      {s.may_reconfirm ? (
        <div className="mt-4 max-w-xl space-y-2 text-sm" data-testid="reconfirm-form">
          <label className="flex flex-col gap-1">
            What you checked: that the agent is still needed, and still held at the right level
            <textarea rows={2} data-testid="reconfirm-note" value={note} onChange={(e) => setNote(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
          </label>
          {reconfirm.error ? <div role="alert" className="text-red-800"><Reasons error={reconfirm.error} fallback="The re-confirmation could not be recorded." /></div> : null}
          <button type="button" data-testid="reconfirm-send" disabled={reconfirm.isPending || !note.trim()} onClick={() => reconfirm.mutate({ note }, { onSuccess: () => { notify("Agent re-confirmed."); setNote(""); } })} className="btn btn-primary">
            Re-confirm this agent
          </button>
        </div>
      ) : (
        <p className="mt-3 text-sm text-slate-600" data-testid="cannot-reconfirm">{s.may_reconfirm_reasons.length ? sentence(s.may_reconfirm_reasons[0]) : "You may not re-confirm this agent."}</p>
      )}

      {(s.history.length > 0 || s.reconfirmations.length > 0) && (
        <>
          <button type="button" data-testid="sponsor-history-toggle" onClick={() => setShowing(!showing)} className="btn btn-link mt-3">
            {showing ? "Hide the history" : "Show the history"}
          </button>
          {showing && (
            <ul className="mt-2 space-y-1 text-sm" data-testid="sponsor-history">
              {s.history.map((h) => (
                <li key={h.id}>Sponsor {h.sponsor_label ?? h.sponsor_id} assigned {byOn(h.assigned_by_label ?? h.assigned_by, h.assigned_at)}. <span className="text-slate-500">Reason: </span>{sentence(h.reason)}</li>
              ))}
              {s.reconfirmations.map((r) => (
                <li key={r.id}>Re-confirmed {byOn(r.confirmed_by_label ?? r.confirmed_by, r.confirmed_at)}. <span className="text-slate-500">Checked: </span>{sentence(r.note)}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </Section>
  );
}

function ChangesSection({ agentId }: { agentId: string }) {
  const changes = useAgentChangeApprovals(agentId);
  if (changes.isLoading) return <Loading what="the changes" />;
  if (changes.error) return <Failure error={changes.error} what="the changes" />;
  const list = changes.data!.approvals;
  return (
    <Section level="section" title="What each version changed" description="Each version is compared with the one before it. A version that widens what the agent can do needs approval.">
      {list.length === 0 ? <p className="text-sm text-slate-600">No version has been compared yet.</p> : (
        <div className="space-y-4" data-testid="agent-changes">
          {list.map((a) => <ApprovalCard key={a.id} approval={a} showAgent={false} enforce={changes.data!.enforce} />)}
        </div>
      )}
    </Section>
  );
}

function ReasonForm({ testid, label, button, pending, error, onSend }: { testid: string; label: string; button: string; pending: boolean; error: unknown; onSend: (reason: string) => void }) {
  const [reason, setReason] = useState("");
  return (
    <div className="mt-3 max-w-xl space-y-2 text-sm" data-testid={testid}>
      <label className="flex flex-col gap-1">
        {label}
        <textarea rows={2} data-testid={`${testid}-reason`} value={reason} onChange={(e) => setReason(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      {error ? <div role="alert" className="text-red-800"><Reasons error={error} fallback="That could not be done." /></div> : null}
      <button type="button" data-testid={`${testid}-send`} disabled={pending || !reason.trim()} onClick={() => { onSend(reason); setReason(""); }} className="btn btn-primary">
        {button}
      </button>
    </div>
  );
}

function IncidentForm({ agentId }: { agentId: string }) {
  const report = useReportIncident(agentId);
  const [what, setWhat] = useState("");
  const [onward, setOnward] = useState("");
  const [action, setAction] = useState("");
  return (
    <div className="mt-3 max-w-xl space-y-2 text-sm" data-testid="incident-form">
      <label className="flex flex-col gap-1">
        What happened. Do not include personal data.
        <textarea rows={2} data-testid="incident-what" value={what} onChange={(e) => setWhat(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      <label className="flex flex-col gap-1">
        Whom the organisation has told, if anyone. The platform sends nothing.
        <input data-testid="incident-onward" value={onward} onChange={(e) => setOnward(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      <label className="flex flex-col gap-1">
        What was done about it
        <input data-testid="incident-action" value={action} onChange={(e) => setAction(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
      </label>
      {report.error ? <div role="alert" className="text-red-800"><Reasons error={report.error} fallback="The incident could not be recorded." /></div> : null}
      <button
        type="button"
        data-testid="incident-send"
        disabled={report.isPending || !what.trim()}
        onClick={() => report.mutate({ what, reported_onward: onward || undefined, action_taken: action || undefined }, { onSuccess: () => { notify("Incident recorded."); setWhat(""); setOnward(""); setAction(""); } })}
        className="btn btn-primary"
      >
        Record the incident
      </button>
    </div>
  );
}

function OversightSection({ agentId }: { agentId: string }) {
  const oversight = useAgentOversight(agentId);
  const suspend = useSuspend(agentId);
  const resume = useResume(agentId);
  if (oversight.isLoading) return <Loading what="the oversight" />;
  if (oversight.error) return <Failure error={oversight.error} what="the oversight" />;
  const o = oversight.data!;
  const f = o.review_figures;
  return (
    <>
      <Section level="section" title="Suspension" description="Suspending an agent stops any new run, and halts every run that has not finished.">
        {o.suspension.suspended ? (
          <p className="text-sm" data-testid="suspended">
            Suspended {byOn(o.suspension.by_label ?? o.suspension.by, o.suspension.at)}. <span className="text-slate-500">Reason: </span>{sentence(o.suspension.reason)}
          </p>
        ) : (
          <p className="text-sm" data-testid="not-suspended">This agent is not suspended.</p>
        )}
        {o.suspension.suspended ? (
          o.may_resume ? (
            <ReasonForm testid="resume-form" label="Why it may run again" button="Let it run again" pending={resume.isPending} error={resume.error} onSend={(reason) => resume.mutate({ reason }, { onSuccess: () => notify("The agent may run again.") })} />
          ) : (
            <p className="mt-2 text-sm text-slate-600" data-testid="cannot-resume">{o.may_resume_reasons.length ? sentence(o.may_resume_reasons[0]) : "You may not resume this agent."}</p>
          )
        ) : o.may_suspend ? (
          <ReasonForm testid="suspend-form" label="Why it is suspended" button="Suspend this agent" pending={suspend.isPending} error={suspend.error} onSend={(reason) => suspend.mutate({ reason }, { onSuccess: () => notify("The agent is suspended.") })} />
        ) : (
          <p className="mt-2 text-sm text-slate-600" data-testid="cannot-suspend">{o.may_suspend_reasons.length ? sentence(o.may_suspend_reasons[0]) : "You may not suspend this agent."}</p>
        )}
        {/* While the agent is suspended the newest entry is the state shown above, so only the earlier ones are listed. */}
        {(o.suspension.suspended ? o.suspension_history.slice(1) : o.suspension_history).length > 0 && (
          <ul className="mt-3 space-y-1 text-sm" data-testid="suspension-history">
            {(o.suspension.suspended ? o.suspension_history.slice(1) : o.suspension_history).map((h, i) => (
              <li key={i}>{h.action === "suspended" ? "Suspended" : "Let run again"} {byOn(h.by_label ?? h.by, h.at)}. <span className="text-slate-500">Reason: </span>{sentence(h.reason)}</li>
            ))}
          </ul>
        )}
      </Section>

      <Section level="section" title="Incidents" description="Something that went wrong with this agent, what the organisation has done about it, and whom it has told.">
        {o.incidents.length === 0 ? <p className="text-sm text-slate-600" data-testid="no-incidents">No incident has been reported.</p> : (
          <ul className="space-y-2 text-sm" data-testid="incident-list">
            {o.incidents.map((i) => (
              <li key={i.id} className="rounded border border-slate-200 p-3">
                <p>{sentence(i.what)}</p>
                <p className="text-slate-600">Reported {byOn(i.reported_by_label ?? i.reported_by, i.reported_at)}.</p>
                {i.reported_onward && <p><span className="text-slate-500">Told: </span>{sentence(i.reported_onward)}</p>}
                {i.action_taken && <p><span className="text-slate-500">Done: </span>{sentence(i.action_taken)}</p>}
              </li>
            ))}
          </ul>
        )}
        {o.may_report ? <IncidentForm agentId={agentId} /> : <p className="mt-2 text-sm text-slate-600" data-testid="cannot-report">{o.may_report_reasons.length ? sentence(o.may_report_reasons[0]) : "You may not report an incident."}</p>}
      </Section>

      <Section level="section" title="How its output is reviewed" description="How the people who review what this agent produces have decided. Counted for the agent, never for a person.">
        <p className="text-sm" data-testid="review-figures">
          {f.reviewed === 0
            ? "Nobody has reviewed what this agent produced yet."
            : `${f.reviewed} reviewed: ${f.accepted_unchanged} accepted as they were, ${f.changed} changed, ${f.refused} refused. ${f.different_people} different ${f.different_people === 1 ? "person" : "people"} decided${f.median_seconds !== null ? `, and a decision took ${f.median_seconds} ${f.median_seconds === 1 ? "second" : "seconds"} at the middle` : ""}.`}
        </p>
      </Section>
    </>
  );
}

const PROVIDED_WORDS = { records: "Records kept", declared: "Declared, not checked", "not provided": "Not provided" } as const;

function PackSection({ agentId }: { agentId: string }) {
  const make = useMakeEvidencePack(agentId);
  const chain = useCheckChain(agentId);
  const [mapping, setMapping] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const download = () => {
    if (!make.data) return;
    const blob = new Blob([JSON.stringify(make.data, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `evidence-pack-${agentId}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  };
  return (
    <Section level="section" title="Evidence pack" description="Everything the platform can back about this agent over a period: its risk level history, each version that ran and who approved it, its runs, how its output was reviewed, suspensions and incidents. It holds identifiers, counts, hashes and times, and no personal data. Making a pack is recorded.">
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <label className="flex flex-col gap-1">
          From
          <input type="date" data-testid="pack-start" value={start} onChange={(e) => setStart(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
        </label>
        <label className="flex flex-col gap-1">
          To
          <input type="date" data-testid="pack-end" value={end} onChange={(e) => setEnd(e.target.value)} className="rounded border border-slate-400 px-2 py-1" />
        </label>
        <button type="button" data-testid="pack-make" disabled={make.isPending} onClick={() => make.mutate({ start: start || undefined, end: end ? `${end}T23:59:59Z` : undefined })} className="btn btn-primary">
          {make.isPending ? "Making" : "Make the evidence pack"}
        </button>
        <p className="text-slate-600">Without dates, the last 90 days.</p>
      </div>
      <div className="mt-4 text-sm">
        <button type="button" data-testid="chain-check" disabled={chain.isPending} onClick={() => chain.mutate()} className="btn btn-secondary">
          {chain.isPending ? "Checking" : "Check that the records have not been changed"}
        </button>
        {chain.error ? <div role="alert" className="mt-2 text-red-800"><Reasons error={chain.error} fallback="The records could not be checked." /></div> : null}
        {chain.data && (
          <div className="mt-2" data-testid="chain-result">
            {chain.data.ok ? (
              <p>{chain.data.checked} records checked. None was changed, removed or added outside the platform.</p>
            ) : (
              <div className="text-red-800">
                <p className="font-medium">{chain.data.problems.length} problem{chain.data.problems.length === 1 ? "" : "s"} found in {chain.data.checked} records.</p>
                <ul className="list-disc pl-5" data-testid="chain-problems">
                  {chain.data.problems.map((p, i) => (
                    <li key={i}>{p.what} ({p.kind.replace("_", " ")})</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
      {make.error ? <div role="alert" className="mt-2 text-sm text-red-800"><Reasons error={make.error} fallback="The evidence pack could not be made." /></div> : null}
      {make.data && (
        <div className="mt-3 space-y-1 text-sm" data-testid="pack-result">
          <p>
            Made for {make.data.pack.period.from.slice(0, 10)} to {make.data.pack.period.to.slice(0, 10)}. <span className="text-slate-500">Hash: </span>
            <code className="break-all text-xs">{make.data.pack_hash}</code>
          </p>
          <p data-testid="pack-integrity">
            Records in the chain: {make.data.pack.integrity.checked}. {make.data.pack.integrity.ok ? "Nothing was changed." : "Something was changed or added outside the platform."} <span className="text-slate-500">Head: </span>
            <code className="break-all text-xs">{make.data.pack.integrity.head}</code>
          </p>
          <button type="button" data-testid="pack-download" onClick={download} className="btn btn-secondary">Download it as a file</button>
          <button type="button" data-testid="pack-mapping-toggle" onClick={() => setMapping(!mapping)} className="btn btn-link ml-3">
            {mapping ? "Hide which records support which outcome" : "Show which records support which outcome"}
          </button>
          {mapping && (
            <div className="mt-3" data-testid="pack-mapping">
              <p className="mb-2 text-slate-700">{make.data.pack.framework_mapping.about}</p>
              <div className="data-card">
                <table className="data-table w-full">
                  <thead>
                    <tr>
                      <th>Outcome</th>
                      <th>NIST framework</th>
                      <th>EU law</th>
                      <th>In this pack</th>
                      <th>What the records are</th>
                    </tr>
                  </thead>
                  <tbody>
                    {make.data.pack.framework_mapping.rows.map((row) => (
                      <tr key={row.outcome} className="align-top">
                        <td>{row.outcome}</td>
                        <td>{row.nist_ai_rmf}</td>
                        <td>{row.eu_ai_act}</td>
                        <td data-testid={`mapping-${row.provided.replace(" ", "-")}`}>{PROVIDED_WORDS[row.provided]}</td>
                        <td>{row.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="mt-2 list-disc pl-5 text-xs text-slate-600">
                {make.data.pack.framework_mapping.sources.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Section>
  );
}

export function AgentOversightPanel({ agentId }: { agentId: string }) {
  return (
    <div className="mt-8 space-y-6" data-testid="oversight-panel">
      <RiskSection agentId={agentId} />
      <SponsorSection agentId={agentId} />
      <ChangesSection agentId={agentId} />
      <OversightSection agentId={agentId} />
      <PackSection agentId={agentId} />
    </div>
  );
}
