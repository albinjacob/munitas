"""Department approvers: who may approve access to a department's data and confirm claims about it.

A department has any number of approvers, and any one of them may act, so a department is never blocked by one person being away. A person is
an approver of a department when they hold the data custodian role (what they may do) and are listed here for that department (which data
they may do it for). Any current approver may add another or remove one, with a reason that is recorded with who made the change and when.
The person added must already hold the data custodian role: adding them is not a way of giving them the role, which the person asks for and a
different custodian approves. A department always keeps at least one permanent approver; temporary cover has an end date and lapses by itself.

The rows are history and are never edited (see `department_approver` in schema.sql), so "who answered for this department on a given day" can
always be answered. Each change is decided by the policy (`approver_addition_decision`, `approver_removal_decision`), which refuses with the reason.
"""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg.errors
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import auth, db, opa

router = APIRouter(tags=["departments"])

# The same live union `auth.current_session` reads: a role held permanently plus any grant that is approved, not revoked and not expired.
_ROLES_OF = """
    select array(
        select distinct role from (
            select unnest(d.roles) as role
            union
            select role from role_grant where principal = d.id and not revoked and expires_at > now()
        ) effective
    ) as roles
      from directory d
     where d.id = %s
"""

_ACTIVE = """
    a.removed_at is null and (a.valid_until is null or a.valid_until > now()) and p.ended_at is null
"""


class AddApprover(BaseModel):
    person_id: str
    reason: str = ""
    valid_until: datetime | None = None


class RemoveApprover(BaseModel):
    reason: str = ""


def _department(department_id: str, identity: dict) -> dict:
    """A department of another organisation is not found, not forbidden, so its existence is not disclosed."""
    try:
        row = db.one("select id, tenant_id, name from department where id = %s", (department_id,))
    except psycopg.errors.InvalidTextRepresentation:
        row = None
    if not row or row["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such department")
    return row


def _roles_of(person_id: str) -> list[str]:
    row = db.one(_ROLES_OF, (person_id,))
    return list(row["roles"]) if row else []


def _approvers(department_id: str) -> list[dict]:
    return db.all_rows(
        f"""select a.person_id, p.label, a.added_by, ab.label as added_by_label, a.added_at, a.valid_until, a.reason,
                   person_holds_live_role(a.person_id, 'data_custodian') as holds_role
              from department_approver a
              join directory p on p.id = a.person_id
              left join directory ab on ab.id = a.added_by
             where a.department_id = %s and {_ACTIVE}
             order by a.added_at, p.label""",
        (department_id,),
    )


def _jsonable(rows: list[dict]) -> list[dict]:
    return [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()} for r in rows]


@router.get("/departments/{department_id}/approvers")
def list_approvers(department_id: str, history: bool = False, identity: dict = Depends(auth.current_session)) -> dict:
    """Who is listed as an approver of this department now, and whether each still holds the data custodian role (`holds_role`). One who does
    not is listed but cannot act, and does not count. With `history`, everybody who ever answered, and who changed it and why.

    Anybody of the organisation can see who to ask. The history is for the department's own approvers and the data protection officer."""
    department = _department(department_id, identity)
    current = _approvers(department_id)
    result = {"department": {"id": str(department["id"]), "name": department["name"]}, "approvers": _jsonable(current)}
    if history:
        mine = any(a["person_id"] == identity["id"] for a in current)
        if not (mine or "dpo" in identity["roles"]):
            raise HTTPException(403, {"reasons": ["the history of a department's approvers is for its approvers and the data protection officer"]})
        rows = db.all_rows(
            """select a.person_id, p.label, a.added_by, ab.label as added_by_label, a.added_at, a.valid_until, a.reason,
                      a.removed_by, rb.label as removed_by_label, a.removed_at, a.removal_reason
                 from department_approver a
                 join directory p on p.id = a.person_id
                 left join directory ab on ab.id = a.added_by
                 left join directory rb on rb.id = a.removed_by
                where a.department_id = %s
                order by a.added_at, p.label""",
            (department_id,),
        )
        result["history"] = _jsonable(rows)
    return result


