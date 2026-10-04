"""U49: an externally-developed agent can register a version with no
reliance on this repository's own agent code, and its declared location is
recorded as given, not silently defaulted to a local path.

Runs on the host, because it drives `scripts/admin/register-agent-version.py`, which sits
beside the Compose file rather than inside the API image, the same reason
`v29_reclamation.py` and `v47_cleanup_dataset.py` run on the host.

    .venv\\Scripts\\python.exe verify\\v49_external_agent_version.py
"""

from __future__ import annotations

import os
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


def register(*args: str, as_person: str | None = None) -> subprocess.CompletedProcess:
    """Run the script as a signed-in person. The platform registers a version as whoever is signed in and refuses a name that is not
    theirs, so the script needs that person's session. It goes in the environment, not on the command line, where it would show in a process list.
    With no person given the script gets none, which is how a check proves it refuses to run without one."""
    env = {**os.environ}
    env.pop("MUNITAS_SESSION_TOKEN", None)
    if as_person:
        env["MUNITAS_SESSION_TOKEN"] = bearer_for(as_person)["Authorization"].removeprefix("Bearer ")
    # An empty stdin, so nothing the script might ask waits for a person.
    return subprocess.run(
        [str(PYTHON), str(SCRIPT), *args],
        capture_output=True, text=True, cwd=str(ROOT), timeout=300, env=env, stdin=subprocess.DEVNULL,
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
        "--tool", "search_docs", as_person="canary-engineer",
    )
    check("registering with no --model-id fails, rather than defaulting to one",
          missing_model.returncode != 0 and "--model-id" in missing_model.stderr,
          f"exit {missing_model.returncode} {missing_model.stderr[-160:]}")

    missing_tool = register(
        "--agent-id", agent_id, "--registered-by", "canary-engineer",
        "--model-id", "gpt-4.1", as_person="canary-engineer",
    )
    check("registering with no --tool fails, rather than defaulting to an empty scope",
          missing_tool.returncode != 0 and "--tool" in missing_tool.stderr,
          f"exit {missing_tool.returncode} {missing_tool.stderr[-160:]}")

    heading("U49: an external location round-trips exactly as declared")

    run = register(
        "--agent-id", agent_id, "--registered-by", "canary-engineer",
        "--model-id", "gpt-4.1", "--tool", "search_docs", "--tool", "summarise",
        "--source-path", fake_repo_url, as_person="canary-engineer",
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
    # Signed in as that person, so the refusal is the platform's own and not just a missing session.
    cross_tenant = register(
        "--agent-id", agent_id, "--registered-by", "eng-devi",
        "--model-id", "gpt-4.1", "--tool", "search_docs",
        "--source-path", fake_repo_url, as_person="eng-devi",
    )
    # A 404, not a 403: somebody from another organisation is told there is no such agent, so they cannot learn that one exists.
    check("a --registered-by from a different tenant is refused by the platform, as if the agent did not exist",
          cross_tenant.returncode == 1 and "HTTP 404" in cross_tenant.stdout and "no such agent" in cross_tenant.stdout,
          cross_tenant.stdout + cross_tenant.stderr)
    nobody = register("--agent-id", agent_id, "--registered-by", "canary-engineer", "--model-id", "gpt-4.1",
                      "--tool", "search_docs", "--source-path", fake_repo_url)
    check("with no signed-in session the script refuses to run at all",
          nobody.returncode == 2 and "session token is needed" in nobody.stdout, f"exit {nobody.returncode} {nobody.stdout[:120]}")

    heading("U49: the script is genuinely self-contained")

    source_text = SCRIPT.read_text(encoding="utf-8")
    imports_agent = "from agent" in source_text or "import agent" in source_text
    check("scripts/admin/register-agent-version.py has no import from this repo's agent package",
          not imports_agent,
          "an import from agent/ was found" if imports_agent else "none found")

    return summary("U49")


if __name__ == "__main__":
    sys.exit(main())
