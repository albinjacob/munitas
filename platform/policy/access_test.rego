package munitas.access_test

import data.munitas.access
import rego.v1

future := "2099-01-01T00:00:00Z"

past := "2020-01-01T00:00:00Z"

principal(roles, leases) := {
	"id": "svc-alice",
	"tenant": "t1",
	"roles": roles,
	"leases": leases,
}

ds(class) := {
	"tenant": "t1",
	"version_id": "dv-1",
	"visibility_class": class,
}

lease(over) := object.union(
	{
		"id": "l-1",
		"dataset_version": "dv-1",
		"purpose": "shape exploration",
		"pattern": "strict",
		"approved_by": "svc-bob",
		"revoked": false,
		"expires_at": future,
	},
	over,
)

# A training job may read the training class.
test_standing_access_allowed if {
	access.allow with input as {
		"principal": principal(["training_job"], []),
		"dataset": ds("OPEN_FOR_TRAINING"),
		"purpose": "train model",
	}
}

# The same job may not read raw.
test_below_floor_denied_without_lease if {
	not access.allow with input as {
		"principal": principal(["training_job"], []),
		"dataset": ds("RAW"),
		"purpose": "train model",
	}
}

# A notebook may read raw with a valid, approved lease.
test_lease_grants_below_floor if {
	access.allow with input as {
		"principal": principal(["notebook_explore"], [lease({})]),
		"dataset": ds("RAW"),
		"purpose": "shape exploration",
	}
}

test_expired_lease_denied if {
	not access.allow with input as {
		"principal": principal(["notebook_explore"], [lease({"expires_at": past})]),
		"dataset": ds("RAW"),
		"purpose": "shape exploration",
	}
}

test_revoked_lease_denied if {
	not access.allow with input as {
		"principal": principal(["notebook_explore"], [lease({"revoked": true})]),
		"dataset": ds("RAW"),
		"purpose": "shape exploration",
	}
}

# Many leases on one version have ended over time. The refusal names the most
# recent one, once, not every lease the person has ever held on it.
test_many_ended_leases_give_one_reason_each if {
	d := access.decision with input as {
		"principal": principal(["notebook_explore"], [
			# The most recently revoked is not the one due to run longest: l-mid ran
			# furthest into the future but was withdrawn first of the two later ones.
			lease({"id": "l-old", "revoked": true, "revoked_at": "2026-10-01T00:00:00Z", "expires_at": "2026-10-02T00:00:00Z"}),
			lease({"id": "l-mid", "revoked": true, "revoked_at": "2026-10-02T00:00:00Z", "expires_at": "2040-01-01T00:00:00Z"}),
			lease({"id": "l-new", "revoked": true, "revoked_at": "2026-10-03T00:00:00Z", "expires_at": "2026-10-04T00:00:00Z"}),
			lease({"id": "l-ran-out", "expires_at": past}),
			lease({"id": "l-ran-out-earlier", "expires_at": "2019-01-01T00:00:00Z"}),
		]),
		"dataset": ds("RAW"),
		"purpose": "shape exploration",
	}
	"lease l-new revoked" in d.reasons
	not "lease l-old revoked" in d.reasons
	not "lease l-mid revoked" in d.reasons
	"lease l-ran-out expired" in d.reasons
	not "lease l-ran-out-earlier expired" in d.reasons
}

# A lease that was revoked is not also reported as having expired.
test_revoked_lease_is_not_also_expired if {
	d := access.decision with input as {
		"principal": principal(["notebook_explore"], [lease({"id": "l-1", "revoked": true, "expires_at": past})]),
		"dataset": ds("RAW"),
		"purpose": "shape exploration",
	}
	"lease l-1 revoked" in d.reasons
	not "lease l-1 expired" in d.reasons
}

# Self-approval must not work, even with an otherwise valid lease.
test_self_approved_lease_denied if {
	not access.allow with input as {
		"principal": principal(["notebook_explore"], [lease({"approved_by": "svc-alice"})]),
		"dataset": ds("RAW"),
		"purpose": "shape exploration",
	}
}

# A standing lease (no expiry) grants access exactly like a bounded one would,
# with no timer to run out.
test_standing_lease_never_expires if {
	access.allow with input as {
		"principal": principal(["notebook_explore"], [lease({"expires_at": null})]),
		"dataset": ds("RAW"),
		"purpose": "shape exploration",
	}
}

# Never expiring is not the same as never ending. Revocation still works.
test_standing_lease_still_revocable if {
	not access.allow with input as {
		"principal": principal(["notebook_explore"], [lease({"expires_at": null, "revoked": true})]),
		"dataset": ds("RAW"),
		"purpose": "shape exploration",
	}
}

