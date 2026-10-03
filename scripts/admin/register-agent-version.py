"""Register and seal an agent version from outside this repository.

For a team whose agent code lives in its own repository, not inside
`munitas/`. Unlike `agent/register_version.py`, which defaults `model_id`
and `tool_scope` from this repo's own `agent/model.py` and `agent/tools.py`,
this script has no defaults tied to Munitas's own agent: `--model-id` and
`--tool` are required, because an external team's model and tools are not
this codebase's to guess.

Self-contained on purpose. The path-scoped git hashing below duplicates
`agent/version_hash.py`'s logic rather than importing it, so this one file
can be copied into an external agent's own repository with nothing else
needed from `munitas/`. See `agent/version_hash.py` for why the hash is
scoped to a path rather than to bare `HEAD`: a bare HEAD hash would move on
every unrelated commit anywhere in the external repo, not just commits that
touched the agent's own code.

Usage
-----
    python scripts/admin/register-agent-version.py \\
        --agent-id <uuid> --registered-by <directory-id> \\
        --model-id gpt-4.1 --tool search_docs --tool summarise \\
        --source-path "$(git remote get-url origin)"

    curl -X POST http://localhost:8000/agents/<uuid>/versions \\
        -H "content-type: application/json" \\
        -d '{"code_hash": "'"$(git log -1 --format=git:%H -- .)"'", \\
             "source_path": "https://github.com/example/their-agent", \\
             "model_id": "gpt-4.1", "tool_scope": ["search_docs"], \\
             "registered_by": "some-directory-id"}'

`--hash-root` (default: here) is where the git commit is actually computed
from. `--source-path` is what gets recorded as this version's canonical
location and should be the repository's own remote URL, not a local disk
path: a local path means nothing to anyone who is not standing at this exact
machine. Defaults to `--hash-root` itself when omitted, which keeps the
simple case (register a version for code that lives right here) working
with no flags beyond the required ones.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ports_config import PORTS  # noqa: E402

API = f"http://localhost:{PORTS['munitas_api_http']}"

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache"}


def code_hash(root: Path) -> str:
    """Duplicated from `agent/version_hash.py`, deliberately: see module docstring."""
    root = root.resolve()

    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", "."],
            cwd=root, capture_output=True, text=True, timeout=5,
        )
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--registered-by", required=True)
    parser.add_argument("--hash-root", default=".",
                        help="local checkout to compute the hash from (default: here)")
    parser.add_argument("--source-path",
                        help="this repository's own remote URL, ideally, not a local path. "
                             "Defaults to --hash-root, which is only meaningful on this machine")
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--tool", action="append", dest="tools", required=True,
                        help="repeatable; every tool this version may call")
    parser.add_argument("--image-digest", default="native:host-venv")
    parser.add_argument("--api", default=API)
    parser.add_argument("--session-token", default=os.environ.get("MUNITAS_SESSION_TOKEN"),
                        help="the signed-in session token of --registered-by (or set MUNITAS_SESSION_TOKEN). A version is "
                             "registered as the person who is signed in, and the platform refuses a name that is not theirs")
    args = parser.parse_args()
    if not args.session_token:
        print("a session token is needed: sign in as --registered-by and pass --session-token, or set MUNITAS_SESSION_TOKEN")
        return 2

    body = {
        "code_hash": code_hash(Path(args.hash_root)),
        "source_path": args.source_path or str(Path(args.hash_root).resolve()),
        "image_digest": args.image_digest,
        "model_id": args.model_id,
        "tool_scope": sorted(args.tools),
        "registered_by": args.registered_by,
    }

    response = httpx.post(f"{args.api}/agents/{args.agent_id}/versions",
                          json=body, timeout=20.0, headers={"Authorization": f"Bearer {args.session_token}"})
    if response.status_code != 201:
        print(f"could not register the version: HTTP {response.status_code}: "
              f"{response.text[:300]}")
        return 1

    result = response.json()
    print(f"agent {args.agent_id} version {result['version']}")
    print(f"  code_hash    {body['code_hash']}")
    print(f"  source_path  {body['source_path']}")
    print(f"  model_id     {body['model_id']}")
    print(f"  tool_scope   {', '.join(body['tool_scope'])}")
    print(f"  content_hash {result['content_hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
