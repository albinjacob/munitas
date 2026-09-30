"""U49: an externally-developed agent can register a version with no
reliance on this repository's own agent code, and its declared location is
recorded as given, not silently defaulted to a local path.

Runs on the host, because it drives `scripts/admin/register-agent-version.py`, which sits
beside the Compose file rather than inside the API image, the same reason
`v29_reclamation.py` and `v47_cleanup_dataset.py` run on the host.

    .venv\\Scripts\\python.exe verify\\v49_external_agent_version.py
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verify"))

from common import (CANARY, api, bearer_for, check, heading, require_api,  # noqa: E402
                    summary)

SCRIPT = ROOT / "scripts" / "admin" / "register-agent-version.py"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
TENANT = CANARY
DEPARTMENT = "Verification"


def register(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PYTHON), str(SCRIPT), *args],
        capture_output=True, text=True, cwd=str(ROOT), timeout=300,
    )


def department() -> str:
    from common import db
    with db() as conn:
        row = conn.execute(
            "select id from department where tenant_id = %s and name = %s",
            (TENANT, DEPARTMENT),
        ).fetchone()
    if not row:
        raise RuntimeError(
            f"department {DEPARTMENT!r} is missing from tenant {TENANT!r}. Apply "
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


def main() -> int:
    require_api()
    owning = department()

    heading("U49: no Munitas defaults are relied on")

    agent_id = register_agent(owning, f"external-{uuid.uuid4().hex[:8]}")
    fake_repo_url = "https://example.com/some-team/their-agent"

    missing_model = register(
        "--agent-id", agent_id, "--registered-by", "canary-engineer",
        "--tool", "search_docs",
    )
    check("registering with no --model-id fails, rather than defaulting to one",
          missing_model.returncode != 0, f"exit {missing_model.returncode}")

    missing_tool = register(
        "--agent-id", agent_id, "--registered-by", "canary-engineer",
        "--model-id", "gpt-4.1",
    )
    check("registering with no --tool fails, rather than defaulting to an empty scope",
          missing_tool.returncode != 0, f"exit {missing_tool.returncode}")

    heading("U49: an external location round-trips exactly as declared")

    run = register(
        "--agent-id", agent_id, "--registered-by", "canary-engineer",
        "--model-id", "gpt-4.1", "--tool", "search_docs", "--tool", "summarise",
        "--source-path", fake_repo_url,
    )
    check("registering with an explicit model and tool scope succeeds",
          run.returncode == 0, run.stdout + run.stderr)

    fetched = api("GET", f"/agents/{agent_id}", headers=bearer_for("canary-engineer")).json()
    row = fetched["versions"][0] if fetched.get("versions") else None
    check("the declared repo URL is recorded, not a local path",
          bool(row) and row["source_path"] == fake_repo_url,
          str(row.get("source_path") if row else None))
    check("the declared model_id is recorded exactly",
          bool(row) and row["model_id"] == "gpt-4.1",
          str(row.get("model_id") if row else None))
    check("the declared tool_scope is recorded exactly, nothing added",
          bool(row) and sorted(row["tool_scope"]) == ["search_docs", "summarise"],
          str(row.get("tool_scope") if row else None))
    check("a real code_hash was computed, not left blank",
          bool(row) and bool(row["code_hash"]), str(row.get("code_hash") if row else None))

    heading("U49: registering a version is scoped to the agent's own tenant")

    # eng-devi is registered in `health`, not `canary`. Naming them as
    # --registered-by against an agent that lives in canary must be refused,
    # the same as register() itself refuses a cross-tenant registered_by.
    cross_tenant = register(
        "--agent-id", agent_id, "--registered-by", "eng-devi",
        "--model-id", "gpt-4.1", "--tool", "search_docs",
        "--source-path", fake_repo_url,
    )
    check("a --registered-by from a different tenant is refused",
          cross_tenant.returncode != 0, cross_tenant.stdout + cross_tenant.stderr)

    heading("U49: the script is genuinely self-contained")

    source_text = SCRIPT.read_text(encoding="utf-8")
    imports_agent = "from agent" in source_text or "import agent" in source_text
    check("scripts/admin/register-agent-version.py has no import from this repo's agent package",
          not imports_agent,
          "an import from agent/ was found" if imports_agent else "none found")

    return summary("U49")


if __name__ == "__main__":
    sys.exit(main())
