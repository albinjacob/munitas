package munitas.access

import rego.v1

# Visibility ordering. A lower ordinal is less visible (more restricted).
class_order := {"RAW": 0, "UNDER_REVIEW": 1, "OPEN_FOR_ANNOTATION": 2, "OPEN_FOR_TRAINING": 3, "PUBLISHED": 4}

# The most sensitive class each role may read without an explicit lease.
#
# Every human role sits at 4. That is deliberate rather than cautious: people
# reach data through a workspace that holds a credential on their behalf, and
# anything less visible than published needs a lease somebody else
# approved. Only workloads running inside the boundary have a lower floor.
role_floor := {
	# Workloads.
	"pipeline_action": 0, # actions run inside the boundary and may read anything
	"annotation_tool": 2,
	"training_job": 3,
	"model_eval": 3,
	"agent_runtime": 4, # agents never read below published by default
	# People.
	"notebook_explore": 4, # researchers; anything more sensitive needs a lease
	"analyst": 4,
	"data_custodian": 4, # deciding who may read is not the same as reading
	"deid_reviewer": 4, # judging whether a de-identification is adequate is not a licence to read the data
	"dpo": 4, # sees every decision, reads no more data than anyone else
	"pipeline_operator": 4, # runs the jobs, does not read what they produce
	"platform_admin": 4, # runs the system, holds no standing access to its contents
	"hybridops": 4, # support and reliability work needs telemetry, not records
	"network_architect": 4, # approves which hosts an agent may call, reads no data by holding the role
}

# Which roles hold a storage key of their own, and what each may do across
# every organisation's bucket without a lease or a per-version grant.
#
# The storage permissions document is compiled from this, the register and
# nothing else (platform/api/app/grants.py). It used to be the other way
# round: a role's bucket-wide access to a new bucket was copied from its
# access to the buckets it already had, so the rule existed only as a pattern
# in the previous document, and a fresh install depended on a template grant
# naming a bucket that no longer exists.
#
# Only a workload running inside the boundary holds standing access to whole
# buckets. Everyone else reads through a per-version grant that the register
# justifies, so an empty list is a decision, not an omission.
#
# `pipeline_action` no longer holds standing Write here. Reading anything was
# already made request-justified (task_credential.py, item 65); writing was
# the one verb still trusted on the static key alone, the only role and only
# verb still running on "just trust the password" (see
# docs/internal/design/write-credential-rationale.md). A task now proves itself the
# same way for a write as for a read, and platform/api/app/grants.py adds
# whatever write_grant actually justifies from that proof -- see
# justified_write_pairs() there. List/Tagging stay standing: they expose
# object names, not contents or the ability to alter them, a materially
# smaller blast radius than Read or Write, so narrowing them the same way was
# asked about and deliberately deferred.
storage_roles := {
	"pipeline_action": {"every_bucket": ["Read", "List", "Tagging"]},
	"training_job": {"every_bucket": []},
	"annotation_tool": {"every_bucket": []},
	"notebook_explore": {"every_bucket": []},
	"agent_runtime": {"every_bucket": []},
}

# Roles that may approve somebody else's lease.
#
# A short list on purpose. The data protection officer is absent: GDPR Article
# 38(6) bars them from determining the purposes and means of processing, and an
# approver does exactly that. Their independence is the thing that makes their
# oversight worth having, so making them an approver would cost more than it
# gained.
#
# The platform administrator is absent for a different reason. They operate the
# system that records approvals, so if they could also grant them there would be
# no check on the most powerful account in the platform.
approver_roles := {"data_custodian"}

default allow := false

# The parts `allow` is made of, named so `preview` below can use the same ones.
# A second copy of any of these would be a second rulebook, and the preview
# would drift from the check it is meant to predict.
default same_tenant := false

same_tenant if input.principal.tenant == input.dataset.tenant

default role_reaches := false

role_reaches if {
	some role in input.principal.roles
	class_order[input.dataset.visibility_class] >= role_floor[role]
}

# A lease that currently grants this version, whatever purpose it is for.
lease_grants(lease) if {
	lease.dataset_version == input.dataset.version_id
	lease.approved_by != input.principal.id # no self-approval
	not lease.revoked
	lease_current(lease)
}

# Standing access, from the role.
allow if {
	baseline_ok
	role_reaches
}

# Access below the role floor, from an explicit, current, approved lease
# naming this exact dataset version, whose purpose this call either matches
# or is exempt from matching.
allow if {
	baseline_ok
	some lease in input.principal.leases
	lease_grants(lease)
	lease_purpose_ok(lease)
}

