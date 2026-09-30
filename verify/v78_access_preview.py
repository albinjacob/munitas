"""U78: the access preview tells each person what they can read, and agrees
with the real check.

The preview is a prediction. The only proof a prediction is right is the thing
it predicts, so every case here takes the mark and then makes the real
`POST /credentials` as the same person: "role" and "lease" must be let in,
every other mark must be refused. Two independent answers that have to agree.

Covers, for the canary researcher:
  * a published version: readable by role
  * a raw version with a current approved lease: readable by lease, with its purpose
  * a raw version with an expired lease, and one with a withdrawn lease: ended
  * a raw version with a pending request: pending, naming who decides
  * a raw version with a custodian and nothing else: needs a request
  * a raw version with no department: nobody can grant it
and the edges: no session (401), no ids (422), too many (422), another
organisation's version (omitted), the dataset count, the fixed mark order,
and the policy engine unreachable (503).

Runs inside the API container, like the rest of the suite:

    docker exec -w /verify munitas-munitas-api-1 python v78_access_preview.py
"""

from __future__ import annotations

import sys
import uuid

from common import (CUSTODIAN, RESEARCHER, api, bearer_for, check, db, fixture_contract,
                    fixture_department, fixture_tenant, fixture_version, heading,
                    require_api, summary)

TENANT = fixture_tenant()
PURPOSE = "U78 shape exploration"


def preview(body: dict, who: str = RESEARCHER):
    return api("POST", "/access-preview", json=body, headers=bearer_for(who))


def can_read(version_id: str, purpose: str) -> tuple[bool, str]:
    """The real check, as the researcher. True when policy let them in.

    200 and 202 are both "allowed" (202 is allowed but not yet in effect).
    503 with allowed true is policy saying yes and storage failing, which is
    still the policy's answer. Anything else is a refusal.
    """
    r = api("POST", "/credentials", json={
        "principal": RESEARCHER, "principal_kind": "human",
        "roles": ["notebook_explore"], "tenant_id": TENANT,
        "dataset_version_id": version_id, "purpose": purpose,
    })
    detail = r.json().get("detail", r.json()) if r.content else {}
    allowed = r.status_code in (200, 202) or (
        r.status_code == 503 and isinstance(detail, dict) and detail.get("allowed") is True)
    return allowed, f"HTTP {r.status_code}"


def lease_for(version_id: str, *, ttl_hours: int = 4) -> str:
    """Ask as the researcher and approve as the custodian. Returns the lease id."""
    asked = api("POST", "/leases/requests", json={
        "tenant_id": TENANT, "principal": RESEARCHER, "dataset_version_id": version_id,
        "purpose": PURPOSE, "justification": "U78 verification", "ttl_hours": ttl_hours,
    }, headers=bearer_for(RESEARCHER))
    asked.raise_for_status()
    approved = api("POST", f"/leases/requests/{asked.json()['id']}/approve",
                   headers=bearer_for(CUSTODIAN))
    approved.raise_for_status()
    with db() as conn:
        row = conn.execute("select lease_id from lease_request where id = %s",
                           (asked.json()["id"],)).fetchone()
    return str(row["lease_id"])


def platform():
    """The platform package, imported in-process. Only inside the API image."""
    if "/app" not in sys.path:
        sys.path.insert(0, "/app")
    from app import db as app_db
    if app_db.pool.closed:
        app_db.pool.open()
    import app
    return app


def owned_raw() -> dict:
    contract = fixture_contract(TENANT)
    v = fixture_version(TENANT, contract, "RAW", dataset_name=f"u78-{uuid.uuid4().hex[:8]}")
    fixture_department(TENANT, v["dataset_id"])
    return v


