package munitas.screens_test

import data.munitas.access
import data.munitas.role_words
import data.munitas.screens
import rego.v1

# Every role that a person can hold.
people_roles := {"analyst", "data_custodian", "deid_reviewer", "dpo", "network_architect", "notebook_explore", "pipeline_operator", "platform_admin", "privacy_auditor", "hybridops"}

is_complete(c) if {
	every field in ["path", "label", "open_to", "menu", "why", "api"] {
		object.get(c, field, null) != null
	}
}

test_every_page_says_everything_about_itself if {
	every _, c in screens.catalogue {
		is_complete(c)
	}
}

test_no_two_pages_share_a_path if {
	paths := [c.path | some _, c in screens.catalogue]
	count(paths) == count({p | some p in paths})
}

# The menu may offer a page to fewer people than may open it, but never to more: it would offer a link that refuses the person who follows it.
test_the_menu_never_offers_a_page_to_somebody_it_refuses if {
	every _, c in screens.catalogue {
		menu_within_open(c)
	}
}

menu_within_open(c) if c.open_to == "everyone"

menu_within_open(c) if {
	is_set(c.open_to)
	is_set(c.menu)
	count(c.menu - c.open_to) == 0
}

# A typo in a role would limit a page to nobody, or look like a role that does not exist. Every role named has to be a role the policy knows.
test_every_role_named_is_a_role_the_policy_knows if {
	named := {r |
		some _, c in screens.catalogue
		some list in [c.open_to, c.menu]
		is_set(list)
		some r in list
	}
	known := {r | some r, _ in access.role_floor}
	count(named - known) == 0
}

test_a_page_that_asks_for_a_platform_administrator_is_open_to_that_role_only if {
	screens.may_open with input as {"screen": "services", "roles": ["platform_admin"]}
	screens.may_open with input as {"screen": "legal_holds", "roles": ["analyst", "platform_admin"]}
	every role in people_roles - {"platform_admin"} {
		not screens.may_open with input as {"screen": "services", "roles": [role]}
		not screens.may_open with input as {"screen": "legal_holds", "roles": [role]}
	}
}

test_access_decisions_are_for_custodians_the_data_protection_officer_and_administrators if {
	every role in {"data_custodian", "dpo", "platform_admin"} {
		screens.may_open with input as {"screen": "audit", "roles": [role]}
	}
	every role in people_roles - {"data_custodian", "dpo", "platform_admin"} {
		not screens.may_open with input as {"screen": "audit", "roles": [role]}
	}
}

test_a_person_with_several_roles_may_open_what_any_of_them_may if {
	screens.may_open with input as {"screen": "audit", "roles": ["analyst", "dpo"]}
}

test_a_page_open_to_everyone_is_open_to_every_role if {
	every name, c in screens.catalogue {
		everyone_may_open(name, c)
	}
}

everyone_may_open(_, c) if c.open_to != "everyone"

everyone_may_open(name, c) if {
	c.open_to == "everyone"
	every role in people_roles {
		screens.may_open with input as {"screen": name, "roles": [role]}
	}
}

test_pages_open_to_everyone_are_open_to_every_role_including_a_person_with_none if {
	screens.may_open with input as {"screen": "gates", "roles": []}
	screens.may_open with input as {"screen": "directory", "roles": ["analyst"]}
	screens.may_open with input as {"screen": "closing", "roles": ["notebook_explore"]}
}

test_a_page_that_does_not_exist_is_refused_and_says_so if {
	d := screens.screen_decision with input as {"screen": "no-such-page", "roles": ["platform_admin"]}
	d.allow == false
	d.reasons == ["there is no such page"]
}

test_a_refusal_says_who_the_page_is_for if {
	d := screens.screen_decision with input as {"screen": "services", "roles": ["analyst"]}
	d.allow == false
	d.reasons == ["this page is for the platform administrator"]
}

test_an_allowed_page_has_no_reasons if {
	d := screens.screen_decision with input as {"screen": "audit", "roles": ["dpo"]}
	d.allow == true
	d.reasons == []
}

# The two pages whose roles another rule already owns take them from it, so the roles cannot drift apart.
test_the_locked_data_audit_takes_its_roles_from_the_evidence_rule if {
	screens.catalogue.locking_audit.open_to == access.audit_evidence_roles
	every role in people_roles {
		opened := screens.may_open with input as {"screen": "locking_audit", "roles": [role]}
		read := access.may_read_audit_evidence with input as {"person": {"id": "x", "roles": [role]}}
		opened == read
	}
}

test_storage_housekeeping_takes_its_roles_from_the_housekeeping_rule if {
	screens.catalogue.housekeeping.open_to == (access.platform_housekeeping_roles | access.tenant_housekeeping_roles)
}

test_the_console_is_told_about_every_page if {
	for_a_dpo := screens.for_roles with input as {"roles": ["dpo"]}
	count(for_a_dpo) == count(screens.catalogue)
	for_a_dpo.audit.open == true
	for_a_dpo.services.open == false
	for_a_dpo.services.who == "the platform administrator"
}

test_the_menu_for_a_researcher_offers_only_what_is_for_everyone if {
	for_a_researcher := screens.for_roles with input as {"roles": ["notebook_explore"]}
	for_a_researcher.datasets.menu == true
	for_a_researcher.notebooks.menu == true
	for_a_researcher.services.menu == false
	for_a_researcher.audit.menu == false
	for_a_researcher.agents.menu == false
	for_a_researcher.agents.open == false
	for_a_researcher.pipelines.open == true
}

test_the_agents_runs_and_egress_lists_are_for_their_menu_roles if {
	every screen in ["agents", "action_runs"] {
		screens.may_open with input as {"screen": screen, "roles": ["pipeline_operator"]}
		screens.may_open with input as {"screen": screen, "roles": ["platform_admin"]}
		every role in people_roles - {"pipeline_operator", "platform_admin"} {
			not screens.may_open with input as {"screen": screen, "roles": [role]}
		}
	}
}

test_the_egress_list_is_for_architects_engineers_and_administrators if {
	every role in ["network_architect", "pipeline_operator", "platform_admin"] {
		screens.may_open with input as {"screen": "egress_approvals", "roles": [role]}
	}
	every role in people_roles - {"network_architect", "pipeline_operator", "platform_admin"} {
		not screens.may_open with input as {"screen": "egress_approvals", "roles": [role]}
	}
}

# The sentence a refused person reads is built from the roles the page is open to, so it cannot name anyone else.
test_the_sentence_names_the_roles_the_page_is_open_to if {
	limited := [c | some c in screens.catalogue; is_set(c.open_to)]
	count(limited) > 0
	every c in limited {
		screens.who_of(c) == role_words.people(c.open_to)
	}
}

test_the_sentences_for_the_limited_pages if {
	by := screens.for_roles with input as {"roles": []}
	by.services.who == "the platform administrator"
	by.audit.who == "data custodians, the data protection officer and platform administrators"
	by.agents.who == "data engineers and platform administrators"
	by.egress_approvals.who == "network architects, data engineers and platform administrators"
	by.locking_audit.who == "privacy auditors and the data protection officer"
	by.housekeeping.who == "data custodians, the data protection officer, support and reliability and platform administrators"
	by.datasets.who == "everybody"
}
