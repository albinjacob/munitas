"""Content-addressing an agent's code, without assuming its layout.

The same problem `worker/platform_client.py`'s `code_hash()` already solved
for `action_run`, generalised: a version's identity should never depend on a
specific filename existing, because different agent developers structure
their code differently. A git commit is preferred when the declared source
root sits inside a git repository, since it captures whatever files are
actually there, in whatever layout, without this module needing to know what
they are. Only when there is no repository at all does this fall back to
hashing the files under the root directly, walked recursively rather than
assuming a flat `*.py` list, so a subpackage or a non-Python prompt file is
covered too.

The commit is scoped to `root`, not `HEAD` itself: `git log -1 -- .`, run
with `root` as the working directory, returns the last commit that actually
touched a file under it. Plain `HEAD` was tried first and rejected, because
it names the tip of the whole repository. When an agent's code sits inside a
larger monorepo (as it does here, inside `munitas/`), that means every
unrelated commit anywhere in the repo, a console tweak, a schema change,
would have bumped the agent's `code_hash` despite the agent's own code never
having changed. A hash that moves for reasons unconnected to what it claims
to identify is worse than a coarser one that only moves for the right
reason.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

# Directories that are never part of a developer's actual code, present in
# effectively every checkout regardless of language or layout.
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache"}


def code_hash(root: Path) -> str:
    """The content-addressed identity of everything under `root`.

    Not cached: unlike `worker/platform_client.py`'s single-process
    `code_hash()`, this is called once per version registration, against
    whatever root the caller declares, so there is nothing to reuse across
    calls.
    """
    root = root.resolve()

    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", "."],
            cwd=root, capture_output=True, text=True, timeout=5,
        )
        # Empty output means the path exists in a git repository but no
        # commit has ever touched it, a fresh checkout with only uncommitted
        # files under `root`. That is a real state, not an error, and it
        # falls through to the same directory hash a non-git checkout gets,
        # rather than reporting a commit that says nothing true about this
        # content.
        if out.returncode == 0 and out.stdout.strip():
            return f"git:{out.stdout.strip()}"
    except (OSError, subprocess.SubprocessError):
        pass

    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()
                       and not any(part in _SKIP_DIRS for part in p.parts)):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()[:32]}"
