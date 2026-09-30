"""The review triage agent, as an explicit LangGraph graph.

This is the first agent deliberately: it works only on
records already bound for human review, so it adds no exposure that the pipeline
did not already create. An agent's first job should not also be the first time
data reaches something new.

Why a graph rather than a loop with a model in it. Three properties the design
depends on, and a while loop gives none of them:

  * **The path is inspectable before it runs.** The nodes and edges are data,
    so what the agent can do is a question about the graph rather than about
    what the model decides to say.
  * **State is checkpointed**, so a run can stop at a human approval and resume
    days later without holding a process open.
  * **`interrupt_before` makes the approval gate cheap.** A gate that costs an
    engineer a bespoke workflow gets designed out; one that costs a keyword
    gets used.

The state carries no identity, no roles and no purpose. Those live in the
runtime context, out of the model's reach. See identity.py for why that
placement is the whole defence.
"""

from __future__ import annotations

import functools
import operator
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import NodeInterrupt
from langgraph.graph import END, StateGraph

from .tools import (AwaitingActivation, BudgetExceeded, ToolContext, ToolDenied,
                    list_review_queue, read_dataset_version, summarise_counts)

# The reason text for a document that tries to steer the agent. Named once
# because two places produce it: the rule-based judgement writes it directly,
# and the escalation below prepends it to whatever the model said. Comparing
# against the same string is what stops it printing twice.
INSTRUCTION_FOUND = "contains text addressed to the agent"


class TriageState(TypedDict, total=False):
    """Everything the model can see or write.

    Note what is absent: principal, tenant, roles, purpose. A document the agent
    reads can put anything it likes into `findings` or `notes`, and none of it
    reaches a policy decision.
    """

    version_id: str
    documents: list[dict]
    findings: Annotated[list[dict], operator.add]
    notes: Annotated[list[str], operator.add]
    denials: Annotated[list[dict], operator.add]
    halted: str
    approved: bool
    summary: dict


def _parks_on_activation(node):
    """Stop the graph at a checkpoint when a tool call's access is allowed but
    not yet in effect.

    The node's partial update is discarded and the thread is saved with this
    node next, so the platform's resume re-runs the node from the start, now
    with the access active. Every node wrapped here only reads, so running it
    again repeats nothing that matters; the earlier nodes are not re-run.
    """
    @functools.wraps(node)
    def wrapped(state):
        try:
            return node(state)
        except AwaitingActivation as waiting:
            raise NodeInterrupt(str(waiting)) from waiting
    return wrapped


