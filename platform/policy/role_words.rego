package munitas.role_words

import rego.v1

# What each role is called, written once.
#
# Every sentence that names a role is built from this table: the console's labels (through `GET /policy/roles`), the refusals about locked data in access.rego,
# the limits an organisation sets (access_rules.py), and the "This page is for ..." sentence of every page in screens.rego. A role is renamed here and nowhere
# else.
#
#   label    what the console calls the role: "Data engineer"
#   one      one person who holds it, with its article: "a data engineer"
#   many     the people who hold it: "data engineers"
#   person   whether a person can hold it. The roles that are not people are the identities that platform components run as, and only have a label.
#
# `order` is the order the roles are listed in when a sentence names several of them.

roles := {
	"data_custodian": {"label": "Data custodian", "one": "a data custodian", "many": "data custodians", "person": true},
	"network_architect": {"label": "Network architect", "one": "a network architect", "many": "network architects", "person": true},
	"pipeline_operator": {"label": "Data engineer", "one": "a data engineer", "many": "data engineers", "person": true},
	"deid_reviewer": {"label": "De-identification reviewer", "one": "a de-identification reviewer", "many": "de-identification reviewers", "person": true},
	"privacy_auditor": {"label": "Privacy auditor", "one": "a privacy auditor", "many": "privacy auditors", "person": true},
	"analyst": {"label": "Analyst", "one": "an analyst", "many": "analysts", "person": true},
	"notebook_explore": {"label": "Researcher", "one": "a researcher", "many": "researchers", "person": true},
	"dpo": {"label": "Data protection officer", "one": "the data protection officer", "many": "the data protection officer", "person": true},
	"hybridops": {"label": "Support and reliability", "one": "support and reliability", "many": "support and reliability", "person": true},
	"platform_admin": {"label": "Platform administrator", "one": "a platform administrator", "many": "platform administrators", "person": true},
	"pipeline_action": {"label": "Pipeline action", "person": false},
	"annotation_tool": {"label": "Annotation tool", "person": false},
	"training_job": {"label": "Training job", "person": false},
	"model_eval": {"label": "Model evaluation", "person": false},
	"agent_runtime": {"label": "Agent runtime", "person": false},
}

order := ["data_custodian", "network_architect", "pipeline_operator", "deid_reviewer", "privacy_auditor", "analyst", "notebook_explore", "dpo", "hybridops", "platform_admin"]

# A list of items as a sentence part: "a", "a and b", "a, b and c".
list_text(items) := items[0] if count(items) == 1

list_text(items) := concat(" and ", items) if count(items) == 2

list_text(items) := sprintf("%s and %s", [concat(", ", array.slice(items, 0, count(items) - 1)), items[count(items) - 1]]) if count(items) > 2

# The people a set of roles are, in words: "data engineers and platform administrators". A set of one role is spoken of as that role: "the platform
# administrator".
people(set) := regex.replace(roles[only].one, `^an? `, "the ") if {
	names := [r | some r in order; set[r]]
	count(names) == 1
	only := names[0]
}

people(set) := list_text([roles[r].many | some r in order; set[r]]) if count([r | some r in order; set[r]]) > 1