# A "strict" lease (every lease this platform granted before `pattern`
# existed, and its own default) covers only the purpose it was approved
# under; a different purpose is a different ask and gets its own decision.
#
# A "simple" lease is the custodian's own choice, made once at approval, to
# trust this principal with this dataset version for any purpose while the
# lease lasts -- the same judgment a resource owner already makes handing a
# service a broad role instead of a narrow one, so a workload doing
# recognisably the same job every day does not need a fresh approval every
# time its own purpose text is reworded. Never available for RAW data:
# access_lease's own trigger refuses to create a simple lease against it, so
# `lease.pattern == "simple"` here can only ever be true for data at or above
# UNDER_REVIEW, regardless of how much the custodian trusts the principal.
lease_purpose_ok(lease) if lease.pattern == "simple"

lease_purpose_ok(lease) if lease.purpose == input.purpose

# A standing lease (expires_at null) never expires; a bounded one is current
# until its timer runs out. Split into two rules rather than one comparison,
# because `time.parse_rfc3339_ns(null)` is undefined, and a standing lease
# would otherwise be silently treated as unusable instead of correctly
# matched.
lease_current(lease) if lease.expires_at == null

lease_current(lease) if {
	lease.expires_at != null
	time.parse_rfc3339_ns(lease.expires_at) > time.now_ns()
}

baseline_ok if {
	same_tenant
	input.purpose != ""
}

# ------------------------------------------------------------ preview --
#
# One person, many versions: what `allow` would decide about each, before a
# purpose is stated. Each version is evaluated with the same parts `allow` is
# built from, under `with input as`, so the two cannot disagree.
#
# Purpose is not an input. A role covers any stated purpose; a lease covers
# only its own, so current leases are reported with their purpose rather than
# checked against one. Nothing here is recorded: nothing is being attempted.
preview := {v.version_id: answer |
	some v in input.versions
	answer := version_preview with input as {"principal": input.principal, "dataset": v}
}

version_preview := {
	"same_tenant": same_tenant,
	"by_role": readable_by_role,
	"current_leases": [{"id": l.id, "purpose": l.purpose, "expires_at": l.expires_at} |
		some l in input.principal.leases
		same_tenant
		lease_grants(l)
	],
	"ended_leases": [{"id": l.id, "purpose": l.purpose, "ended": ended} |
		some l in input.principal.leases
		l.dataset_version == input.dataset.version_id
		ended := lease_ended(l)
	],
}

default readable_by_role := false

readable_by_role if {
	same_tenant
	role_reaches
}

lease_ended(l) := "revoked" if l.revoked

lease_ended(l) := "expired" if {
	not l.revoked
	l.expires_at != null
	time.parse_rfc3339_ns(l.expires_at) <= time.now_ns()
}

# Reasons are attached to every decision, allowed or denied, and are what lands
# in the audit record.
reason contains "principal tenant does not match dataset tenant" if {
	input.principal.tenant != input.dataset.tenant
}

reason contains "no declared purpose" if {
	input.purpose == ""
}

reason contains sprintf("lease %v expired", [lease.id]) if {
	some lease in input.principal.leases
	lease.dataset_version == input.dataset.version_id
	lease.expires_at != null
	time.parse_rfc3339_ns(lease.expires_at) <= time.now_ns()
}

reason contains sprintf("lease %v revoked", [lease.id]) if {
	some lease in input.principal.leases
	lease.dataset_version == input.dataset.version_id
	lease.revoked
}

reason contains sprintf("no role reaches class %v", [input.dataset.visibility_class]) if {
	not allow
	baseline_ok
	every role in input.principal.roles {
		class_order[input.dataset.visibility_class] < role_floor[role]
	}
}

reason contains "granted by role" if {
	allow
	some role in input.principal.roles
	class_order[input.dataset.visibility_class] >= role_floor[role]
}

reason contains sprintf("granted by lease %v", [lease.id]) if {
	allow
	some lease in input.principal.leases
	lease.dataset_version == input.dataset.version_id
	lease_purpose_ok(lease)
	not lease.revoked
	lease.expires_at != null
	time.parse_rfc3339_ns(lease.expires_at) > time.now_ns()
}

