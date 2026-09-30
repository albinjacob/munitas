"""Actually invoking an agent, as a Temporal activity.

Runs in a process separate from the API, the same reason
`hf_ingest_activities.py` exists: a slow or stuck run has no business
holding the process that serves every other request. Writes the
`agent_run` row's outcome directly via psycopg, the same as
`hf_ingest_activities.py`'s activities write straight to
`huggingface_fetch_job` rather than calling back through an HTTP endpoint.
`access_decision` rows are already written by the graph's own tool
calls, through the existing `/credentials` path, so there is nothing else
to report back.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from temporalio import activity

from . import config
from .agent_attempt import run_once
from .db import _db
from agent.graph import build_graph
from agent.identity import AgentIdentity, Budget
from agent.tools import ToolContext

log = logging.getLogger("munitas.worker")


def _context_for(params: dict) -> ToolContext:
    """The runtime identity a run executes under.

    Rebuilt from the run's own pinned fields on every activity, never carried
    across from a previous one: identity is derived, not handed along, the
    same rule agent/identity.py keeps everywhere else.

    `allowed_versions` and `tool_scope` are resolved here from the database
    rather than trusted from `params`, the same reason `resume_run` below
    re-derives its identity fields from `agent_run` instead of the workflow
    argument: a value that travelled through a Temporal workflow start is
    still just something a caller once said, and these two are meant to be
    checked against what the platform itself recorded, not against that.
    `allowed_versions` is a fast, free pre-check only; the real boundary for
    dataset scope is server-side in POST /credentials; see agent_run.
    dataset_version_id's comment in schema.sql. `tool_scope` has no
    equivalent real boundary yet; see agent/identity.py's own field comment.
    """
    with _db() as conn:
        row = conn.execute(
            "select dataset_version_id, run_secret from agent_run where id = %s",
            (params["run_id"],),
        ).fetchone()
        version_row = conn.execute(
            "select tool_scope from agent_version where id = %s",
            (params["agent_version_id"],),
        ).fetchone()

    allowed_versions = frozenset({str(row[0])}) if row and row[0] else frozenset()
    tool_scope = frozenset(version_row[0]) if version_row and version_row[0] else frozenset()
    run_secret = row[1] if row else None

    identity = AgentIdentity(
        principal=params["principal_id"],
        tenant=params["tenant_id"],
        roles=("agent_runtime",),
        purpose=params["purpose"],
        agent_version_id=params["agent_version_id"],
        allowed_versions=allowed_versions,
        tool_scope=tool_scope,
        run_secret=run_secret,
    )
    return ToolContext(
        identity=identity, run_id=params["run_id"], budget=Budget(), use_model=False
    )


def _record_outcome(graph, thread: dict, run_id: str, ctx: ToolContext) -> dict:
    """Read what the graph actually did, and write it back to `agent_run`.

    Shared by the first invocation and the resume, because the question "did
    this finish?" has exactly one right answer and a second copy of it would
    eventually disagree with the first.

    `interrupt_before=["await_approval"]` means an ordinary `invoke()` returns
    a state that looks complete and is not: the graph stopped at the approval
    gate and the `finish` node never ran. `get_state().next` is what tells the
    two apart. A non-empty `next` is work still pending, so the run is waiting
    on a human, not finished. Before this, every run that was not explicitly
    halted was written down as `succeeded`, which was false for all of them.
    """
    snapshot = graph.get_state(thread)
    pending, state = snapshot.next, snapshot.values
    halted = state.get("halted")

    # Stopped anywhere other than the approval gate means a tool call's access
    # was allowed but not yet in effect (agent/graph.py, _parks_on_activation).
    # Nobody decides anything then: the platform resumes the run by itself.
    if halted:
        status = "halted"
    elif pending == ("await_approval",):
        status = "awaiting_approval"
    elif pending:
        status = "awaiting_activation"
    else:
        status = "succeeded"

    # `tool_calls` accumulates rather than overwrites. Each activity counts
    # only the calls made in its own process, so the resume would otherwise
    # erase what the first invocation recorded. Today's graph makes no tool
    # call after the approval gate, so the increment is zero, but a graph that
    # grows one later should not need this line changed to stay correct.
    # Findings overwrite rather than accumulate, the opposite of `tool_calls`
    # and for the opposite reason. The count is a tally of what each process
    # spent, so halves must be added. The findings are the graph's current
    # conclusion about the same documents, so the later read is the whole
    # answer and appending would list every document twice after a resume.
    findings = state.get("findings", [])

    with _db() as conn:
        conn.execute(
            """update agent_run
                 set status = %s,
                     tool_calls = tool_calls + %s,
                     halted_reason = %s,
                     findings = %s,
                     ended_at = case when %s in ('awaiting_approval', 'awaiting_activation')
                                     then null else now() end,
                     awaiting_activation_since = case when %s = 'awaiting_activation'
                                                      then now() end
               where id = %s and status in ('running', 'awaiting_approval')""",
            (status, ctx.tool_calls, halted, json.dumps(findings), status, status, run_id),
        )

    return {"status": status, "tool_calls": ctx.tool_calls, "findings": findings}


@contextmanager
def _graph_for(params: dict):
    """A graph wired to the durable checkpointer, plus its context and thread.

    `.setup()` creates the checkpointer's own tables. They are the library's,
    not the platform's, so they stay out of `platform/schema.sql` for the same
    reason Temporal's tables do: a schema file that describes tables nobody
    here designed cannot be trusted to describe them correctly. Running it on
    every activity is wasteful and harmless, since every statement in it is
    `if not exists`.
    """
    ctx = _context_for(params)
    with PostgresSaver.from_conn_string(config.PG_DSN) as checkpointer:
        checkpointer.setup()
        graph = build_graph(ctx, checkpointer=checkpointer)
        # The thread id is how the checkpointer finds this run's state again.
        # It is derived from the run id so a resume needs nothing carried over
        # from the process that paused.
        yield graph, ctx, {"configurable": {"thread_id": f"run-{params['run_id']}"}}


@activity.defn
def run_agent(params: dict) -> dict:
    """Build the identity, invoke the graph once, record the outcome.

    A single `graph.invoke()` call. `agent/graph.py`'s own nodes already
    catch `BudgetExceeded` and `ToolDenied` internally and fold them into
    `state["halted"]`/`state["denials"]`, so this only needs to distinguish
    "the graph ran and returned a state" (halted if the state says so,
    waiting on a human if the graph stopped at the approval gate, succeeded
    only if it reached the end) from "something below the graph itself broke"
    (an uncaught exception, handled by `fail_run` in the workflow).

    Done at most once however often Temporal hands it over (agent_attempt).
    """
    def work() -> dict:
        with _graph_for(params) as (graph, ctx, thread):
            graph.invoke(
                {"version_id": params["dataset_version_id"],
                 "documents": params.get("documents", [])},
                thread,
            )
            return _record_outcome(graph, thread, params["run_id"], ctx)

    return run_once(params["run_id"], work)


@activity.defn
def resume_run(params: dict) -> dict:
    """Carry on a run a human has approved.

    Everything the identity is built from comes back off the `agent_run` row
    rather than from the caller. The endpoint that starts this workflow knows
    all of it already, but a run's principal and purpose are what its tool
    calls are judged against, and those must come from the record of the run,
    not from a dictionary that travelled through a workflow argument.

    `invoke(None, ...)` is the resume: passing no input tells LangGraph to
    continue the checkpointed thread from where it stopped, rather than
    starting the graph over from `inspect`. The same resume carries on a run
    that parked waiting for its storage access to take effect: there the
    thread stopped at the node whose tool call was answered 202, and that
    node runs again.
    """
    run_id = params["run_id"]
    with _db() as conn:
        row = conn.execute(
            """select ar.agent_version_id, ar.purpose, ar.tenant_id, a.principal_id
                 from agent_run ar
                 join agent a on a.id = ar.agent_id
                where ar.id = %s""",
            (run_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"no such run: {run_id}")

    resolved = {
        "run_id": run_id,
        "agent_version_id": str(row[0]),
        "purpose": row[1],
        "tenant_id": row[2],
        "principal_id": row[3],
    }
    def work() -> dict:
        with _graph_for(resolved) as (graph, ctx, thread):
            graph.invoke(None, thread)
            return _record_outcome(graph, thread, run_id, ctx)

    return run_once(run_id, work)


@activity.defn
def fail_run(params: dict) -> None:
    """Mark a run failed, so a crash anywhere leaves a row the console can
    show, not a row stuck at whatever it was when the process died.

    `awaiting_approval` is in the guard alongside `running` because a resume
    that crashes starts from there, not from `running`.

    A tenant retired while this run was in flight is the one crash this
    cannot recover from by writing anything: the same trigger that made the
    original write to `agent_run` fail refuses this one too, by design, and
    retrying would just fail again forever. That case is logged and left
    alone rather than raised again, so the workflow still ends (visible as
    failed in Temporal's own history) instead of retrying against a write
    that can never succeed.
    """
    with _db() as conn:
        try:
            conn.execute(
                """update agent_run
                     set status = 'failed', error = %s, ended_at = now()
                   where id = %s and status in ('running', 'awaiting_approval')""",
                (params["error"][:2000], params["run_id"]),
            )
        except psycopg.errors.ReadOnlySqlTransaction:
            log.warning(
                "agent_run %s could not be marked failed: its tenant was "
                "retired while the run was in flight",
                params["run_id"],
            )
