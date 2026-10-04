"""U127: a department has several approvers, any one of whom may act, and who they are is changed by approvers, on the record.

One custodian per department meant one person on leave blocked the whole department. A department now has a set of approvers: a person who holds
the data custodian role and is listed for that department. This checks, one item at a time, with real sign-ins, in an organisation of its own:

  * the person a department is made with is its first approver, however it was made;
  * anybody of the organisation can see who to ask, and only the approvers and the data protection officer see the history;
  * only a current approver may add or remove one, only for a person who already holds the data custodian role, and always with a reason;
  * any one approver can confirm a claim, so a second approver, and temporary cover, unblock the department; the maker never confirms their own;
  * the last resort (another data custodian confirming) exists only while the claimant is the single approver, and closes when there is a second;
  * a department always keeps one permanent approver, by the policy and by the database; temporary cover lapses by itself;
  * the history is never edited, and records who changed what and why;
  * the claims queue shows each custodian what they can act on.

    docker compose exec -T munitas-api python /verify/v127_department_approvers.py
"""

from __future__ import annotations

import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg.errors  # noqa: E402

from common import api, bearer_for, check, db, heading, require_api, summary  # noqa: E402
from lifecycle_fixture import drop_org, give_login, make_org  # noqa: E402

ONLY_APPROVERS_ADD = "only a current approver of this department may add another"
ONLY_APPROVERS_REMOVE = "only a current approver of this department may remove one"
NEEDS_ROLE = "this person does not hold the data custodian role, which an approver must hold; they can ask for it first"
NEEDS_REASON = "a change to a department's approvers needs a reason, which is recorded with it"
KEEPS_ONE = "a department always keeps at least one permanent approver, so this one cannot be removed until another is added"


def reasons(r) -> list[str]:
    try:
        return r.json().get("detail", {}).get("reasons", [])
    except Exception:  # noqa: BLE001
        return []


def add_person(org, key: str, role: str, label: str) -> str:
    person = f"{org.id}-{key}"
    with db() as conn:
        conn.execute("insert into directory (id, tenant_id, label, kind, roles) values (%s, %s, %s, 'human', %s)", (person, org.id, label, [role]))
        org.identities.append(give_login(conn, person, label))
    org.people[key] = person
    return person


def approvers(department: str) -> list[str]:
    with db() as conn:
        return conn.execute("select active_department_approvers(%s::uuid) as a", (department,)).fetchone()["a"]