# A standing lease says so explicitly, rather than leaving the reader to infer
# it from the absence of an expiry date, which is the one thing this design
# must not make somebody have to notice on their own.
reason contains sprintf("granted by standing lease %v", [lease.id]) if {
	allow
	some lease in input.principal.leases
	lease.dataset_version == input.dataset.version_id
	lease_purpose_ok(lease)
	not lease.revoked
	lease.expires_at == null
}

decision := {
	"allow": allow,
	"reasons": [r | some r in reason],
}

# ---------------------------------------------------------- approvals --
#
# Whether a principal may approve a specific lease request.
#
# Separate from `allow`, which answers whether somebody may read data. These are
# different questions with different answers, and the difference is the point:
# a custodian decides who reads an asset without necessarily being able to read
# it themselves.
#
# The database enforces the two parts it can hold: the approver is registered
# (foreign key) and is not the requester (check constraint). This adds the part
# that needs a relationship: the approver is the custodian of the department
# that owns the asset.
#
# What none of it can establish is that the caller really is the approver. That
# needs authentication, so an approval remains an assertion by a registered
# person rather than a proven act.

default may_approve := false

may_approve if {
	# Not your own request. Restated here rather than left to the database,
	# because a caller should be refused before the write is attempted, and
	# because the constraint is the guarantee while this is the explanation.
	input.approver.id != input.request.principal

	# Nor a request made on your behalf. A custodian approving their own
	# on-behalf-of ask for a workload is the same self-approval, one identity
	# removed, and the same reasoning applies: the constraint on access_lease
	# is the guarantee, this is the explanation.
	input.approver.id != object.get(input.request, "requested_by", null)

	some role in input.approver.roles
	approver_roles[role]

	# The approver is the custodian of the department owning this asset.
	input.approver.id == input.asset.custodian
}

approve_reason contains "an approver cannot approve their own request" if {
	input.approver.id == input.request.principal
}

approve_reason contains "an approver cannot approve a request made on their behalf" if {
	input.approver.id == object.get(input.request, "requested_by", null)
	input.request.requested_by != null
}

approve_reason contains "this principal holds no role that may approve" if {
	every role in input.approver.roles {
		not approver_roles[role]
	}
}

approve_reason contains sprintf(
	"this asset is owned by a department whose custodian is %v",
	[input.asset.custodian],
) if {
	input.asset.custodian != ""
	input.approver.id != input.asset.custodian
}

approve_reason contains "this asset has no owning department, so nobody can approve access to it" if {
	not input.asset.custodian
}

approval_decision := {
	"allow": may_approve,
	"reasons": [r | some r in approve_reason],
}

# ------------------------------------------------------------- export --
#
# Whether data may leave the platform.
#
# A separate question from whether somebody may read it, and answered by
# provenance rather than by class. Two datasets can sit at the same class and
# differ here: a public benchmark corpus may be redistributed because it already
# was, and a de-identified clinical dataset may not, because de-identification
# reduces re-identification risk without eliminating it and the consent basis
# that allowed the original collection did not cover republishing.
#
# Answering this with class alone gives one of two wrong answers. Refuse
# everything, and downloading a public corpus is blocked for no benefit. Permit
# by class, and regulated data walks out at the moment it is judged safe enough
# to share internally, which is the serious mistake.

default may_export := false

may_export if {
	input.dataset.provenance == "external_public"
	input.dataset.visibility_class == "PUBLISHED"
}

# A HuggingFace-derived provenance carries its own licence terms, checked by
# the platform rather than declared by a person. Two rules, not one, because
# a licence can answer "yes as fetched, no once modified" (no-derivatives)
# differently from "no either way" (non-commercial). `input.export.modified`
# is computed by the caller from lineage: whether the version being exported
# has any pipeline action_run ancestor, or is the original sealed fetch.
may_export if {
	input.dataset.provenance == "external_licensed"
	input.dataset.visibility_class == "PUBLISHED"
	not input.export.modified
	input.dataset.license_export_unmodified
}

may_export if {
	input.dataset.provenance == "external_licensed"
	input.dataset.visibility_class == "PUBLISHED"
	input.export.modified
	input.dataset.license_export_modified
}

export_reason contains "only published data can leave" if {
	input.dataset.visibility_class != "PUBLISHED"
}

export_reason contains sprintf(
	"this data came from %v, and only data that was already public or licensed for release can leave",
	[input.dataset.provenance],
) if {
	input.dataset.provenance != "external_public"
	input.dataset.provenance != "external_licensed"
}

export_reason contains sprintf(
	"this data's licence (%v) does not permit export once modified",
	[input.dataset.license_tag],
) if {
	input.dataset.provenance == "external_licensed"
	input.export.modified
	not input.dataset.license_export_modified
}