# Self-approval is denied the same way whether the lease is bounded or
# standing; a lease's lifetime is a separate question from who approved it.
test_standing_lease_self_approval_denied if {
	not access.allow with input as {
		"principal": principal(["notebook_explore"], [lease({"expires_at": null, "approved_by": "svc-alice"})]),
		"dataset": ds("RAW"),
		"purpose": "shape exploration",
	}
}

# A lease is bound to the purpose it was granted for.
test_lease_purpose_must_match if {
	not access.allow with input as {
		"principal": principal(["notebook_explore"], [lease({})]),
		"dataset": ds("RAW"),
		"purpose": "something else",
	}
}

# A simple lease is the custodian's own choice to cover any purpose, made
# once at approval -- not the platform's default, and not available for the
# lease fixture's own purpose text alone; it must be requested explicitly.
test_simple_lease_covers_any_purpose if {
	access.allow with input as {
		"principal": principal(["notebook_explore"], [lease({"pattern": "simple"})]),
		"dataset": ds("UNDER_REVIEW"),
		"purpose": "something else entirely",
	}
}

# RAW is guarded at creation (access_lease's own trigger refuses to create a
# simple lease against it), not re-checked here at read time -- the same
# layering `refuse_standing_lease_for_human` uses for who may hold a standing
# lease. This pins the read-time side that stays true regardless: a strict
# lease against RAW is still bound to its own purpose.
test_raw_lease_still_purpose_bound if {
	not access.allow with input as {
		"principal": principal(["notebook_explore"], [lease({"pattern": "strict"})]),
		"dataset": ds("RAW"),
		"purpose": "something else",
	}
}

test_missing_purpose_denied if {
	not access.allow with input as {
		"principal": principal(["training_job"], []),
		"dataset": ds("OPEN_FOR_TRAINING"),
		"purpose": "",
	}
}

test_cross_tenant_denied if {
	not access.allow with input as {
		"principal": principal(["training_job"], []),
		"dataset": object.union(ds("OPEN_FOR_TRAINING"), {"tenant": "t2"}),
		"purpose": "train model",
	}
}

# Agents do not read below published by default.
test_agent_denied_below_published if {
	not access.allow with input as {
		"principal": principal(["agent_runtime"], []),
		"dataset": ds("OPEN_FOR_TRAINING"),
		"purpose": "triage",
	}
}

# --------------------------------------------------- the new human roles --

# Running the system is not a licence to read what is in it. This is the claim
# people will doubt, so it is tested directly.
test_platform_admin_denied_raw if {
	not access.allow with input as {
		"principal": principal(["platform_admin"], []),
		"dataset": ds("RAW"),
		"purpose": "investigating a failure",
	}
}

test_platform_admin_allowed_published if {
	access.allow with input as {
		"principal": principal(["platform_admin"], []),
		"dataset": ds("PUBLISHED"),
		"purpose": "investigating a failure",
	}
}

# Oversight is not access. The data protection officer reads every decision and
# no more data than anybody else.
test_dpo_denied_raw if {
	not access.allow with input as {
		"principal": principal(["dpo"], []),
		"dataset": ds("RAW"),
		"purpose": "compliance review",
	}
}

# Support and reliability work is done with telemetry, not with records.
test_hybridops_denied_raw if {
	not access.allow with input as {
		"principal": principal(["hybridops"], []),
		"dataset": ds("RAW"),
		"purpose": "incident triage",
	}
}

# Deciding who may read something is not the same as reading it.
test_custodian_denied_raw_without_lease if {
	not access.allow with input as {
		"principal": principal(["data_custodian"], []),
		"dataset": ds("RAW"),
		"purpose": "reviewing a request",
	}
}

# ------------------------------------------------------------ approvals --

approver(id, roles) := {"id": id, "roles": roles}

request(who) := {"principal": who, "dataset_version": "dv-1"}

# A request made by a human on a workload's behalf. `requested_by` is the
# human; `who` stays the reading principal, exactly as it does for a request
# made for oneself, because policy must never treat the two as the same
# question.
request_on_behalf_of(who, requested_by) := {
	"principal": who,
	"dataset_version": "dv-1",
	"requested_by": requested_by,
}

asset(custodian) := {"dataset_version": "dv-1", "custodian": custodian}

test_custodian_may_approve_their_own_department if {
	access.may_approve with input as {
		"approver": approver("cust-hartley", ["data_custodian"]),
		"request": request("sam-researcher"),
		"asset": asset("cust-hartley"),
	}
}

# The rule that makes custodianship mean something: a custodian of one
# department cannot approve access to another department's asset.
test_custodian_cannot_approve_another_department if {
	not access.may_approve with input as {
		"approver": approver("cust-hartley", ["data_custodian"]),
		"request": request("sam-researcher"),
		"asset": asset("cust-okonjo"),
	}
}

test_custodian_cannot_approve_their_own_request if {
	not access.may_approve with input as {
		"approver": approver("cust-hartley", ["data_custodian"]),
		"request": request("cust-hartley"),
		"asset": asset("cust-hartley"),
	}
}

