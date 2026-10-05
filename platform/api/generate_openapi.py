"""Regenerate docs/public/reference/openapi.json from the FastAPI app's own route definitions.

Static schema generation, not a live call: FastAPI's app.openapi() introspects
the registered routers and Pydantic models without opening a database
connection or talking to any other service, so this runs with nothing else
up. The one thing it does need is MUNITAS_MASTER_KEY, because
EnvelopeCrypto() is instantiated at import time in app/main.py; a throwaway
value is fine here since nothing is actually encrypted.

Run it where the API's own dependencies already are, which is the running
API container, rather than installing a second copy of them beside it. The
schema comes back on stdout for the host to write:

    docker compose exec -T munitas-api python generate_openapi.py --stdout > docs/public/reference/openapi.json

Never `docker compose run munitas-api python generate_openapi.py`. That looks
like a disposable container that prints and exits, but the image's entrypoint
(docker-entrypoint.sh) ignores the command and starts a whole second API
server beside the real one, on the same database, and with whatever master
key the `run` was given. Anything that then reaches "the API" can land on the
second one and write data the real one cannot decrypt. Its container keeps
running until it is removed by hand, and the verification wrapper refuses to
start while it is there.

Nothing is mounted for writing to make that work. The API has no business
writing this repository's documentation, and the read-only `./docs:/docs:ro`
mount it does carry exists for the opposite direction: so
`verify/v67_openapi_current.py` can read the committed file and compare it
against what the service serves.

With platform/api/requirements.txt installed locally it still runs directly,
which is how it was written:

    python generate_openapi.py

Regenerate this whenever a route, request model, or response model changes,
then also run docs/tools/generate_api_reference.py to re-render the HTML page.
verify/v67_openapi_current.py checks that the committed docs/public/reference/
openapi.json still matches what the running API actually serves, the same
drift-check
shape already used elsewhere in this repo (e.g. worker/API pipeline-kind
parity).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("MUNITAS_MASTER_KEY", "openapi-generation-only-not-a-real-key")
os.environ.setdefault("PG_DSN", "postgresql://unused:unused@localhost:5432/unused")

api_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(api_dir))
# `crypto` is a sibling package under platform/, alongside api/ -- matches
# platform/api/Dockerfile's build context of ./platform, one level up.
sys.path.insert(0, str(api_dir.parent))

from app.main import app  # noqa: E402

schema = app.openapi()

rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"

# --stdout is what makes this runnable from inside the API's own image
# without granting that container write access to the source tree. The
# caller redirects it into docs/public/reference/openapi.json, so the file is written by
# whoever owns the repository rather than by a service.
if "--stdout" in sys.argv:
    sys.stdout.write(rendered)
else:
    out_path = Path(__file__).resolve().parent.parent.parent / "docs" / "reference" / "openapi.json"
    out_path.write_text(rendered, encoding="utf-8")
    print(f"wrote {out_path} ({len(schema.get('paths', {}))} paths)")