def main() -> int:
    require_api()
    org = make_org(engineer=True)
    other = make_org()
    departments: list[str] = []
    try:
        c1 = org.people["custodian"]
        c2 = add_person(org, "c2", "data_custodian", "Second custodian")
        c3 = add_person(org, "c3", "data_custodian", "Third custodian")
        c4 = add_person(org, "c4", "data_custodian", "Custodian of nothing")
        engineer, member, dpo = org.people["engineer"], org.people["member"], org.people["dpo"]
        as_ = lambda who: bearer_for(who)  # noqa: E731

        def made_department(name: str, custodian: str) -> str:
            department = str(uuid.uuid4())
            with db() as conn:
                conn.execute("insert into department (id, tenant_id, name, custodian) values (%s, %s, %s, %s)", (department, org.id, name, custodian))
            departments.append(department)
            return department

        def call(who: str, method: str, path: str, **kw):
            return api(method, path, headers=as_(who), **kw)

        def add(who: str, department: str, person: str, reason: str = "cover while away", valid_until: str | None = None):
            body = {"person_id": person, "reason": reason}
            if valid_until:
                body["valid_until"] = valid_until
            return call(who, "POST", f"/departments/{department}/approvers", json=body)

        def remove(who: str, department: str, person: str, reason: str = "no longer needed"):
            return call(who, "POST", f"/departments/{department}/approvers/{person}/remove", json={"reason": reason})

        def claim(who: str, department: str):
            r = call(who, "POST", "/datasets/register", json={
                "tenant_id": org.id, "name": f"u127-{uuid.uuid4().hex[:8]}", "department_id": department, "registered_by": who,
                "provenance": "external_public", "declared_class": "PUBLISHED", "source_kind": "upload"})
            assert r.status_code == 201, (r.status_code, r.text[:150])
            return r.json()["id"]

        def confirm(who: str, dataset: str):
            return call(who, "POST", f"/datasets/{dataset}/confirm-classification", json={"confirmed_by": who})

        def queue(who: str) -> list[str]:
            q = call(who, "GET", "/datasets/awaiting-confirmation", params={"tenant_id": org.id, "custodian": who, "limit": 500}).json()
            return [d["id"] for d in q["items"]]

        heading("The person a department is made with is its first approver")
        cardiology = made_department("Cardiology", c1)
        oncology = made_department("Oncology", c2)
        check("Cardiology starts with its custodian as its one approver", approvers(cardiology) == [c1], str(approvers(cardiology)))
        check("and Oncology with its own", approvers(oncology) == [c2], str(approvers(oncology)))
        with db() as conn:
            row = conn.execute("select added_by, reason, valid_until from department_approver where department_id = %s", (cardiology,)).fetchone()
        check("recorded as made with the department, permanent", row["added_by"] == c1 and "named when the department was made" in row["reason"] and row["valid_until"] is None, str(dict(row)))

        heading("Who to ask is open to the organisation, the history is not")
        seen = call(member, "GET", f"/departments/{cardiology}/approvers")
        check("anybody of the organisation sees the approvers", seen.status_code == 200 and [a["person_id"] for a in seen.json()["approvers"]] == [c1], str(seen.status_code))
        check("a member may not read the history", call(member, "GET", f"/departments/{cardiology}/approvers", params={"history": "true"}).status_code == 403)
        check("an approver may", call(c1, "GET", f"/departments/{cardiology}/approvers", params={"history": "true"}).status_code == 200)
        check("so may the data protection officer", call(dpo, "GET", f"/departments/{cardiology}/approvers", params={"history": "true"}).status_code == 200)
        check("another organisation does not find the department",
              api("GET", f"/departments/{cardiology}/approvers", headers=other.bearer("custodian")).status_code == 404)

        heading("Only a current approver adds, for a custodian, with a reason")
        for who, label in ((member, "a member"), (engineer, "an engineer"), (dpo, "the data protection officer"), (c2, "a custodian of another department"), (c4, "a custodian of nothing")):
            r = add(who, cardiology, c4 if who != c4 else c3)
            check(f"{label} may not add an approver", r.status_code == 403 and any(x in reasons(r) for x in (ONLY_APPROVERS_ADD, "only a data custodian may change a department's approvers")), f"{r.status_code} {reasons(r)}")
        r = add(c1, cardiology, member)
        check("the person added must already hold the data custodian role", r.status_code == 403 and NEEDS_ROLE in reasons(r), f"{r.status_code} {reasons(r)}")
        r = add(c1, cardiology, c2, reason="  ")
        check("a change needs a reason", r.status_code == 403 and NEEDS_REASON in reasons(r), f"{r.status_code} {reasons(r)}")
        check("somebody of another organisation is not found", add(c1, cardiology, other.people["custodian"]).status_code == 404)
        check("an approver cannot be added twice", add(c1, cardiology, c1).status_code == 403)
        check("temporary cover must end in the future",
              add(c1, cardiology, c3, valid_until=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat()).status_code == 422)
        added = add(c1, cardiology, c2, reason="covers while the first custodian is away")
        check("a current approver adds a custodian", added.status_code == 201, f"{added.status_code} {added.text[:100]}")
        check("and the department now has two approvers", sorted(approvers(cardiology)) == sorted([c1, c2]), str(approvers(cardiology)))

        heading("Any one approver can act, so one person away does not block the department")
        by_engineer = claim(engineer, cardiology)
        check("a claim by an engineer is confirmed by the second approver", confirm(c2, by_engineer).status_code == 200)
        by_c1 = claim(c1, cardiology)
        check("the maker never confirms their own claim", confirm(c1, by_c1).status_code == 403)
        check("a custodian who is not an approver may not, now that there is a second approver", confirm(c4, by_c1).status_code == 403)
        check("the other approver does", confirm(c2, by_c1).status_code == 200)

        heading("The last resort is only for a single approver")
        lone = claim(c2, oncology)
        check("in a department with one approver, that approver cannot confirm", confirm(c2, lone).status_code == 403)
        check("another data custodian of the organisation can", confirm(c4, lone).status_code == 200)

        heading("The queue shows each custodian what they can act on")
        mine = claim(engineer, cardiology)
        theirs = claim(c2, oncology)
        check("an approver sees an engineer's claim about their department", mine in queue(c1) and mine in queue(c2))
        check("a custodian who is not an approver does not", mine not in queue(c4))
        check("the maker does not see their own claim", theirs not in queue(c2))
        check("another data custodian sees the single approver's claim", theirs in queue(c4))
        check("and an approver of another department does not see this one's", mine not in queue(c3))

        heading("Temporary cover lapses by itself")
        soon = (datetime.now(timezone.utc) + timedelta(seconds=4)).isoformat()
        cover = add(c1, cardiology, c3, reason="covers for a week of leave", valid_until=soon)
        check("a temporary approver is added", cover.status_code == 201, f"{cover.status_code} {cover.text[:100]}")
        check("they count while the cover lasts", c3 in approvers(cardiology))
        check("and can act", confirm(c3, mine).status_code == 200)
        time.sleep(5)
        check("when it ends they are no longer an approver", c3 not in approvers(cardiology), str(approvers(cardiology)))
        late = claim(engineer, cardiology)
        check("and can no longer confirm", confirm(c3, late).status_code == 403)
        check("the row is kept as history", call(c1, "GET", f"/departments/{cardiology}/approvers", params={"history": "true"}).json()["history"] is not None)

        heading("Removing: by an approver, with a reason, never the last permanent one")
        for who, label in ((member, "a member"), (c4, "a custodian of nothing")):
            r = remove(who, cardiology, c2)
            check(f"{label} may not remove", r.status_code == 403, f"{r.status_code} {reasons(r)}")
        check("a removal needs a reason", remove(c1, cardiology, c2, reason="").status_code == 403)
        check("somebody who is not an approver cannot be removed", remove(c1, cardiology, c4).status_code == 403)
        removed = remove(c1, cardiology, c2, reason="moved to another department")
        check("an approver removes another when a permanent one remains", removed.status_code == 200, f"{removed.status_code} {removed.text[:100]}")
        check("so the department is back to one approver", approvers(cardiology) == [c1], str(approvers(cardiology)))
        last = remove(c1, cardiology, c1, reason="leaving")
        check("the last permanent approver cannot be removed, and the reason is given", last.status_code == 403 and KEEPS_ONE in reasons(last), f"{last.status_code} {reasons(last)}")
        try:
            with db() as conn:
                conn.execute("update department_approver set removed_by = %s, removed_at = now(), removal_reason = 'direct' "
                             "where department_id = %s and person_id = %s and removed_at is null", (c1, cardiology, c1))
            guarded = False
        except psycopg.errors.CheckViolation as exc:
            guarded = "department_keeps_a_permanent_approver" in str(exc.diag.constraint_name)
        check("and the database refuses it too, whatever the route does", guarded)

        heading("The history is never edited, and says who changed what and why")
        history = call(c1, "GET", f"/departments/{cardiology}/approvers", params={"history": "true"}).json()["history"]
        gone = next(h for h in history if h["person_id"] == c2 and h["removed_at"])
        check("the removed approver's row says who removed them and why", gone["removed_by"] == c1 and gone["removal_reason"] == "moved to another department", str(gone))
        check("and who added them and why", gone["added_by"] == c1 and "covers while the first custodian is away" in gone["reason"], str(gone))
        try:
            with db() as conn:
                conn.execute("update department_approver set reason = 'rewritten' where department_id = %s and person_id = %s", (cardiology, c1))
            edited = True
        except psycopg.errors.CheckViolation:
            edited = False
        check("a row cannot be edited, only removed", not edited)
        check("a removed approver can be added again, as a new row",
              add(c1, cardiology, c2, reason="back from the other department").status_code == 201 and c2 in approvers(cardiology))
    finally:
        with db() as conn:
            conn.execute("delete from dataset_source where dataset_id in (select id from dataset where tenant_id = %s)", (org.id,))
            conn.execute("delete from dataset where tenant_id = %s", (org.id,))
            conn.execute("delete from department_approver where tenant_id = %s", (org.id,))
            conn.execute("delete from department where tenant_id = %s", (org.id,))
        drop_org(org)
        drop_org(other)
    return summary("U127")


if __name__ == "__main__":
    sys.exit(main())
