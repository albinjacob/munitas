/**
 * Overseeing agents: the risk levels an organisation writes, the level each agent is held at, the approval of a version that widens an agent, suspending an agent, incidents, the
 * registry of every agent, and the evidence pack.
 *
 * Anybody signed in may read these for their own organisation. Who may write is decided by the policy engine, and the platform's refusal says why.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

export interface RiskLevel {
  key: string;
  name: string;
  description: string;
  framework_label: string | null;
  /** Whether widening an agent at this level needs the officer as well as the department. */
  second_approver: boolean;
  /** Whether an agent at this level may not be deployed at all. */
  blocks: boolean;
  /** How many days may pass before an agent at this level has to be re-confirmed. Empty for never. */
  reconfirm_days: number | null;
}

export interface RiskLevelList {
  /** Empty for the built-in list. */
  version: number | null;
  levels: RiskLevel[];
  enforce: boolean;
  reason: string | null;
  set_by: string | null;
  set_by_label: string | null;
  set_at: string | null;
  built_in: boolean;
}

export interface RiskLevelsPage {
  current: RiskLevelList;
  history: Omit<RiskLevelList, "built_in">[];
  may_manage: boolean;
  may_manage_reasons: string[];
  /** Who writes the levels, and who confirms an agent's level, in words the platform builds from its rules. */
  manage_words: string;
  confirm_words: string;
}

export interface RiskRecord {
  id: string;
  kind: "proposed" | "confirmed";
  level_key: string;
  level_name: string;
  framework_label: string | null;
  second_approver: boolean;
  blocks: boolean;
  list_version: number | null;
  intended_purpose: string;
  affected: string;
  reasoning: string;
  recorded_by: string;
  recorded_by_label: string | null;
  recorded_at: string;
}

export interface AgentRisk {
  classified: boolean;
  confirmed: RiskRecord | null;
  proposal_waiting: RiskRecord | null;
  held_at: { key: string; name: string; second_approver: boolean; blocks: boolean };
  held_at_because: string;
  enforce: boolean;
  history: RiskRecord[];
  levels: RiskLevelList;
  may_confirm: boolean;
  may_confirm_reasons: string[];
  /** Who confirms an agent's level, in words the platform builds from its rules. */
  confirm_words: string;
}

/** The entry of the approved tools list that covered a tool a version adds, so that the record says why nobody was asked. */
export interface CoveredBy {
  tool: string;
  tier: number;
  tier_name: string;
  ceiling: string;
  ceiling_name: string;
  conditions: string;
  reason: string;
  list_version: number;
  set_by_label: string | null;
  set_at: string | null;
}

export interface Change {
  kind: string;
  rank: number;
  label: string;
  what: string;
  widens: boolean;
  /** Only on a tool the approved tools list covered. */
  covered_by?: CoveredBy;
}

export interface ChangeDecision {
  part: "department" | "officer";
  decision: "approved" | "refused";
  reason: string;
  decided_by: string;
  decided_by_label: string | null;
  decided_at: string;
  /** Who the decision was for, in words: the department, or the role the person gave the officer's part as. */
  decided_as: string;
}

/** For a first version: the level proposed with it, and the level confirmed if one already is. The officer decides both together. */
export interface FirstSubmission {
  proposal: RiskRecord | null;
  confirmed: RiskRecord | null;
}

export interface ChangeApproval {
  id: string;
  first_submission: FirstSubmission | null;
  agent_id: string;
  agent_name: string | null;
  department_name: string | null;
  agent_version_id: string;
  version: number | null;
  previous_version: number | null;
  changes: Change[];
  widening: boolean;
  needs_officer: boolean;
  inside_envelope: boolean | null;
  submitted_by: string;
  submitted_by_label: string | null;
  created_at: string;
  state: "not_needed" | "approved_in_advance" | "waiting" | "approved" | "refused";
  waiting_for: string[];
  /** Who gives the officer's part, in words the platform builds from its rules: "the AI risk owner". */
  officer_words: string;
  decisions: ChangeDecision[];
  can_decide: Record<string, { allowed: boolean; reasons: string[] }>;
}

export interface Suspension {
  suspended: boolean;
  reason: string | null;
  by: string | null;
  by_label: string | null;
  at: string | null;
}

export interface Incident {
  id: string;
  what: string;
  reported_onward: string | null;
  action_taken: string | null;
  reported_by: string;
  reported_by_label: string | null;
  reported_at: string;
}

export interface ReviewFigures {
  reviewed: number;
  accepted_unchanged: number;
  changed: number;
  refused: number;
  different_people: number;
  median_seconds: number | null;
}

export interface AgentOversight {
  suspension: Suspension;
  suspension_history: { action: "suspended" | "resumed"; reason: string; by: string; by_label: string | null; at: string }[];
  incidents: Incident[];
  review_figures: ReviewFigures;
  may_suspend: boolean;
  may_suspend_reasons: string[];
  may_resume: boolean;
  may_resume_reasons: string[];
  may_report: boolean;
  may_report_reasons: string[];
}

