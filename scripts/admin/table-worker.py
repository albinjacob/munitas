"""Start, stop or look at an organisation's own table worker.

A large table is written by a table worker. By default the shared pool does it for every organisation. An organisation can be
given a worker of its own: a platform administrator sets it (PUT /tenants/<id>/table-worker), the organisation's tables are then
put on a line of work only that worker takes, and this script runs that worker. It is the same image as the shared pool, told
which organisation it serves, and it refuses a job of any other.

    python scripts/admin/table-worker.py start  harbour
    python scripts/admin/table-worker.py status harbour
    python scripts/admin/table-worker.py stop   harbour

Giving the organisation the line is a separate step, and the order matters in one direction only: a job made while the
organisation is on its own line waits for this worker, however long that takes, and never moves to the shared pool. Start the
worker first, then give the organisation the line.

Docker runs inside WSL2 on this machine, so every command goes through `wsl.exe`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

DISTRO = "Ubuntu-20.04"
ROOT = Path(__file__).resolve().parents[2]


def wsl_path(path: Path) -> str:
    drive, rest = path.drive.rstrip(":").lower(), path.as_posix().split(":", 1)[1]
    return f"/mnt/{drive}{rest}"


def name_of(tenant: str) -> str:
    return f"munitas-table-worker-{tenant}"


def run(command: str, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(["wsl.exe", "-d", DISTRO, "--", "bash", "-lc", command], capture_output=True, text=True)
    if check and done.returncode:
        raise SystemExit(f"failed: {command}\n{done.stdout}{done.stderr}")
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("tenant", help="the organisation's id")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.tenant):
        raise SystemExit("an organisation's id is lower case letters, digits and hyphens")
    name = name_of(args.tenant)

    if args.action == "start":
        run(f"docker rm -f {name} >/dev/null 2>&1; true", check=False)
        prefix = f"cd '{wsl_path(ROOT)}' && MUNITAS_DATA='/mnt/o/munitas-data' MUNITAS_HOST_WORK_DIR='' TABLE_WORKER_TENANT='{args.tenant}' docker compose"
        # The shared image is built with the stack. This does not rebuild it, so a worker started later runs the same code as the pool.
        run(f"{prefix} --profile dedicated run -d --no-deps --name {name} table-worker-dedicated")
        run(f"docker update --restart unless-stopped {name}")
        print(f"started {name}, serving only {args.tenant!r} on its own line of work")
        print("now give the organisation the line, if it has not been given it:  PUT /tenants/%s/table-worker  {\"dedicated\": true}" % args.tenant)
    elif args.action == "stop":
        run(f"docker rm -f {name}")
        print(f"stopped {name}. Jobs already on the organisation's line wait for it to be started again.")
    else:
        shown = run(f"docker ps -a --filter name=^{name}$ --format '{{{{.Names}}}}  {{{{.Status}}}}'").stdout.strip()
        print(shown or f"no worker for {args.tenant!r} exists")
        if shown:
            print(run(f"docker logs --tail 3 {name} 2>&1").stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