# A human requesting a lease for a workload, approved by somebody who is
# neither the workload nor the requesting human.
test_on_behalf_of_approval_allowed if {
	access.may_approve with input as {
		"approver": approver("cust-hartley", ["data_custodian"]),
		"request": request_on_behalf_of("svc-trainer", "eng-devi"),
		"asset": asset("cust-hartley"),
	}
}

# The same self-approval rule, one identity removed: a custodian cannot
# approve a request they themselves asked for on a workload's behalf, even
# though the reading principal on the request is the workload, not them.
test_self_approval_by_requester_denied if {
	not access.may_approve with input as {
		"approver": approver("cust-hartley", ["data_custodian"]),
		"request": request_on_behalf_of("svc-trainer", "cust-hartley"),
		"asset": asset("cust-hartley"),
	}
}

# The data protection officer must not approve, even for an asset they oversee.
# Making them an approver would have them determine the purposes and means of
# processing, which is what their independence exists to prevent.
test_dpo_cannot_approve if {
	not access.may_approve with input as {
		"approver": approver("dpo-nakamura", ["dpo"]),
		"request": request("sam-researcher"),
		"asset": asset("dpo-nakamura"),
	}
}

# The administrator operates the system that records approvals, so they cannot
# also grant them.
test_platform_admin_cannot_approve if {
	not access.may_approve with input as {
		"approver": approver("ops-priya", ["platform_admin"]),
		"request": request("sam-researcher"),
		"asset": asset("ops-priya"),
	}
}

test_unowned_asset_cannot_be_approved if {
	not access.may_approve with input as {
		"approver": approver("cust-hartley", ["data_custodian"]),
		"request": request("sam-researcher"),
		"asset": {"dataset_version": "dv-1"},
	}
}

test_refusal_to_approve_carries_a_reason if {
	d := access.approval_decision with input as {
		"approver": approver("cust-hartley", ["data_custodian"]),
		"request": request("sam-researcher"),
		"asset": asset("cust-okonjo"),
	}
	d.allow == false
	count(d.reasons) > 0
}

# --------------------------------------------------------------- export --

asset_from(provenance, klass) := {
	"provenance": provenance,
	"visibility_class": klass,
}

# Already public, and released. It can go.
test_public_ga_may_leave if {
	access.may_export with input as {"dataset": asset_from("external_public", "PUBLISHED")}
}

# The case people expect to be allowed and which is the reason this rule exists.
# Same class as the corpus above, refused, because it started as regulated data
# and de-identification does not make it publishable.
test_deidentified_clinical_ga_may_not_leave if {
	not access.may_export with input as {"dataset": asset_from("internal_regulated", "PUBLISHED")}
}

test_licensed_data_may_not_leave if {
	not access.may_export with input as {"dataset": asset_from("external_licensed", "PUBLISHED")}
}

# --------------------------------------------------- licensed export --

asset_licensed(export_unmodified, export_modified) := {
	"provenance": "external_licensed",
	"visibility_class": "PUBLISHED",
	"license_tag": "cc-by-nd-4.0",
	"license_export_unmodified": export_unmodified,
	"license_export_modified": export_modified,
}

# A no-derivatives-style licence: fine as fetched.
test_licensed_export_unmodified_allowed_when_licence_grants_it if {
	access.may_export with input as {
		"dataset": asset_licensed(true, false),
		"export": {"modified": false},
	}
}

# The same licence, once the platform has modified the data: refused.
test_licensed_export_modified_refused_when_licence_forbids_it if {
	not access.may_export with input as {
		"dataset": asset_licensed(true, false),
		"export": {"modified": true},
	}
}

# A licence that grants neither aspect (non-commercial, or nothing found):
# refused either way.
test_licensed_export_refused_both_ways_when_licence_grants_neither if {
	not access.may_export with input as {
		"dataset": asset_licensed(false, false),
		"export": {"modified": false},
	}
	not access.may_export with input as {
		"dataset": asset_licensed(false, false),
		"export": {"modified": true},
	}
}

# A licence that grants modified export too (share-alike, permissive).
test_licensed_export_modified_allowed_when_licence_grants_it if {
	access.may_export with input as {
		"dataset": asset_licensed(true, true),
		"export": {"modified": true},
	}
}

test_licensed_export_refusal_names_the_licence if {
	d := access.export_decision with input as {
		"dataset": asset_licensed(true, false),
		"export": {"modified": true},
	}
	d.allow == false
	some r in d.reasons
	contains(r, "cc-by-nd-4.0")
}

# external_public behaviour is unaffected by any of this: no license fields
# in the input at all, same as before this change.
test_public_export_still_unaffected_by_license_rules if {
	access.may_export with input as {
		"dataset": asset_from("external_public", "PUBLISHED"),
		"export": {"modified": true},
	}
}

