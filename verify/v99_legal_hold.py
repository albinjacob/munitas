"""U99: a legal hold, and the two platform administrators it needs.

A hold is recorded by one platform administrator and approved by a different one, names a
temporary custodian who acknowledges it, and stops the organisation being deleted for as long
as it stands, including while it is only proposed. This places holds on a closing organisation
and checks each rule from the point of view of the person it constrains: an administrator
approving their own, a data custodian of the organisation placing one, a notice with parts
missing, a proposal nobody approves, and a release made by mistake. It also tries to break the
two-person rule in the database directly, because a rule that only the API enforces is one
bug away from not being a rule.

    docker compose exec -T munitas-api python /verify/v99_legal_hold.py
"""

from __future__ import annotations

import sys

import httpx
import psycopg

from common import api, bearer_for, check, db, heading, require_api, summary
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, hold_body, make_org, move_dates


def reasons(r: httpx.Response) -> str:
    try:
        body = r.json()["detail"]
        return " ".join(body["reasons"]) if isinstance(body, dict) else str(body)
    except Exception:
        return r.text[:200]


def state(org_id: str) -> tuple[str, str]:
    with db() as conn:
        row = conn.execute("select tenant_phase(%s) as p, tenant_hold_state(%s) as h, "
                           "tenant_purge_allowed(%s) as a", (org_id, org_id, org_id)).fetchone()
    return row["p"], row["h"]


