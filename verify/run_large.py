r"""The large tier: checks that need millions of rows, gigabytes of files, or a killed worker. Run on demand, never as part of the ordinary suite.

    .venv\Scripts\python.exe verify\run_large.py                   lists the checks, with what each does and needs; changes nothing
    .venv\Scripts\python.exe verify\run_large.py --plan L1 L3      says what those would do, how long, and whether the disk is enough; changes nothing
    .venv\Scripts\python.exe verify\run_large.py --go L1 L3        prints the same plan, then runs them one after another and records the run

`--go` needs the checks named. There is no "all": a person chooses what is worth the time and the disk this time. The checks are not listed in run_all.py
or affects.py, and the ordinary suite fails (U183) if one ever is.

The made files go under C:\Work\testdata-large, each check removes what it made, and a check that would not fit on the disk stops before it writes anything.
The run is recorded in verify\history\large-runs.jsonl and shown on verify\history\large.html, beside, and not mixed with, the ordinary history. A run
that cannot be recorded is reported as failed.

The time given for a check is an estimate until it has been run once; the real time of every run is recorded, so the next plan can say it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from markers import COUNTS_MARKER  # noqa: E402

CHECKS = {
    "L1": "l01_text_five_million.py",
    "L2": "l02_binary_five_million.py",
    "L3": "l03_one_gigabyte_resumed.py",
    "L4": "l04_worker_killed.py",
    "L5": "l05_console_one_gigabyte.py",
    "L6": "l06_people_one_million.py",
    "L7": "l07_people_ten_million.py",
}
DEFAULTS = {"MUNITAS_PG_DSN": "postgresql://munitas:munitas@localhost:5432/platform", "MUNITAS_VERIFY_KRATOS": "http://localhost:4433",
            "MUNITAS_S3_ENDPOINT": "http://localhost:8333", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
DATA = Path("C:/Work/testdata-large")
for _key, _value in DEFAULTS.items():          # the checks read these when they are imported, which planning does too
    os.environ.setdefault(_key, _value)


def load(check: str):
    path = HERE / "large" / CHECKS[check]
    spec = importlib.util.spec_from_file_location(f"large_{check}", path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module


def environment() -> dict:
    env = dict(os.environ)
    for key, value in DEFAULTS.items():
        env.setdefault(key, value)
    return env


def describe(ids: list[str]) -> int:
    """Print what these would do and need. Returns the disk, in bytes, the biggest of them needs at one time."""
    free = shutil.disk_usage(DATA.anchor).free
    print(f"Disk: {free / 1e9:.0f} GB free where the made files go ({DATA}).\n")
    biggest = 0
    for check in ids:
        module = load(check)
        need = module.need()
        biggest = max(biggest, need["disk_bytes"])
        print(f"{check}  {module.TITLE}")
        for line in need["does"]:
            print(f"      - {line}")
        print(f"      disk about {need['disk_bytes'] / 1e9:.1f} GB at its largest (removed when it ends), time {need['minutes']} minutes\n")
    print("Each check removes its files before the next starts, so the disk needed is the largest of them, not the total.")
    return biggest


def run(ids: list[str]) -> int:
    biggest = describe(ids)
    free = shutil.disk_usage(DATA.anchor).free
    if biggest * 1.3 > free:
        print(f"\nSTOPPED: the largest needs about {biggest / 1e9:.1f} GB and, with room to spare, {biggest * 1.3 / 1e9:.1f} GB; {free / 1e9:.0f} GB is free. Nothing was written.")
        return 2
    DATA.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    clock = time.monotonic()
    results = []
    for check in ids:
        module = load(check)
        print(f"\n===== {check}  {module.TITLE}\n", flush=True)
        began = time.monotonic()
        process = subprocess.Popen([sys.executable, str(HERE / "large" / CHECKS[check])], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                   errors="replace", env=environment(), cwd=str(ROOT))
        counts, failures = None, []
        for line in process.stdout:
            print(line, end="", flush=True)
            if line.startswith(COUNTS_MARKER):
                counts = json.loads(line[len(COUNTS_MARKER):])
            elif "[FAIL]" in line or "[SKIP]" in line:
                failures.append(line.strip())
        code = process.wait()
        seconds = round(time.monotonic() - began, 2)
        status = "pass" if code == 0 and counts and counts["failed"] == 0 and counts["passed"] > 0 and not counts["skipped"] else ("skip" if counts and counts["skipped"] and not counts["failed"] and code == 0 else "fail")
        results.append({"check": check, "label": module.TITLE, "script": f"large/{CHECKS[check]}", "status": status,
                        "passed": counts["passed"] if counts else None, "failed": counts["failed"] if counts else None, "skipped": counts["skipped"] if counts else None,
                        "seconds": seconds, "detail": " | ".join(failures)[:600]})
        print(f"\n{check}: {status} in {seconds / 60:.1f} minutes", flush=True)

    run_record = {"started_at": started.isoformat(), "seconds": round(time.monotonic() - clock, 2), "host": platform.node(), "partial": True, "tier": "large", "results": results}
    print("\nRecording the run ...")
    recorded = subprocess.run([sys.executable, str(HERE / "report.py"), "--add", "-"], input=json.dumps(run_record), text=True, env={**environment(), "MUNITAS_VERIFY_TIER": "large"})
    if recorded.returncode != 0:
        print("THE RUN COULD NOT BE RECORDED, so it is reported as failed whatever the checks said.")
        return 1
    print("Recorded: verify\\history\\large.html")
    for r in results:
        print(f"  {r['check']}  {r['status']:5s} {r['seconds'] / 60:6.1f} min  {r['label']}")
    return 1 if any(r["status"] != "pass" for r in results) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", nargs="+", metavar="ID", help="say what these would do and need, and stop")
    ap.add_argument("--go", nargs="+", metavar="ID", help="run these, one after another, and record the run")
    args = ap.parse_args()
    chosen = args.plan or args.go
    if not chosen:
        describe(list(CHECKS))
        print("\nName the checks to plan or run, for example:  --plan L1 L3   or   --go L3")
        return 0
    unknown = [c for c in chosen if c not in CHECKS]
    if unknown:
        raise SystemExit(f"no such large check: {', '.join(unknown)}. They are {', '.join(CHECKS)}")
    if args.plan:
        describe(chosen)
        return 0
    return run(chosen)


if __name__ == "__main__":
    sys.exit(main())
