"""Remove old fixtures from the canary tenant: their rows and their files. The canary tenant only.

The verification suite writes into the canary tenant and, because a sealed version cannot be deleted through the platform, nothing
else removes what it leaves. The rows pile up (thousands) and show in screens; a custodian's queue that lists the oldest 100 arrivals
stops showing new ones. This is test-harness housekeeping, kept out of the platform, and it takes no tenant argument on purpose:
the canary tenant is a constant in `_canary_purge.py`, and the guards there are checked again at every step (see its docstring).

    .venv\\Scripts\\python.exe scripts\\admin\\tidy-canary.py                        # preview what would go
    .venv\\Scripts\\python.exe scripts\\admin\\tidy-canary.py --apply                # delete what is older than 2 hours
    .venv\\Scripts\\python.exe scripts\\admin\\tidy-canary.py --apply --older-than-hours 24 --limit 100

Only datasets older than --older-than-hours are touched, so the last run's data is still there to look at when it fails, the same
reason reclaim-storage.py keeps a day. Exit codes: 0 done or nothing to do, 1 a batch failed and nothing in it was changed, 2 a
guard refused.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _canary_purge as canary  # noqa: E402

# Where the platform's own reprint runs. The permissions document in storage still holds the grants of the datasets just deleted until
# it is printed again, and a print that removes most of them is refused by the platform's safety guard (which also leaves new keys
# unable to activate, shown as 503s). So the document is reprinted after EVERY batch by _canary_reprint.py, which relaxes that guard
# only when it can prove that everything the print removes is a grant of a canary dataset that no longer exists.
DISTRO = os.environ.get("MUNITAS_WSL_DISTRO", "Ubuntu-20.04")
CONTAINER = os.environ.get("MUNITAS_API_CONTAINER", "munitas-munitas-api-1")


def reprint() -> tuple[bool, str]:
    done = subprocess.run(["wsl.exe", "-d", DISTRO, "--", "docker", "exec", CONTAINER, "python", "/scripts-admin/_canary_reprint.py"],
                          capture_output=True, text=True, timeout=180)
    return done.returncode == 0, (done.stdout + done.stderr).strip().splitlines()[-1] if (done.stdout + done.stderr).strip() else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--older-than-hours", type=float, default=2.0, help="only datasets created longer ago than this (default 2)")
    parser.add_argument("--limit", type=int, default=None, help="at most this many datasets, oldest first")
    parser.add_argument("--max-datasets", type=int, default=3000, help="refuse outright if more than this would be selected (default 3000)")
    parser.add_argument("--batch", type=int, default=50, help="datasets per transaction (default 50)")
    parser.add_argument("--apply", action="store_true", help="delete; without this nothing is changed")
    parser.add_argument("--no-reprint", action="store_true", help="do not reprint storage permissions after each batch (leaves them out of step)")
    args = parser.parse_args()

    with canary.connect() as conn:
        try:
            for line in canary.verify_canary(conn):
                print(f"  [guard ok] {line}")
            selected = canary.select_old(conn, args.older_than_hours, args.limit)
        except canary.NotCanary as exc:
            print(f"REFUSED: {exc}")
            return 2
        conn.rollback()

        count = sum(len(g) for g in selected)
        print(f"{count} canary dataset(s), in {len(selected)} group(s) that go together, are all older than {args.older_than_hours:g} hour(s).")
        if count > args.max_datasets:
            print(f"REFUSED: that is more than --max-datasets ({args.max_datasets}). Pass a larger --max-datasets or a smaller --limit if it is right.")
            return 2
        if not selected:
            return 0
        flat = [d for g in selected for d in g]

        client = canary.s3() if args.apply else None
        total: dict[str, int] = {}
        objects = 0
        batches, current = [], []
        for group in selected:  # a group that goes together is never split across batches
            if current and len(current) + len(group) > args.batch:
                batches.append(current)
                current = []
            current = current + group
        if current:
            batches.append(current)
        for batch in batches:
            try:
                plan = canary.make_plan(conn, batch)
                if not args.apply:
                    conn.rollback()
                    print(f"  batch of {len(plan.datasets)}: {len(plan.versions)} version(s); would delete")
                    continue
                deleted, removed = canary.purge(conn, plan, client)
            except canary.NotCanary as exc:
                conn.rollback()
                print(f"REFUSED: {exc}")
                return 2
            except Exception as exc:  # noqa: BLE001 - reported with the exit code; the batch was rolled back
                conn.rollback()
                print(f"FAILED, nothing in this batch was changed: {exc}")
                return 1
            objects += len(removed)
            for table, n in deleted.items():
                total[table] = total.get(table, 0) + n
            print(f"  batch of {len(plan.datasets)} deleted; guards: " + "; ".join(g.split(' ')[0] for g in plan.guards))
            if not args.no_reprint:
                ok, message = reprint()
                if not ok:
                    print(f"STOPPED: storage permissions could not be reprinted after this batch ({message}). "
                          "The batch is deleted; nothing more will be, because every further batch would widen the gap.")
                    return 1

    if not args.apply:
        print("\nPreview only. Nothing changed. Pass --apply to delete.")
        return 0
    print(f"\ndeleted {len(flat)} dataset(s), {objects} object(s); rows per table:")
    for table, n in sorted(total.items(), key=lambda kv: -kv[1]):
        print(f"  {table}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
