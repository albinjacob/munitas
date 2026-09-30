"""The single Python entry point for config.json's ports.

Every script that needs a host-published port's *default* value imports
`PORTS` from here instead of reading config.json directly or hardcoding a
number, so there is one parsing path. This never replaces an env-var
override -- every call site still reads e.g. `os.environ.get("MUNITAS_API",
...)` first, exactly as before; only the literal fallback changes, from a
number nobody could find to one line in config.json that is.

Deliberately the one loose .py file at repo root rather than tucked inside
a package: it is cross-cutting infrastructure every top-level package needs
(worker/, verify/, agent/, scripts/, infra/kratos/), the same category as
config.json and docker-compose.yml sitting beside it here, not application
code that belongs to any one of them. See ARCHITECTURE.md section 8's
repository map for the same note, and scripts/render_ports_env.py for how
this bridges into the things that cannot read JSON themselves at all
(docker-compose.yml, infra/kratos/kratos.yml).

    from ports_config import PORTS
    API = os.environ.get("MUNITAS_API", f"http://localhost:{PORTS['munitas_api_http']}")
"""
from __future__ import annotations

import json
from pathlib import Path

# No .resolve() here on purpose: this module gets imported transitively by
# worker/dag_workflow.py, a Temporal workflow definition, whose sandbox
# intercepts every import in its dependency chain and blocks
# Path.resolve() as a non-deterministic filesystem call. __file__ is
# already an absolute path for a normally-imported module (guaranteed by
# importlib since Python 3.4), so .parent alone is both sufficient and
# sandbox-safe.
_CONFIG_PATH = Path(__file__).parent / "config.json"
PORTS: dict[str, int] = {
    k: v for k, v in json.loads(_CONFIG_PATH.read_text(encoding="utf-8")).items()
    if not k.startswith("_")
}
