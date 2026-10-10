r"""U183: the large tier stays apart from the ordinary suite, and says what it will do before it does anything.

Runs on the host and needs no database: it reads the repository.

    .venv\Scripts\python.exe verify\v173_large_tier_guard.py

A check that takes hours, or makes gigabytes, must never run because somebody ran "everything". Checked one item at a time:

  * every large check is named in verify/run_large.py, has a script under verify/large, says it is the large tier, and describes what it does and needs;
  * none of them is listed in run_all.py (the ordinary suite) or affects.py (the quick loop's choice of checks);
  * no check the ordinary suite runs asks the generator for more than 100,000 rows, either in a call or in a setting named for its rows (U110, which kills a
    worker over 1.2 million rows, runs alone on the host and is not part of that suite);
  * the console's large test is a Playwright project that exists only when MUNITAS_LARGE=1, and the ordinary project ignores its folder;
  * run_large.py with no name lists and changes nothing; planning says what it would do and writes nothing; running needs the checks named, and a name
    it does not know is refused.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os  # noqa: E402

# common.py reads these when it is imported; this check opens no connection, so the usual local values are enough.
os.environ.setdefault("MUNITAS_PG_DSN", "postgresql://munitas:munitas@localhost:5432/platform")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS", "http://localhost:4433")
os.environ.setdefault("MUNITAS_S3_ENDPOINT", "http://localhost:8333")

from common import check, heading, summary  # noqa: E402

LIMIT = 100_000
VERIFY = ROOT / "verify"
LARGE = VERIFY / "large"


def large_ids() -> dict[str, str]:
    tree = ast.parse((VERIFY / "run_large.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "CHECKS" for t in node.targets):
            return ast.literal_eval(node.value)
    return {}


def big_row_counts(path: Path) -> list[str]:
    """Where an ordinary check names more rows than the limit: a call to make() with a row count, or a setting whose name has ROWS in it."""
    found = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "make":
            rows = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "rows"), None)
            if isinstance(rows, ast.Constant) and isinstance(rows.value, int) and rows.value > LIMIT:
                found.append(f"{path.name}:{node.lineno} asks for {rows.value:,} rows")
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, int) and node.value.value > LIMIT:
            for target in node.targets:
                if isinstance(target, ast.Name) and "ROWS" in target.id.upper():
                    found.append(f"{path.name}:{node.lineno} sets {target.id} to {node.value.value:,}")
    return found


def main() -> int:
    heading("Every large check is named, described and marked as the large tier")
    ids = large_ids()
    check("run_large.py names the large checks, each with a script under verify/large", bool(ids) and all((LARGE / script).exists() for script in ids.values()), str(ids))
    check("the ids all start with L, so they cannot be taken for an ordinary check", all(i.startswith("L") and i[1:].isdigit() for i in ids), ", ".join(ids))
    for check_id, script in ids.items():
        tree = ast.parse((LARGE / script).read_text(encoding="utf-8"))
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        constants = {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
        doc = ast.get_docstring(tree) or ""
        check(f"{check_id}: says it is the large tier, defines what it does and needs, and has a main", "LARGE TIER" in doc and "need" in names and "main" in names
              and {"ID", "TITLE"} <= constants, f"{script}: functions {sorted(names)}")

    heading("None of them is in the ordinary suite or the quick loop")
    def strings(path: Path) -> set[str]:
        return {n.value for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(n, ast.Constant) and isinstance(n.value, str)}

    listed = strings(VERIFY / "run_all.py") | strings(VERIFY / "affects.py")
    for check_id, script in ids.items():
        check(f"{check_id}: its id and script are named in neither run_all.py nor affects.py", check_id not in listed and script not in listed)
    check("run_all.py runs nothing from the large folder", not any("large/" in v or "large\\" in v for v in strings(VERIFY / "run_all.py")))

    heading("No ordinary check asks for more than 100,000 rows")
    problems = []
    scripts = sorted(s for s in strings(VERIFY / "run_all.py") if re.fullmatch(r"v\d+_\w+\.py", s) and (VERIFY / s).exists())
    for script in scripts:
        problems += big_row_counts(VERIFY / script)
    check(f"none of the {len(scripts)} checks the ordinary suite runs names more rows than the generator allows without --large", len(scripts) > 50 and not problems,
          "; ".join(problems[:3]) or f"{len(scripts)} scripts read")

    heading("The console's large test cannot be selected by an ordinary run")
    config = (ROOT / "web" / "playwright.config.ts").read_text(encoding="utf-8")
    check("the Playwright config makes the large project only when MUNITAS_LARGE is 1", 'process.env.MUNITAS_LARGE === "1"' in config and 'name: "large"' in config, "")
    check("the ordinary project ignores the large folder", 'testIgnore: "**/large/**"' in config, "")
    stray = [p.name for p in (ROOT / "web" / "tests").glob("*.spec.ts") if "tests/large" in p.read_text(encoding="utf-8").replace("\\", "/")]
    check("no ordinary test file refers to the large folder", not stray, ", ".join(stray))

    heading("The runner says what it will do first, and needs the checks named")
    folder = Path(tempfile.mkdtemp(prefix="munitas-u183-"))
    try:
        python = sys.executable
        listing = subprocess.run([python, str(VERIFY / "run_large.py")], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=folder, timeout=300)
        check("with no names it lists every check with its disk and time, and exits cleanly", listing.returncode == 0 and all(i in listing.stdout for i in ids)
              and "disk about" in listing.stdout and "Name the checks" in listing.stdout, listing.stdout[-200:] + listing.stderr[-200:])
        planning = subprocess.run([python, str(VERIFY / "run_large.py"), "--plan", next(iter(ids))], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  cwd=folder, timeout=300)
        check("--plan says what the check would do, how much disk and how long, and exits cleanly", planning.returncode == 0 and "disk about" in planning.stdout
              and "minutes" in planning.stdout, planning.stdout[-200:] + planning.stderr[-200:])
        bare = subprocess.run([python, str(VERIFY / "run_large.py"), "--go"], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=folder, timeout=60)
        check("--go with no check named is refused: there is no 'everything'", bare.returncode != 0, bare.stderr[-200:])
        unknown = subprocess.run([python, str(VERIFY / "run_large.py"), "--go", "L99"], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=folder, timeout=60)
        check("a name it does not know is refused in words, and nothing starts", unknown.returncode != 0 and "no such large check" in (unknown.stderr + unknown.stdout), unknown.stderr[-200:])
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return summary("U183")


if __name__ == "__main__":
    sys.exit(main())
