r"""Capture the Health walkthroughs as one story, in the order it happens, on one organisation.

    .venv\Scripts\python.exe scripts\walkthroughs\capture-health-story.py [--from NAME] [--only NAME]

Run it against a Health organisation that was just rebuilt (scripts\seed\reseed-tenant.ps1 -Tenant health -Force), so that nothing from an earlier capture
is in the pictures: the same datasets and the same people appear on every page, and what one part makes is what the next part finds. The parts, in order:

    part0   Part 0, before the programme starts      (web/walkthroughs/before-any-data.spec.ts)
    a1      A table arrives                          (web/walkthroughs/story-a-1.spec.ts)
    a2      find, ask and make a dataset             (scripts/demo/derive-demo.py with web/walkthroughs/story-a-derive.spec.ts between its steps)
    a3      work from a notebook                     (scripts/demo/notebook-demo.py with web/walkthroughs/story-a-notebook.spec.ts while it waits)
    b1      patient data arrives locked              (web/walkthroughs/story-b.spec.ts, parts recordings and table)
    b2      Devi checks what arrived                 (story-b.spec.ts, parts rows and files)
    b3      release bars, names removed, a reviewer decides  (story-b.spec.ts, parts bars, remove-names and after-release: real speech work on the graphics card)
    b4      evidence without names                   (story-b.spec.ts, part evidence)

The derivation is one timeline in two programs: the Python session stops after each of its steps (its --sync option) and this script runs the console
screens that belong at that point, then lets the session go on. Every part fails loudly: a picture is never taken of something that did not happen.
The pages are built afterwards by the docs/tools/build_*_walkthrough.py scripts, which check every note against what is on the screen.

Say how long it takes before running it: part0 under a minute, a1 about 20 seconds, a2 about 2 minutes, a3 about 1 minute, b1 about 40 seconds, b2 about
1 minute, b3 about 3 minutes plus a few seconds for the page after the release (the host worker and its graphics card must be running), b4 under a minute: about 10 minutes in all.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
SHOTS = WEB / "walkthroughs" / "shots"
CONFIG = "--config=walkthroughs/playwright.config.ts"
SESSION_STEPS = 11            # steps the Python session has for Health (see scripts/demo/derive-demo.py)
CONSOLE_AFTER = {1, 2, 6, 7, 10, 11}   # after which session steps a console part runs (web/walkthroughs/story-a-derive.spec.ts)


def say(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def playwright(spec: str, env: dict | None = None) -> None:
    done = subprocess.run(f"npx playwright test {CONFIG} {spec}", cwd=WEB, shell=True, env={**os.environ, **(env or {})})
    if done.returncode != 0:
        raise SystemExit(f"The capture {spec} failed (exit {done.returncode}). Nothing after it was captured.")


def part0() -> None:
    say("part 0: before the programme starts")
    playwright("before-any-data")


def a1() -> None:
    say("a1: a table arrives")
    subprocess.run([str(PY), str(ROOT / "scripts" / "seed" / "make-story-a-files.py")], check=True)
    playwright("story-a-1")


def a2() -> None:
    say("a2: find, ask and make a dataset (the session and the console take turns)")
    folder = Path(tempfile.mkdtemp(prefix="derive-sync-"))
    transcript = SHOTS / "derive-health" / "transcript.json"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    session = subprocess.Popen(
        [str(PY), "-W", "ignore", str(ROOT / "scripts" / "demo" / "derive-demo.py"), "--tenant", "health", "--name", "chronic-heart-patients-over-65",
         "--console-asks", "--sync", str(folder), "--transcript", str(transcript)],
        cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        for n in range(1, SESSION_STEPS + 1):
            ready, go = folder / f"after-{n}.ready", folder / f"after-{n}.go"
            deadline = time.time() + 600
            while not ready.exists():
                if session.poll() is not None and not ready.exists():
                    raise SystemExit(f"The session ended before step {n} was reached (exit {session.returncode}).")
                if time.time() > deadline:
                    raise SystemExit(f"The session did not reach step {n} within 10 minutes.")
                time.sleep(0.5)
            if n in CONSOLE_AFTER:
                say(f"  console screens after session step {n}")
                playwright("story-a-derive", {"DERIVE_PART": str(n), "DERIVE_SYNC": str(folder)})
            go.write_text("go", encoding="utf-8")
        code = session.wait(timeout=300)
        if code != 0:
            raise SystemExit(f"The session ended with exit {code}.")
    finally:
        if session.poll() is None:
            session.kill()
        shutil.rmtree(folder, ignore_errors=True)


def a3() -> None:
    say("a3: work from a notebook (the notebook waits while the console approves it)")
    folder = Path(tempfile.mkdtemp(prefix="notebook-sync-"))
    shots = SHOTS / "notebook-health"
    shots.mkdir(parents=True, exist_ok=True)
    session = subprocess.Popen(
        [str(PY), "-W", "ignore", str(ROOT / "scripts" / "demo" / "notebook-demo.py"), "--sync", str(folder), "--transcript", str(shots / "transcript.json")],
        cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"})

    def wait_for(path: Path, what: str) -> None:
        deadline = time.time() + 300
        while not path.exists():
            if session.poll() is not None and not path.exists():
                raise SystemExit(f"The notebook session ended before {what} (exit {session.returncode}).")
            if time.time() > deadline:
                raise SystemExit(f"The notebook session did not reach {what} within 5 minutes.")
            time.sleep(0.3)

    try:
        wait_for(folder / "signin.json", "asking for a code")
        say("  console: type the code and approve")
        playwright("story-a-notebook", {"NOTEBOOK_PART": "approve", "NOTEBOOK_SYNC": str(folder)})
        for n in range(1, 5):
            wait_for(folder / f"after-{n}.ready", f"the end of step {n}")
            if n == 3:
                say("  console: Your notebooks, and End now")
                playwright("story-a-notebook", {"NOTEBOOK_PART": "end", "NOTEBOOK_SYNC": str(folder)})
            (folder / f"after-{n}.go").write_text("go", encoding="utf-8")
        code = session.wait(timeout=120)
        if code != 0:
            raise SystemExit(f"The notebook session ended with exit {code}.")
    finally:
        if session.poll() is None:
            session.kill()
        shutil.rmtree(folder, ignore_errors=True)


def story_b(*parts: str) -> None:
    for name in parts:
        playwright("story-b", {"STORY_B_PART": name})


def b1() -> None:
    say("b1: patient data arrives locked (recordings, then the table)")
    state = SHOTS / "_story-b-state.json"
    if state.exists():
        state.unlink()
    subprocess.run([str(PY), str(ROOT / "scripts" / "seed" / "make-story-b-files.py")], check=True)
    story_b("recordings", "table")


def b2() -> None:
    say("b2: Devi checks what arrived (five rows, then two recordings)")
    story_b("rows", "files")


def b3() -> None:
    say("b3: Nakamura sets the release bars, names are removed on the graphics card, and Imani decides (about 3 minutes)")
    story_b("bars", "remove-names", "after-release")


def b4() -> None:
    say("b4: the auditor and the data protection officer read the evidence")
    story_b("evidence")


PARTS = {"part0": part0, "a1": a1, "a2": a2, "a3": a3, "b1": b1, "b2": b2, "b3": b3, "b4": b4}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", choices=list(PARTS), help="start at this part, and go on to the end")
    ap.add_argument("--only", choices=list(PARTS), help="run this part alone")
    args = ap.parse_args()
    names = list(PARTS)
    chosen = [args.only] if args.only else names[names.index(args.start):] if args.start else names
    began = time.time()
    for name in chosen:
        PARTS[name]()
    say(f"done in {time.time() - began:.0f} seconds: {', '.join(chosen)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
