"""U120: the custodian's queue of unconfirmed sensitivity claims says how many are waiting, and can be read page by page.

The queue used to return the oldest 100 and nothing else: no total, no way to ask for the rest, so a custodian with 150 waiting could not
tell that 50 were hidden. This checks, one item at a time, on the route the console uses:

  * the answer carries the items, the total waiting, and the limit and offset it was asked with;
  * three new claims raise the total by exactly three;
  * a short page still reports the full total;
  * reading the queue in pages of two returns every waiting dataset once, none twice, oldest first;
  * a limit of 0, a limit above 500 and a negative offset are refused;
  * another organisation's custodian sees none of this organisation's claims.

    docker compose exec -T munitas-api python /verify/v120_custodian_queue_total_and_paging.py
"""

from __future__ import annotations

import sys
import uuid

sys.path.insert(0, "/app")

from common import CANARY, ENGINEER, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402
from lifecycle_fixture import drop_org, make_org  # noqa: E402

CUSTODIAN = "canary-custodian"


def queue(headers: dict, **params):
    return api("GET", "/datasets/awaiting-confirmation", params={"tenant_id": CANARY, "custodian": CUSTODIAN, **params}, headers=headers)


def department_id() -> str:
    with db() as conn:
        row = conn.execute("select id::text as id from department where tenant_id = %s and custodian = %s limit 1", (CANARY, CUSTODIAN)).fetchone()
    if not row:
        raise RuntimeError(f"no department of {CUSTODIAN!r} in {CANARY!r}. Apply infra/postgres/seed-canary.sql")
    return row["id"]


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    engineer = bearer_for(ENGINEER)
    other = make_org()
    try:
        heading("The answer says how many are waiting")
        before = queue(engineer)
        check("the queue answers", before.status_code == 200, f"{before.status_code} {before.text[:120]}")
        body = before.json()
        check("it carries items, total, limit and offset", isinstance(body, dict) and {"items", "total", "limit", "offset"} <= set(body), str(list(body)[:6]))
        check("the limit and offset are the defaults, 100 and 0", (body["limit"], body["offset"]) == (100, 0), f"{body['limit']}, {body['offset']}")
        base = body["total"]

        heading("Three new claims raise the total by three")
        made = []
        for _ in range(3):
            r = api("POST", "/datasets/register", json={
                "tenant_id": CANARY, "name": f"u120-{uuid.uuid4().hex[:8]}", "department_id": department_id(), "registered_by": ENGINEER,
                "provenance": "external_public", "declared_class": "PUBLISHED", "source_kind": "upload"})
            check("a claim is registered", r.status_code == 201 and r.json().get("declaration_basis") == "asserted", f"{r.status_code} {r.text[:120]}")
            made.append(r.json()["id"])
        after = queue(engineer).json()
        check("the total is three higher", after["total"] == base + 3, f"{base} then {after['total']}")

        heading("A short page still reports the full total")
        short = queue(engineer, limit=1).json()
        check("one item comes back", len(short["items"]) == 1, str(len(short["items"])))
        check("the total is still everything waiting", short["total"] == after["total"], f"{short['total']} vs {after['total']}")
        check("the limit asked for is echoed back", short["limit"] == 1, str(short["limit"]))

        heading("Pages of two cover the whole queue once, oldest first")
        seen, offset, guard = [], 0, 0
        while guard < 2000:
            page = queue(engineer, limit=2, offset=offset).json()
            check_page = page["offset"] == offset
            if not check_page:
                break
            seen.extend(page["items"])
            offset += 2
            guard += 1
            if offset >= page["total"]:
                break
        ids = [d["id"] for d in seen]
        check("every waiting dataset is read", len(ids) == after["total"], f"{len(ids)} read, {after['total']} waiting")
        check("none is read twice", len(set(ids)) == len(ids), f"{len(ids) - len(set(ids))} repeated")
        order = [(d["declared_at"], d["id"]) for d in seen]
        check("they are in the order oldest first, the id breaking ties", order == sorted(order), "out of order")
        check("the three new claims are among them", set(made) <= set(ids))

        heading("Values that make no sense are refused")
        check("a limit of 0 is refused", queue(engineer, limit=0).status_code == 422)
        check("a limit above 500 is refused", queue(engineer, limit=501).status_code == 422)
        check("a negative offset is refused", queue(engineer, offset=-1).status_code == 422)
        beyond = queue(engineer, offset=after["total"] + 10).json()
        check("an offset past the end gives an empty page and the same total", beyond["items"] == [] and beyond["total"] == after["total"], str(beyond["total"]))

        heading("Another organisation sees none of it")
        theirs = api("GET", "/datasets/awaiting-confirmation", params={"tenant_id": other.id}, headers=other.bearer("custodian"))
        check("their queue answers", theirs.status_code == 200, f"{theirs.status_code} {theirs.text[:120]}")
        if theirs.status_code == 200:
            check("it holds none of canary's claims", theirs.json()["total"] == 0 and not (set(made) & {d["id"] for d in theirs.json()["items"]}),
                  str(theirs.json()["total"]))
    finally:
        drop_org(other)
    return summary("U120")


if __name__ == "__main__":
    sys.exit(main())
