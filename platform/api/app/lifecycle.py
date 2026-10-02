"""Closing an organisation, and holding its records while a legal matter is open.

WHAT HAPPENS, IN ORDER

  1. Somebody with the standing to do it starts the retirement and says why. The
     organisation takes no more writes at once (the retired-tenant guard in
     platform/schema.sql), and two dates are set from the day it started.
  2. Retiring, 15 days by default. The organisation's people can still read what it
     holds, and a data custodian of the organisation, or a platform administrator,
     may cancel the retirement. Cancelling puts everything back as it was.
  3. Closing, another 15 days. The organisation's people can do nothing at all
     (auth.CLOSED_TO_PEOPLE). A platform administrator can act, and the only thing
     there is to do is place or lift a legal hold.
  4. Purge. When both periods have ended and no hold stands, everything inside the
     organisation is deleted (purge.py), and a small record says that it was.

The phase is worked out from the dates each time it is asked (`tenant_phase` in
the schema), so there is no timer whose failure would leave an organisation open
or closed by mistake.

A LEGAL HOLD, AND WHY TWO ADMINISTRATORS

A legal hold is an instruction from outside the platform, in practice from a court,
a regulator or the organisation's own lawyers, to keep an organisation's records
and not destroy them. It reaches a platform administrator in writing. The
platform does not decide whether a hold is justified. It records that one exists,
who recorded it, who approved it and on whose notice, and it refuses to purge while
one stands.

One administrator records the notice and a different one approves it, so no single
account can stop or allow a deletion on its own. A proposal also stops the purge
until it is decided or lapses (HOLD_APPROVAL_DAYS), because a purge that could land
between recording a hold and approving it would make the hold worthless.

A hold names a temporary custodian, the person who answers for the preserved records
while it stands, and that person acknowledges it. Releasing a hold restarts the
closing period, so a release made by mistake still leaves time to notice.

WHAT THIS DELIBERATELY DOES NOT DO

It does not let anybody read a held organisation's records, and it does not export
them. Preservation is the whole of what a hold does. Producing records to a court
is a separate act that takes place outside the platform.
"""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from . import auth, config, db, logs, opa, purge
from .auth import current_session, current_session_while_closing

log = logs.get_logger("lifecycle")

router = APIRouter(prefix="/lifecycle", tags=["lifecycle"])


def _refuse(reasons: list[str], status: int = 403):
    raise HTTPException(status, {"reasons": reasons})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(tenant_id: str, actor: str, event: str, hold_id: str | None = None,
           detail: dict | None = None) -> None:
    db.execute(
        "insert into lifecycle_event (tenant_id, hold_id, actor, event, detail) "
        "values (%s, %s, %s, %s, %s::jsonb)",
        (tenant_id, hold_id, actor, event, json.dumps(detail or {}, default=str)),
    )


def _is_admin(session: dict) -> bool:
    return "platform_admin" in session["roles"]


def _target(session: dict, tenant_id: str | None) -> str:
    """The organisation an action is about: the caller's own unless a platform
    administrator names another. Anybody else naming a different one is refused."""
    if tenant_id and tenant_id != session["tenant_id"] and not _is_admin(session):
        _refuse(["an organisation's closing is handled by its own people and by platform administrators"])
    return tenant_id or session["tenant_id"]


def _org_row(tenant_id: str) -> dict:
    row = db.one(
        """select t.id, t.purpose, t.note, t.retire_reason, t.retire_requested_by, t.retired_at,
                  t.retiring_until, t.closing_until, t.purged_at,
                  tenant_phase(t.id) as phase, tenant_hold_state(t.id) as hold,
                  d.label as requested_by_label
             from tenant t left join directory d on d.id = t.retire_requested_by
            where t.id = %s""",
        (tenant_id,),
    )
    if not row:
        _refuse([f"there is no organisation {tenant_id!r}"], status=404)
    return row


def _status(row: dict) -> dict:
    """What an organisation's people, and the console, are told about its closing."""
    now = _now()
    until = {"retiring": row["retiring_until"], "closing": row["closing_until"]}.get(row["phase"])
    return {
        "tenant_id": row["id"],
        "phase": row["phase"],
        "hold": row["hold"],
        "retire_reason": row["retire_reason"],
        "requested_by": row["requested_by_label"],
        "retired_at": row["retired_at"],
        "retiring_until": row["retiring_until"],
        "closing_until": row["closing_until"],
        "phase_ends_at": until,
        "days_left": max(0, math.ceil((until - now).total_seconds() / 86400)) if until else None,
        "can_cancel": row["phase"] == "retiring",
        "purged_at": row["purged_at"],
    }


