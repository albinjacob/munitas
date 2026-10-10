package munitas.screens

import data.munitas.access
import data.munitas.role_words
import rego.v1

# Who may open each page of the console, and to whom the menu shows it.
#
# This is the one place that says so. The API asks it before it answers (`screen_decision`), the console asks the API what the signed-in person may open
# (`for_roles`) and builds its menu and its "this page is for ..." sentence from the answer, and a test compares it with the console's routes and a check reads the API to prove each route in `api` refuses through it. Nothing else
# holds a list of roles for a page, so a page cannot be limited in one place and open in another.
#
# Each entry says:
#   path     where the page is in the console
#   label    what the menu calls it
#   open_to  who may open it: the word "everyone" (everyone signed in to the organisation) or a set of roles
#   menu     to whom the menu offers it. It can be narrower than open_to, because a link is about relevance. It can never be wider, or the menu would
#            offer a page that refuses the person who follows it (checked by a test)
#   (who)    not written here: the people it is for are built from the roles in `open_to` and the words in role_words.rego, as a phrase that completes
#            "This page is for ...", so the sentence cannot name different people from the ones the page is open to
#   why      why it is open to the people it is open to, so that nobody has to guess whether an open page is open on purpose
#   api      the API routes that refuse a role that is not in open_to, so the test can try them, with {tenant} standing for the caller's own organisation;
#            empty where the page is open to everyone, or where the API scopes what it returns to the caller instead of refusing
#   decided_by  only where a route in `api` does not call `require_screen` because it asks a rule of its own (the roles in that rule are the ones this entry
#            takes from it, so the two cannot differ). The check of the API names these routes and does not try to prove them
#
# Every page is limited to the caller's own organisation whatever it says here. Where a screen has its own, finer rule (who may decide, who may read a table)
# that rule stays with the action in access.rego. This says only who may open the page.
#
# A role list that another rule already owns is taken from that rule, never copied.