# Public origin is not enough on its own. Nothing leaves before it is released.
test_public_but_unreleased_may_not_leave if {
	not access.may_export with input as {"dataset": asset_from("external_public", "RAW")}
}

test_refusal_to_export_names_the_provenance if {
	d := access.export_decision with input as {
		"dataset": asset_from("internal_regulated", "PUBLISHED"),
	}
	d.allow == false
	count(d.reasons) > 0
}

# -------------------------------------------------------------- release --

declared(basis, by, confirmed_by) := {"dataset": {
	"declaration_basis": basis,
	"declared_by": by,
	"classification_confirmed_by": confirmed_by,
}}

# One person's judgement is not enough to widen access to their own upload.
test_asserted_and_unconfirmed_cannot_be_released if {
	not access.may_release with input as {"dataset": {
		"declaration_basis": "asserted",
		"declared_by": "eng-devi",
	}}
}

test_asserted_and_confirmed_can_be_released if {
	access.may_release with input as declared("asserted", "eng-devi", "cust-hartley")
}

# The same person confirming their own claim is not a second opinion.
test_confirming_your_own_claim_does_not_count if {
	not access.may_release with input as declared("asserted", "eng-devi", "eng-devi")
}

# The platform fetched it and can show where from, so nobody was believed and
# there is nothing to confirm.
test_verified_source_needs_no_confirmation if {
	access.may_release with input as {"dataset": {
		"declaration_basis": "verified_source",
	}}
}

# Ordinary pipeline output, where nobody declared anything and the class gate is
# what governs promotion.
test_undeclared_data_is_governed_by_the_class_gate if {
	access.may_release with input as {"dataset": {}}
}

test_refusal_to_release_explains_itself if {
	d := access.release_decision with input as {"dataset": {
		"declaration_basis": "asserted",
		"declared_by": "eng-devi",
	}}
	d.allow == false
	count(d.reasons) > 0
}

# Denials carry a reason, which is what lands in the audit record.
test_denial_has_reason if {
	d := access.decision with input as {
		"principal": principal(["training_job"], []),
		"dataset": ds("RAW"),
		"purpose": "train model",
	}
	d.allow == false
	count(d.reasons) > 0
}

test_reviewer_may_decide_a_gate if {
	access.may_decide_gate with input as {
		"decider": {"id": "canary-reviewer", "roles": ["deid_reviewer"]},
		"gate": {"triggered_by": "canary-engineer"},
	}
}

test_custodian_may_not_decide_a_gate if {
	not access.may_decide_gate with input as {
		"decider": {"id": "canary-custodian", "roles": ["data_custodian"]},
		"gate": {"triggered_by": "canary-engineer"},
	}
}

test_dpo_may_not_decide_a_gate if {
	not access.may_decide_gate with input as {
		"decider": {"id": "canary-dpo", "roles": ["dpo"]},
		"gate": {"triggered_by": "canary-engineer"},
	}
}

test_reviewer_may_not_decide_their_own_run if {
	not access.may_decide_gate with input as {
		"decider": {"id": "canary-reviewer", "roles": ["deid_reviewer"]},
		"gate": {"triggered_by": "canary-reviewer"},
	}
}

test_gate_refusal_says_why if {
	decision := access.gate_decision with input as {
		"decider": {"id": "canary-custodian", "roles": ["data_custodian"]},
		"gate": {"triggered_by": "canary-engineer"},
	}
	decision.allow == false
	"this principal holds no role that may decide a de-identification gate" in decision.reasons
}

test_operator_may_start_a_pipeline_run if {
	access.may_start_pipeline with input as {
		"operator": {"id": "canary-engineer", "roles": ["pipeline_operator"]},
	}
}

test_reviewer_may_not_start_a_pipeline_run if {
	not access.may_start_pipeline with input as {
		"operator": {"id": "canary-reviewer", "roles": ["deid_reviewer"]},
	}
}

test_admin_may_not_start_a_pipeline_run if {
	not access.may_start_pipeline with input as {
		"operator": {"id": "canary-admin", "roles": ["platform_admin"]},
	}
}

test_architect_may_approve_egress_hosts if {
	access.may_approve_egress_hosts with input as {
		"approver": {"id": "canary-architect", "roles": ["network_architect"]},
		"version": {"submitted_by": "canary-engineer"},
	}
}

test_engineer_may_not_approve_their_own_agent_egress_hosts if {
	not access.may_approve_egress_hosts with input as {
		"approver": {"id": "canary-architect", "roles": ["network_architect"]},
		"version": {"submitted_by": "canary-architect"},
	}
}

test_custodian_may_not_approve_egress_hosts if {
	not access.may_approve_egress_hosts with input as {
		"approver": {"id": "canary-custodian", "roles": ["data_custodian"]},
		"version": {"submitted_by": "canary-engineer"},
	}
}

