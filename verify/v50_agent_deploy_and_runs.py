"""U50: an agent's active version is a deployment log, not a mutable
pointer, and a real run pins the version it used regardless of what gets
deployed afterward.

    docker compose exec -T munitas-api python /verify/v50_agent_deploy_and_runs.py
"""

from __future__ import annotations

import sys
import time
import uuid

from common import (CANARY, api, bearer_for, check, db, fixture_contract,
                    fixture_version, heading, require_api, skip, summary)

TENANT = CANARY
DEPARTMENT = "Verification"


def department() -> str:
    with db() as conn:
        row = conn.execute(
            "select id from department where tenant_id = %s and name = %s",
            (TENANT, DEPARTMENT),
        ).fetchone()
    if not row:
        raise RuntimeError(f"department {DEPARTMENT!r} missing; apply infra/postgres/seed-canary.sql")
    return str(row["id"])


def register_agent(owning: str) -> str:
    r = api("POST", "/agents/register", json={
        "tenant_id": TENANT, "name": f"deploy-{uuid.uuid4().hex[:8]}",
        "department_id": owning, "registered_by": "canary-engineer",
        "purpose": "verification fixture",
    })
    r.raise_for_status()
    return r.json()["id"]


def register_version(agent_id: str) -> str:
    r = api("POST", f"/agents/{agent_id}/versions", json={
        "code_hash": f"git:{uuid.uuid4().hex}", "source_path": "/checkout/agent",
        "model_id": "qwen2.5:7b",
        "tool_scope": ["list_review_queue", "read_dataset_version"],
        "registered_by": "canary-engineer",
    })
    r.raise_for_status()
    return r.json()["id"]


