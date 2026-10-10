"""Reprint the storage permissions, as the tidy does after it deletes canary datasets.

Runs inside the API container (the tidy mounts scripts/admin there as /scripts-admin), because it needs the platform's own grants module:

    docker exec munitas-munitas-api-1 python /scripts-admin/_canary_reprint.py

Why it exists. Deleting a canary dataset removes its rows at once, but the permissions document in storage keeps that dataset's folder grants
until it is printed again, and nothing else would print it soon. So the tidy prints it after every batch. An update is always written
(see `grants.py`): what is odd about one is raised as an alert, not as a refusal, so this script no longer has to prove anything first.

Exit codes: 0 printed, 1 the print itself failed.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "/app")

from app import db, grants  # noqa: E402


def main() -> int:
    db.pool.open(wait=True)
    try:
        result = grants.reconcile(trigger="manual")
    except Exception as exc:  # noqa: BLE001 - reported with the exit code
        print(f"the print failed: {exc}")
        return 1
    print(f"reconciled {result['identities']} identities: {result['actions_before']} actions before, {result['actions_after']} after, "
          f"{result['removed']} removed" + (f"; {len(result['alerts'])} alert(s) raised" if result.get("alerts") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
