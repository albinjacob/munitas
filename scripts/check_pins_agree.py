"""Fail if a package is pinned to different versions in different places.

The worker, the lite worker, the sandbox worker and the API each have their own
requirements file, and .github/workflows/verify.yml repeats some of those pins
for the runner. Each file says its pins match the others, and a comment cannot
enforce that: psycopg sat at 3.2.1 in all of them after the host worker had
moved to 3.3.4, and the file stopped installing on Python 3.13 without anyone
being told. This is the enforcement.

The rule is narrow on purpose. A package pinned with == in two or more of these
sources must carry the same version in every one of them. A package that appears
in only one source, a range (>=), or a different extra ([binary] against
[binary,pool]) is not compared. It does not say the pins are current, only that
they agree.

Standard library only, so it runs before anything else is installed.

    python scripts/check_pins_agree.py            # the repository containing this script
    python scripts/check_pins_agree.py <folder>   # another checkout, used to test the check itself
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIREMENTS = [
    "worker/requirements.txt",
    "worker/requirements-lite.txt",
    "worker/requirements-sandbox.txt",
    "platform/api/requirements.txt",
]
WORKFLOW = ".github/workflows/verify.yml"

# name, optional [extras], ==, version. The version ends at whitespace, a quote,
# a comma or a line-continuation backslash, whichever the source uses.
PIN = re.compile(r"""([A-Za-z0-9][A-Za-z0-9_.\-]*)(?:\[[^\]]*\])?==([0-9][^\s"',\\;]*)""")


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins_in_requirements(path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("#", 1)[0]
        match = PIN.search(code)
        if match:
            found[normalise(match.group(1))] = match.group(2)
    return found


def pins_in_workflow(path: Path) -> dict[str, str]:
    """Pins on the `pip install` command, continuation lines included."""
    lines = path.read_text(encoding="utf-8").splitlines()
    found: dict[str, str] = {}
    i = 0
    while i < len(lines):
        if re.match(r"\s*pip install\b", lines[i]):
            while True:
                for match in PIN.finditer(lines[i].split("#", 1)[0]):
                    found[normalise(match.group(1))] = match.group(2)
                if not lines[i].rstrip().endswith("\\") or i + 1 >= len(lines):
                    break
                i += 1
        i += 1
    return found


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    sources: dict[str, dict[str, str]] = {}
    for rel in REQUIREMENTS:
        sources[rel] = pins_in_requirements(root / rel)
    sources[WORKFLOW] = pins_in_workflow(root / WORKFLOW)

    # A source with no pins at all means the parser missed its format, not that
    # the file is clean. A check that silently compares nothing is worse than none.
    empty = [rel for rel, pins in sources.items() if not pins]
    if empty:
        print("no pins were read from: " + ", ".join(empty))
        print("the file format changed or a path moved, so this check proves nothing")
        return 2

    by_package: dict[str, dict[str, str]] = {}
    for rel, pins in sources.items():
        for name, version in pins.items():
            by_package.setdefault(name, {})[rel] = version

    compared = {name: where for name, where in by_package.items() if len(where) > 1}
    disagree = {name: where for name, where in compared.items() if len(set(where.values())) > 1}

    for name in sorted(compared):
        where = compared[name]
        versions = sorted(set(where.values()))
        mark = "DISAGREE" if name in disagree else "agree   "
        print(f"  {mark}  {name:<34} {', '.join(versions)}  ({len(where)} files)")
        if name in disagree:
            for rel, version in sorted(where.items()):
                print(f"            {version:<12} {rel}")

    print(f"\n{len(compared)} packages pinned in more than one place, {len(disagree)} disagree")
    return 1 if disagree else 0


if __name__ == "__main__":
    sys.exit(main())
