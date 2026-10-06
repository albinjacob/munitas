"""Which checks does a change need? The quick loop's answer, with its reasons.

The full suite takes about 13 minutes, which is too long to run after every edit. This picks a smaller set from what changed, and says why each
check is in it, so a reader can disagree. It is a speed-up for the loop and never a substitute for the full suite: CI runs the whole of it, and so
must anybody before pushing.

How a set is chosen, in this order:

  1. A changed check script is run (its own id).
  2. Shared test code (common.py, the fixtures, this runner) changes what every check does, so the answer is the full suite.
  3. A table below names the checks for the files that have an obvious home.
  4. Any other changed file picks the checks whose script mentions the file's module name. A check that imports or names the code it covers
     is the best evidence there is of what it covers.
  5. A source file that none of those places answers for means nobody has said what covers it, so the answer is the full suite, and the file is
     named. Silence is not taken to mean "nothing needs checking".
  6. Always added, because they are the ones that caught the surprises: the cheap checks that a change anywhere can break by rule (the
     retired-organisation guard, the API reference, every route knowing its caller, no tenant left behind, immutability of deletion). About
     fifteen seconds in all.

Not part of any set, deliberately: nothing under docs/, web/ or .github/ needs the back-end checks. The console has its own build and tests.

What "changed" means: since the last full run that passed, not since a commit. At the start of a full run the tree is photographed
(`--snapshot FILE`: a fingerprint of every file git knows or would add); when that run passes, the photograph is kept (`--accept FILE`) as
`verify/history/last-full-pass.json`. A change is any file whose fingerprint differs from it, or that is new, or that is gone. Taken at the
start, so an edit made while the suite was running counts as unchecked. With no photograph yet, it falls back to the difference from `main`.

    python verify/affects.py                       prints one check id per line (or FULL), then a reason per line starting with "#"
    python verify/affects.py --snapshot FILE       photographs the tree into FILE
    python verify/affects.py --accept FILE         keeps FILE as the last full pass
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Cheap, and broken by rule from anywhere: see 6 above.
RIPPLE = ["V10", "U22", "U33", "U67", "U113", "U124"]

# Files that make every check behave differently.
SHARED = ["verify/common.py", "verify/lifecycle_fixture.py", "verify/markers.py", "verify/run_all.py", "verify/report.py"]

# Where a file has an obvious home. The first rule that matches a file decides it; its checks are added to the set.
TABLE: list[tuple[str, list[str], str]] = [
    ("platform/crypto/*", ["U128", "U129"], "the key service and the locking of columns"),
    ("platform/api/app/person_*.py", ["U128", "U129"], "locked columns and their routes"),
    ("platform/api/app/locked_leases.py", ["U128", "U129"], "leases on locked columns"),
    ("platform/api/app/privacy_contract.py", ["U128", "U129"], "the privacy contract"),
    ("platform/api/app/key_store.py", ["U128", "U129"], "where keys are kept"),
    ("platform/policy/*.rego", ["U72", "U78", "U98", "U99", "U126", "U127", "U128", "U129"],
     "a policy change can reach every decision; this is the set that asks people for access or roles. Run the policy tests too"),
    ("platform/schema.sql", ["V1", "V4", "U98", "U99", "U100"],
     "a table or rule in the schema: immutability, classes, closing, holds and the purge, which deletes whatever has a tenant id"),
    ("docs/*", [], "documentation"),
    ("web/*", [], "the console, which has its own build and tests"),
    (".github/*", [], "the CI workflow"),
    ("*.md", [], "documentation"),
]

# A source file under one of these has to be answered for by a rule above or by a check that mentions it.
SOURCE_ROOTS = ("platform/", "worker/", "agent/", "infra/", "scripts/", "docker-compose", "start-dev", "stop-dev", "run-verification", "wsl-docker")


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


LAST_PASS = ROOT / "verify" / "history" / "last-full-pass.json"


def tree() -> dict[str, str]:
    """A fingerprint of every file git knows or would add, so a change to any of them can be told from a file that stayed as it was."""
    files = set(git("ls-files")) | set(git("ls-files", "--others", "--exclude-standard"))
    out = {}
    for f in sorted(files):
        path = ROOT / f
        if path.is_file() and not f.startswith("verify/history/"):
            # Line endings do not count: git rewrites them on checkout, and a file restored to what it was is not a change.
            out[f] = hashlib.sha1(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return out


def changed(since: str) -> tuple[list[str], str]:
    """The files that differ from the last full pass (or from `since`, with none recorded), and which of those it was."""
    if LAST_PASS.exists():
        raw = json.loads(LAST_PASS.read_text(encoding="utf-8"))
        was = {k: v for k, v in raw.items() if not k.startswith("#")}
        now = tree()
        return sorted({f for f in set(was) | set(now) if was.get(f) != now.get(f)}), f"the last full pass, {raw.get('#taken', 'time unknown')}"
    files = set(git("diff", "--name-only", since)) | set(git("ls-files", "--others", "--exclude-standard"))
    return sorted(files), f"{since}, because no full run has passed on this machine yet"


def scripts() -> dict[str, str]:
    """script file -> check id, read from the runner's own list, so there is one list."""
    text = (ROOT / "verify" / "run_all.py").read_text(encoding="utf-8")
    return {m.group(2): m.group(1).split()[0] for m in re.finditer(r'\("([^"]+)",\s*"(v[^"]+\.py)"\)', text)}