test_egress_approval_refusal_says_why if {
	decision := access.egress_approval_decision with input as {
		"approver": {"id": "canary-custodian", "roles": ["data_custodian"]},
		"version": {"submitted_by": "canary-engineer"},
	}
	decision.allow == false
	"this principal holds no role that may approve an agent version's requested hosts" in decision.reasons
}

test_egress_approval_self_submission_says_why if {
	decision := access.egress_approval_decision with input as {
		"approver": {"id": "canary-architect", "roles": ["network_architect"]},
		"version": {"submitted_by": "canary-architect"},
	}
	decision.allow == false
	"an agent version's own submitter cannot approve its requested hosts" in decision.reasons
}

test_pipeline_start_refusal_says_why if {
	decision := access.pipeline_start_decision with input as {
		"operator": {"id": "canary-reviewer", "roles": ["deid_reviewer"]},
	}
	decision.allow == false
	"this principal holds no role that may start a pipeline run" in decision.reasons
}

# ------------------------------------------------------------------
# Housekeeping: who sees storage telemetry, and who may destroy bytes.

test_support_sees_platform_wide_housekeeping if {
	access.may_see_housekeeping with input as {
		"scope": "platform",
		"viewer": {"id": "ops-priya", "tenant_id": "health", "roles": ["hybridops"]},
	}
}

test_platform_admin_sees_platform_wide_housekeeping if {
	access.may_see_housekeeping with input as {
		"scope": "platform",
		"viewer": {"id": "admin", "tenant_id": "health", "roles": ["platform_admin"]},
	}
}

# A custodian runs an organisation, not the machine. Every tenant's storage is
# not theirs to look at, however ordinary their own half of it is.
test_custodian_does_not_see_platform_wide_housekeeping if {
	not access.may_see_housekeeping with input as {
		"scope": "platform",
		"viewer": {"id": "cust-hartley", "tenant_id": "health", "roles": ["data_custodian"]},
	}
}

test_researcher_sees_no_housekeeping_at_all if {
	not access.may_see_housekeeping with input as {
		"scope": "platform",
		"viewer": {"id": "sam", "tenant_id": "health", "roles": ["notebook_explore"]},
	}
}

test_custodian_sees_their_own_organisation if {
	access.may_see_housekeeping with input as {
		"scope": "tenant",
		"tenant_id": "health",
		"viewer": {"id": "cust-hartley", "tenant_id": "health", "roles": ["data_custodian"]},
	}
}

test_dpo_sees_their_own_organisation if {
	access.may_see_housekeeping with input as {
		"scope": "tenant",
		"tenant_id": "health",
		"viewer": {"id": "dpo-nakamura", "tenant_id": "health", "roles": ["dpo"]},
	}
}

# The one that matters: a custodian of one organisation is refused another's,
# which is the same boundary every other read in this platform respects.
test_custodian_refused_another_organisation if {
	not access.may_see_housekeeping with input as {
		"scope": "tenant",
		"tenant_id": "finance",
		"viewer": {"id": "cust-hartley", "tenant_id": "health", "roles": ["data_custodian"]},
	}
}

test_refusal_names_whose_organisation_it_is if {
	decision := access.housekeeping_decision with input as {
		"scope": "tenant",
		"tenant_id": "finance",
		"viewer": {"id": "cust-hartley", "tenant_id": "health", "roles": ["data_custodian"]},
	}
	some reason in decision.reasons
	contains(reason, "finance")
}

test_support_sees_any_organisations_own_view if {
	access.may_see_housekeeping with input as {
		"scope": "tenant",
		"tenant_id": "finance",
		"viewer": {"id": "ops-priya", "tenant_id": "health", "roles": ["hybridops"]},
	}
}

test_platform_admin_may_free_storage if {
	access.may_free_storage with input as {
		"actor": {"id": "admin", "roles": ["platform_admin"]},
		"reason": "canary leftovers older than a day",
	}
}

# Seeing that bytes could be freed is not permission to free them. Support and
# reliability diagnose; the administrator acts.
test_support_may_not_free_storage if {
	not access.may_free_storage with input as {
		"actor": {"id": "ops-priya", "roles": ["hybridops"]},
		"reason": "canary leftovers older than a day",
	}
}

test_custodian_may_not_free_storage if {
	not access.may_free_storage with input as {
		"actor": {"id": "cust-hartley", "roles": ["data_custodian"]},
		"reason": "tidying up",
	}
}

test_freeing_without_a_reason_is_refused if {
	not access.may_free_storage with input as {
		"actor": {"id": "admin", "roles": ["platform_admin"]},
		"reason": "   ",
	}
}

