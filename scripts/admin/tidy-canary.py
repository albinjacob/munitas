"""Remove old fixtures from the canary tenant: their rows and their files. The canary tenant only.

The verification suite writes into the canary tenant and, because a sealed version cannot be deleted through the platform, nothing
else removes what it leaves. The rows pile up (thousands) and show in screens; a custodian's queue that lists the oldest 100 arrivals
stops showing new ones. This is test-harness housekeeping, kept out of the platform, and it takes no tenant argument on purpose:
the canary tenant is a constant in `_canary_purge.py`, and the guards there are checked again at every step (see its docstring).

Three stages, in this order: old datasets (with everything that hangs off them), then old agents (with their versions, runs and stored
code), then the invented people nothing refers to any more. The people the seed file creates, and anyone with a login, always stay.

    .venv\\Scripts\\python.exe scripts\\admin\\tidy-canary.py                        # preview what would go
    .venv\\Scripts\\python.exe scripts\\admin\\tidy-canary.py --apply                # delete what is older than 2 hours
    .venv\\Scripts\\python.exe scripts\\admin\\tidy-canary.py --apply --older-than-hours 24 --limit 100

Only what is older than --older-than-hours is touched, so the last run's data is still there to look at when it fails, the same
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
    text = (done.stdout + done.stderr).strip()
    return done.returncode == 0, text.splitlines()[-1] if text else ""


def batches_of(groups: list[list[str]], size: int) -> list[list[str]]:
    """Pack groups into batches without ever splitting a group that has to be deleted together."""
    batches, current = [], []
    for group in groups:
        if current and len(current) + len(group) > size:
            batches.append(current)
            current = []
        current = current + group
    if current:
        batches.append(current)
    return batches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--older-than-hours", type=float, default=2.0, help="only what was created longer ago than this (default 2)")
    parser.add_argument("--limit", type=int, default=None, help="at most this many datasets, oldest first")
    parser.add_argument("--max-datasets", type=int, default=3000, help="refuse outright if more datasets than this would be selected (default 3000)")
    parser.add_argument("--max-agents", type=int, default=3000, help="refuse outright if more agents than this would be selected (default 3000)")
    parser.add_argument("--max-people", type=int, default=5000, help="refuse outright if more people than this would be selected (default 5000)")
    parser.add_argument("--batch", type=int, default=50, help="datasets or agents per transaction (default 50)")
    parser.add_argument("--apply", action="store_true", help="delete; without this nothing is changed")
    parser.add_argument("--no-reprint", action="store_true", help="do not reprint storage permissions after each batch (leaves them out of step)")
    args = parser.parse_args()

    with canary.connect() as conn:
        try:
            for line in canary.verify_canary(conn):
                print(f"  [guard ok] {line}")
            groups = canary.select_old(conn, args.older_than_hours, args.limit)
            agents = canary.select_old_agents(conn, args.older_than_hours)
            people_candidates = canary.select_old_people(conn, args.older_than_hours)
        except canary.NotCanary as exc:
            print(f"REFUSED: {exc}")
            return 2
        conn.rollback()

        count = sum(len(g) for g in groups)
        print(f"{count} canary dataset(s), in {len(groups)} group(s) that go together, are all older than {args.older_than_hours:g} hour(s).")
        print(f"{len(agents)} canary agent(s) and {len(people_candidates)} invented identit(ies) with nothing referring to them are older than that.")
        for label, n, cap, flag in (("datasets", count, args.max_datasets, "--max-datasets"), ("agents", len(agents), args.max_agents, "--max-agents"),
                                    ("people", len(people_candidates), args.max_people, "--max-people")):
            if n > cap:
                print(f"REFUSED: {n} {label} is more than {flag} ({cap}). Pass a larger {flag} if it is right.")
                return 2

        client = canary.s3() if args.apply else None
        totals: dict[str, int] = {}
        objects = 0

        def reprint_after(what: str) -> int | None:
            if args.no_reprint:
                return None
            ok, message = reprint()
            if not ok:
                print(f"STOPPED: storage permissions could not be reprinted after this {what} ({message}). "
                      "It is deleted; nothing more will be, because every further batch would widen the gap.")
                return 1
            return None

        # ---- stage 1: datasets
        for batch in batches_of(groups, args.batch):
            try:
                plan = canary.make_plan(conn, batch)
                if not args.apply:
                    conn.rollback()
                    print(f"  datasets, batch of {len(plan.datasets)}: {len(plan.versions)} version(s); would delete")
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
                totals[table] = totals.get(table, 0) + n
            print(f"  datasets, batch of {len(plan.datasets)} deleted; guards: " + "; ".join(g.split(' ')[0] for g in plan.guards))
            if (stopped := reprint_after("batch")) is not None:
                return stopped

        # ---- stage 2: agents (chosen again, because deleting datasets deleted runs and may have freed more agents)
        if args.apply:
            agents = canary.select_old_agents(conn, args.older_than_hours)
            conn.rollback()
        for start in range(0, len(agents), args.batch):
            batch = agents[start:start + args.batch]
            if not args.apply:
                print(f"  agents, batch of {len(batch)}; would delete")
                continue
            try:
                deleted, removed = canary.purge_agents(conn, batch, client)
            except canary.NotCanary as exc:
                conn.rollback()
                print(f"REFUSED: {exc}")
                return 2
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                print(f"FAILED, nothing in this batch was changed: {exc}")
                return 1
            objects += len(removed)
            for table, n in deleted.items():
                totals[table] = totals.get(table, 0) + n
            print(f"  agents, batch of {len(batch)} deleted ({len(removed)} stored code object(s))")
            if (stopped := reprint_after("batch of agents")) is not None:
                return stopped

        # ---- stage 3: invented people nothing refers to any more (chosen again, after the stages above)
        people = people_candidates
        if args.apply:
            people = canary.select_old_people(conn, args.older_than_hours)
            conn.rollback()
        for start in range(0, len(people), 200):
            batch = people[start:start + 200]
            if not args.apply:
                print(f"  people, batch of {len(batch)}; would delete")
                continue
            try:
                n = canary.purge_people(conn, batch)
            except canary.NotCanary as exc:
                conn.rollback()
                print(f"REFUSED: {exc}")
                return 2
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                print(f"FAILED, nothing in this batch was changed: {exc}")
                return 1
            totals["directory"] = totals.get("directory", 0) + n
            print(f"  people, batch of {len(batch)} deleted")

    if not args.apply:
        print("\nPreview only. Nothing changed. Pass --apply to delete.")
        return 0
    print(f"\ndeleted {count} dataset(s), {len(agents)} agent(s), {len(people)} identit(ies) and {objects} object(s); rows per table:")
    for table, n in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {table}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