def mentions(stem: str, by_script: dict[str, str]) -> list[str]:
    """The checks whose script names this module."""
    found = []
    for script, check_id in by_script.items():
        try:
            if re.search(rf"\b{re.escape(stem)}\b", (ROOT / "verify" / script).read_text(encoding="utf-8")):
                found.append(check_id)
        except OSError:
            pass
    return found


def choose(files: list[str]) -> tuple[list[str] | None, list[str]]:
    """(check ids, or None for the full suite) and the reasons, one per line."""
    by_script, ids, why = scripts(), set(), []
    for f in files:
        if f in SHARED:
            return None, [f"{f} is shared test code that changes what every check does, so the full suite"]
    unanswered = []
    for f in files:
        name = Path(f).name
        if f.startswith("verify/") and name in by_script:
            ids.add(by_script[name])
            why.append(f"{f}: the check itself changed")
            continue
        rule = next(((p, c, r) for p, c, r in TABLE if fnmatch(f, p)), None)
        if rule:
            ids.update(rule[1])
            why.append(f"{f}: {rule[2]}" + (f" ({', '.join(rule[1])})" if rule[1] else ""))
            continue
        if f.endswith(".py") and not f.startswith("verify/"):
            hits = mentions(Path(f).stem, by_script)
            if hits:
                ids.update(hits)
                why.append(f"{f}: named by {', '.join(sorted(set(hits)))}")
                continue
        if f.startswith(SOURCE_ROOTS):
            unanswered.append(f)
        elif not f.startswith("verify/"):
            why.append(f"{f}: nothing to check")
    if unanswered:
        return None, [f"no quick set is known for {', '.join(unanswered)}, so the full suite"] + why
    if ids:
        ids.update(RIPPLE)
        why.append(f"always added: {', '.join(RIPPLE)} (cheap, and broken by rule from anywhere)")
    return sorted(ids, key=lambda i: (i[0], int(re.sub(r"\D", "", i) or 0))), why


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--snapshot" in args:
        target = Path(args[args.index("--snapshot") + 1])
        target.write_text(json.dumps({**tree(), "#taken": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")}), encoding="utf-8")
        sys.exit(0)
    if "--accept" in args:
        LAST_PASS.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args[args.index("--accept") + 1], LAST_PASS)
        sys.exit(0)
    since = args[args.index("--since") + 1] if "--since" in args else "main"
    files, base = changed(since)
    ids, reasons = choose(files)
    if ids is None or ids:
        print("FULL" if ids is None else "\n".join(ids))
    print(f"# changed since {base}: {len(files)} file(s)")
    for r in reasons:
        print("# " + r)
