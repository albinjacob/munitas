"""Agent identity, and why it is not in the graph state.

One rule is load bearing here: an agent's identity comes
from the runtime and is never read from anything the model can write.

The reason is specific. A LangGraph agent's state is a dictionary the model
contributes to. If the principal, the roles or the purpose live in that
dictionary, then a document the agent reads can contain text that persuades the
model to write a different principal into state, and the next policy check is
made against the identity the attacker chose. No amount of prompt hardening
fixes that, because the vulnerability is in where the value is stored, not in
how the model was asked to behave.

So identity is held here, in a frozen object created before the graph starts,
and every tool call reads it from the runtime rather than from state. The model
cannot reach it. V8 tests exactly this: a planted instruction asks the agent to
read an out-of-scope dataset, and the request is evaluated against the real
identity regardless of what the model was told.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentIdentity:
    """Who the agent is. Immutable, and never serialised into graph state."""

    principal: str
    tenant: str
    roles: tuple[str, ...]
    purpose: str
    # Which sealed agent_version this identity was launched as. Required,
    # not optional: an identity with no version is exactly the traceability
    # gap this field exists to close, since "which code made this decision"
    # must always have an answer.
    agent_version_id: str
    # Datasets this run is allowed to touch. Named at launch, so an agent that
    # decides mid-run it needs another dataset has to be denied rather than
    # trusted to ask nicely.
    #
    # A fast, free pre-check only. The real boundary is server-side, in
    # POST /credentials (main.py), checked against agent_run.dataset_version_id
    # which is a fact the server owns and no calling code can talk its way around.
    # This field being populated (worker/agent_run_activities.py's
    # _context_for()) just means well-behaved code never has to make the
    # round trip to find out.
    allowed_versions: frozenset[str] = field(default_factory=frozenset)
    # Tool functions this agent version declared it needs
    # (agent_version.tool_scope). Empty means unrestricted, matching every
    # version sealed before this field existed.
    #
    # Explicitly NOT a security boundary: the server has no independent way
    # to know which Python function decided to make a given request, only
    # this identity's own idea of it, which is exactly what an agent whose
    # code is not Munitas's own could misreport. This catches a mistake in
    # code this platform controls; it does not catch a deliberate lie.
    # Defending against that is a distinct, harder problem, and not solved
    # here.
    tool_scope: frozenset[str] = field(default_factory=frozenset)
    # Proof this identity is the run it claims to be, not merely something
    # that learned the run's id. Minted once by the platform at run creation
    # (agent_run.run_secret) and read from the runtime the same way every
    # other field here is -- never from state, never asserted by the caller.
    # `POST /credentials` (main.py) refuses a request naming a real run if
    # this does not match what it stored. `None` for a run row created
    # before this field existed, in which case the server does not require
    # one either.
    run_secret: str | None = None

    def policy_input(self, dataset: dict, purpose: str | None = None) -> dict:
        """The OPA input for a request by this agent.

        `purpose` defaults to the purpose fixed at launch. A caller may narrow
        it but the agent cannot widen it, because the value comes from here.
        """
        return {
            "principal": {
                "id": self.principal,
                "tenant": self.tenant,
                "roles": list(self.roles),
                "leases": [],
            },
            "dataset": dataset,
            "purpose": purpose or self.purpose,
        }


@dataclass(frozen=True)
class Budget:
    """Ceilings that stop a run rather than warn about it.

    An agent that loops does not error. It works, indefinitely, producing
    plausible output and consuming tokens, which is why a budget has to be a
    hard stop rather than an alert. V9 tests that a deliberately looping task
    halts on the tool-call ceiling.
    """

    max_tool_calls: int = 25
    max_tokens: int = 100_000
    max_seconds: int = 300
