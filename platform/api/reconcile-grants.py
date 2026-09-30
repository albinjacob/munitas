"""Reprint the storage permissions document from the register.

Why this exists
---------------
A lease expiring is not an event. Nothing happens at the moment it lapses, so
something has to come and notice. `POST /credentials` reprints the document for
the identity it just granted for, which covers everything in active use; this
covers the rest, where nobody has asked for a credential lately and the stale
grants would otherwise sit indefinitely.

It is the same function the API calls, not a second implementation. Two
mechanisms doing one job can only be trusted when they are literally the same
code, and this file is thirty lines because that is all it should be.

    docker compose exec -T munitas-api python /app/reconcile-grants.py

What this cannot do
-------------------
It cannot make revocation finer than a role. A grant names a role and a prefix
and never records who asked, so it is kept while any holder of that role is
still entitled to it. Per-consumer identities are what change that, and they
change what is printed rather than how.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "/app")

from app import db, grants  # noqa: E402


def main() -> int:
    db.pool.open()
    try:
        result = grants.reconcile(trigger="manual")
    except grants.UnsafeProjection as exc:
        print(f"refused: {exc}")
        return 1
    print(
        f"reconciled {result['identities']} identities: "
        f"{result['actions_before']} actions before, "
        f"{result['actions_after']} after, {result['removed']} removed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