catalogue := {
	"home": {
		"path": "/", "label": "Home", "open_to": "everyone", "menu": "everyone",
		"why": "Every role has a home page of its own, built from what that role may see.", "api": [],
	},
	"datasets": {
		"path": "/datasets", "label": "Datasets", "open_to": "everyone", "menu": "everyone",
		"why": "Everything registered in the organisation is listed to everyone in it. What a person may read is decided dataset by dataset.", "api": [],
	},
	"departments": {
		"path": "/departments", "label": "Departments", "open_to": "everyone", "menu": "everyone",
		"why": "Who answers for each department is not a secret inside the organisation. Only an approver of a department can change its list.", "api": [],
	},
	"gates": {
		"path": "/gates", "label": "De-identification results", "open_to": "everyone",
		"menu": {"deid_reviewer", "dpo", "pipeline_operator", "platform_admin"},
		"why": "Everyone can see what waits and who it waits on. Each row says whether the caller may decide it, and only a reviewer can.", "api": [],
	},
	"locked_requests": {
		"path": "/locked-column-access", "label": "Locked data requests", "open_to": "everyone",
		"menu": {"data_custodian", "pipeline_operator", "deid_reviewer", "privacy_auditor", "dpo"},
		"why": "Anybody who does not answer for a table can ask, so the page is open to ask from. Each person is shown their own requests and a custodian is shown the ones to decide.",
		"api": [],
	},
	"notebooks": {
		"path": "/notebook-sign-ins", "label": "Your notebooks", "open_to": "everyone", "menu": "everyone",
		"why": "Each person is shown only the notebooks that signed in as themselves.", "api": [],
	},
	"agents": {
		"path": "/agents", "label": "Agents", "open_to": {"pipeline_operator", "platform_admin"}, "menu": {"pipeline_operator", "platform_admin"},
		"why": "The list shows every agent the organisation has registered and what each one is running, so it is for the people who run pipelines and the people who run the platform (decided 2026-10-10). An agent's own page and registering one are limited by role as before.",
		"api": ["GET /agents"],
	},
	"pipelines": {
		"path": "/pipelines", "label": "Pipelines", "open_to": "everyone", "menu": {"pipeline_operator", "platform_admin"},
		"why": "Every pipeline in the caller's own organisation is listed to everyone in it, as the route says. Registering one is limited by role.", "api": [],
	},
	"action_runs": {
		"path": "/action-runs", "label": "Action runs", "open_to": {"pipeline_operator", "platform_admin"}, "menu": {"pipeline_operator", "platform_admin"},
		"why": "Each run names the operator, the code that ran and the versions it read, so the list is for the people who run pipelines and the people who run the platform (decided 2026-10-10).",
		"api": ["GET /action-runs"],
	},
	"egress_approvals": {
		"path": "/egress-approvals", "label": "Egress approvals", "open_to": {"network_architect", "pipeline_operator", "platform_admin"},
		"menu": {"network_architect", "pipeline_operator", "platform_admin"},
		"why": "The list is for the people who decide a request and the people who ask for one, because a data engineer's home shows the requests that wait on somebody else (decided 2026-10-10). A single request can still be opened by whoever follows its link, so that the person who asked can see why it waits. Only a network architect decides it.",
		"api": ["GET /egress-approvals"],
	},
	"services": {
		"path": "/services", "label": "Services", "open_to": {"platform_admin"}, "menu": {"platform_admin"},
		"why": "It shows what the platform's own services have done across every organisation, which only the people who run the platform may see.",
		"api": ["GET /services"],
	},
	"legal_holds": {
		"path": "/legal-holds", "label": "Legal holds", "open_to": {"platform_admin"}, "menu": {"platform_admin"},
		"why": "Holds and the records produced for them cut across organisations, and two different platform administrators have to act on each. The list the API returns is scoped to the caller: a platform administrator sees every hold, and anybody else only a hold that names them as its custodian.",
		"api": [],
	},
	"housekeeping": {
		"path": "/housekeeping", "label": "Storage housekeeping",
		"open_to": access.platform_housekeeping_roles | access.tenant_housekeeping_roles,
		"menu": access.platform_housekeeping_roles | access.tenant_housekeeping_roles,
		"why": "Platform staff see every organisation's storage. A custodian and the data protection officer see their own organisation's. The page shows the policy's own refusal to the rest.",
		"api": ["GET /housekeeping/storage?tenant_id={tenant}"],
		"decided_by": "the housekeeping rule in access.rego, asked by the route itself",
	},
	"directory": {
		"path": "/directory", "label": "People and roles", "open_to": "everyone", "menu": {"dpo", "platform_admin", "data_custodian"},
		"why": "Anybody may ask for a role on this page, so every role can open it. Deciding a request is limited to data custodians.", "api": [],
	},
	"roles": {
		"path": "/roles", "label": "What each role can do", "open_to": "everyone", "menu": "everyone",
		"why": "What each role may read and approve is published to everyone, so that nobody has to guess what a refusal means.", "api": [],
	},
	"access_rules": {
		"path": "/access-rules", "label": "Access rules", "open_to": "everyone", "menu": "everyone",
		"why": "The limits an organisation has set are public inside it, so that a person knows what they may ask for.", "api": [],
	},
	"closing": {
		"path": "/closing", "label": "Closing down the organisation", "open_to": "everyone", "menu": {"data_custodian", "dpo", "platform_admin"},
		"why": "Everybody in the organisation is told where a closing stands. Only a data custodian or a platform administrator can start or cancel one, and the platform refuses the rest.",
		"api": [],
	},
	"audit": {
		"path": "/audit", "label": "Access decisions", "open_to": {"data_custodian", "dpo", "platform_admin"},
		"menu": {"data_custodian", "dpo", "platform_admin"},
		"why": "It lists every request in the organisation and what was asked for, so it is for the people who answer for data, the person who watches how it is handled and the people who run the platform (decided 2026-10-10).",
		"api": ["GET /access-decisions"],
	},
	"locking_audit": {
		"path": "/locking-audit", "label": "Locked data audit", "open_to": access.audit_evidence_roles, "menu": access.audit_evidence_roles,
		"why": "It is the evidence of how locked data was used, never a name or a value. The roles are the ones that rule already names for that evidence.",
		"api": ["GET /audit/locked-columns"],
		"decided_by": "the audit evidence rule in access.rego, asked by the route itself",
	},
}

# A list of roles is the word "everyone" or a set of role names. The caller's roles are `input.roles`.
allows(list) if list == "everyone"

allows(list) if {
	is_set(list)
	some role in input.roles
	list[role]
}

# `allows` has no answer when the caller may not, so a list of pages needs a plain yes or no for each page.
can(list) := true if allows(list)

else := false

# ---------------------------------------------- the decision the API asks for --

default may_open := false

may_open if allows(catalogue[input.screen].open_to)

screen_reason contains sprintf("this page is for %s", [who_of(catalogue[input.screen])]) if {
	catalogue[input.screen]
	not may_open
}

screen_reason contains "there is no such page" if not catalogue[input.screen]

screen_decision := {
	"allow": may_open,
	"reasons": [r | some r in screen_reason],
}

# ---------------------------------------- what the console asks the API for --

# The people a page is for, in words, for the sentence a person is shown when the page is not for them.
who_of(c) := "everybody" if c.open_to == "everyone"

who_of(c) := role_words.people(c.open_to) if is_set(c.open_to)

# Every page, with whether the caller may open it and whether the menu offers it to them.
for_roles := {screen: {
	"path": c.path,
	"label": c.label,
	"open": can(c.open_to),
	"menu": can(c.menu),
	"who": who_of(c),
} |
	some screen, c in catalogue
}