@router.get("/departments/{department_id}/approver-candidates")
def list_approver_candidates(department_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """The people who could be added as an approver right now: the organisation's people who hold the data custodian role today and are not
    already approvers of this department. For the approvers of the department, who are the only people who may add one.

    "Hold the role today" is worked out here, at the moment of asking, from the same live union the add route checks (the role a person has
    permanently plus any grant that has not been withdrawn or lapsed), so the list a person chooses from agrees with what the platform will accept."""
    _department(department_id, identity)
    current = [a["person_id"] for a in _approvers(department_id)]
    if identity["id"] not in current or "data_custodian" not in identity["roles"]:
        raise HTTPException(403, {"reasons": ["only an approver of this department, who holds the data custodian role, may see who can be added"]})
    rows = db.all_rows(
        """select d.id as person_id, d.label
             from directory d
            where d.tenant_id = %s and d.kind = 'human' and d.ended_at is null
              and not (d.id = any(%s))
              and ('data_custodian' = any(d.roles)
                   or exists (select 1 from role_grant g
                               where g.principal = d.id and g.role = 'data_custodian' and not g.revoked and g.expires_at > now()))
            order by d.label""",
        (identity["tenant_id"], current),
    )
    return {"candidates": rows}


@router.post("/departments/{department_id}/approvers", status_code=201)
def add_approver(department_id: str, body: AddApprover, identity: dict = Depends(auth.current_session)) -> dict:
    """Add an approver to a department. By a current approver, for a person who already holds the data custodian role."""
    department = _department(department_id, identity)
    person = db.one("select id, tenant_id, kind, ended_at from directory where id = %s", (body.person_id,))
    if not person or person["tenant_id"] != identity["tenant_id"] or person["kind"] != "human" or person["ended_at"]:
        raise HTTPException(404, {"reasons": ["no such person in this organisation"]})
    if body.valid_until is not None:
        end = body.valid_until if body.valid_until.tzinfo else body.valid_until.replace(tzinfo=timezone.utc)
        if end <= datetime.now(timezone.utc):
            raise HTTPException(422, {"reasons": ["temporary cover must end in the future"]})
    approvers = [a["person_id"] for a in _approvers(department_id)]
    permitted, reasons = opa.may_add_department_approver({
        "actor": {"id": identity["id"], "roles": identity["roles"]},
        "person": {"id": person["id"], "roles": _roles_of(person["id"])},
        "department": {"name": department["name"], "approvers": approvers},
        "reason": body.reason,
    })
    if not permitted:
        raise HTTPException(403, {"added": False, "reasons": reasons})
    try:
        with db.cursor(commit=True) as cur:
            cur.execute("select id from department where id = %s for update", (department_id,))  # one change at a time per department
            cur.execute(
                """insert into department_approver (tenant_id, department_id, person_id, added_by, valid_until, reason)
                   values (%s, %s, %s, %s, %s, %s) returning id""",
                (department["tenant_id"], department_id, person["id"], identity["id"], body.valid_until, body.reason.strip()),
            )
            created = cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(409, {"added": False, "reasons": ["this person is already an approver of this department"]}) from exc
    return {"id": str(created["id"]), "department_id": str(department["id"]), "person_id": person["id"],
            "valid_until": body.valid_until.isoformat() if body.valid_until else None}


@router.post("/departments/{department_id}/approvers/{person_id}/remove")
def remove_approver(department_id: str, person_id: str, body: RemoveApprover, identity: dict = Depends(auth.current_session)) -> dict:
    """Remove an approver, by a current approver of the same department. Refused for the last permanent one."""
    department = _department(department_id, identity)
    current = _approvers(department_id)
    approvers = [a["person_id"] for a in current]
    target = next((a for a in current if a["person_id"] == person_id), None)
    # Only an approver who can act counts toward the one permanent approver a department keeps.
    permanent_after = sum(1 for a in current if a["valid_until"] is None and a["person_id"] != person_id and a["holds_role"])
    permitted, reasons = opa.may_remove_department_approver({
        "actor": {"id": identity["id"], "roles": identity["roles"]},
        "person": {"id": person_id, "permanent": bool(target and target["valid_until"] is None)},
        "department": {"name": department["name"], "approvers": approvers, "permanent_after": permanent_after},
        "reason": body.reason,
    })
    if not permitted:
        raise HTTPException(403, {"removed": False, "reasons": reasons})
    try:
        with db.cursor(commit=True) as cur:
            cur.execute("select id from department where id = %s for update", (department_id,))
            cur.execute(
                """update department_approver
                      set removed_by = %s, removed_at = now(), removal_reason = %s
                    where department_id = %s and person_id = %s and removed_at is null""",
                (identity["id"], body.reason.strip(), department_id, person_id),
            )
    except psycopg.errors.CheckViolation as exc:
        # The policy counted from what it read; the database is the guarantee when two changes race.
        raise HTTPException(409, {"removed": False, "reasons": [
            "a department always keeps at least one permanent approver, so this one cannot be removed until another is added"]}) from exc
    return {"department_id": str(department["id"]), "person_id": person_id, "removed": True}