def _check(allowed_and_reasons: tuple[bool, list[str]]) -> None:
    allowed, reasons = allowed_and_reasons
    if not allowed:
        _refuse(reasons)


# ---------------------------------------------------------------- status --


@router.get("/organisation")
def organisation_status(tenant_id: str | None = Query(default=None),
                        session: dict = Depends(current_session_while_closing)) -> dict:
    """Where one organisation is in its closing. Open to its own people even while
    it is closing, because it is how they find out why they can do nothing."""
    target = _target(session, tenant_id)
    _check(opa.may_see_lifecycle({
        "scope": "tenant", "tenant_id": target,
        "viewer": {"id": session["id"], "tenant_id": session["tenant_id"], "roles": session["roles"]},
    }))
    return _status(_org_row(target))


@router.get("/organisations")
def organisations(legacy: bool = Query(default=False),
                  session: dict = Depends(current_session)) -> dict:
    """Every organisation and where it is, for a platform administrator.

    Dates and states only, never contents. An organisation closed before closing
    had dates (nothing then deletes it) is left out unless `legacy` is set.
    """
    _check(opa.may_see_lifecycle({
        "scope": "platform",
        "viewer": {"id": session["id"], "tenant_id": session["tenant_id"], "roles": session["roles"]},
    }))
    rows = db.all_rows(
        """select t.id, t.purpose, t.note, t.retire_reason, t.retire_requested_by, t.retired_at,
                  t.retiring_until, t.closing_until, t.purged_at,
                  tenant_phase(t.id) as phase, tenant_hold_state(t.id) as hold,
                  d.label as requested_by_label
             from tenant t left join directory d on d.id = t.retire_requested_by
            where t.purpose in ('production', 'retired')
            order by t.id"""
    )
    shown = [_status(r) | {"note": r["note"], "purpose": r["purpose"]}
             for r in rows if legacy or r["phase"] != "retired"]
    return {"organisations": shown,
            "periods": {"retiring_days": config.RETIRING_DAYS, "closing_days": config.CLOSING_DAYS}}


@router.get("/deletions")
def deletions(session: dict = Depends(current_session)) -> dict:
    """What was left behind by each purge, for a platform administrator."""
    _check(opa.may_see_lifecycle({
        "scope": "platform",
        "viewer": {"id": session["id"], "tenant_id": session["tenant_id"], "roles": session["roles"]},
    }))
    rows = db.all_rows("select * from tenant_deletion_record order by purged_at desc")
    return {"deletions": json.loads(json.dumps(rows, default=str))}


@router.get("/events")
def events(tenant_id: str = Query(...), session: dict = Depends(current_session_while_closing)) -> dict:
    """What happened to an organisation's closing, newest first."""
    target = _target(session, tenant_id)
    _check(opa.may_see_lifecycle({
        "scope": "tenant", "tenant_id": target,
        "viewer": {"id": session["id"], "tenant_id": session["tenant_id"], "roles": session["roles"]},
    }))
    rows = db.all_rows(
        "select at, actor, event, hold_id, detail from lifecycle_event "
        "where tenant_id = %s order by at desc, id desc limit 200", (target,))
    return {"events": json.loads(json.dumps(rows, default=str))}


# ------------------------------------------------------------ retirement --


class RetireIn(BaseModel):
    tenant_id: str | None = None
    reason: str


class CancelIn(BaseModel):
    tenant_id: str | None = None


@router.post("/organisation/retire")
def retire(body: RetireIn, session: dict = Depends(current_session)) -> dict:
    """Start closing an organisation. Nothing is deleted yet: its people can read
    for the retiring period and cancel, then there is a closing period after that."""
    target = _target(session, body.tenant_id)
    _check(opa.may_retire({
        "actor": {"id": session["id"], "tenant_id": session["tenant_id"], "roles": session["roles"]},
        "organisation": target, "reason": body.reason,
    }))
    row = _org_row(target)
    if row["purpose"] != "production":
        _refuse([f"{target} is a {row['purpose']} organisation. Only a customer organisation is "
                 "closed this way"], status=409)
    now = _now()
    retiring_until = now + timedelta(days=config.RETIRING_DAYS)
    closing_until = retiring_until + timedelta(days=config.CLOSING_DAYS)
    done = db.execute(
        """update tenant set purpose = 'retired', retire_requested_by = %s, retire_reason = %s,
                  retired_at = %s, retiring_until = %s, closing_until = %s
            where id = %s and purpose = 'production' returning id""",
        (session["id"], body.reason.strip(), now, retiring_until, closing_until, target),
    )
    if not done:
        _refuse([f"{target} was already closing down"], status=409)
    _event(target, session["id"], "retirement_started",
           detail={"reason": body.reason.strip(), "retiring_until": retiring_until,
                   "closing_until": closing_until})
    log.info("organisation retirement started", extra={"tenant_id": target})
    return _status(_org_row(target))