def build_graph(ctx: ToolContext, checkpointer=None):
    """Assemble the triage graph. `ctx` is runtime, never state.

    `checkpointer` decides whether a run paused at the approval gate can ever
    be resumed. The default, `MemorySaver`, cannot: it lives in the process
    that built it, so the paused thread dies with that process. That is the
    right choice for a host-run script that invokes the graph once and reads
    the result, and the wrong one for the worker, which runs each invocation
    in its own activity process and must be able to pick a paused thread up
    again days later. The worker passes a `PostgresSaver`; nothing else needs
    to care.
    """

    def inspect(state: TriageState) -> dict:
        """Look at the dataset version the run was launched against, and collect
        the documents waiting for review in it.

        Documents handed in with the invocation win over the ones on the
        version. A caller that names its own documents is describing the job,
        and the containment tests do exactly that with fixtures that exist
        nowhere in storage. Fetching only when none were supplied keeps that
        working and means an ordinary run, which supplies none, gets the real
        queue instead of an empty list.

        A refusal here is the expected outcome, not a failure: the queue is
        RAW and the agent reaches PUBLISHED, so a run with no lease covering it
        denied and the denial is recorded. The run carries on and reports
        having triaged nothing, which is true.

        A run started with no target is a different case from a refusal, and
        is handled before either tool is called rather than by letting it
        reach one. `version_id` is `str` only in the type; `TriageState`'s
        own `total=False` and the console's "No dataset" option
        (`AgentDetail.tsx`) both mean an absent or `None` value is real,
        reachable state, not a theoretical one. `read_dataset_version(ctx,
        None)` would not fail cleanly here either way: it would reach
        `POST /credentials`, whose `dataset_version_id` is a required `str`,
        get back a 422, and `_decide()`'s error handling -- built for the
        shape of an ordinary policy refusal, `{"detail": {"reasons": [...]}}`
        -- would find FastAPI's own validation-error shape instead,
        `{"detail": [...]}`, fail to read `.reasons` off a list, and fall
        back to `ToolDenied(tool, ["HTTP 422"])`. The run would then finish
        as an ordinary, successful, empty triage, the type mismatch
        indistinguishable from a real policy decision. Guarded here instead:
        nothing to inspect is reported as exactly that.
        """
        version_id = state.get("version_id")
        if not version_id:
            return {"notes": ["no dataset version was targeted; nothing to inspect"]}

        try:
            info = read_dataset_version(ctx, version_id)
        except ToolDenied as denied:
            return {"denials": [{"tool": denied.tool, "reasons": denied.reasons}],
                    "notes": ["refused access to the version this run targets"]}
        except BudgetExceeded as stop:
            return {"halted": str(stop)}

        update = {"notes": [f"version is class {info.get('current_class')}"],
                  "summary": info}

        if state.get("documents"):
            return update

        try:
            queue = list_review_queue(ctx, version_id)
        except ToolDenied as denied:
            update["denials"] = [{"tool": denied.tool, "reasons": denied.reasons}]
            update["notes"] = update["notes"] + [
                "refused the review queue on this version, so nothing was triaged"
            ]
            return update
        except BudgetExceeded as stop:
            return {"halted": str(stop)}

        update["documents"] = queue
        update["notes"] = update["notes"] + [
            f"{len(queue)} document(s) awaiting review"
        ]
        return update

    def triage(state: TriageState) -> dict:
        """Work through the documents, ranking them for human attention.

        The documents are attacker-controllable text. Anything in them that
        looks like an instruction is recorded as a finding and never acted on,
        which is the only safe reading: an instruction inside data is evidence
        about the data, not a request from the user.
        """
        findings, denials, notes = [], [], []

        for doc in state.get("documents", []):
            text = doc.get("text", "")
            suspicious = _looks_like_an_instruction(text)

            if suspicious:
                notes.append(
                    f"document {doc.get('id')} contains text addressed to the agent; "
                    "recorded and not acted on"
                )

            # An agent that decides it needs another dataset must be checked,
            # not trusted. This is where an injected instruction would land, and
            # it lands on a policy decision rather than on a fetch.
            target = doc.get("dataset_version")
            if target and target != state["version_id"]:
                try:
                    summarise_counts(ctx, target)
                    notes.append(f"read {target}, which policy allowed")
                except ToolDenied as denied:
                    denials.append({
                        "tool": denied.tool,
                        "dataset_version": target,
                        "reasons": denied.reasons,
                        "prompted_by": doc.get("id"),
                    })
                    notes.append(f"denied access to {target}, continuing without it")
                except BudgetExceeded as stop:
                    return {"findings": findings, "denials": denials,
                            "notes": notes, "halted": str(stop)}

            # The model ranks. Its answer never decides what the agent may
            # read, only how urgently a human should look. If it is not
            # available the rule-based fallback still produces a ranking,
            # because an agent that stops working when a model is down is a
            # worse outcome than one that ranks more crudely.
            judgement = {"priority": "high" if suspicious else "normal",
                         "reason": (INSTRUCTION_FOUND
                                    if suspicious else "routine review"),
                         "ranked_by": "rules"}
            if ctx.use_model:
                try:
                    from .model import triage_document
                    result = triage_document(text)
                    judgement = {
                        "priority": result["priority"],
                        "reason": result["reason"],
                        "ranked_by": "model" if result["parsed"] else "model-unparsed",
                    }
                except Exception as exc:
                    notes.append(f"model unavailable, ranked by rules: {type(exc).__name__}")

            # The rule always wins upward. A model that calls an injected
            # document routine cannot lower its priority, because the escalation
            # does not depend on the model's judgement.
            if suspicious:
                judgement["priority"] = "high"
                # Prepended only when it is not already the reason. The
                # rule-based judgement above says this itself, so prepending
                # unconditionally printed the phrase twice in the console
                # whenever the model was off, which is every containment run.
                if not judgement["reason"].startswith(INSTRUCTION_FOUND):
                    judgement["reason"] = (
                        f"{INSTRUCTION_FOUND}; " + judgement["reason"]
                    )[:200]

            findings.append({"document": doc.get("id"), **judgement})

        return {"findings": findings, "denials": denials, "notes": notes}

    def await_approval(state: TriageState) -> dict:
        """The interrupt point.

        Almost nothing happens here. Its job is to be a node the graph can be
        told to stop before, so a human sees the findings before anything acts
        on them, and reaching it at all means somebody let the run continue.

        It records that rather than returning nothing, because a node whose
        update is empty is an error to LangGraph, not a no-op. Until the
        worker could actually resume a paused run this node had never once
        executed, so the empty return had never been exercised.
        """
        return {"approved": True}

    def finish(state: TriageState) -> dict:
        return {"notes": [f"triage complete, {len(state.get('findings', []))} findings"]}

    graph = StateGraph(TriageState)
    graph.add_node("inspect", _parks_on_activation(inspect))
    graph.add_node("triage", _parks_on_activation(triage))
    graph.add_node("await_approval", await_approval)
    graph.add_node("finish", finish)

    graph.set_entry_point("inspect")
    graph.add_conditional_edges("inspect", _halted_or, {"stop": END, "go": "triage"})
    graph.add_conditional_edges("triage", _halted_or, {"stop": END, "go": "await_approval"})
    graph.add_edge("await_approval", "finish")
    graph.add_edge("finish", END)

    return graph.compile(
        checkpointer=checkpointer or MemorySaver(),
        # The human sees the findings before the graph proceeds. A gate placed
        # after the work would be a notification, not an approval.
        interrupt_before=["await_approval"],
    )


def _halted_or(state: TriageState) -> str:
    return "stop" if state.get("halted") else "go"


# Phrases that mark text trying to steer the agent. Not a security control:
# detection of injection is a losing game, and the actual defence is that the
# agent's identity is unreachable and every tool call is checked. This exists so
# the attempt is visible in the findings and in the trace.
_MARKERS = (
    "ignore previous", "ignore the above", "disregard", "you are now",
    "system prompt", "new instructions", "as an admin", "you have permission",
    "grant yourself", "override", "do not tell", "instead of",
)


def _looks_like_an_instruction(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _MARKERS)