export_reason contains sprintf(
	"this data's licence (%v) does not permit export",
	[input.dataset.license_tag],
) if {
	input.dataset.provenance == "external_licensed"
	not input.export.modified
	not input.dataset.license_export_unmodified
}

export_reason contains "already public, and released" if {
	may_export
	input.dataset.provenance == "external_public"
}

export_reason contains sprintf("licensed for release under %v", [input.dataset.license_tag]) if {
	may_export
	input.dataset.provenance == "external_licensed"
}

export_decision := {
	"allow": may_export,
	"reasons": [r | some r in export_reason],
}

# ------------------------------------------------------------ release --
#
# Whether a dataset may be promoted above the class somebody declared for it.
#
# When a person asserts that an upload is not sensitive, that assertion is the
# only thing standing between the file and everybody who can read that class. It
# holds until the custodian of the owning department agrees, and then the
# ordinary promotion rules take over.
#
# Data the platform fetched itself needs no confirmation. Nobody was believed:
# the origin is on record and can be checked.

default may_release := false

# Verified origin, so there is no claim to confirm.
may_release if {
	input.dataset.declaration_basis == "verified_source"
}

# A person's claim, confirmed by somebody else.
may_release if {
	input.dataset.declaration_basis == "asserted"
	input.dataset.classification_confirmed_by != ""
	input.dataset.classification_confirmed_by != input.dataset.declared_by
}

# Nothing was declared, so this is ordinary pipeline output and the class gate
# is what governs it.
may_release if {
	not input.dataset.declaration_basis
}

release_reason contains "the sensitivity of this data is one person's judgement, and nobody has confirmed it yet" if {
	input.dataset.declaration_basis == "asserted"
	not input.dataset.classification_confirmed_by
}

release_reason contains "confirmed by somebody other than whoever declared it" if {
	may_release
	input.dataset.declaration_basis == "asserted"
}

release_decision := {
	"allow": may_release,
	"reasons": [r | some r in release_reason],
}

# ------------------------------------------------- the gate decision --
#
# Whether this person may decide that a de-identification run clears the gate
# to promotion.
#
# A different question from may_approve. That one asks who may let a named
# person read a named asset, and is answered by the department's custodian.
# This one asks whether the de-identification is good enough to widen access at
# all, which is a statistical judgement about a measurement. HIPAA's expert
# determination method assigns exactly that to somebody with the background to
# read it, and this role is that person.
#
# The data protection officer is absent for the reason given above for leases,
# and harder: whoever approves an act cannot afterwards give independent
# assurance over it, and independence is the whole value of the role.
#
# The custodian is absent too, which is the less obvious call. They decide who
# may read an asset, which is ownership. Whether the redaction actually worked
# is a different question and being the owner does not answer it.
decider_roles := {"deid_reviewer"}

default may_decide_gate := false

may_decide_gate if {
	# Not a run you started. Restated here rather than left to the database,
	# because a caller should be refused before the write is attempted, and
	# because the constraint is the guarantee while this is the explanation.
	input.decider.id != object.get(input.gate, "triggered_by", null)

	some role in input.decider.roles
	decider_roles[role]
}

gate_reason contains "a de-identification run cannot be cleared by whoever started it" if {
	input.decider.id == object.get(input.gate, "triggered_by", null)
}

gate_reason contains "this principal holds no role that may decide a de-identification gate" if {
	every role in input.decider.roles {
		not decider_roles[role]
	}
}

gate_decision := {
	"allow": may_decide_gate,
	"reasons": [r | some r in gate_reason],
}

# ------------------------------------------------ starting a pipeline run --

# Starting a de-identification run is an operational act, not an access
# decision. It moves no data to the person who starts it: the operator sees
# that a run happened and what the gate measured, never what was said in the
# recordings. So the only question is whose job it is to press the button, and
# one role already says so in role_floor above.
#
# The platform administrator is absent, the same call the gate rule made. An
# account that operates the system and could also start runs against any
# tenant's data would be the least checked account in the platform holding the
# most capability.
#
# Whoever starts a run cannot afterwards clear its gate. That is not restated
# here: may_decide_gate already refuses a decision by input.gate.triggered_by,
# and saying it twice would create two places for it to drift.
operator_roles := {"pipeline_operator"}

default may_start_pipeline := false

may_start_pipeline if {
	some role in input.operator.roles
	operator_roles[role]
}