@router.post("/organisation/cancel")
def cancel(body: CancelIn, session: dict = Depends(current_session)) -> dict:
    """Cancel a retirement while the retiring period is still running."""
    target = _target(session, body.tenant_id)
    _check(opa.may_cancel_retirement({
        "actor": {"id": session["id"], "tenant_id": session["tenant_id"], "roles": session["roles"]},
        "organisation": target,
    }))
    row = _org_row(target)
    if row["phase"] != "retiring":
        ended = f"{row['retiring_until']:%Y-%m-%d}" if row["retiring_until"] else ""
        why = {
            "active": f"{target} is not closing down",
            "retired": f"{target} was closed before closing dates were recorded, so it cannot be cancelled here",
            "closing": f"the time to cancel ended on {ended}",
            "purge_due": f"the time to cancel ended on {ended}",
            "purged": f"{target} has been deleted",
        }[row["phase"]]
        _refuse([why], status=409)
    done = db.execute(
        """update tenant set purpose = 'production', retire_requested_by = null, retire_reason = null,
                  retired_at = null, retiring_until = null, closing_until = null
            where id = %s and purpose = 'retired' and now() < retiring_until returning id""",
        (target,),
    )
    if not done:
        _refuse(["the time to cancel ended while this was being sent"], status=409)
    _event(target, session["id"], "retirement_cancelled", detail={"was_requested_by": row["retire_requested_by"]})
    log.info("organisation retirement cancelled", extra={"tenant_id": target})
    return _status(_org_row(target))


# ------------------------------------------------------------ legal holds --


class HoldIn(BaseModel):
    tenant_id: str
    matter_name: str
    matter_number: str
    description: str
    triggering_event: str
    issuing_authority: str
    authority_reference: str
    attorney_name: str
    attorney_email: str
    notice_received_on: date
    preserve: str
    data_from: date | None = None
    data_to: date | None = None
    custodian_id: str


class DecideIn(BaseModel):
    approve: bool
    note: str = ""


class ReleaseIn(BaseModel):
    reason: str


_HOLD_COLUMNS = """h.id, h.tenant_id, h.status, h.matter_name, h.matter_number, h.description,
       h.triggering_event, h.issuing_authority, h.authority_reference, h.attorney_name,
       h.attorney_email, h.notice_received_on, h.preserve, h.data_from, h.data_to,
       h.custodian_id, c.label as custodian_label, h.custodian_acknowledged_at,
       h.placed_by, p.label as placed_by_label, h.placed_at, h.expires_unapproved_at,
       h.decided_by, dd.label as decided_by_label, h.decided_at, h.decision_note, h.review_due_on,
       h.released_by, rr.label as released_by_label, h.released_at, h.release_reason"""
_HOLD_FROM = """from legal_hold h
       join directory c on c.id = h.custodian_id
       join directory p on p.id = h.placed_by
       left join directory dd on dd.id = h.decided_by
       left join directory rr on rr.id = h.released_by"""


def _hold(hold_id: str) -> dict:
    try:
        uuid.UUID(hold_id)
    except ValueError:
        _refuse(["that is not a hold id"], status=404)
    row = db.one(f"select {_HOLD_COLUMNS} {_HOLD_FROM} where h.id = %s", (hold_id,))
    if not row:
        _refuse(["there is no such hold"], status=404)
    return row


def _clean(rows):
    return json.loads(json.dumps(rows, default=str))


@router.get("/holds")
def holds(tenant_id: str | None = Query(default=None),
          session: dict = Depends(current_session_while_closing)) -> dict:
    """Holds for a platform administrator, or the holds a custodian answers for.

    A hold carries the notice's contact details, so nobody else sees it."""
    if _is_admin(session):
        if tenant_id:
            rows = db.all_rows(f"select {_HOLD_COLUMNS} {_HOLD_FROM} where h.tenant_id = %s "
                               "order by h.placed_at desc", (tenant_id,))
        else:
            rows = db.all_rows(f"select {_HOLD_COLUMNS} {_HOLD_FROM} order by h.placed_at desc")
    else:
        rows = db.all_rows(f"select {_HOLD_COLUMNS} {_HOLD_FROM} where h.custodian_id = %s "
                           "order by h.placed_at desc", (session["id"],))
    return {"holds": _clean(rows)}


