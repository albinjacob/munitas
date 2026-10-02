"""U98: closing an organisation. Who may start it, who may stop it, and what its people can do while it runs.

The platform closes an organisation in two stages. Retiring (15 days) lets its people read
and cancel. Closing (another 15) lets them do nothing. This builds a customer organisation
with real people, closes it, and watches each stage from the point of view of each person:
the custodian who may cancel, the member who may not, a platform administrator, and the
catalog token a member made earlier. The days are brought forward by changing the two dates
the platform works the phase out from, which is all that a clock moving on would change.

    docker compose exec -T munitas-api python /verify/v98_organisation_closing.py
"""

from __future__ import annotations

import sys

import httpx

from common import api, bearer_for, check, db, heading, require_api, summary
from lifecycle_fixture import ADMIN_A, drop_org, make_org, move_dates


def reasons(r: httpx.Response) -> str:
    try:
        body = r.json()["detail"]
        return " ".join(body["reasons"]) if isinstance(body, dict) else str(body)
    except Exception:
        return r.text[:200]


def phase(org_id: str) -> str:
    with db() as conn:
        return conn.execute("select tenant_phase(%s) as p", (org_id,)).fetchone()["p"]


def main() -> int:
    require_api()
    org = make_org()
    other = make_org()
    try:
        custodian, member = org.bearer("custodian"), org.bearer("member")
        admin = bearer_for(ADMIN_A)
        token = api("POST", "/iceberg/tokens", headers=member,
                    json={"purpose": "reading my own organisation's tables", "hours": 1}).json()["token"]
        catalog = lambda: api("GET", f"/iceberg/v1/{org.id}/namespaces",  # noqa: E731
                              headers={"Authorization": f"Bearer {token}"})

        heading("Before anything: an open organisation")
        check("a new organisation is active", phase(org.id) == "active", phase(org.id))
        listing = api("GET", "/lifecycle/organisations", headers=admin).json()["organisations"]
        by_id = {o["tenant_id"]: o for o in listing}
        check("a platform administrator sees every customer organisation and its phase",
              by_id.get(org.id, {}).get("phase") == "active" and by_id.get("health", {}).get("phase") == "active",
              ", ".join(sorted(by_id)[:6]))
        check("a member does not see the all-organisations view",
              api("GET", "/lifecycle/organisations", headers=member).status_code == 403)
        check("the catalog token its member made answers", catalog().status_code == 200, str(catalog().status_code))

        heading("Who may start closing it")
        body = {"reason": "The contract ends on 31 October"}
        r = api("POST", "/lifecycle/organisation/retire", headers=member, json=body)
        check("an ordinary member may not", r.status_code == 403 and "data custodian" in reasons(r), reasons(r))
        r = api("POST", "/lifecycle/organisation/retire", headers=other.bearer("custodian"),
                json={**body, "tenant_id": org.id})
        check("the custodian of a different organisation may not, and may not even name it",
              r.status_code == 403, f"{r.status_code} {reasons(r)}")
        r = api("POST", "/lifecycle/organisation/retire", headers=custodian, json={"reason": "  "})
        check("it needs a reason", r.status_code == 403 and "reason" in reasons(r), reasons(r))
        check("nothing changed after those refusals", phase(org.id) == "active")

        heading("Retiring: its people can read, and cannot write")
        r = api("POST", "/lifecycle/organisation/retire", headers=custodian, json=body)
        now_status = r.json() if r.status_code == 200 else {}
        check("its own data custodian starts it", r.status_code == 200 and now_status.get("phase") == "retiring",
              reasons(r))
        with db() as conn:
            dates = conn.execute(
                "select extract(epoch from retiring_until - retired_at)/86400 as a, "
                "extract(epoch from closing_until - retiring_until)/86400 as b from tenant where id = %s",
                (org.id,)).fetchone()
        check("the two periods are 15 days each", round(dates["a"]) == 15 and round(dates["b"]) == 15,
              f"{float(dates['a']):.2f} and {float(dates['b']):.2f}")
        check("the status carries the days left and says cancelling is possible",
              now_status.get("days_left") == 15 and now_status.get("can_cancel") is True, str(now_status.get("days_left")))
        check("the reason and the person who asked are on record",
              now_status.get("retire_reason") == body["reason"] and now_status.get("requested_by") == "Custodian")
        r = api("POST", "/datasets", json={"tenant_id": org.id, "name": "late"})
        check("nothing more may be written to it", r.status_code == 409, f"{r.status_code}")
        check("a member can still read the status", api("GET", "/lifecycle/organisation", headers=member).json()["phase"] == "retiring")
        check("and the catalog token still answers", catalog().status_code == 200)
        r = api("POST", "/lifecycle/organisation/cancel", headers=member, json={})
        check("a member may not cancel", r.status_code == 403, reasons(r))
        r = api("POST", "/lifecycle/organisation/retire", headers=custodian, json=body)
        check("retiring it twice is refused", r.status_code == 409, reasons(r))

        heading("Cancelling puts everything back")
        r = api("POST", "/lifecycle/organisation/cancel", headers=custodian, json={})
        check("its custodian cancels while it is retiring", r.status_code == 200 and r.json()["phase"] == "active", reasons(r))
        with db() as conn:
            left = conn.execute("select purpose, retiring_until, closing_until, retire_reason from tenant where id = %s",
                                (org.id,)).fetchone()
        check("the dates and the reason are cleared",
              left["purpose"] == "production" and left["retiring_until"] is None and left["retire_reason"] is None)
        r = api("POST", "/datasets", json={"tenant_id": org.id, "name": "after-cancel"})
        check("it takes writes again", r.status_code == 201, f"{r.status_code}")
        events = api("GET", "/lifecycle/events", params={"tenant_id": org.id}, headers=admin).json()["events"]
        check("starting and cancelling are both on record, newest first",
              [e["event"] for e in events[:2]] == ["retirement_cancelled", "retirement_started"],
              ", ".join(e["event"] for e in events))

        heading("Closing: its people can do nothing")
        api("POST", "/lifecycle/organisation/retire", headers=custodian, json=body).raise_for_status()
        move_dates(org.id, retiring_ended=True)
        check("once the retiring period ends it is closing", phase(org.id) == "closing", phase(org.id))
        r = api("POST", "/lifecycle/organisation/cancel", headers=custodian, json={})
        check("the custodian can no longer cancel, and is told when that ended",
              r.status_code == 403 and "closing" in reasons(r), f"{r.status_code} {reasons(r)}")
        for who in ("custodian", "member"):
            headers = org.bearer(who)
            r = api("GET", "/people/roles", headers=headers)
            check(f"the {who} is refused everything else", r.status_code == 403 and "closing" in reasons(r),
                  f"{r.status_code} {reasons(r)[:90]}")
            r = api("GET", "/auth/whoami", headers=headers)
            check(f"the {who} can still ask who they are, and is told the organisation is closing",
                  r.status_code == 200 and r.json()["phase"] == "closing", f"{r.status_code}")
        check("the token a member made earlier is refused too", catalog().status_code == 401, str(catalog().status_code))
        r = api("GET", "/lifecycle/organisation", headers=org.bearer("member"))
        check("a member can read where the closing stands", r.status_code == 200 and r.json()["can_cancel"] is False,
              f"{r.status_code}")
        r = api("GET", "/lifecycle/holds", headers=admin)
        check("a platform administrator is not shut out", r.status_code == 200, str(r.status_code))
        r = api("GET", "/lifecycle/organisation", params={"tenant_id": other.id}, headers=org.bearer("member"))
        check("and one organisation's member cannot look at another's", r.status_code == 403, str(r.status_code))
        r = api("POST", "/lifecycle/organisation/retire", headers=admin,
                json={"tenant_id": "canary", "reason": "should be refused: canary is not a customer"})
        check("only a customer organisation can be closed this way", r.status_code == 409, f"{r.status_code} {reasons(r)}")

        heading("No organisation is left closed without a deadline")
        with db() as conn:
            undated = conn.execute("select id from tenant where purpose = 'retired' and retiring_until is null "
                                   "and purged_at is null and id not like 'verify-%%'").fetchall()
        check("every organisation closed by the old script was given one day, and so will be deleted",
              undated == [], ", ".join(r["id"] for r in undated)[:120])

    finally:
        drop_org(org)
        drop_org(other)
    return summary("U98")


if __name__ == "__main__":
    sys.exit(main())
