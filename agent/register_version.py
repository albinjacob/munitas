"""Register and seal a version of an agent, from the agent's own checkout.

Run by an agent developer, not by the platform: the producer-submits-what-it-
produced pattern `worker/run_pipeline.py` already uses for dataset versions.
This module reads its own code to compute `code_hash`, its own `model.py` for
`model_id`, and its own `tools.py` for a default `tool_scope`, then posts the
result to `POST /agents/{id}/versions`. The platform never inspects source
itself: everything here is declared by the one process that actually knows
what it shipped, the same trust posture `dataset.provenance` already gets.

Usage
-----
    python -m agent.register_version --agent-id <uuid> --registered-by <directory-id>

    python -m agent.register_version --agent-id <uuid> --registered-by canary-engineer \\
        --source-path . --model-id qwen2.5:7b --tool read_dataset_version --tool fetch_url
"""

from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path

import httpx

from . import model, tools
from .version_hash import code_hash
from ports_config import PORTS

API = f"http://localhost:{PORTS['munitas_api_http']}"

AGENT_ROOT = Path(__file__).resolve().parent


def default_tool_scope() -> list[str]:
    """The tool functions this checkout actually exposes.

    A default only. Declared, not enforced: a developer who removed a tool
    but left its function defined would still see it listed here, which is
    why `--tool` exists to override this rather than trust it blindly.
    """
    names = [
        name for name, fn in inspect.getmembers(tools, inspect.isfunction)
        if fn.__module__ == tools.__name__ and not name.startswith("_")
    ]
    return sorted(names)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--registered-by", required=True)
    parser.add_argument("--source-path", default=str(AGENT_ROOT),
                        help="what code_hash covers (default: this package's directory)")
    parser.add_argument("--model-id", default=model.MODEL)
    parser.add_argument("--tool", action="append", dest="tools",
                        help="repeatable; defaults to every public function in tools.py")
    parser.add_argument("--image-digest", default="native:host-venv")
    parser.add_argument("--api", default=API)
    args = parser.parse_args()

    source_path = Path(args.source_path)
    body = {
        "code_hash": code_hash(source_path),
        "source_path": str(source_path.resolve()),
        "image_digest": args.image_digest,
        "model_id": args.model_id,
        "tool_scope": sorted(args.tools) if args.tools else default_tool_scope(),
        "registered_by": args.registered_by,
    }

    response = httpx.post(f"{args.api}/agents/{args.agent_id}/versions",
                          json=body, timeout=20.0)
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
