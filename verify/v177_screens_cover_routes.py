r"""U188: every API route that refuses a role from a console page does it through the page catalogue, and the catalogue names every such route.

Runs on the host and needs no database and no running stack, so it runs anywhere, including CI:

    .venv\Scripts\python.exe verify\v177_screens_cover_routes.py

Who may open which page is written once, in platform/policy/screens.rego. A page it limits has API routes behind it, and those routes must refuse the same
people. Three things can drift, and each is a separate check here:

  * a route the catalogue names in `api` that does not call `screens.require_screen` for that page (so the page says it is limited and the route answers
    anybody), unless the entry says it is `decided_by` a rule of its own;
  * a call to `require_screen` for a page the catalogue does not have, or in a route the page's `api` does not name (so the route is limited and the
    catalogue does not know);
  * a route named in `api` that the API does not serve at all (it was renamed or removed).

The routes served are read from the committed docs/public/reference/openapi.json, which U67 keeps equal to what the running API serves. The routes that call
`require_screen` are read from the code of the API.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

# The shared helpers read these at import; this check uses neither a database nor a service.
os.environ.setdefault("MUNITAS_PG_DSN", "postgresql://munitas:munitas@localhost:5432/platform")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS", "http://localhost:4433")
os.environ.setdefault("MUNITAS_S3_ENDPOINT", "http://localhost:8333")

from common import check, heading, summary  # noqa: E402

REGO = ROOT / "platform" / "policy" / "screens.rego"
OPENAPI = ROOT / "docs" / "public" / "reference" / "openapi.json"
APP = ROOT / "platform" / "api" / "app"


def catalogue() -> dict[str, dict]:
    """Each page's key, its `api` routes and whether it is `decided_by` a rule of its own, read from the policy file."""
    text = REGO.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("catalogue := {")
    body = text[start:text.index("\n}\n", start)]
    pages: dict[str, dict] = {}
    for m in re.finditer(r'^\t"(\w+)": \{\n(.*?)^\t\},', body, re.S | re.M):
        entry = m.group(2)
        api = re.search(r'"api": \[(.*?)\]', entry, re.S)
        pages[m.group(1)] = {
            "api": re.findall(r'"([^"]+)"', api.group(1)) if api else [],
            "decided_by": bool(re.search(r'"decided_by":', entry)),
        }
    return pages


def route_of(text: str) -> tuple[str, str]:
    """'GET /housekeeping/storage?tenant_id={tenant}' as ('get', '/housekeeping/storage')."""
    method, path = text.split(" ", 1)
    return method.lower(), path.split("?", 1)[0]


def served() -> dict[tuple[str, str], str]:
    """(method, path) to operationId, from the committed reference."""
    paths = json.loads(OPENAPI.read_text(encoding="utf-8"))["paths"]
    return {(method, path): op["operationId"] for path, ops in paths.items() for method, op in ops.items() if isinstance(op, dict) and "operationId" in op}


def functions_that_require_a_screen() -> dict[str, set[str]]:
    """Function name to the pages it calls `require_screen` for, from every module of the API."""
    found: dict[str, set[str]] = {}
    for file in sorted(APP.glob("*.py")):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "require_screen"
                        and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
                    found.setdefault(node.name, set()).add(call.args[0].value)
    return found


def keys_for(operation_id: str, method: str, callers: dict[str, set[str]]) -> set[str]:
    """The pages the handler behind an operation calls `require_screen` for. FastAPI builds the id as <function>_<path>_<method>, so the longest name that fits."""
    named = [name for name in callers if operation_id.startswith(name + "_") and operation_id.endswith("_" + method)]
    return callers[max(named, key=len)] if named else set()


def main() -> int:
    pages = catalogue()
    routes = served()
    callers = functions_that_require_a_screen()

    heading("U188: the page catalogue and the API refuse the same people")
    check("the catalogue was read", len(pages) >= 15, f"{len(pages)} pages")

    named: set[tuple[str, str]] = set()
    for key, page in pages.items():
        for text in page["api"]:
            route = route_of(text)
            named.add((route, key))
            if route not in routes:
                check(f"{text} (page {key}) is a route the API serves", False, "the reference has no such route")
                continue
            if page["decided_by"]:
                check(f"{text} (page {key}) asks a rule of its own, as the catalogue says", True)
                continue
            ok = key in keys_for(routes[route], route[0], callers)
            check(f"{text} refuses through the catalogue for page {key}", ok, "" if ok else f"its handler does not call require_screen({key!r})")

    by_route = {route: key for route, key in named}
    for (method, path), operation_id in routes.items():
        keys = keys_for(operation_id, method, callers)
        for key in sorted(keys):
            if key not in pages:
                check(f"{method.upper()} {path} calls require_screen for a page the catalogue has", False, f"no page {key!r}")
            else:
                ok = by_route.get((method, path)) == key
                check(f"{method.upper()} {path} is named in the api list of page {key}", ok, "" if ok else f"page {key!r} does not name this route in its api list")
    return summary("U188")


if __name__ == "__main__":
    sys.exit(main())
