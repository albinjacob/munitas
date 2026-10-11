r"""U191: when the policy engine cannot answer, every route that asks it says so in one plain sentence, with a 503, and the sentence gives nothing away.

Run in the API container, which holds the API's code:

    docker compose exec -T munitas-api python /verify/v180_policy_down_is_503.py

Every decision about who may do what is put to the policy engine, and with no answer the platform refuses. What the caller used to see depended on the route: two
routes answered 503 themselves and put the exception's own text in the answer (the engine's address and the connection error), and every other route fell through to
the catch-all, "the platform ran into a problem it did not expect", a 500 that reads as a bug. This calls three routes of different kinds with the engine's address
made unreachable, and then with the real engine, and checks the answer each time:

  * GET /policy/roles, which asks the engine directly and needs no sign-in;
  * GET /screens, which asks it for the pages the signed-in person may open;
  * GET /services, which asks it first, through the page catalogue, before it reads anything.

It runs the API's own application in this process, without its start-up, so nothing is written and no database is needed. The sign-in is replaced by a person with
the one role the routes need to see.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "/verify")
sys.path.insert(0, "/app")

from common import check, heading, summary  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, config  # noqa: E402
from app.main import app  # noqa: E402

PERSON = {"id": "canary-researcher", "tenant_id": "canary", "roles": ["notebook_explore"], "label": "Canary researcher"}
SENTENCE = "The platform's policy engine did not answer, so this was refused. Try again in a moment."
DEAD = "http://127.0.0.1:9"


def reasons(response) -> list[str]:
    try:
        return response.json().get("detail", {}).get("reasons", [])
    except ValueError:
        return []


def main() -> int:
    app.dependency_overrides[auth.current_session] = lambda: PERSON
    client = TestClient(app, raise_server_exceptions=False)
    real = config.OPA_URL
    try:
        heading("U191: the engine cannot be reached")
        config.OPA_URL = DEAD
        for path in ("/policy/roles", "/screens", "/services"):
            r = client.get(path)
            check(f"GET {path} answers 503", r.status_code == 503, f"HTTP {r.status_code}: {r.text[:120]}")
            check(f"GET {path} says why in one plain sentence", reasons(r) == [SENTENCE], str(reasons(r)))
            leaked = [w for w in ("127.0.0.1", "Errno", "unreachable", "httpx", "Traceback") if w.lower() in r.text.lower()]
            check(f"GET {path} gives away neither the address nor the error", not leaked, str(leaked))

        heading("The engine answers again")
        config.OPA_URL = real
        r = client.get("/policy/roles")
        check("GET /policy/roles answers 200 again", r.status_code == 200, f"HTTP {r.status_code}")
        check("and it carries the role table", "role_words" in (r.json() if r.status_code == 200 else {}))
        r = client.get("/screens")
        check("GET /screens answers 200 again", r.status_code == 200, f"HTTP {r.status_code}")
        r = client.get("/services")
        check("GET /services is refused for this person by the policy, not by a failure", r.status_code == 403, f"HTTP {r.status_code}")
    finally:
        config.OPA_URL = real
        app.dependency_overrides.pop(auth.current_session, None)
    return summary("U191")


if __name__ == "__main__":
    sys.exit(main())