test_refusal_says_a_reason_is_recorded if {
	decision := access.free_storage_decision with input as {
		"actor": {"id": "admin", "roles": ["platform_admin"]},
		"reason": "",
	}
	some r in decision.reasons
	contains(r, "recorded beside what was freed")
}

# ------------------------------------------------------------------
# Holding a role: asked for by one person, decided by another.

test_a_person_may_ask_for_a_role if {
	access.may_request_role with input as {
		"requester": {"id": "sam-researcher", "roles": ["notebook_explore"]},
		"request": {
			"principal": "sam-researcher",
			"role": "deid_reviewer",
			"justification": "covering reviews while Imani is away",
		},
	}
}

test_nobody_asks_on_somebody_elses_behalf if {
	not access.may_request_role with input as {
		"requester": {"id": "ops-priya", "roles": ["platform_admin"]},
		"request": {
			"principal": "sam-researcher",
			"role": "deid_reviewer",
			"justification": "sorting out cover",
		},
	}
}

test_asking_without_a_reason_is_refused if {
	not access.may_request_role with input as {
		"requester": {"id": "sam-researcher", "roles": ["notebook_explore"]},
		"request": {
			"principal": "sam-researcher",
			"role": "deid_reviewer",
			"justification": "   ",
		},
	}
}

# The one that matters most: the role that administers roles cannot be
# obtained from inside the running system, by anybody, for any reason.
test_the_administering_role_cannot_be_asked_for if {
	not access.may_request_role with input as {
		"requester": {"id": "ops-priya", "roles": ["platform_admin"]},
		"request": {
			"principal": "ops-priya",
			"role": "platform_admin",
			"justification": "second administrator for cover",
		},
	}
}

test_refusal_says_why_the_administering_role_is_refused if {
	decision := access.role_request_decision with input as {
		"requester": {"id": "sam-researcher", "roles": ["notebook_explore"]},
		"request": {
			"principal": "sam-researcher",
			"role": "platform_admin",
			"justification": "I would like to run the platform",
		},
	}
	some reason in decision.reasons
	contains(reason, "not obtainable from the running system")
}

test_a_custodian_may_decide_somebody_elses_role if {
	access.may_approve_role with input as {
		"approver": {"id": "cust-hartley", "roles": ["data_custodian"]},
		"request": {"principal": "sam-researcher", "role": "deid_reviewer"},
	}
}

test_nobody_approves_their_own_role if {
	not access.may_approve_role with input as {
		"approver": {"id": "sam-researcher", "roles": ["data_custodian"]},
		"request": {"principal": "sam-researcher", "role": "deid_reviewer"},
	}
}

# The administrator operates the system that records the decision, so they do
# not also make it. Same reasoning as approver_roles, restated because this is
# the combination an auditor tests for.
test_the_administrator_may_not_grant_a_role if {
	not access.may_approve_role with input as {
		"approver": {"id": "ops-priya", "roles": ["platform_admin", "hybridops"]},
		"request": {"principal": "sam-researcher", "role": "deid_reviewer"},
	}
}

test_a_researcher_may_not_grant_a_role if {
	not access.may_approve_role with input as {
		"approver": {"id": "sam-researcher", "roles": ["notebook_explore"]},
		"request": {"principal": "eng-devi", "role": "deid_reviewer"},
	}
}

test_confirming_a_role_is_somebody_elses_job if {
	access.may_attest_role with input as {
		"attester": {"id": "dpo-nakamura", "roles": ["dpo"]},
		"grant": {"principal": "sam-researcher", "role": "deid_reviewer"},
	}
}

test_nobody_confirms_their_own_role if {
	not access.may_attest_role with input as {
		"attester": {"id": "dpo-nakamura", "roles": ["dpo"]},
		"grant": {"principal": "dpo-nakamura", "role": "dpo"},
	}
}

# Standing access to whole buckets is held only inside the boundary. A role
# that reads data (a person, a training job, an agent) must go through a
# per-version grant the register justifies, never a bucket-wide one.
test_only_the_pipeline_holds_bucket_wide_access if {
	holders := {role | some role, spec in access.storage_roles; count(spec.every_bucket) > 0}
	holders == {"pipeline_action"}
}

test_pipeline_bucket_wide_access_is_exactly_read_list_tagging if {
	{verb | some verb in access.storage_roles.pipeline_action.every_bucket} == {"Read", "List", "Tagging"}
}

# Writing is not standing access any more. It is granted per task and per prefix,
# on the same proof reading needs (POST /write-credentials), so Write must never
# come back into the bucket-wide list.
test_pipeline_has_no_standing_write if {
	not "Write" in access.storage_roles.pipeline_action.every_bucket
}

# Every role that holds a storage key is a role the policy already knows, so a
# key can never be issued for a role no floor governs.
test_every_storage_role_has_a_floor if {
	every role, _ in access.storage_roles {
		access.role_floor[role] >= 0
	}
}