start_reason contains "this principal holds no role that may start a pipeline run" if {
	every role in input.operator.roles {
		not operator_roles[role]
	}
}

pipeline_start_decision := {
	"allow": may_start_pipeline,
	"reasons": [r | some r in start_reason],
}

# --------------------------------------------- agent egress allowlist --
#
# Which external hosts an agent version may call, approved before that
# version is deployable at all (see docs/internal/diagrams/agent-egress-allowlist).
# A network tool like fetch_url is only as safe as the network it happens to
# run on; this is what makes it safe by construction instead, the same shape
# as every other consequential act on this platform: a named role approves
# through a real decision, not a config file nobody reviewed.
#
# The developer who wrote the version is deliberately excluded from
# approving its own requested hosts, the same separation-of-duties
# invariant may_decide_gate and may_approve_role already state for a run's
# own operator and a role's own requester: the person who chose what the
# code asks for cannot also be the only check on whether that is safe.
egress_architect_roles := {"network_architect"}

default may_approve_egress_hosts := false

may_approve_egress_hosts if {
	input.approver.id != object.get(input.version, "submitted_by", null)

	some role in input.approver.roles
	egress_architect_roles[role]
}

egress_reason contains "an agent version's own submitter cannot approve its requested hosts" if {
	input.approver.id == object.get(input.version, "submitted_by", null)
}

egress_reason contains "this principal holds no role that may approve an agent version's requested hosts" if {
	every role in input.approver.roles {
		not egress_architect_roles[role]
	}
}

egress_approval_decision := {
	"allow": may_approve_egress_hosts,
	"reasons": [r | some r in egress_reason],
}

# ------------------------------------------------------------------
# Housekeeping: storage telemetry, and the acts that free it.
#
# Two questions, not one, and keeping them apart is the point. Reading which
# buckets exist and how many bytes could be freed is telemetry. Freeing them
# destroys data that cannot come back. A screen that shows both must not
# authorise both to the same people by accident.
#
# What is readable here is deliberately telemetry rather than records:
# tenant ids, bucket names, counts, bytes, dates. This is what `hybridops`
# is for in role_floor above, in that role's own words, "support and
# reliability work needs telemetry, not records", and it is what lets
# `platform_admin` keep the property recorded beside it, "runs the system,
# holds no standing access to its contents". Dataset names are customer
# metadata, not telemetry, and the platform-wide view does not carry them.
#
# The narrower view is the tenant's own. Reclamation deletes a tenant's
# bytes, and what was deleted, when, and why is a governance fact belonging
# to that tenant rather than to the people who run the machine. A custodian
# and a data protection officer see their own organisation's, scoped by the
# caller's tenant, which is also why they may see dataset names there: it is
# their own data being named.

platform_housekeeping_roles := {"hybridops", "platform_admin"}

tenant_housekeeping_roles := {"data_custodian", "dpo"}

default may_see_housekeeping := false

# The platform-wide view: every tenant's storage, no tenant's contents.
may_see_housekeeping if {
	input.scope == "platform"
	some role in input.viewer.roles
	platform_housekeeping_roles[role]
}

# One organisation's own, and only its own.
may_see_housekeeping if {
	input.scope == "tenant"
	input.viewer.tenant_id == input.tenant_id
	some role in input.viewer.roles
	tenant_housekeeping_roles[role]
}

# Running the platform is a reason to see every tenant's storage, so the two
# platform roles reach the tenant-scoped view as well.
may_see_housekeeping if {
	input.scope == "tenant"
	some role in input.viewer.roles
	platform_housekeeping_roles[role]
}

housekeeping_reason contains "this principal holds no role that may see storage housekeeping" if {
	input.scope == "platform"
	every role in input.viewer.roles {
		not platform_housekeeping_roles[role]
	}
}

housekeeping_reason contains sprintf("housekeeping for %v is not this principal's organisation to see", [input.tenant_id]) if {
	input.scope == "tenant"
	input.viewer.tenant_id != input.tenant_id
	every role in input.viewer.roles {
		not platform_housekeeping_roles[role]
	}
}

housekeeping_reason contains "this principal holds no role that may see an organisation's storage housekeeping" if {
	input.scope == "tenant"
	input.viewer.tenant_id == input.tenant_id
	every role in input.viewer.roles {
		not tenant_housekeeping_roles[role]
		not platform_housekeeping_roles[role]
	}
}

housekeeping_decision := {
	"allow": may_see_housekeeping,
	"reasons": [r | some r in housekeeping_reason],
}

