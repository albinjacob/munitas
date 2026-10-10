package system.authz_test

import data.system.authz
import rego.v1

env := {"env": {"MUNITAS_OPA_TOKEN": "right-token"}}

req(method, path, identity) := {"method": method, "path": path, "identity": identity}

test_a_decision_is_answered_for_the_right_token if {
	authz.allow with input as req("POST", ["v1", "data", "munitas", "screens", "screen_decision"], "right-token") with opa.runtime as env
	authz.allow with input as req("GET", ["v1", "data", "munitas", "access", "class_order"], "right-token") with opa.runtime as env
}

test_a_wrong_or_missing_token_is_refused if {
	not authz.allow with input as req("POST", ["v1", "data", "munitas", "screens", "screen_decision"], "wrong-token") with opa.runtime as env
	not authz.allow with input as req("POST", ["v1", "data", "munitas", "screens", "screen_decision"], "") with opa.runtime as env
}

test_a_policy_cannot_be_changed_over_http_even_with_the_token if {
	not authz.allow with input as req("PUT", ["v1", "policies", "evil"], "right-token") with opa.runtime as env
	not authz.allow with input as req("DELETE", ["v1", "policies", "access"], "right-token") with opa.runtime as env
	not authz.allow with input as req("PUT", ["v1", "data", "munitas", "x"], "right-token") with opa.runtime as env
	not authz.allow with input as req("PATCH", ["v1", "data", "munitas", "x"], "right-token") with opa.runtime as env
}

test_other_interfaces_are_refused_even_with_the_token if {
	not authz.allow with input as req("GET", ["v1", "policies"], "right-token") with opa.runtime as env
	not authz.allow with input as req("POST", ["v1", "query"], "right-token") with opa.runtime as env
	not authz.allow with input as req("POST", ["v1", "compile"], "right-token") with opa.runtime as env
	not authz.allow with input as req("GET", ["metrics"], "right-token") with opa.runtime as env
}

test_the_health_check_needs_no_token if {
	authz.allow with input as {"method": "GET", "path": ["health"]} with opa.runtime as env
}

test_nothing_is_allowed_while_the_token_is_not_set if {
	not authz.allow with input as req("POST", ["v1", "data", "munitas", "x"], "") with opa.runtime as {"env": {}}
	not authz.allow with input as req("POST", ["v1", "data", "munitas", "x"], "anything") with opa.runtime as {"env": {}}
}