# The pipeline's floor is what justifies its standing access: it is the one
# role allowed to read below published without a lease.
test_bucket_wide_access_only_where_the_floor_is_raw if {
	violators := {role |
		some role, spec in access.storage_roles
		count(spec.every_bucket) > 0
		access.role_floor[role] != 0
	}
	count(violators) == 0
}

# ------------------------------------------------------------ preview --
#
# The preview must say, for one person and many versions, what `allow` would
# say about each. These cases pin both halves: the answer itself, and that
# it agrees with `allow` for the same person, version and purpose.

pv(p, versions) := r if {
	r := access.preview with input as {"principal": p, "versions": versions}
}

ds2(class) := object.union(ds(class), {"version_id": "dv-2"})

test_preview_role_reaches if {
	a := pv(principal(["notebook_explore"], []), [ds("PUBLISHED")])["dv-1"]
	a.by_role == true
	a.current_leases == []
	a.ended_leases == []
}

test_preview_role_does_not_reach if {
	a := pv(principal(["notebook_explore"], []), [ds("RAW")])["dv-1"]
	a.by_role == false
}

test_preview_current_lease if {
	a := pv(principal(["notebook_explore"], [lease({})]), [ds("RAW")])["dv-1"]
	a.by_role == false
	count(a.current_leases) == 1
	a.current_leases[0].purpose == "shape exploration"
	a.current_leases[0].expires_at == future
}

test_preview_expired_lease if {
	a := pv(principal(["notebook_explore"], [lease({"expires_at": past})]), [ds("RAW")])["dv-1"]
	a.current_leases == []
	a.ended_leases == [{"id": "l-1", "purpose": "shape exploration", "ended": "expired"}]
}

test_preview_revoked_lease if {
	a := pv(principal(["notebook_explore"], [lease({"revoked": true})]), [ds("RAW")])["dv-1"]
	a.current_leases == []
	a.ended_leases == [{"id": "l-1", "purpose": "shape exploration", "ended": "revoked"}]
}

test_preview_self_approved_lease_is_not_current if {
	p := principal(["notebook_explore"], [lease({"approved_by": "svc-alice"})])
	a := pv(p, [ds("RAW")])["dv-1"]
	a.current_leases == []
}

test_preview_lease_for_another_version_is_ignored if {
	p := principal(["notebook_explore"], [lease({"dataset_version": "dv-2"})])
	a := pv(p, [ds("RAW")])["dv-1"]
	a.current_leases == []
	a.ended_leases == []
}

test_preview_other_organisation if {
	other := object.union(ds("PUBLISHED"), {"tenant": "t2"})
	a := pv(principal(["notebook_explore"], [lease({})]), [other])["dv-1"]
	a.same_tenant == false
	a.by_role == false
	a.current_leases == []
}

test_preview_answers_each_version_separately if {
	r := pv(principal(["notebook_explore"], []), [ds("RAW"), ds2("PUBLISHED")])
	r["dv-1"].by_role == false
	r["dv-2"].by_role == true
}

# Agreement: whatever the preview calls readable, `allow` allows.
test_preview_role_agrees_with_allow if {
	p := principal(["notebook_explore"], [])
	pv(p, [ds("PUBLISHED")])["dv-1"].by_role
	access.allow with input as {"principal": p, "dataset": ds("PUBLISHED"), "purpose": "any stated purpose"}
}

test_preview_lease_agrees_with_allow if {
	p := principal(["notebook_explore"], [lease({})])
	l := pv(p, [ds("RAW")])["dv-1"].current_leases[0]
	access.allow with input as {"principal": p, "dataset": ds("RAW"), "purpose": l.purpose}
}

# And what it does not call readable, `allow` refuses.
test_preview_unreadable_agrees_with_allow if {
	p := principal(["notebook_explore"], [lease({"expires_at": past})])
	a := pv(p, [ds("RAW")])["dv-1"]
	not a.by_role
	a.current_leases == []
	not access.allow with input as {"principal": p, "dataset": ds("RAW"), "purpose": "shape exploration"}
}

# ---------------------------------------------------------------- closing --

full_hold := {
	"matter_name": "Doe v Harbour Clinic",
	"matter_number": "HC-2026-0417",
	"description": "A patient claim about a cardiology procedure in 2024.",
	"triggering_event": "Letter before claim received on 2026-09-30",
	"issuing_authority": "Aldous and Brennan LLP, for the claimant",
	"authority_reference": "AB/2026/17",
	"attorney_name": "Ruth Aldous",
	"attorney_email": "ruth.aldous@example.test",
	"notice_received_on": "2026-10-01",
	"preserve": "Every record of the claimant and the audit trail of who read it.",
	"custodian_id": "hold-keeper",
}

