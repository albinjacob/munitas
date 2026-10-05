"""U102: who may ask for an organisation's records to be produced, and what stops a record being erased meanwhile.

Producing records for a legal matter is the most sensitive thing the platform does with a legal hold, so it has
more separation than anything else: a platform administrator asks, a different one approves, and the custodian the
hold names confirms the scope. This puts a legal hold in force on an organisation and tries to get past each of
them from the side of the person each rule is aimed at, including from the database directly, and checks that
every refusal is written to the audit trail. It then checks the related rule that a record cannot be erased while
a hold stands, and that the erasure happens, by the sweep, once the hold ends.

    docker compose exec -T munitas-api python /verify/v102_legal_export_rules.py
"""

from __future__ import annotations

import base64
import sys
import time

import httpx
import psycopg

from common import api, bearer_for, check, db, heading, require_api, summary
from lifecycle_fixture import (ADMIN_A, ADMIN_B, export_body, finish_org, hold_body, make_org, seal_data)


def reasons(r: httpx.Response) -> str:
    try:
        body = r.json()["detail"]
        return " ".join(body["reasons"]) if isinstance(body, dict) else str(body)
    except Exception:
        return r.text[:200]


def audit_rows(org_id: str, like: str) -> list[dict]:
    with db() as conn:
        return conn.execute("select principal, allowed, purpose from access_decision where tenant_id = %s and purpose like %s",
                            (org_id, like)).fetchall()


