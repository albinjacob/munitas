"""U48: agents are a first-class, versioned resource, registered and sealed
the same way a dataset is, content-addressed rather than trusted on
description alone.

    docker compose exec -T munitas-api python /verify/v48_agent_registry.py
"""

from __future__ import annotations

import sys
import uuid

from common import (AGENT, CANARY, api, bearer_for, check, db, heading,
                    require_api, summary)

TENANT = CANARY
DEPARTMENT = "Verification"


def department(name: str = DEPARTMENT) -> str:
    with db() as conn:
        row = conn.execute(
            "select id from department where tenant_id = %s and name = %s",
            (TENANT, name),
        ).fetchone()
    if not row:
        raise RuntimeError(
            f"department {name!r} is missing from tenant {TENANT!r}. Apply "
            "infra/postgres/seed-canary.sql."
        )
    return str(row["id"])


def register_agent(owning: str, name: str) -> str:
    r = api("POST", "/agents/register", json={
        "tenant_id": TENANT, "name": name, "department_id": owning,
        "registered_by": "canary-engineer", "purpose": "verification fixture",
    })
    r.raise_for_status()
    return r.json()["id"]


def register_version(agent_id: str, **over) -> dict:
    body = {
        "code_hash": f"git:{uuid.uuid4().hex}",
        "source_path": "/checkout/agent",
        "model_id": "qwen2.5:7b",
        "tool_scope": ["read_dataset_version", "summarise_counts"],
        "registered_by": "canary-engineer",
    }
    body.update(over)
    return api("POST", f"/agents/{agent_id}/versions", json=body)


def main() -> int:
    require_api()
    owning = department()

    heading("U48: registering an agent creates its own runtime identity")

    name = f"agent-{uuid.uuid4().hex[:8]}"
    agent_id = register_agent(owning, name)
    check("registering an agent succeeds", bool(agent_id))

    fresh = api("GET", f"/agents/{agent_id}", headers=bearer_for("canary-engineer"))
    check("a freshly registered agent has no versions",
          fresh.status_code == 200 and fresh.json()["versions"] == [],
          str(fresh.json().get("versions")))

    principal_id = fresh.json().get("principal_id", "")
    check("its runtime identity is tenant-prefixed and self-named, not shared",
          principal_id.startswith(f"{TENANT}-") and principal_id.endswith("-runtime")
          and principal_id != AGENT,
          principal_id)

    with db() as conn:
        row = conn.execute(
            "select tenant_id, kind, roles from directory where id = %s",
            (principal_id,),
        ).fetchone()
    check("the auto-created identity is registered as an agent_runtime workload",
          bool(row) and row["tenant_id"] == TENANT and row["kind"] == "workload"
          and "agent_runtime" in row["roles"],
          str(row))

    second_id = register_agent(owning, f"agent-{uuid.uuid4().hex[:8]}")
    second_agent = api("GET", f"/agents/{second_id}", headers=bearer_for("canary-engineer")).json()
    check("a second agent gets its own identity, not the first one's",
          second_agent.get("principal_id") not in (None, principal_id),
          f"{second_agent.get('principal_id')} vs {principal_id}")

    heading("U48: registering an agent is scoped to its own tenant")

    # eng-devi is registered in `health`, not `canary`. A caller naming the
    # canary tenant while pointing at a directory row that actually belongs
    # to a different tenant must be refused, the same as deploy/start_run/
    # approve_run already refuse a cross-tenant directory lookup.
    cross_tenant = api("POST", "/agents/register", json={
        "tenant_id": TENANT, "name": f"agent-{uuid.uuid4().hex[:8]}",
        "department_id": owning, "registered_by": "eng-devi",
        "purpose": "verification fixture",
    })
    check("a registered_by from a different tenant is refused",
          cross_tenant.status_code == 403, f"HTTP {cross_tenant.status_code}")

    heading("U48: a version round-trips its declared content")

    real_hash = "git:0000000000000000000000000000000000000abc"
    first = register_version(agent_id, code_hash=real_hash, source_path="/checkout/agent")
    check("registering a version succeeds", first.status_code == 201,
          f"HTTP {first.status_code}: {first.text[:200]}")
    v1 = first.json()
    check("it is version 1", v1.get("version") == 1, str(v1.get("version")))

    fetched = api("GET", f"/agents/{agent_id}", headers=bearer_for("canary-engineer"))
    row = next((v for v in fetched.json()["versions"] if v["version"] == 1), None)
    check("the code hash round-trips exactly", bool(row) and row["code_hash"] == real_hash,
          str(row.get("code_hash") if row else None))
    check("the declared tool scope round-trips",
          bool(row) and sorted(row["tool_scope"]) == ["read_dataset_version", "summarise_counts"],
          str(row.get("tool_scope") if row else None))

    heading("U48: versions increment and do not collide")

    second = register_version(agent_id, source_path="/checkout/agent-worktree-2")
    check("a second version succeeds", second.status_code == 201, f"HTTP {second.status_code}")
    v2 = second.json()
    check("it is version 2", v2.get("version") == 2, str(v2.get("version")))
    check("its content hash differs from the first version's",
          v2.get("content_hash") != v1.get("content_hash"),
          f"{v1.get('content_hash')} vs {v2.get('content_hash')}")

    heading("U48: a sealed version cannot be changed")

    with db() as conn:
        updated = conn.execute(
            "update agent_version set model_id = 'tampered' where id = %s",
            (v1["id"],),
        ).rowcount
        deleted = conn.execute(
            "delete from agent_version where id = %s", (v1["id"],),
        ).rowcount
        still = conn.execute(
            "select model_id from agent_version where id = %s", (v1["id"],),
        ).fetchone()
    check("an update against a sealed version is a no-op", updated == 0, f"UPDATE {updated}")
    check("a delete against a sealed version is a no-op", deleted == 0, f"DELETE {deleted}")
    check("and its model_id was never changed",
          bool(still) and still["model_id"] != "tampered", str(still))

    return summary("U48")


if __name__ == "__main__":
    sys.exit(main())