test_own_custodian_may_close_their_organisation if {
	access.may_retire with input as {
		"actor": {"id": "cust-hartley", "tenant_id": "health", "roles": ["data_custodian"]},
		"organisation": "health",
		"reason": "The contract ends on 31 October",
	}
}

test_custodian_of_another_organisation_may_not_close_it if {
	not access.may_retire with input as {
		"actor": {"id": "cust-marcus", "tenant_id": "finance", "roles": ["data_custodian"]},
		"organisation": "health",
		"reason": "Tidying up",
	}
}

test_ordinary_member_may_not_close_their_organisation if {
	not access.may_retire with input as {
		"actor": {"id": "sam-researcher", "tenant_id": "health", "roles": ["notebook_explore"]},
		"organisation": "health",
		"reason": "I would like it gone",
	}
}

test_platform_administrator_may_close_on_instruction if {
	access.may_retire with input as {
		"actor": {"id": "ops-priya", "tenant_id": "health", "roles": ["platform_admin"]},
		"organisation": "finance",
		"reason": "Written instruction from the customer",
	}
}

test_closing_without_a_reason_is_refused if {
	not access.may_retire with input as {
		"actor": {"id": "cust-hartley", "tenant_id": "health", "roles": ["data_custodian"]},
		"organisation": "health",
		"reason": "  ",
	}
}

test_own_custodian_may_cancel if {
	access.may_cancel_retirement with input as {
		"actor": {"id": "cust-hartley", "tenant_id": "health", "roles": ["data_custodian"]},
		"organisation": "health",
	}
}

test_ordinary_member_may_not_cancel if {
	not access.may_cancel_retirement with input as {
		"actor": {"id": "sam-researcher", "tenant_id": "health", "roles": ["notebook_explore"]},
		"organisation": "health",
	}
}

test_platform_administrator_may_place_a_complete_hold if {
	access.may_place_hold with input as {
		"actor": {"id": "ops-priya", "roles": ["platform_admin"]},
		"hold": full_hold,
	}
}

test_custodian_may_not_place_a_hold if {
	not access.may_place_hold with input as {
		"actor": {"id": "cust-hartley", "roles": ["data_custodian"]},
		"hold": full_hold,
	}
}

test_hold_without_an_attorney_is_refused if {
	not access.may_place_hold with input as {
		"actor": {"id": "ops-priya", "roles": ["platform_admin"]},
		"hold": object.remove(full_hold, ["attorney_name"]),
	}
}

test_hold_refusal_names_what_is_missing if {
	r := access.place_hold_decision with input as {
		"actor": {"id": "ops-priya", "roles": ["platform_admin"]},
		"hold": object.remove(full_hold, ["attorney_name", "preserve"]),
	}
	r.reasons == ["the notice is missing: attorney_name, preserve"]
}

test_a_different_administrator_may_approve if {
	access.may_decide_hold with input as {
		"actor": {"id": "ops-ravi", "roles": ["platform_admin"]},
		"hold": {"placed_by": "ops-priya"},
	}
}

test_the_administrator_who_placed_it_may_not_approve if {
	not access.may_decide_hold with input as {
		"actor": {"id": "ops-priya", "roles": ["platform_admin"]},
		"hold": {"placed_by": "ops-priya"},
	}
}

test_custodian_may_not_approve_a_hold if {
	not access.may_decide_hold with input as {
		"actor": {"id": "cust-hartley", "roles": ["data_custodian"]},
		"hold": {"placed_by": "ops-priya"},
	}
}

test_release_needs_a_reason_and_an_administrator if {
	access.may_release_hold with input as {
		"actor": {"id": "ops-ravi", "roles": ["platform_admin"]},
		"reason": "Matter settled, written confirmation received",
	}
	not access.may_release_hold with input as {
		"actor": {"id": "ops-ravi", "roles": ["platform_admin"]},
		"reason": "",
	}
	not access.may_release_hold with input as {
		"actor": {"id": "cust-hartley", "roles": ["data_custodian"]},
		"reason": "Matter settled",
	}
}

test_people_see_their_own_organisations_closing_and_no_other if {
	access.may_see_lifecycle with input as {
		"scope": "tenant", "tenant_id": "health",
		"viewer": {"id": "sam-researcher", "tenant_id": "health", "roles": ["notebook_explore"]},
	}
	not access.may_see_lifecycle with input as {
		"scope": "tenant", "tenant_id": "finance",
		"viewer": {"id": "sam-researcher", "tenant_id": "health", "roles": ["notebook_explore"]},
	}
}

test_only_administrators_see_every_organisation if {
	access.may_see_lifecycle with input as {
		"scope": "platform",
		"viewer": {"id": "ops-priya", "tenant_id": "health", "roles": ["platform_admin"]},
	}
	not access.may_see_lifecycle with input as {
		"scope": "platform",
		"viewer": {"id": "cust-hartley", "tenant_id": "health", "roles": ["data_custodian"]},
	}
}