@router.post("/holds", status_code=201)
def place_hold(body: HoldIn, session: dict = Depends(current_session)) -> dict:
    """Record a legal hold on a whole organisation. It takes effect when a different
    platform administrator approves it, and in the meantime it already stops a purge."""
    payload = body.model_dump(mode="json")
    _check(opa.may_place_hold({
        "actor": {"id": session["id"], "tenant_id": session["tenant_id"], "roles": session["roles"]},
        "hold": payload,
    }))
    org = _org_row(body.tenant_id)
    if org["phase"] == "purged":
        _refuse([f"{body.tenant_id} has already been deleted, so there is nothing left to hold"], status=409)
    custodian = db.one("select id, kind, ended_at from directory where id = %s", (body.custodian_id,))
    if not custodian or custodian["kind"] != "human" or custodian["ended_at"]:
        _refuse([f"{body.custodian_id!r} is not a registered person who can act as custodian"], status=422)
    if db.one("""select 1 as x from legal_hold where tenant_id = %s and matter_number = %s
                    and status in ('proposed', 'active')""", (body.tenant_id, body.matter_number)):
        _refuse([f"a hold for matter {body.matter_number} is already open on {body.tenant_id}"], status=409)
    hold_id = str(uuid.uuid4())
    now = _now()
    db.execute(
        """insert into legal_hold (id, tenant_id, status, matter_name, matter_number, description,
                  triggering_event, issuing_authority, authority_reference, attorney_name, attorney_email,
                  notice_received_on, preserve, data_from, data_to, custodian_id, placed_by, placed_at,
                  expires_unapproved_at, review_due_on)
           values (%s, %s, 'proposed', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           returning id""",
        (hold_id, body.tenant_id, body.matter_name.strip(), body.matter_number.strip(),
         body.description.strip(), body.triggering_event.strip(), body.issuing_authority.strip(),
         body.authority_reference.strip(), body.attorney_name.strip(), body.attorney_email.strip(),
         body.notice_received_on, body.preserve.strip(), body.data_from, body.data_to,
         body.custodian_id, session["id"], now,
         now + timedelta(days=config.HOLD_APPROVAL_DAYS),
         (now + timedelta(days=config.HOLD_REVIEW_DAYS)).date()),
    )
    _event(body.tenant_id, session["id"], "hold_placed", hold_id,
           {"matter_number": body.matter_number, "authority_reference": body.authority_reference})
    log.info("legal hold recorded", extra={"tenant_id": body.tenant_id})
    return _clean(_hold(hold_id))


@router.post("/holds/{hold_id}/decide")
def decide_hold(hold_id: str, body: DecideIn, session: dict = Depends(current_session)) -> dict:
    """Approve or decline a hold that a different platform administrator recorded."""
    hold = _hold(hold_id)
    _check(opa.may_decide_hold({
        "actor": {"id": session["id"], "tenant_id": session["tenant_id"], "roles": session["roles"]},
        "hold": {"placed_by": hold["placed_by"]},
    }))
    if hold["status"] != "proposed":
        _refuse([f"this hold is already {hold['status']}"], status=409)
    if not body.approve and not body.note.strip():
        _refuse(["declining a hold needs a note saying why"], status=422)
    if hold["expires_unapproved_at"] <= _now():
        sweep()
        _refuse(["this proposal waited too long for a second administrator and has lapsed. "
                 "Record the notice again"], status=409)
    status = "active" if body.approve else "declined"
    done = db.execute(
        """update legal_hold set status = %s, decided_by = %s, decided_at = now(), decision_note = %s,
                  review_due_on = %s
            where id = %s and status = 'proposed' returning id""",
        (status, session["id"], body.note.strip() or None,
         (_now() + timedelta(days=config.HOLD_REVIEW_DAYS)).date(), hold_id),
    )
    if not done:
        _refuse(["this hold was decided while this was being sent"], status=409)
    _event(hold["tenant_id"], session["id"], "hold_approved" if body.approve else "hold_declined",
           hold_id, {"note": body.note.strip()})
    return _clean(_hold(hold_id))