def main() -> int:
    require_api()
    contract = fixture_contract(TENANT)

    published = fixture_version(TENANT, contract, "PUBLISHED", dataset_name=f"u78-{uuid.uuid4().hex[:8]}")
    leased = owned_raw()
    expired = owned_raw()
    withdrawn = owned_raw()
    pending = owned_raw()
    askable = owned_raw()
    orphan = fixture_version(TENANT, contract, "RAW", dataset_name=f"u78-{uuid.uuid4().hex[:8]}")

    lease_for(leased["id"])
    expired_lease = lease_for(expired["id"])
    with db() as conn:
        conn.execute("update access_lease set expires_at = now() - interval '1 hour' where id = %s",
                     (expired_lease,))
    withdrawn_lease = lease_for(withdrawn["id"])
    api("POST", f"/leases/{withdrawn_lease}/revoke", headers=bearer_for(CUSTODIAN)).raise_for_status()
    api("POST", "/leases/requests", json={
        "tenant_id": TENANT, "principal": RESEARCHER, "dataset_version_id": pending["id"],
        "purpose": PURPOSE, "justification": "U78 verification", "ttl_hours": 4,
    }, headers=bearer_for(RESEARCHER)).raise_for_status()

    cases = {
        "role": published, "lease": leased, "ended (expired)": expired,
        "ended (withdrawn)": withdrawn, "pending": pending, "ask": askable,
        "no_approver": orphan,
    }
    expected = {
        "role": "role", "lease": "lease", "ended (expired)": "ended",
        "ended (withdrawn)": "ended", "pending": "pending", "ask": "ask",
        "no_approver": "no_approver",
    }

    heading("U78: each version gets the mark its state calls for")
    r = preview({"version_ids": [v["id"] for v in cases.values()]})
    check("the preview answers", r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}")
    if r.status_code != 200:
        return summary("U78")
    marks = r.json()["versions"]
    for name, v in cases.items():
        got = marks.get(v["id"], {})
        check(f"{name}: marked {expected[name]}", got.get("mark") == expected[name], str(got))

    check("the lease mark names its purpose and end",
          marks[leased["id"]].get("purpose") == PURPOSE and marks[leased["id"]].get("until") is not None,
          str(marks[leased["id"]]))
    check("an expired lease says expired",
          marks[expired["id"]].get("ended") == "expired", str(marks[expired["id"]]))
    check("a withdrawn lease says withdrawn, and when",
          marks[withdrawn["id"]].get("ended") == "revoked"
          and marks[withdrawn["id"]].get("ended_at") is not None, str(marks[withdrawn["id"]]))
    check("pending names who decides",
          bool(marks[pending["id"]].get("decider_label")), str(marks[pending["id"]]))

    heading("U78: the preview agrees with the real check")
    for name, v in cases.items():
        readable = marks[v["id"]]["mark"] in ("role", "lease")
        purpose = marks[v["id"]].get("purpose") or PURPOSE
        allowed, detail = can_read(v["id"], purpose)
        check(f"{name}: preview says {'readable' if readable else 'not readable'}, "
              f"the real check {'allows' if allowed else 'refuses'}",
              readable == allowed, detail)

    heading("U78: the dataset count comes from the same marks")
    r = preview({"dataset_ids": [published["dataset_id"], askable["dataset_id"]]})
    counts = r.json().get("datasets", {}) if r.status_code == 200 else {}
    check("a dataset of one readable version reads 1 of 1",
          counts.get(published["dataset_id"]) == {"readable": 1, "total": 1}, str(counts))
    check("a dataset of one unreadable version reads 0 of 1",
          counts.get(askable["dataset_id"]) == {"readable": 0, "total": 1}, str(counts))

    heading("U78: edges")
    r = api("POST", "/access-preview", json={"version_ids": [published["id"]]})
    check("no session: 401", r.status_code == 401, f"HTTP {r.status_code}")
    r = preview({})
    check("no ids: 422", r.status_code == 422, f"HTTP {r.status_code}")
    r = preview({"version_ids": [str(uuid.uuid4()) for _ in range(1001)]})
    check("more than 1,000 ids: 422", r.status_code == 422, f"HTTP {r.status_code}")
    with db() as conn:
        foreign = conn.execute(
            "select dataset_version_id from version_class where tenant_id <> %s limit 1",
            (TENANT,)).fetchone()
    if foreign:
        r = preview({"version_ids": [str(foreign["dataset_version_id"]), published["id"]]})
        got = r.json().get("versions", {}) if r.status_code == 200 else {}
        check("another organisation's version is left out, not reported",
              str(foreign["dataset_version_id"]) not in got and published["id"] in got, str(list(got)))

    heading("U78: the mark order is fixed")
    platform()
    from app.access_preview import choose_mark
    lease = {"id": "l1", "purpose": "p", "expires_at": "2099-01-01T00:00:00Z"}
    both = {"same_tenant": True, "by_role": True, "current_leases": [lease], "ended_leases": []}
    check("removed beats everything",
          choose_mark(reclaimed=True, answer=both, pending=True, lease_rows={},
                      decider_label="x", has_custodian=True)["mark"] == "removed")
    check("role beats a lease",
          choose_mark(reclaimed=False, answer=both, pending=True, lease_rows={},
                      decider_label="x", has_custodian=True)["mark"] == "role")
    ended_and_pending = {"same_tenant": True, "by_role": False, "current_leases": [],
                         "ended_leases": [{"id": "l1", "purpose": "p", "ended": "expired"}]}
    check("pending beats ended",
          choose_mark(reclaimed=False, answer=ended_and_pending, pending=True,
                      lease_rows={"l1": {"revoked_at": None, "expires_at": None}},
                      decider_label="x", has_custodian=True)["mark"] == "pending")

    heading("U78: the policy engine down is never a readable claim")
    # Called in-process with the engine address pointed at a closed port:
    # stopping the real OPA container would break every other check running
    # against this stack at the same time.
    from fastapi import HTTPException
    from app import access_preview, config
    from app.models import AccessPreviewIn
    saved = config.OPA_URL
    config.OPA_URL = "http://127.0.0.1:9"
    try:
        access_preview.access_preview(
            AccessPreviewIn(version_ids=[published["id"]]),
            identity={"id": RESEARCHER, "tenant_id": TENANT, "roles": ["notebook_explore"]})
        check("unreachable policy engine: 503", False, "returned an answer")
    except HTTPException as exc:
        check("unreachable policy engine: 503", exc.status_code == 503, str(exc.detail))
    finally:
        config.OPA_URL = saved

    return summary("U78")


if __name__ == "__main__":
    sys.exit(main())
