package munitas.role_words_test

import data.munitas.access
import data.munitas.role_words
import rego.v1

test_every_role_a_person_can_hold_is_in_the_order_once if {
	people := {r | some r, w in role_words.roles; w.person}
	count(role_words.order) == count(people)
	{r | some r in role_words.order} == people
}

test_every_person_role_has_all_three_words if {
	every r in role_words.order {
		w := role_words.roles[r]
		w.label != ""
		w.one != ""
		w.many != ""
	}
}

test_the_roles_that_are_not_people_only_have_a_label if {
	machines := {r | some r, w in role_words.roles; not w.person}
	count(machines) == 5
	every r in machines {
		count(object.keys(role_words.roles[r])) == 2
	}
}

test_the_roles_the_policy_names_are_all_known if {
	named := access.approver_roles | access.operator_roles | access.intake_roles | access.code_registration_roles | access.set_standard_roles
	every r in named {
		role_words.roles[r]
	}
}

test_a_set_of_roles_is_named_in_words if {
	role_words.people({"platform_admin"}) == "the platform administrator"
	role_words.people({"platform_admin", "pipeline_operator"}) == "data engineers and platform administrators"
	role_words.people({"data_custodian", "dpo", "platform_admin"}) == "data custodians, the data protection officer and platform administrators"
	role_words.people({"hybridops", "platform_admin", "data_custodian", "dpo"}) == "data custodians, the data protection officer, support and reliability and platform administrators"
}

test_the_order_does_not_depend_on_how_the_set_was_written if {
	role_words.people({"platform_admin", "network_architect", "pipeline_operator"}) == "network architects, data engineers and platform administrators"
}