export interface RegistryFlag {
  key: string;
  label: string;
}

export interface RegistryRow {
  id: string;
  sponsor: string | null;
  reconfirm_due_at: string | null;
  name: string;
  department_name: string | null;
  registered_by: string;
  registered_by_label: string | null;
  purpose: string;
  run_by: "platform" | "outside";
  risk_level: { name: string; framework_label: string | null; confirmed_at: string } | null;
  proposal_waiting: boolean;
  active_version: number | null;
  newest_version: number | null;
  tools: { state: string; list: string[] };
  model_id: string | null;
  model_hosting: string | null;
  hosts: string[];
  last_run_at: string | null;
  changes_waiting: number;
  suspended: boolean;
  flags: RegistryFlag[];
}

export interface Registry {
  agents: RegistryRow[];
  stale_after_days: number;
  enforce: boolean;
}

export interface MappingRow {
  outcome: string;
  nist_ai_rmf: string;
  eu_ai_act: string;
  supported_by: string[];
  all_present_in_this_pack: boolean;
  provided: "records" | "declared" | "not provided";
  note: string;
}

export interface ChainProblem {
  seq: number | null;
  kind: string;
  record_id: string;
  what: string;
}

export interface ChainCheck {
  ok: boolean;
  checked: number;
  head: string;
  problems: ChainProblem[];
  before_chain: number;
}

export interface EvidencePack {
  pack: Record<string, unknown> & {
    period: { from: string; to: string };
    human_oversight: ReviewFigures;
    integrity: ChainCheck & { about: string };
    framework_mapping: { about: string; sources: string[]; rows: MappingRow[] };
  };
  pack_hash: string;
}

export interface Sponsorship {
  sponsor: { id: string; sponsor_id: string; sponsor_label: string | null; assigned_by: string; assigned_by_label: string | null; reason: string; assigned_at: string } | null;
  sponsor_can_act: boolean;
  reconfirm: { days: number | null; last_at: string | null; due_at: string | null; overdue: boolean; due_soon: boolean };
  history: NonNullable<Sponsorship["sponsor"]>[];
  reconfirmations: { id: string; confirmed_by: string; confirmed_by_label: string | null; note: string; confirmed_at: string }[];
  candidates: { id: string; label: string }[];
  may_assign: boolean;
  may_assign_reasons: string[];
  may_reconfirm: boolean;
  may_reconfirm_reasons: string[];
}

export interface ToolEntry {
  tool: string;
  description: string;
  reach: string;
  tier: number;
  ceiling: string;
  conditions: string;
  reason: string;
  review_by: string;
  /** Whether the date to look at it again has passed. An entry past it approves nothing. */
  overdue?: boolean;
}

export interface ToolList {
  /** Empty when the organisation has written none. */
  version: number | null;
  entries: ToolEntry[];
  reason: string | null;
  set_by: string | null;
  set_by_label: string | null;
  set_at: string | null;
}

export interface ToolListPage {
  current: ToolList;
  history: ToolList[];
  levels: { key: string; name: string; blocks: boolean }[];
  tiers: Record<string, string>;
  reach: Record<string, string>;
  tier_one_reach: string[];
  may_manage: boolean;
  may_manage_reasons: string[];
}

export function useToolList() {
  return useQuery({ queryKey: ["agent-tool-list"], queryFn: () => api.get<ToolListPage>("/agent-tool-list") });
}

export function useWriteToolList() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { entries: Omit<ToolEntry, "overdue">[]; reason: string }) => api.post<{ current: ToolList }>("/agent-tool-list", v),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent-tool-list"] }),
  });
}

export function useRiskLevels() {
  return useQuery({ queryKey: ["risk-levels"], queryFn: () => api.get<RiskLevelsPage>("/risk-levels") });
}

export function useWriteRiskLevels() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { levels: Omit<RiskLevel, "key">[]; enforce: boolean; reason: string }) => api.post<{ current: RiskLevelList }>("/risk-levels", v),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["risk-levels"] });
      queryClient.invalidateQueries({ queryKey: ["agent-risk"] });
    },
  });
}

export function useAgentRisk(agentId: string | undefined) {
  return useQuery({ queryKey: ["agent-risk", agentId], queryFn: () => api.get<AgentRisk>(`/agents/${agentId}/risk`), enabled: Boolean(agentId) });
}

/** Refreshes everything the console keeps about one agent's oversight, so a screen never shows what was true before the change. */
export function invalidateAgent(queryClient: ReturnType<typeof useQueryClient>, agentId: string) {
  for (const key of ["agent-risk", "agent-oversight", "agent-change-approvals", "agent-registry", "agent-changes"]) queryClient.invalidateQueries({ queryKey: [key] });
  queryClient.invalidateQueries({ queryKey: ["agent", agentId] });
}

export function useProposeLevel(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { level_key: string; intended_purpose: string; affected: string; reasoning: string }) => api.post(`/agents/${agentId}/risk/proposals`, v),
    onSuccess: () => invalidateAgent(queryClient, agentId),
  });
}