@router.post("/holds/{hold_id}/acknowledge")
def acknowledge_hold(hold_id: str, session: dict = Depends(current_session_while_closing)) -> dict:
    """The temporary custodian confirms that they have read the notice and will answer for the records."""
    hold = _hold(hold_id)
    if hold["custodian_id"] != session["id"]:
        _refuse([f"this hold names {hold['custodian_label']} as custodian, and only they acknowledge it"])
    if hold["status"] != "active":
        _refuse([f"a hold is acknowledged once it is approved, and this one is {hold['status']}"], status=409)
    db.execute("update legal_hold set custodian_acknowledged_at = coalesce(custodian_acknowledged_at, now()) "
               "where id = %s returning id", (hold_id,))
    _event(hold["tenant_id"], session["id"], "hold_acknowledged", hold_id)
    return _clean(_hold(hold_id))


@router.post("/holds/{hold_id}/release")
def release_hold(hold_id: str, body: ReleaseIn, session: dict = Depends(current_session)) -> dict:
    """End a hold that is in force. The closing period starts again, so a release
    made by mistake still leaves time before anything is deleted."""
    hold = _hold(hold_id)
    _check(opa.may_release_hold({
        "actor": {"id": session["id"], "tenant_id": session["tenant_id"], "roles": session["roles"]},
        "reason": body.reason,
    }))
    if hold["status"] != "active":
        _refuse([f"only a hold in force is released, and this one is {hold['status']}"], status=409)
    done = db.execute(
        """update legal_hold set status = 'released', released_by = %s, released_at = now(),
                  release_reason = %s
            where id = %s and status = 'active' returning id""",
        (session["id"], body.reason.strip(), hold_id),
    )
    if not done:
        _refuse(["this hold was released while this was being sent"], status=409)
    restarted = db.one(
        """update tenant set closing_until = greatest(closing_until, now() + make_interval(days => %s))
            where id = %s and purpose = 'retired' and closing_until is not null and purged_at is null
        returning closing_until""",
        (config.CLOSING_DAYS, hold["tenant_id"]),
    )
    _event(hold["tenant_id"], session["id"], "hold_released", hold_id,
           {"reason": body.reason.strip(),
            "closing_until": restarted["closing_until"] if restarted else None})
    return _clean(_hold(hold_id))


# ------------------------------------------------------------------ sweep --


def sweep(purged_by: str = "the scheduled sweep") -> dict:
    """One pass: let proposals that waited too long lapse, then purge every
    organisation whose time is up and that no hold stands over."""
    lapsed = db.all_rows(
        """update legal_hold set status = 'lapsed'
            where status = 'proposed' and expires_unapproved_at <= now()
        returning id, tenant_id""")
    for row in lapsed:
        _event(row["tenant_id"], "the platform", "hold_lapsed", str(row["id"]),
               {"reason": "no second administrator decided it in time"})
    due = [r["id"] for r in db.all_rows(
        "select id from tenant where purged_at is null and purpose = 'retired' "
        "and retiring_until is not null and tenant_purge_allowed(id)")]
    purged = []
    for tenant_id in due:
        try:
            purged.append(purge.purge_tenant(tenant_id, purged_by))
        except Exception as exc:
            # Nothing was deleted from the database (it is one transaction), and the
            # next sweep tries again. The reason is recorded where the organisation's
            # administrators can see it.
            log.exception("purge failed", extra={"tenant_id": tenant_id})
            _event(tenant_id, "the platform", "purge_failed", None, {"reason": str(exc)[:500]})
    audit_removed = purge.expire_audit()
    return {"lapsed": [str(r["id"]) for r in lapsed],
            "purged": [p["tenant_id"] for p in purged], "records": _clean(purged),
            "audit_removed": _clean(audit_removed)}


@router.post("/sweep")
def run_sweep(session: dict = Depends(current_session)) -> dict:
    """Do now what the timer does every few minutes. For a platform administrator."""
    _check(opa.may_see_lifecycle({
        "scope": "platform",
        "viewer": {"id": session["id"], "tenant_id": session["tenant_id"], "roles": session["roles"]},
    }))
    return sweep(purged_by=f"a sweep run by {session['label']}")


async def run_forever() -> None:
    """The timer. Everything it decides is read from the database each pass, so a
    restart loses nothing."""
    if config.LIFECYCLE_SWEEP_SECONDS <= 0:
        return
    while True:
        try:
            await asyncio.to_thread(sweep)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("lifecycle sweep failed; trying again next time")
        await asyncio.sleep(config.LIFECYCLE_SWEEP_SECONDS)
