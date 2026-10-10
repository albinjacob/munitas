"""U170: the directory of people is one organisation's, except for the platform administrator, who runs the platform across organisations.

The directory used to hand every signed-in person the people of every live organisation, so a custodian of one organisation could read the names, roles and
departments of another organisation's staff. Now whoever asks gets their own organisation and no other, whatever they pass for `purpose`. The platform
administrator keeps the wider view, because legal holds, closing an organisation and the services page act on other organisations.

Checked here one item at a time, against the live platform, with the two test organisations (each has its own people):

  * a custodian, an engineer and a researcher each see their own organisation, themselves included, and nobody of the other test organisation;
  * the other test organisation's custodian sees theirs and none of this one's;
  * asking for every organisation changes nothing for somebody who is not the platform administrator;
  * the platform administrator sees both test organisations when asking for every organisation, and the live view leaves the test organisations out;
  * a custodian's row still says which department they answer for;
  * nobody who is not signed in is given the list.

    docker compose exec -T munitas-api python /verify/v163_directory_is_one_organisation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    CANARY, CANARY_ADMIN, CUSTODIAN, ENGINEER, NEIGHBOUR, NEIGHBOUR_CUSTODIAN, api, bearer_for, check, heading, require_api, summary,
)

RESEARCHER = "canary-researcher"


def people_of(asker: str, **params) -> list[dict]:
    r = api("GET", "/directory", headers=bearer_for(asker), params={"kind": "human", **params})
    assert r.status_code == 200, f"{asker}: HTTP {r.status_code} {r.text[:160]}"
    return r.json()


def main() -> int:
    require_api()

    heading("U170: somebody who holds an ordinary role is given their own organisation and no other")
    for who, word in ((CUSTODIAN, "custodian"), (ENGINEER, "engineer"), (RESEARCHER, "researcher")):
        rows = people_of(who)
        organisations = {p["tenant_id"] for p in rows}
        check(f"a {word} sees only their own organisation", organisations == {CANARY}, ", ".join(sorted(organisations)) or "nobody")
        check(f"and finds themselves in it, which is how the console knows who is signed in", any(p["id"] == who for p in rows), f"{len(rows)} people")

    neighbour = people_of(NEIGHBOUR_CUSTODIAN)
    check("the other test organisation's custodian sees theirs and nothing of this one",
          {p["tenant_id"] for p in neighbour} == {NEIGHBOUR} and any(p["id"] == NEIGHBOUR_CUSTODIAN for p in neighbour),
          f"{len(neighbour)} people")

    heading("U170: asking for every organisation does not widen it")
    for who in (CUSTODIAN, ENGINEER, RESEARCHER, NEIGHBOUR_CUSTODIAN):
        narrow, wide = people_of(who), people_of(who, purpose="all")
        check(f"{who} gets the same people with purpose=all", {p["id"] for p in narrow} == {p["id"] for p in wide}, f"{len(narrow)} and {len(wide)}")

    heading("U170: the platform administrator, who runs the platform, is offered more")
    everyone = people_of(CANARY_ADMIN, purpose="all")
    organisations = {p["tenant_id"] for p in everyone}
    check("asking for every organisation, the platform administrator sees both test organisations", {CANARY, NEIGHBOUR} <= organisations,
          ", ".join(sorted(organisations)))
    live = {p["tenant_id"] for p in people_of(CANARY_ADMIN)}
    check("and the live view leaves the test organisations out, as it always did", CANARY not in live and NEIGHBOUR not in live,
          ", ".join(sorted(live)) or "nobody")

    heading("U170: what a row says is unchanged")
    custodian = next(p for p in people_of(CUSTODIAN) if p["id"] == CUSTODIAN)
    check("a custodian's row still says which department they answer for", bool(custodian["department_name"]) and custodian["approver_of"],
          f"{custodian['department_name']!r}, {custodian['approver_of']}")

    heading("U170: nobody who is not signed in is given the list")
    anonymous = api("GET", "/directory", params={"kind": "human"})
    check("a request with no session is refused", anonymous.status_code == 401, f"HTTP {anonymous.status_code}")

    return summary("U170")


if __name__ == "__main__":
    sys.exit(main())