def wait_for_run(run_id: str, timeout: float = 60.0) -> dict | None:
    """Poll until the run stops moving.

    `awaiting_approval` counts as stopped: the graph pauses at its approval
    gate and stays there until a human acts, which could be days. Only
    `running` means something is still happening on its own.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = api("GET", f"/agents/runs/{run_id}", headers=bearer_for("canary-engineer")).json()
        if run["status"] != "running":
            return run
        time.sleep(2.0)
    return None


def main() -> int:
    require_api()
    owning = department()

    heading("U50: deployment is a log, not a pointer")

    agent_id = register_agent(owning)
    v1 = register_version(agent_id)
    v2 = register_version(agent_id)

    d1 = api("POST", f"/agents/{agent_id}/deploy", json={"agent_version_id": v1},
             headers=bearer_for("canary-engineer"))
    check("deploying v1 succeeds", d1.status_code == 201, f"HTTP {d1.status_code}")

    fetched = api("GET", f"/agents/{agent_id}", headers=bearer_for("canary-engineer")).json()
    check("v1 is now active", fetched["active_version"]["agent_version_id"] == v1,
          str(fetched.get("active_version")))

    d2 = api("POST", f"/agents/{agent_id}/deploy", json={"agent_version_id": v2},
             headers=bearer_for("canary-engineer"))
    check("deploying v2 succeeds", d2.status_code == 201, f"HTTP {d2.status_code}")

    fetched = api("GET", f"/agents/{agent_id}", headers=bearer_for("canary-engineer")).json()
    check("v2 is now active", fetched["active_version"]["agent_version_id"] == v2,
          str(fetched.get("active_version")))

    with db() as conn:
        n = conn.execute(
            "select count(*) as n from agent_deployment where agent_id = %s", (agent_id,)
        ).fetchone()["n"]
    check("both deployments survive as history, neither overwritten", n == 2, f"{n} rows")

    heading("U50: a run started with no target reports nothing to inspect, honestly")

    # Was a real bug: agent/graph.py's inspect node called
    # read_dataset_version(ctx, state["version_id"]) unconditionally, even
    # when None, reaching POST /credentials -- whose dataset_version_id is a
    # required, non-Optional str -- for a real HTTP 422. agent/tools.py's
    # _decide() was built only for the shape of an ordinary policy refusal
    # and could not parse FastAPI's own validation-error shape, so it fell
    # back to treating the 422 as an unremarkable denial. The run then
    # reported status='succeeded' with zero access_decision rows, a type
    # mismatch indistinguishable from a genuine, empty triage. Fixed by
    # having inspect check for a target before calling either tool (so no
    # doomed request is attempted at all), and by making _decide() raise a
    # distinct, uncaught error for any /credentials answer that is not one
    # of its three documented outcomes (200, 202, 403), rather than folding
    # it into a denial.
    untargeted = api("POST", f"/agents/{agent_id}/runs",
                     json={"purpose": "verification run, no target"},
                     headers=bearer_for("canary-engineer"))
    check("starting a run with no dataset_version_id returns 202",
          untargeted.status_code == 202,
          f"HTTP {untargeted.status_code}: {untargeted.text[:200]}")
    untargeted_id = untargeted.json().get("run_id")
    untargeted_run = wait_for_run(untargeted_id) if untargeted.status_code == 202 else None
    if untargeted_run is None:
        skip("the untargeted run reaches a terminal status", "no result within the timeout")
    else:
        # awaiting_approval is a terminal status for wait_for_run's purposes
        # (see its own docstring); this agent's graph always pauses at a
        # human gate, targeted or not, so reaching it -- not erroring, not
        # hanging -- is itself part of what this proves.
        check("it stops honestly rather than erroring or hanging",
              untargeted_run["status"] == "awaiting_approval",
              f"status={untargeted_run['status']!r} error={untargeted_run.get('error')!r}")
        check("no doomed credential request was attempted",
              untargeted_run["tool_calls"] == 0 and untargeted_run["decisions"] == [],
              f"tool_calls={untargeted_run['tool_calls']}, decisions={untargeted_run['decisions']}")

    heading("U50: a run pins its version, and does not move if redeployed")

    # A real, targeted run from here on, so it can exercise deployment
    # history, run pinning and access_decision linkage below -- the
    # untargeted case above already proved on its own terms.
    # Sealed at PUBLISHED on purpose. An agent reaches published data and no
    # further, so pointing this run at anything more sensitive would make it
    # wait for a custodian to grant access, which U51 covers and this script
    # does not: the subject here is which version a run pins, not how it gets
    # permission to read one.
    schema_id = fixture_contract(TENANT)
    target_version = fixture_version(TENANT, schema_id, "PUBLISHED")

    started = api("POST", f"/agents/{agent_id}/runs",
                  json={"purpose": "verification run",
                        "dataset_version_id": target_version["id"]},
                  headers=bearer_for("canary-engineer"))
    check("starting a run returns 202 immediately", started.status_code == 202,
          f"HTTP {started.status_code}: {started.text[:200]}")
    run_id = started.json().get("run_id")

    # Redeploy back to v1 while (or just after) the run above is in flight.
    api("POST", f"/agents/{agent_id}/deploy", json={"agent_version_id": v1},
        headers=bearer_for("canary-engineer"))

    run = wait_for_run(run_id) if started.status_code == 202 else None
    if run is None:
        skip("the run reaches a terminal status",
             "no result within the timeout; is `python -m worker.main` running?")
    else:
        # The graph stops before `await_approval` on every run, so this is
        # where a normal run ends up. It used to be written down as
        # `succeeded`, which claimed the run had finished work it had not
        # started.
        check("the run stops at its approval gate rather than claiming it finished",
              run["status"] == "awaiting_approval", str(run["status"]))
        check("the run is pinned to v2, the version active when it started, "
              "not v1, redeployed afterward",
              run["agent_version_id"] == v2, str(run["agent_version_id"]))

        with db() as conn:
            decisions = conn.execute(
                "select count(*) as n from access_decision where agent_run_id = %s", (run_id,)
            ).fetchone()["n"]
        check("its tool calls left access_decision rows tagged with this run",
              decisions > 0, f"{decisions} rows")

        heading("U50: a paused run resumes only when somebody else approves it")

        paused_tool_calls = run["tool_calls"]

        self_approve = api("POST", f"/agents/runs/{run_id}/approve",
                           headers=bearer_for("canary-engineer"))
        check("the person who started the run cannot approve it",
              self_approve.status_code == 403,
              f"HTTP {self_approve.status_code}: {self_approve.text[:200]}")

        # The branch above is the readable refusal. This is the guarantee: the
        # constraint refuses the same write even with the API out of the way.
        with db() as conn:
            try:
                conn.execute(
                    "update agent_run set approved_by = requested_by where id = %s", (run_id,)
                )
                refused = None
            except Exception as exc:
                refused = str(exc)
        check("the database refuses a self-approval on its own, not only the API",
              refused is not None and "agent_run_no_self_approval" in (refused or ""),
              refused or "the update was accepted")

        approved = api("POST", f"/agents/runs/{run_id}/approve",
                       headers=bearer_for("canary-dpo"))
        check("a different person can approve it", approved.status_code == 202,
              f"HTTP {approved.status_code}: {approved.text[:200]}")

        resumed = wait_for_run(run_id) if approved.status_code == 202 else None
        if resumed is None:
            skip("the approved run finishes", "no result within the timeout")
        else:
            check("the approved run runs to the end", resumed["status"] == "succeeded",
                  str(resumed["status"]))
            check("resuming records the approver", resumed["approved_by"] == "canary-dpo",
                  str(resumed.get("approved_by")))
            # The resume counts tool calls in its own process, starting at
            # zero. Written back as an increment rather than an absolute, so
            # what the first invocation recorded survives.
            check("the tool calls the first half made are not overwritten by the resume",
                  resumed["tool_calls"] >= paused_tool_calls,
                  f"{paused_tool_calls} before, {resumed['tool_calls']} after")

        already = api("POST", f"/agents/runs/{run_id}/approve",
                      headers=bearer_for("canary-custodian"))
        check("a run that is not waiting cannot be approved again",
              already.status_code == 400,
              f"HTTP {already.status_code}: {already.text[:200]}")

    heading("U50: agent, agent_run and agent_deployment are guarded by the retired-tenant trigger")

    with db() as conn:
        guarded = {
            row["tgrelid_name"] for row in conn.execute(
                "select c.relname as tgrelid_name from pg_trigger t "
                "join pg_class c on c.oid = t.tgrelid "
                "where t.tgname like 'refuse_retired_%'"
            ).fetchall()
        }
    for table in ("agent", "agent_version", "agent_deployment", "agent_run"):
        check(f"{table} is guarded", table in guarded, f"guarded tables: {sorted(guarded)}")

    return summary("U50")


if __name__ == "__main__":
    sys.exit(main())
