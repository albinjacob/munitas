"""U124: the check that fails a run for leaving test tenants behind flags the right tenants, and only those.

`scripts/admin/check-leftover-tenants.py` is the last step of `run-verification.ps1`. A check that wrongly flagged a customer would fail every run, and
one that missed a leak would hide the very thing it exists to find, so this proves, one item at a time:

  * none of the standing tenants is ever flagged, and neither is an ordinary customer organisation, whether live or retired;
  * a disposable (`scratch`) tenant, a `canary` tenant that is not a standing one, and a tenant named like a test fixture are flagged, including a
    throwaway organisation that is still `production` (what the closing and export checks build);
  * every name prefix the fixtures use for disposable tenants is covered, so a new prefix added there cannot be missed here;
  * run against the real database, a tenant left behind is named and the run exits 1, and once it is removed it is no longer named;
  * when the database cannot be reached it says nothing is known and exits 2, instead of reporting a clean result.

    docker compose exec -T munitas-api python /verify/v124_leftover_tenant_check.py
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
from common import check, db, fixture_tenant, heading, summary  # noqa: E402

HERE = Path(__file__).resolve().parent
# Mounted at /scripts-admin inside the API container, beside the Compose file on the host.
SCRIPT = next(p for p in (Path("/scripts-admin/check-leftover-tenants.py"), HERE.parent / "scripts" / "admin" / "check-leftover-tenants.py") if p.exists())

spec = importlib.util.spec_from_file_location("check_leftover_tenants", SCRIPT)
leak = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = leak
spec.loader.exec_module(leak)


def flagged(tenant_id: str, purpose: str) -> bool:
    return leak.why_left_over({"id": tenant_id, "purpose": purpose}) is not None


def run_script(env_over: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_over or {})}
    return subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60, stdin=subprocess.DEVNULL)


def main() -> int:
    made: list[str] = []
    try:
        heading("What is never flagged")
        for name in sorted(leak.STANDING):
            check(f"the standing tenant {name} is not flagged", not flagged(name, "canary" if name.endswith(("canary", "-a", "-b")) else "production"))
        check("an ordinary customer organisation is not flagged", not flagged("acme-hospital", "production"))
        check("a retired customer organisation is not flagged", not flagged("acme-closed", "retired"))
        check("a name that only starts like a prefix is not flagged (scratchpad-co is not scratch-)", not flagged("scratchpad-co", "production"))

        heading("What is flagged")
        check("a disposable tenant", flagged("anything-at-all", "scratch"))
        check("a canary tenant that is not a standing one", flagged("pipeline-leftover", "canary"))
        check("a throwaway organisation still marked production, named like a fixture", flagged("verify-closing-1a2b3c4d", "production"))
        check("a retired probe tenant", flagged("ingest-probe-retired-1a2b3c4d", "retired"))
        check("the reason is stated, not just a yes", bool(leak.why_left_over({"id": "x", "purpose": "scratch"})))

        heading("Every disposable prefix the fixtures use is covered here")
        missing = [p for p in common._DISPOSABLE_PREFIXES if not p.startswith(leak.TEST_PREFIXES)]
        check("so a prefix added to the fixtures cannot be missed by this check", not missing, f"not covered: {missing}")

        heading("Run against the real database")
        leaked = fixture_tenant(f"ingest-probe-leak-{uuid.uuid4().hex[:8]}")
        made.append(leaked)
        first = run_script()
        check("a tenant left behind is named", leaked in first.stdout, first.stdout[-300:])
        check("and the run exits 1", first.returncode == 1, f"exit {first.returncode}")
        with db() as conn:
            conn.execute("delete from tenant where id = %s", (leaked,))
        made.remove(leaked)
        second = run_script()
        check("once it is removed it is no longer named", leaked not in second.stdout)

        org = f"verify-closing-{uuid.uuid4().hex[:8]}"
        with db() as conn:
            conn.execute("insert into tenant (id, isolation_level, key_ref, purpose) values (%s, 'shared', %s, 'production')", (org, f"key/{org}"))
        made.append(org)
        third = run_script()
        check("a throwaway organisation left as production is named too", org in third.stdout, third.stdout[-300:])

        heading("When it cannot look")
        blind = run_script({"PG_DSN": "postgresql://nobody:nothing@127.0.0.1:1/none"})
        check("it exits 2, not 0", blind.returncode == 2, f"exit {blind.returncode}")
        check("and says nothing is known", "Nothing is known" in blind.stdout, blind.stdout[-200:])
    finally:
        for tenant in made:
            with db() as conn:
                conn.execute("delete from tenant where id = %s", (tenant,))
    return summary("U124")


if __name__ == "__main__":
    sys.exit(main())