def main() -> int:
    require_api()
    org, other = make_org(), make_org()
    priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
    try:
        data = seal_data(org)
        foreign = seal_data(other)
        custodian, member, records = org.bearer("custodian"), org.bearer("member"), org.bearer("dpo")

        heading("No hold in force, no export")
        hold = api("POST", "/lifecycle/holds", headers=priya, json=hold_body(org)).json()
        org.hold_ids.append(hold["id"])
        r = api("POST", "/legal-exports", headers=priya, json=export_body(hold["id"], [data["dataset_id"]]))
        check("a hold that is only proposed does not allow an export",
              r.status_code == 403 and "legal hold is in force" in reasons(r), f"{r.status_code} {reasons(r)}")
        api("POST", f"/lifecycle/holds/{hold['id']}/decide", headers=ravi, json={"approve": True, "note": "ok"}).raise_for_status()

        heading("Who may ask, and what the request must say")
        for who, headers in (("a data custodian of the organisation", custodian), ("an ordinary member", member),
                             ("the hold's temporary custodian", records)):
            r = api("POST", "/legal-exports", headers=headers, json=export_body(hold["id"], [data["dataset_id"]]))
            check(f"{who} may not", r.status_code == 403, f"{r.status_code} {reasons(r)[:80]}")
        body = export_body(hold["id"], [data["dataset_id"]])
        body["demand_reference"], body["recipient_email"] = "", ""
        r = api("POST", "/legal-exports", headers=priya, json=body)
        check("a request without the demand's reference and a recipient is refused, and the refusal names them",
              r.status_code == 403 and "demand_reference" in reasons(r) and "recipient_email" in reasons(r), reasons(r))
        r = api("POST", "/legal-exports", headers=priya, json=export_body(hold["id"], []))
        check("a request naming no dataset is refused", r.status_code == 403, reasons(r))
        r = api("POST", "/legal-exports", headers=priya, json=export_body(hold["id"], [foreign["dataset_id"]]))
        check("a dataset of another organisation cannot be named", r.status_code == 422, f"{r.status_code} {reasons(r)}")
        r = api("POST", "/legal-exports", headers=priya, json=export_body(hold["id"], [data["dataset_id"]]))
        check("a platform administrator asks, naming the demand, the scope and the recipient",
              r.status_code == 201 and r.json()["status"] == "requested", f"{r.status_code} {reasons(r)}")
        export = r.json()
        check("the request is in the audit trail", any(a["allowed"] for a in audit_rows(org.id, "legal export KB-2026-004411: requested")))

        heading("Two different administrators")
        r = api("POST", f"/legal-exports/{export['id']}/approve", headers=priya, json={"approve": True})
        check("the administrator who asked cannot approve it",
              r.status_code == 403 and "different platform administrator" in reasons(r), reasons(r))
        check("and the refusal is in the audit trail", any(not a["allowed"] for a in audit_rows(org.id, "legal export KB-2026-004411: approve refused")))
        for who, headers in (("the organisation's custodian", custodian), ("the hold's custodian", records)):
            r = api("POST", f"/legal-exports/{export['id']}/approve", headers=headers, json={"approve": True})
            check(f"{who} cannot approve it", r.status_code == 403, f"{r.status_code}")
        try:
            with db() as conn:
                conn.execute("update legal_export set approved_by = requested_by, status = 'approved' where id = %s", (export["id"],))
            direct = "accepted"
        except psycopg.errors.CheckViolation:
            direct = "refused"
        check("and the database refuses the same person twice even when the API is bypassed", direct == "refused", direct)

        heading("The custodian confirms the scope, and only after approval")
        r = api("POST", f"/legal-exports/{export['id']}/confirm", headers=records, json={"approve": True})
        check("the custodian cannot confirm before it is approved", r.status_code == 403, f"{r.status_code} {reasons(r)[:70]}")
        r = api("POST", f"/legal-exports/{export['id']}/approve", headers=ravi, json={"approve": False})
        check("declining needs a note", r.status_code == 422, f"{r.status_code}")
        r = api("POST", f"/legal-exports/{export['id']}/approve", headers=ravi, json={"approve": True, "note": "Demand checked"})
        check("a different administrator approves it", r.status_code == 200 and r.json()["status"] == "approved", reasons(r))
        r = api("POST", f"/legal-exports/{export['id']}/confirm", headers=priya, json={"approve": True})
        check("an administrator cannot confirm what the custodian answers for", r.status_code == 403, reasons(r))
        r = api("POST", f"/legal-exports/{export['id']}/confirm", headers=custodian, json={"approve": True})
        check("nor can the organisation's own data custodian", r.status_code == 403, f"{r.status_code}")
        r = api("POST", f"/legal-exports/{export['id']}/confirm", headers=records, json={"approve": False, "note": "The demand names fewer datasets than this"})
        check("the hold's custodian can decline the scope, with a reason",
              r.status_code == 200 and r.json()["status"] == "refused", reasons(r))
        r = api("POST", f"/legal-exports/{export['id']}/confirm", headers=records, json={"approve": True})
        check("a refused export stays refused", r.status_code == 403, f"{r.status_code}")

        heading("An export needs the hold to stay in force")
        second = api("POST", "/legal-exports", headers=priya, json=export_body(hold["id"], [data["dataset_id"]], demand_reference="KB-2026-004412"))
        second.raise_for_status()
        api("POST", f"/lifecycle/holds/{hold['id']}/release", headers=ravi, json={"reason": "Matter closed"}).raise_for_status()
        r = api("POST", f"/legal-exports/{second.json()['id']}/approve", headers=ravi, json={"approve": True})
        check("once the hold is released nothing more is approved", r.status_code in (403, 409), f"{r.status_code} {reasons(r)[:80]}")
        r = api("POST", "/legal-exports", headers=priya, json=export_body(hold["id"], [data["dataset_id"]]))
        check("and nothing new is asked for", r.status_code == 403, reasons(r))

        heading("A record cannot be erased while a hold stands")
        again = hold_body(org, "HC-2026-0900")
        h2 = api("POST", "/lifecycle/holds", headers=priya, json=again).json()
        org.hold_ids.append(h2["id"])
        api("POST", f"/lifecycle/holds/{h2['id']}/decide", headers=ravi, json={"approve": True, "note": "ok"}).raise_for_status()
        record_id = f"u102-{org.id[-8:]}"
        api("POST", "/records", json={"tenant_id": org.id, "record_id": record_id, "plaintext": "a patient's name"}).raise_for_status()
        r = api("DELETE", f"/records/{record_id}", json={"tenant_id": org.id, "reason": "patient asked for erasure", "requested_by": "sam"})
        check("erasing a record of a held organisation is refused, with the reason",
              r.status_code == 409 and "legal hold" in reasons(r), f"{r.status_code} {reasons(r)[:90]}")
        with db() as conn:
            key = conn.execute("select destroyed_at from record_key where record_id = %s", (record_id,)).fetchone()
            kept = conn.execute("select honoured_at from deferred_erasure where record_id = %s", (record_id,)).fetchone()
        check("the key was not destroyed", key and key["destroyed_at"] is None)
        check("the request was kept for later", kept is not None and kept["honoured_at"] is None)
        r = api("POST", f"/records/{record_id}/open", params={"ciphertext_b64": base64.b64encode(b"x").decode()})
        check("and the refusal is in the audit trail", any(not a["allowed"] for a in audit_rows(org.id, "erasure of a record")))
        api("POST", f"/lifecycle/holds/{h2['id']}/release", headers=ravi, json={"reason": "Matter closed"}).raise_for_status()
        swept = api("POST", "/lifecycle/sweep", headers=priya).json()
        check("when the hold ends the sweep carries the erasure out", record_id in swept["exports"]["erasures_honoured"], str(swept["exports"]))
        with db() as conn:
            key = conn.execute("select destroyed_at from record_key where record_id = %s", (record_id,)).fetchone()
            tomb = conn.execute("select 1 as x from tombstone where record_id = %s", (record_id,)).fetchone()
        check("the key is now destroyed and the tombstone written", key["destroyed_at"] is not None and tomb is not None)
    finally:
        finish_org(org, priya, ravi)
        finish_org(other, priya, ravi)
        from lifecycle_fixture import drop_org
        drop_org(org)
        drop_org(other)
    return summary("U102")


if __name__ == "__main__":
    sys.exit(main())
