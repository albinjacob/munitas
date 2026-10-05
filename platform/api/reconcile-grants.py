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
    docker compose exec -T munitas-api python /app/reconcile-grants.py --allow-shrink

The safety guard refuses a print that would remove more than 40% of the prefix grants, which is what a mistake looks like. A deliberate
change can look the same: when a role stops holding standing access (as the pipeline and agent roles did when every task was given a key
of its own), the first print removes a large share at once. `--allow-shrink` is the one way to print it. Read the numbers in the refusal
first; this does not ask again.

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
    allow_shrink = "--allow-shrink" in sys.argv[1:]
    try:
        result = grants.reconcile(armed=not allow_shrink, trigger="manual")
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