# Freeing storage is a separate question with a narrower answer.
#
# Reclaiming destroys bytes a sealed version can never get back, and sweeping
# removes whole throwaway tenants. Support and reliability diagnose that it
# needs doing; the administrator is who does it. Splitting the two is the
# same shape as may_decide_gate refusing the person who started the run: the
# one who reports a problem is not automatically the one who acts on it.
#
# A reason is required and is not decoration. It is written into
# storage_reclamation beside the bytes, so the record says why they went
# rather than only that they did.

housekeeping_actor_roles := {"platform_admin"}

default may_free_storage := false

may_free_storage if {
	count(trim_space(object.get(input, "reason", ""))) > 0
	some role in input.actor.roles
	housekeeping_actor_roles[role]
}

free_reason contains "this principal holds no role that may free stored bytes" if {
	every role in input.actor.roles {
		not housekeeping_actor_roles[role]
	}
}

free_reason contains "freeing stored bytes needs a reason, which is recorded beside what was freed" if {
	count(trim_space(object.get(input, "reason", ""))) == 0
}

free_storage_decision := {
	"allow": may_free_storage,
	"reasons": [r | some r in free_reason],
}

# ------------------------------------------------------------------
# Holding a role: asking for one, and deciding somebody else's ask.
#
# Roles used to change only by editing Postgres by hand, and the obvious fix
# was an administration screen. It is the wrong fix. The administrator
# "runs the system, holds no standing access to its contents" (role_floor
# above), and that sentence stops being true the moment one account can add
# data_custodian to a row. An audit entry does not restore it: an audit says
# what happened afterwards, and this is a claim about what is possible.
#
# So a role is asked for and granted by somebody else, exactly as access to a
# dataset already is. The database holds the two parts it can (the approver is
# registered, and is not the requester); this holds the part that needs the
# rules: who may decide, and which roles are obtainable at all.

# Deciding who may hold a role is the same authority as deciding who may read
# data, so it is the same set. The administrator is deliberately absent, for
# the reason stated at approver_roles above: they operate the system that
# records the decision.
role_approver_roles := approver_roles

default may_request_role := false

may_request_role if {
	# For yourself. Asking on somebody else's behalf would put the reason in
	# one person's words and the holding in another's, and the record is
	# worth less for it.
	input.requester.id == input.request.principal
	input.request.role != "platform_admin"
	count(trim_space(object.get(input.request, "justification", ""))) > 0
}

request_role_reason contains "a role is asked for by the person who would hold it" if {
	input.requester.id != input.request.principal
}

request_role_reason contains "the role that administers roles is not obtainable from the running system" if {
	input.request.role == "platform_admin"
}

request_role_reason contains "asking for a role needs a reason, which is recorded with it" if {
	count(trim_space(object.get(input.request, "justification", ""))) == 0
}

role_request_decision := {
	"allow": may_request_role,
	"reasons": [r | some r in request_role_reason],
}

default may_approve_role := false

may_approve_role if {
	# Not your own ask. The same separation access_lease states as a check
	# constraint, refused here before the write is attempted rather than
	# after.
	input.approver.id != input.request.principal
	input.request.role != "platform_admin"
	some role in input.approver.roles
	role_approver_roles[role]
}

approve_role_reason contains "nobody approves their own role" if {
	input.approver.id == input.request.principal
}

approve_role_reason contains "this principal holds no role that may decide who holds a role" if {
	every role in input.approver.roles {
		not role_approver_roles[role]
	}
}

approve_role_reason contains "the role that administers roles is not granted from the running system" if {
	input.request.role == "platform_admin"
}

role_approval_decision := {
	"allow": may_approve_role,
	"reasons": [r | some r in approve_role_reason],
}

# Confirming a role is still needed is a smaller act than granting one, and a
# different one: it renews nothing and cannot widen anybody's access. What it
# must not be is self-service, or the overdue list becomes a button people
# press about themselves.
default may_attest_role := false

may_attest_role if {
	input.attester.id != input.grant.principal
	some role in input.attester.roles
	attester_roles[role]
}

attester_roles := {"data_custodian", "dpo"}

attest_reason contains "nobody confirms their own role is still needed" if {
	input.attester.id == input.grant.principal
}

attest_reason contains "this principal holds no role that may confirm another's" if {
	every role in input.attester.roles {
		not attester_roles[role]
	}
}

attest_decision := {
	"allow": may_attest_role,
	"reasons": [r | some r in attest_reason],
}
