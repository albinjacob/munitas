package system.authz

import rego.v1

# Who may call the policy engine's own HTTP interface.
#
# The engine is started with token authentication and this policy as its authorization (`--authentication=token --authorization=basic`), so nothing is
# answered unless the request carries the platform's token, and what the token may do is limited to asking for decisions. In particular, nobody can add,
# replace or delete a policy over HTTP: without this, any container on the network could send one request and make every page open to everybody.
#
# The token is in the engine's environment (MUNITAS_OPA_TOKEN), never in this file. If it is not set, no request is allowed.

default allow := false

# Asking for a decision or reading a document, and nothing else.
allow if {
	token_is_right
	input.method in {"GET", "POST"}
	input.path[0] == "v1"
	input.path[1] == "data"
}

# The health check, which the container runtime uses.
allow if {
	input.method == "GET"
	input.path == ["health"]
}

token_is_right if {
	expected := opa.runtime().env.MUNITAS_OPA_TOKEN
	expected != ""
	input.identity == expected
}