export function useConfirmLevel(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { level_key: string; reasoning: string; affected?: string }) => api.post(`/agents/${agentId}/risk/confirmations`, v),
    onSuccess: () => invalidateAgent(queryClient, agentId),
  });
}

export function useChangeApprovals(state?: string) {
  return useQuery({ queryKey: ["agent-changes", state ?? "all"], queryFn: () => api.get<{ approvals: ChangeApproval[]; enforce: boolean }>("/agent-change-approvals", state ? { state } : undefined) });
}

export function useAgentChangeApprovals(agentId: string | undefined) {
  return useQuery({ queryKey: ["agent-change-approvals", agentId], queryFn: () => api.get<{ approvals: ChangeApproval[]; enforce: boolean }>(`/agents/${agentId}/change-approvals`), enabled: Boolean(agentId) });
}

export function useDecideChange() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { approvalId: string; part: "department" | "officer"; decision: "approved" | "refused"; reason: string; agentId: string }) =>
      api.post(`/agent-change-approvals/${v.approvalId}/decisions`, { part: v.part, decision: v.decision, reason: v.reason }),
    onSuccess: (_d, v) => invalidateAgent(queryClient, v.agentId),
  });
}

export function useAgentOversight(agentId: string | undefined) {
  return useQuery({ queryKey: ["agent-oversight", agentId], queryFn: () => api.get<AgentOversight>(`/agents/${agentId}/oversight`), enabled: Boolean(agentId) });
}

export function useSuspend(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({ mutationFn: (v: { reason: string }) => api.post(`/agents/${agentId}/suspension`, v), onSuccess: () => invalidateAgent(queryClient, agentId) });
}

export function useResume(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({ mutationFn: (v: { reason: string }) => api.post(`/agents/${agentId}/resumption`, v), onSuccess: () => invalidateAgent(queryClient, agentId) });
}

export function useReportIncident(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { what: string; reported_onward?: string; action_taken?: string }) => api.post(`/agents/${agentId}/incidents`, v),
    onSuccess: () => invalidateAgent(queryClient, agentId),
  });
}

export function useSponsorship(agentId: string | undefined) {
  return useQuery({ queryKey: ["agent-sponsorship", agentId], queryFn: () => api.get<Sponsorship>(`/agents/${agentId}/sponsorship`), enabled: Boolean(agentId) });
}

export function useAssignSponsor(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { sponsor_id: string; reason: string }) => api.post(`/agents/${agentId}/sponsor`, v),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent-sponsorship", agentId] });
      queryClient.invalidateQueries({ queryKey: ["agent-registry"] });
    },
  });
}

export function useReconfirm(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { note: string }) => api.post(`/agents/${agentId}/reconfirmation`, v),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent-sponsorship", agentId] });
      queryClient.invalidateQueries({ queryKey: ["agent-registry"] });
    },
  });
}

/** Checks that the agent's oversight records have not been changed. A request on demand, so it is a mutation and not a query that runs by itself. */
export function useCheckChain(agentId: string) {
  return useMutation({ mutationFn: () => api.get<ChainCheck>(`/agents/${agentId}/record-chain`) });
}

export function useDecideFirstSubmission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { approvalId: string; agentId: string; level_key: string; reasoning: string; affected?: string }) =>
      api.post(`/agent-change-approvals/${v.approvalId}/first-submission`, { level_key: v.level_key, reasoning: v.reasoning, affected: v.affected }),
    onSuccess: (_d, v) => invalidateAgent(queryClient, v.agentId),
  });
}

export function useRegistry() {
  return useQuery({ queryKey: ["agent-registry"], queryFn: () => api.get<Registry>("/agent-registry") });
}

/** Makes the evidence pack for an agent and period. Making it is recorded, so it is a request on demand and not a query that runs by itself. */
export function useMakeEvidencePack(agentId: string) {
  return useMutation({ mutationFn: (v: { start?: string; end?: string }) => api.get<EvidencePack>(`/agents/${agentId}/evidence-pack`, v) });
}

export function useReviewRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { runId: string; agentId: string; outcome: "accepted" | "changed" | "refused"; final_text?: string; reason?: string }) =>
      api.post(`/agents/runs/${v.runId}/review`, { outcome: v.outcome, final_text: v.final_text, reason: v.reason }),
    onSuccess: (_d, v) => {
      queryClient.invalidateQueries({ queryKey: ["agent", v.agentId] });
      queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
      queryClient.invalidateQueries({ queryKey: ["agent-oversight", v.agentId] });
    },
  });
}

export function useRefuseRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { runId: string; reason: string; agentId: string }) => api.post(`/agents/runs/${v.runId}/refuse`, { reason: v.reason }),
    onSuccess: (_d, v) => {
      queryClient.invalidateQueries({ queryKey: ["agent", v.agentId] });
      queryClient.invalidateQueries({ queryKey: ["agent-oversight", v.agentId] });
    },
  });
}
