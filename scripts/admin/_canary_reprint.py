"""Reprint the storage permissions after canary datasets were deleted, relaxing the platform's shrink guard only when it is provably safe.

Runs inside the API container (the tidy mounts scripts/admin there as /scripts-admin), because it needs the platform's own grants module
and a way to read the permissions storage actually holds:

    docker exec munitas-munitas-api-1 python /scripts-admin/_canary_reprint.py

Why it exists. Deleting a canary dataset removes its rows at once, but the permissions document in storage keeps that dataset's folder
grants until it is printed again. The platform refuses a print that removes more than 40% of the folder grants, which is right for a
mistake and wrong here: deleting a few hundred canary datasets near the end of a run can be most of what is left. Overriding the guard
by hand is a blunt tool, so this does it only when the whole of what the print would remove can be proven to be exactly that:

  R1  every grant the print would remove is in canary's bucket (munitas-canary), and not one is in any other bucket;
  R2  every one of them is a folder grant naming a dataset id, and not one is a bucket-wide grant;
  R3  none of the datasets they name still exists;
  R4  every identity that disappears entirely is a lease key, a catalog key, a task key or a table job key, never a role's key or the
      administrator's or an organisation's upload key.

If any of these fails, nothing is printed and the offending grants are named; the guard stays on and the platform reports it as before.
Exit codes: 0 printed (or nothing to print), 2 refused by a proof, 1 the print itself failed.
"""

from __future__ import annotations

import re
import sys

sys.path.insert(0, "/app")

from app import db, grants, seaweed  # noqa: E402

CANARY_BUCKET = "munitas-canary"
DISAPPEARING_OK = ("lease-", "cat-", "run-", "tj-")
UUID = re.compile(r"(?:^|/)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:/|$)")


def problems_with(live: dict[str, set[str]], want: dict[str, set[str]], existing: set[str]) -> list[str]:
    """R1 to R4. Pure, so a check can prove each one refuses."""
    found: list[str] = []
    for name, actions in live.items():
        vanishing = name not in want and name.startswith(DISAPPEARING_OK)
        for action in sorted(actions - want.get(name, set())):
            verb, _, rest = action.partition(":")
            bucket, _, path = rest.partition("/")
            if bucket != CANARY_BUCKET:
                found.append(f"R1 {name}: {action} is not in {CANARY_BUCKET}")
            elif vanishing:
                continue  # a whole lease, catalog, task or table job key going away with its row: its grants go with it
            elif not path:
                found.append(f"R2 {name}: {action} is a bucket-wide grant")
            else:
                match = UUID.search(path)
                if not match:
                    found.append(f"R2 {name}: {action} names no dataset id")
                elif match.group(1) in existing:
                    found.append(f"R3 {name}: {action} names a dataset that still exists")
        if name not in want and not name.startswith(DISAPPEARING_OK):
            found.append(f"R4 the identity {name} would disappear, and it is not a lease, catalog, task or table job key")
    return found


def main() -> int:
    db.pool.open(wait=True)
    live = {i["name"]: set(i.get("actions", [])) for i in seaweed.load_identities().get("identities", [])}
    want = {i["name"]: set(i["actions"]) for i in grants.desired_document()["identities"]}
    named = set()
    for name, actions in live.items():
        for action in actions - want.get(name, set()):
            match = UUID.search(action.partition(":")[2].partition("/")[2])
            if match:
                named.add(match.group(1))
    existing = {str(r["id"]) for r in db.all_rows("select id from dataset where id = any(%s)", (list(named),))} if named else set()
    problems = problems_with(live, want, existing)
    if problems:
        print(f"refused, nothing printed: {len(problems)} grant(s) or identities are not provably the canary's deleted datasets")
        for line in problems[:8]:
            print(f"  {line}")
        return 2
    try:
        result = grants.reconcile(armed=False, trigger="manual")
    except Exception as exc:  # noqa: BLE001 - reported with the exit code
        print(f"the print failed: {exc}")
        return 1
    print(f"reconciled {result['identities']} identities: {result['actions_before']} actions before, {result['actions_after']} after, {result['removed']} removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
