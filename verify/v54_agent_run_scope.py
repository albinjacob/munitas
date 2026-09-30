"""U54: an agent cannot read a dataset its own run was not launched for,
even when its code skips agent/tools.py entirely and asks the control
plane directly.

The scenario this closes, stated directly: a registered user writes and
registers their own agent, and that agent's code (which this platform
does not control or inspect) asks for a dataset outside what any of its
runs were actually launched against. `agent/tools.py`'s own checks cannot
stop this, because nothing requires an agent's code to call that module at
all. This script proves the boundary that can: `POST /credentials`
(platform/api/app/main.py) itself, checked against `agent_run.
dataset_version_id`, a fact only `start_run`/`_start_waiting_on_access`
ever write.

Every request below is made with plain `httpx`, the same way a hostile
agent's own code would, never through `agent/tools.py`.

`tool_scope`'s best-effort check is not exercised here: it only exists
inside `agent/tools.py`, reachable only by actually running the graph,
which needs `python -m worker.main` on the host, the same precondition
U50/U51 already name. It is exercised implicitly whenever those run with
the worker up, since their fixture already declares a real `tool_scope`.

    docker compose exec -T munitas-api python /verify/v54_agent_run_scope.py
"""

from __future__ import annotations

import sys
import uuid

from common import CANARY, api, bearer_for, check, db, fixture_contract, fixture_version, heading, require_api, summary

DEPARTMENT = "Verification"


def department() -> str:
    with db() as conn:
        row = conn.execute(
            "select id from department where tenant_id = %s and name = %s",
            (CANARY, DEPARTMENT),
        ).fetchone()
    if not row:
        raise RuntimeError(f"department {DEPARTMENT!r} missing; apply infra/postgres/seed-canary.sql")
    return str(row["id"])


def register_agent(owning: str, name: str) -> tuple[str, str]:
    """Returns (agent_id, principal_id)."""
    r = api("POST", "/agents/register", json={
        "tenant_id": CANARY, "name": name, "department_id": owning,
        "registered_by": "canary-engineer", "purpose": "verification fixture",
    })
    r.raise_for_status()
    agent_id = r.json()["id"]
    with db() as conn:
        principal_id = conn.execute(
            "select principal_id from agent where id = %s", (agent_id,)
        ).fetchone()["principal_id"]
    return agent_id, principal_id


def register_and_deploy(agent_id: str) -> str:
    v = api("POST", f"/agents/{agent_id}/versions", json={
        "code_hash": f"git:{uuid.uuid4().hex}", "source_path": "/checkout/agent",
        "model_id": "qwen2.5:7b", "tool_scope": ["list_review_queue"],
        "registered_by": "canary-engineer",
    })
    v.raise_for_status()
    version_id = v.json()["id"]
    d = api("POST", f"/agents/{agent_id}/deploy",
           json={"agent_version_id": version_id},
           headers=bearer_for("canary-engineer"))
    d.raise_for_status()
    return version_id


def start_run(agent_id: str, dataset_version_id: str) -> str:
    r = api("POST", f"/agents/{agent_id}/runs", json={
        "purpose": "U54 run-scope check",
        "dataset_version_id": dataset_version_id,
    }, headers=bearer_for("canary-engineer"))
    check(f"starting a run against {dataset_version_id[:8]} returns 202",
          r.status_code == 202, f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["run_id"]


def run_secret_for(run_id: str) -> str:
    """The real secret `start_run` minted for this run.

    Fetched straight from the database, the same trust level this script
    already gives `principal_id` (`register_agent`, above): this script is
    proving the server-side boundary holds against code that skips
    `agent/tools.py`, not proving the secret itself can be discovered some
    other way.
    """
    with db() as conn:
        return conn.execute(
            "select run_secret from agent_run where id = %s", (run_id,)
        ).fetchone()["run_secret"]


def credential_request(principal: str, dataset_version_id: str, agent_run_id: str | None,
                       run_secret: str | None = None):
    body = {
        "principal": principal, "principal_kind": "workload",
        "roles": ["agent_runtime"], "tenant_id": CANARY,
        "dataset_version_id": dataset_version_id,
        "purpose": "U54 run-scope check",
        "run_secret": run_secret,
    }
    if agent_run_id is not None:
        body["agent_run_id"] = agent_run_id
    return api("POST", "/credentials", json=body)


def main() -> int:
    require_api()
    owning = department()

    heading("U54: fixtures (two agents, two runs, two dataset versions)")

    agent_id, principal = register_agent(owning, f"scope-check-{uuid.uuid4().hex[:8]}")
    other_agent_id, other_principal = register_agent(owning, f"scope-check-other-{uuid.uuid4().hex[:8]}")
    register_and_deploy(agent_id)
    register_and_deploy(other_agent_id)

    schema_id = fixture_contract(CANARY)
    version_a = fixture_version(CANARY, schema_id, "PUBLISHED")
    version_b = fixture_version(CANARY, schema_id, "PUBLISHED")

    run_a = start_run(agent_id, version_a["id"])
    run_b = start_run(agent_id, version_b["id"])
    secret_a = run_secret_for(run_a)
    secret_b = run_secret_for(run_b)

    heading("U54: a run's own dataset version is granted")

    own = credential_request(principal, version_a["id"], run_a, secret_a)
    check("the run's own target version is allowed",
          own.status_code == 200, f"HTTP {own.status_code}: {own.text[:200]}")

    heading("U54: everything else is refused, before policy is even asked")

    wrong_version = credential_request(principal, version_b["id"], run_a, secret_a)
    check("a different run's dataset version is refused, even though the "
          "agent could read it under a different run",
          wrong_version.status_code == 403, f"HTTP {wrong_version.status_code}")
    check("and says why", "outside the scope" in wrong_version.text,
          wrong_version.text[:200])

    no_run = credential_request(principal, version_a["id"], None)
    check("no agent_run_id at all is refused, not silently allowed",
          no_run.status_code == 403, f"HTTP {no_run.status_code}")

    fake_run = credential_request(principal, version_a["id"], str(uuid.uuid4()))
    check("a made-up agent_run_id is refused",
          fake_run.status_code == 403, f"HTTP {fake_run.status_code}")

    wrong_principal = credential_request(other_principal, version_a["id"], run_a, secret_a)
    check("a real run id, claimed by a different agent's principal, is refused",
          wrong_principal.status_code == 403, f"HTTP {wrong_principal.status_code}")

    heading("U54: naming the real run is not enough without its secret")

    wrong_secret = credential_request(principal, version_a["id"], run_a, "guessed-wrong")
    check("a real run id with the wrong secret is refused",
          wrong_secret.status_code == 403, f"HTTP {wrong_secret.status_code}")
    check("and says so specifically", "task credential" in wrong_secret.text,
          wrong_secret.text[:200])

    heading("U54: a second run against a second version is independently scoped")

    own_b = credential_request(principal, version_b["id"], run_b, secret_b)
    check("run B's own target version is allowed",
          own_b.status_code == 200, f"HTTP {own_b.status_code}: {own_b.text[:200]}")

    swapped = credential_request(principal, version_a["id"], run_b, secret_b)
    check("run B asking for run A's version is refused",
          swapped.status_code == 403, f"HTTP {swapped.status_code}")

    return summary("U54")


if __name__ == "__main__":
    sys.exit(main())
