"""Who holds which role: asking for one, deciding somebody else's, ending one.

Roles used to change only by editing Postgres by hand, and the obvious fix was
an administration screen. It is the wrong fix, and the reason is written in the
platform's own policy: `role_floor` says the administrator "runs the system,
holds no standing access to its contents". That sentence stops being true the
moment one account can add `data_custodian` to a row, and an audit entry does
not restore it. An audit says what happened afterwards; this is a claim about
what is possible.

So a role is asked for by the person who would hold it, decided by somebody
else, and lapses. Exactly what this platform already does for access to a
dataset, applied to the larger claim rather than the smaller one.

WHAT IS ENFORCED WHERE, AND WHY IT IS IN THREE PLACES

  the database   the approver is registered (foreign key), is not the
                 requester (check constraint), and `platform_admin` is not a
                 role any request or grant may name at all. A bug in this
                 file cannot write those rows.
  the policy     who may decide, whether a reason was given, and the same
                 separations again, so a caller is refused before a write is
                 attempted rather than after it fails.
  here           the ordering: a request is decided once, a grant follows a
                 decision, and an ended appointment stops somebody acting.

Three places for the same rule is not duplication going wrong. The constraint
is the guarantee, the policy is the explanation, and the explanation is what a
refused caller can act on.

WHAT THIS DELIBERATELY CANNOT DO

Create a person, delete one, reset a password, or grant `platform_admin`.
People come from the identity provider, which is the source of truth for who
exists and who may sign in; a second place that creates people is a second
record that disagrees. Passwords live there too, and doing resets here would
mean this service holding administrative credentials to the identity provider
while it already holds the envelope key and mints storage credentials for
every organisation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import db, logs, opa
from .auth import current_session

log = logs.get_logger("people")

router = APIRouter(prefix="/people", tags=["people"])

# How long a role is held before somebody has to decide again. Not forever,
# and the reason is the one this platform already learned about storage:
# anything nobody has to renew is something nobody ever reviews.
DEFAULT_DAYS = 90


def _refuse(reasons: list[str], status: int = 403):
    raise HTTPException(status, {"reasons": reasons})


def _person(directory_id: str) -> dict:
    row = db.one(
        "select id, tenant_id, label, roles, ended_at from directory where id = %s",
        (directory_id,),
    )
    if not row:
        _refuse([f"{directory_id!r} is not registered"], status=404)
    return row


class RoleRequest(BaseModel):
    role: str
    justification: str
    days: int = DEFAULT_DAYS


# 201, matching every other creation in this API: the lease request this is
# modelled on answers the same way, and a console handling both should not
# have to learn two conventions.
@router.post("/role-requests", status_code=201)
def ask_for_role(body: RoleRequest, session: dict = Depends(current_session)) -> dict:
    """Ask to hold a role, saying why.

    For yourself only. Asking on somebody else's behalf would put the reason
    in one person's words and the holding in another's, and the record is
    worth less for it.
    """
    permitted, reasons = opa.may_request_role({
        "requester": {"id": session["id"], "roles": session["roles"]},
        "request": {
            "principal": session["id"],
            "role": body.role,
            "justification": body.justification,
        },
    })
    if not permitted:
        _refuse(reasons)

    request_id = str(uuid.uuid4())
    db.execute(
        """insert into role_request
             (id, tenant_id, principal, role, justification, requested_days)
           values (%s, %s, %s, %s, %s, %s)""",
        (request_id, session["tenant_id"], session["id"], body.role,
         body.justification, body.days),
    )
    log.info("a role was asked for",
             extra={"principal": session["id"], "role": body.role,
                    "tenant_id": session["tenant_id"]})
    return {"id": request_id, "state": "pending"}


class Decision(BaseModel):
    outcome: str  # "approve" or "reject"
    reason: str


@router.post("/role-requests/{request_id}/decide")
def decide_role_request(
    request_id: str, body: Decision, session: dict = Depends(current_session)
) -> dict:
    """Decide somebody else's request.

    The grant is written in the same statement's transaction as the decision,
    so a request cannot end up approved with nothing granted. Re-deciding is
    refused by the database rather than by a check here: a rewrite rule turns
    an update of an already-decided request into nothing.
    """
    request = db.one("select * from role_request where id = %s", (request_id,))
    if not request:
        _refuse(["no such request"], status=404)
    if request["state"] != "pending":
        _refuse([f"this request was already {request['state']}"], status=409)

    permitted, reasons = opa.may_approve_role({
        "approver": {"id": session["id"], "roles": session["roles"]},
        "request": {"principal": request["principal"], "role": request["role"]},
    })
    if not permitted:
        _refuse(reasons)

    if body.outcome not in ("approve", "reject"):
        _refuse(["a decision is either approve or reject"], status=422)
    if not body.reason.strip():
        _refuse(["a decision needs a reason, which is recorded with it"], status=422)

    grant_id = None
    if body.outcome == "approve":
        grant_id = str(uuid.uuid4())
        expires = datetime.now(timezone.utc) + timedelta(days=request["requested_days"])
        db.execute(
            """insert into role_grant
                 (id, tenant_id, principal, role, approved_by, request_id,
                  expires_at)
               values (%s, %s, %s, %s, %s, %s, %s)""",
            (grant_id, request["tenant_id"], request["principal"],
             request["role"], session["id"], request_id, expires),
        )

    db.execute(
        """update role_request
              set state = %s, decided_by = %s, decided_at = now(),
                  grant_id = %s, justification = justification || %s
            where id = %s""",
        ("approved" if body.outcome == "approve" else "rejected",
         session["id"], grant_id, f"\n\nDecision: {body.reason}", request_id),
    )

    log.info("a role request was decided",
             extra={"principal": request["principal"], "role": request["role"],
                    "outcome": body.outcome, "tenant_id": request["tenant_id"]})
    return {"state": "approved" if body.outcome == "approve" else "rejected",
            "grant_id": grant_id}


@router.get("/roles")
def roles_held(session: dict = Depends(current_session)) -> dict:
    """Who holds what in this organisation, and when it was last confirmed.

    Scoped to the caller's own organisation without asking, because there is
    no cross-organisation question here: roles are held inside one.
    """
    grants = db.all_rows(
        """select g.id, g.principal, d.label, g.role, g.approved_by,
                  g.expires_at, g.attested_at, g.attested_by, g.revoked,
                  g.expires_at < now() as expired
             from role_grant g
             join directory d on d.id = g.principal
            where g.tenant_id = %s and not g.revoked
            order by g.expires_at""",
        (session["tenant_id"],),
    )
    pending = db.all_rows(
        """select r.id, r.principal, d.label, r.role, r.justification,
                  r.requested_days, r.created_at
             from role_request r
             join directory d on d.id = r.principal
            where r.tenant_id = %s and r.state = 'pending'
            order by r.created_at""",
        (session["tenant_id"],),
    )
    return {"held": grants, "pending": pending}


class Attestation(BaseModel):
    still_needed: bool
    reason: str = ""


@router.post("/roles/{grant_id}/attest")
def attest_role(
    grant_id: str, body: Attestation, session: dict = Depends(current_session)
) -> dict:
    """Confirm somebody still needs a role, or withdraw it.

    The part every platform skips, and the one an auditor tests. It renews
    nothing: confirming records that somebody looked, and saying no revokes.
    """
    grant = db.one("select * from role_grant where id = %s", (grant_id,))
    if not grant:
        _refuse(["no such grant"], status=404)

    permitted, reasons = opa.may_attest_role({
        "attester": {"id": session["id"], "roles": session["roles"]},
        "grant": {"principal": grant["principal"], "role": grant["role"]},
    })
    if not permitted:
        _refuse(reasons)

    if body.still_needed:
        db.execute(
            "update role_grant set attested_by = %s, attested_at = now() "
            "where id = %s",
            (session["id"], grant_id),
        )
    else:
        db.execute(
            "update role_grant set revoked = true, attested_by = %s, "
            "attested_at = now() where id = %s",
            (session["id"], grant_id),
        )

    log.info("a role was confirmed" if body.still_needed else "a role was withdrawn",
             extra={"principal": grant["principal"], "role": grant["role"],
                    "outcome": "kept" if body.still_needed else "withdrawn"})
    return {"still_needed": body.still_needed}


class Appointment(BaseModel):
    ended: bool
    reason: str


@router.post("/{directory_id}/appointment")
def end_appointment(
    directory_id: str, body: Appointment, session: dict = Depends(current_session)
) -> dict:
    """Stop somebody acting here, or let them act again.

    Removing somebody's ability to act is the safe direction: it grants
    nothing, so it does not need the two-person rule that granting does. It
    still refuses the caller's own id, because an account that can lock
    itself out is a support call waiting to happen and no security gain.

    This does not touch the identity provider, and that is deliberate rather
    than an omission. Signing in is that system's question: a person
    deactivated there cannot get a session at all, which `auth.py` already
    depends on. This is the other half, so that somebody who has left stops
    acting here even in the window before the identity provider is updated.
    """
    if directory_id == session["id"]:
        _refuse(["an account cannot end its own appointment"])
    if not body.reason.strip():
        _refuse(["ending an appointment needs a reason"], status=422)

    permitted, reasons = opa.may_attest_role({
        "attester": {"id": session["id"], "roles": session["roles"]},
        "grant": {"principal": directory_id, "role": "(appointment)"},
    })
    if not permitted:
        _refuse(reasons)

    person = _person(directory_id)
    db.execute(
        "update directory set ended_at = %s where id = %s",
        (datetime.now(timezone.utc) if body.ended else None, directory_id),
    )
    log.info("an appointment was ended" if body.ended else "an appointment was restored",
             extra={"principal": directory_id, "tenant_id": person["tenant_id"],
                    "outcome": "ended" if body.ended else "restored"})
    return {"id": directory_id, "ended": body.ended}
