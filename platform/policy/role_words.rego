package munitas.role_words

import rego.v1

# What each role is called, written once.
#
# Every sentence that names a role is built from this table: the console's labels (through `GET /policy/roles`), the refusals about locked data in access.rego,
# the limits an organisation sets (limits.py), and the "This page is for ..." sentence of every page in screens.rego. A role is renamed here and nowhere
# else.
#
#   label    what the console calls the role: "Data engineer"
#   one      one person who holds it, with its article: "a data engineer"
#   many     the people who hold it: "data engineers"
#   person   whether a person can hold it. The roles that are not people are the identities that platform components run as, and only have a label.
#
# `order` is the order the roles are listed in when a sentence names several of them.
#
# ADDING OR RENAMING A ROLE touches more than this file. Everything built from this table follows by itself, and these do not:
#   1. the policy sets in access.rego that say what the role may do (the *_roles sets), with tests;
#   2. the console's own lists, written by hand: CUSTOMER_ROLES and INTERNAL_ROLES in web/src/features/roles/Roles.tsx, the role descriptions in web/src/identity/principals.ts, REQUESTABLE_ROLES
#      in web/src/features/operate/RolesHeld.tsx and GOOD_FOR in docs/tools/generate_services_page.py, so a new person role is missing from each page until it is added there;
#   3. docs/tools/roles_reference.json, the catalogue behind the public Roles reference: write what the role can and cannot do, then run docs/tools/generate_roles_reference.py;
#   4. docs/tools/walkthrough_kit.py: the role names in WORDS are written by hand, and so is prose in walkthroughs and page copy;
#   5. docs/internal/ROLE_NAMES.md, which records the roles and how to change them.
# Check U204 (verify/v189_roles_documented.py) fails until 1, 2, 3 and this table agree, and until the public page is regenerated. 4 and 5 are not checked. The screens catalogue
# (screens.rego) lists which roles see each page in their menu and is checked separately by U188.

roles := {
	"data_custodian": {"label": "Data custodian", "one": "a data custodian", "many": "data custodians", "person": true},
	"network_architect": {"label": "Network architect", "one": "a network architect", "many": "network architects", "person": true},
	"pipeline_operator": {"label": "Data engineer", "one": "a data engineer", "many": "data engineers", "person": true},
	"deid_reviewer": {"label": "De-identification reviewer", "one": "a de-identification reviewer", "many": "de-identification reviewers", "person": true},
	"privacy_auditor": {"label": "Privacy auditor", "one": "a privacy auditor", "many": "privacy auditors", "person": true},
	"analyst": {"label": "Analyst", "one": "an analyst", "many": "analysts", "person": true},
	"notebook_explore": {"label": "Researcher", "one": "a researcher", "many": "researchers", "person": true},
	"dpo": {"label": "Data protection officer", "one": "the data protection officer", "many": "the data protection officer", "person": true},
	"ai_risk_owner": {"label": "AI risk owner", "one": "the AI risk owner", "many": "the AI risk owner", "person": true},
	"hybridops": {"label": "Support and reliability", "one": "support and reliability", "many": "support and reliability", "person": true},
	"platform_admin": {"label": "Platform administrator", "one": "a platform administrator", "many": "platform administrators", "person": true},
	"pipeline_action": {"label": "Pipeline action", "person": false},
	"annotation_tool": {"label": "Annotation tool", "person": false},
	"training_job": {"label": "Training job", "person": false},
	"model_eval": {"label": "Model evaluation", "person": false},
	"agent_runtime": {"label": "Agent runtime", "person": false},
}

order := ["data_custodian", "network_architect", "pipeline_operator", "deid_reviewer", "privacy_auditor", "analyst", "notebook_explore", "dpo", "ai_risk_owner", "hybridops", "platform_admin"]

# A list of items as a sentence part: "a", "a and b", "a, b and c".
list_text(items) := items[0] if count(items) == 1

list_text(items) := concat(" and ", items) if count(items) == 2

list_text(items) := sprintf("%s and %s", [concat(", ", array.slice(items, 0, count(items) - 1)), items[count(items) - 1]]) if count(items) > 2

# The same, for a sentence where any one of the roles is enough: "the data protection officer or the AI risk owner". One role reads as in `people`.
either(set) := people(set) if count([r | some r in order; set[r]]) == 1

either(set) := concat(" or ", [roles[r].many | some r in order; set[r]]) if count([r | some r in order; set[r]]) == 2

either(set) := sprintf("%s or %s", [concat(", ", array.slice(names, 0, count(names) - 1)), names[count(names) - 1]]) if {
	names := [roles[r].many | some r in order; set[r]]
	count(names) > 2
}

# The people a set of roles are, in words: "data engineers and platform administrators". A set of one role is spoken of as that role: "the platform
# administrator".
people(set) := regex.replace(roles[only].one, `^an? `, "the ") if {
	names := [r | some r in order; set[r]]
	count(names) == 1
	only := names[0]
}

people(set) := list_text([roles[r].many | some r in order; set[r]]) if count([r | some r in order; set[r]]) > 1