def main() -> int:
    require_api()
    org = make_org()
    try:
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        custodian, records = org.bearer("custodian"), org.bearer("dpo")
        api("POST", "/lifecycle/organisation/retire", headers=custodian,
            json={"reason": "The contract ends"}).raise_for_status()
        move_dates(org.id, retiring_ended=True)
        check("the organisation is closing", state(org.id)[0] == "closing", str(state(org.id)))

        heading("Who may place a hold, and what the notice must say")
        r = api("POST", "/lifecycle/holds", headers=custodian, json=hold_body(org))
        check("its own data custodian may not, and in a closing organisation cannot act at all",
              r.status_code == 403, f"{r.status_code} {reasons(r)[:80]}")
        incomplete = hold_body(org)
        incomplete["attorney_name"] = ""
        incomplete["preserve"] = ""
        r = api("POST", "/lifecycle/holds", headers=priya, json=incomplete)
        check("a notice with parts missing is refused, and the refusal names them",
              r.status_code == 403 and "attorney_name" in reasons(r) and "preserve" in reasons(r), reasons(r))
        r = api("POST", "/lifecycle/holds", headers=priya, json=hold_body(org, custodian_id="nobody-registered"))
        check("a custodian who is not a registered person is refused", r.status_code == 422, f"{r.status_code} {reasons(r)}")
        r = api("POST", "/lifecycle/holds", headers=priya, json=hold_body(org))
        check("a platform administrator records a complete notice, and it starts as proposed",
              r.status_code == 201 and r.json()["status"] == "proposed", f"{r.status_code} {reasons(r)}")
        hold = r.json()
        org.hold_ids.append(hold["id"])
        check("it records who placed it and who the temporary custodian is",
              hold["placed_by"] == ADMIN_A and hold["custodian_id"] == org.people["dpo"])
        check("it already stops a deletion while nobody has decided it", state(org.id)[1] == "pending", str(state(org.id)))
        r = api("POST", "/lifecycle/holds", headers=priya, json=hold_body(org))
        check("the same matter cannot be recorded twice while it is open", r.status_code == 409, reasons(r))

        heading("Two different administrators")
        r = api("POST", f"/lifecycle/holds/{hold['id']}/decide", headers=priya, json={"approve": True})
        check("the administrator who placed it cannot approve it",
              r.status_code == 403 and "different platform administrator" in reasons(r), reasons(r))
        r = api("POST", f"/lifecycle/holds/{hold['id']}/decide", headers=records, json={"approve": True})
        check("nor can anybody else in the organisation", r.status_code == 403, f"{r.status_code}")
        try:
            with db() as conn:
                conn.execute("update legal_hold set status = 'active', decided_by = placed_by, decided_at = now() "
                             "where id = %s", (hold["id"],))
            direct = "accepted"
        except psycopg.errors.CheckViolation:
            direct = "refused"
        check("and the database refuses it even when the API is bypassed", direct == "refused", direct)
        r = api("POST", f"/lifecycle/holds/{hold['id']}/decide", headers=ravi, json={"approve": False})
        check("declining needs a note saying why", r.status_code == 422, f"{r.status_code} {reasons(r)}")
        r = api("POST", f"/lifecycle/holds/{hold['id']}/decide", headers=ravi, json={"approve": True, "note": "Notice checked against the court reference"})
        check("a different administrator approves it, and it is then in force",
              r.status_code == 200 and r.json()["status"] == "active" and r.json()["decided_by"] == ADMIN_B, reasons(r))
        check("the review date is set", r.json()["review_due_on"] is not None)
        r = api("POST", f"/lifecycle/holds/{hold['id']}/decide", headers=ravi, json={"approve": True})
        check("a hold is decided once", r.status_code == 409, reasons(r))
        check("an approved hold is in force", state(org.id)[1] == "active")

        heading("The temporary custodian")
        r = api("POST", f"/lifecycle/holds/{hold['id']}/acknowledge", headers=custodian)
        check("somebody else in the organisation cannot acknowledge it", r.status_code in (403,), f"{r.status_code} {reasons(r)[:60]}")
        r = api("POST", f"/lifecycle/holds/{hold['id']}/acknowledge", headers=records)
        check("the named custodian acknowledges it, even though the organisation is closing",
              r.status_code == 200 and r.json()["custodian_acknowledged_at"] is not None, f"{r.status_code} {reasons(r)}")
        r = api("GET", "/lifecycle/holds", headers=records)
        check("and sees only the hold they answer for", r.status_code == 200 and [h["id"] for h in r.json()["holds"]] == [hold["id"]])
        r = api("GET", "/lifecycle/holds", headers=custodian)
        check("another member sees none, because a hold carries the notice's contact details",
              r.status_code == 403 or r.json()["holds"] == [], str(r.status_code))

        heading("While it stands, nothing is deleted")
        move_dates(org.id, retiring_ended=True, closing_ended=True)
        check("the time is up and only the hold stands in the way", state(org.id) == ("purge_due", "active"), str(state(org.id)))
        r = api("POST", "/lifecycle/sweep", headers=priya)
        check("a sweep deletes nothing", r.status_code == 200 and org.id not in r.json()["purged"], reasons(r))
        check("the organisation is still there", state(org.id)[0] == "purge_due")

        heading("Releasing it")
        r = api("POST", f"/lifecycle/holds/{hold['id']}/release", headers=ravi, json={"reason": ""})
        check("a release needs a reason", r.status_code == 403, reasons(r))
        r = api("POST", f"/lifecycle/holds/{hold['id']}/release", headers=custodian, json={"reason": "settled"})
        check("a member of the organisation cannot release it", r.status_code == 403)
        r = api("POST", f"/lifecycle/holds/{hold['id']}/release", headers=ravi,
                json={"reason": "Matter settled, written confirmation from Aldous and Brennan received"})
        check("an administrator releases it with a reason", r.status_code == 200 and r.json()["status"] == "released", reasons(r))
        check("the closing period starts again, so a mistaken release leaves time",
              state(org.id) == ("closing", "none"), str(state(org.id)))
        with db() as conn:
            days = conn.execute("select extract(epoch from closing_until - now())/86400 as d from tenant where id = %s",
                                (org.id,)).fetchone()["d"]
        check("by 15 days", 14.9 < float(days) < 15.1, f"{float(days):.2f}")
        r = api("POST", f"/lifecycle/holds/{hold['id']}/release", headers=ravi, json={"reason": "again"})
        check("a hold is released once", r.status_code == 409, reasons(r))

        heading("A proposal nobody approves lapses")
        r = api("POST", "/lifecycle/holds", headers=priya, json=hold_body(org, number="HC-2026-0500"))
        second = r.json()
        org.hold_ids.append(second["id"])
        check("a second matter can be recorded after the first is released", r.status_code == 201, reasons(r))
        with db() as conn:
            conn.execute("update legal_hold set expires_unapproved_at = now() - interval '1 hour' where id = %s",
                         (second["id"],))
        r = api("POST", "/lifecycle/sweep", headers=priya)
        check("the sweep lets it lapse", second["id"] in r.json()["lapsed"], reasons(r))
        with db() as conn:
            status = conn.execute("select status from legal_hold where id = %s", (second["id"],)).fetchone()["status"]
        check("it is recorded as lapsed", status == "lapsed", status)
        check("and no longer stands in the way", state(org.id)[1] == "none", str(state(org.id)))
        r = api("POST", f"/lifecycle/holds/{second['id']}/decide", headers=ravi, json={"approve": True})
        check("a lapsed proposal cannot be approved afterwards", r.status_code == 409, reasons(r))

        heading("What was recorded")
        events = api("GET", "/lifecycle/events", params={"tenant_id": org.id}, headers=priya).json()["events"]
        kinds = [e["event"] for e in events]
        for kind in ("retirement_started", "hold_placed", "hold_approved", "hold_acknowledged", "hold_released", "hold_lapsed"):
            check(f"the history includes {kind}", kind in kinds)
        r = api("PUT", "/lifecycle/events", headers=priya)
        check("and it has no way to be edited", r.status_code in (404, 405), str(r.status_code))
        with db() as conn:
            before = conn.execute("select count(*) as n from lifecycle_event where tenant_id = %s", (org.id,)).fetchone()["n"]
            conn.execute("delete from lifecycle_event where tenant_id = %s and event = 'hold_placed'", (org.id,))
            conn.execute("update lifecycle_event set event = 'x' where tenant_id = %s", (org.id,))
            after = conn.execute("select count(*) as n from lifecycle_event where tenant_id = %s and event = 'x'",
                                 (org.id,)).fetchone()["n"]
            still = conn.execute("select count(*) as n from lifecycle_event where tenant_id = %s", (org.id,)).fetchone()["n"]
        check("a row cannot be deleted or changed, even by direct database access", still == before and after == 0,
              f"{before} before, {still} after, {after} changed")
    finally:
        drop_org(org)
    return summary("U99")


if __name__ == "__main__":
    sys.exit(main())
