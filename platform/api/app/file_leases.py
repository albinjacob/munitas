"""Leases on real files: a person opens a few of a version's locked files for a few hours, for a stated purpose.

A person asks (a kind, a purpose, how many files, for how many hours). An approver of the department that owns the files, who is not that person,
decides, and may grant less than was asked for, never more. The platform draws the files, by the same method as a development sample, and keeps the
list with the salt, so the draw can be reproduced and shown not to have been hand-picked. The list is fixed: the lease opens those files and no
others, and they are never drawn again.

The file door (`record_door`) asks `active` for the lease a person holds on a version, and so what the person may list and open is worked out again on
every request: a lease that ends, or runs out, stops what is playing at its next request.

A lease is not a development sample, and nothing here makes it one. A sample lets a pipeline version that is still in development run on real files;
a lease lets a person open them. Which a person holds is decided by `development_samples.is_development_data`, which looks only at samples and
synthetic declarations, so holding a lease never lets code that nobody has approved run on real files.

The kind is the way the files are opened. A `console` lease is for listening in the page; a `notebook` lease is for reading through the client library
and is used only there. The door is told which way it is being asked, and a lease of the other kind is not in force.
"""

from __future__ import annotations

import json
import uuid
from datetime import timezone

import pgdb

from . import access_rules, db, development_samples, opa
from .locked_leases import approvers_of
from .person_errors import LeaseConflict, NoSuchLease, NoSuchVersion, NotPermitted, Refused

KINDS = ("console", "notebook")
# The ways the platform can open files today. A kind that cannot be used yet cannot be asked for, so no lease is granted that nothing can honour.
ENABLED_KINDS = ("console", "notebook")
COLUMNS = ("id", "dataset_version_id", "principal", "kind", "purpose", "requested_count", "hours", "status", "granted_count", "granted_hours",
           "records", "method", "salt", "strata", "decided_by", "decided_at", "decision_note", "expires_at", "ended_by", "ended_at", "requested_at")
METHOD = development_samples.METHOD


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _shape(row: dict) -> dict:
    out = {k: row[k] for k in COLUMNS}
    for k in ("id", "dataset_version_id"):
        out[k] = str(out[k])
    for k in ("decided_at", "expires_at", "ended_at", "requested_at"):
        out[k] = _stamp(out[k])
    out["records"] = out["records"] or []
    out["strata"] = out["strata"] or []
    return out


def _labels(rows: list[dict]) -> list[dict]:
    """Each lease with the people it names as people, and the table it is about by name and version, so a screen shows neither an identifier."""
    ids = {v for r in rows for v in (r["principal"], r["decided_by"], r["ended_by"]) if v}
    names = {r["id"]: r["label"] for r in db.all_rows("select id, label from directory where id = any(%s)", (list(ids),))} if ids else {}
    version_ids = list({r["dataset_version_id"] for r in rows})
    tables = {str(r["id"]): r for r in db.all_rows(
        "select v.id, d.name, v.version from dataset_version v join dataset d on d.id = v.dataset_id where v.id = any(%s::uuid[])", (version_ids,))} if version_ids else {}
    return [{**r, "principal_label": names.get(r["principal"], r["principal"]),
             "decided_by_label": names.get(r["decided_by"]) if r["decided_by"] else None,
             "ended_by_label": names.get(r["ended_by"]) if r["ended_by"] else None,
             "dataset_name": tables.get(r["dataset_version_id"], {}).get("name"),
             "dataset_version": tables.get(r["dataset_version_id"], {}).get("version")} for r in rows]


def sweep() -> int:
    """Mark the leases that have run out as ended. What a lease opens never depends on this: the door checks the time itself."""
    rows = db.all_rows("update file_lease set status = 'ended', ended_at = expires_at where status = 'approved' and expires_at <= now() returning id")
    return len(rows)


def _version(version_id: str, tenant_id: str) -> dict:
    row = db.one("select v.id, v.tenant_id, d.department_id from dataset_version v join dataset d on d.id = v.dataset_id where v.id = %s", (version_id,))
    if not row or row["tenant_id"] != tenant_id:
        raise NoSuchVersion(version_id)
    return row


def _lease(lease_id: str, tenant_id: str) -> dict:
    row = db.one(
        """select l.*, d.department_id from file_lease l
             join dataset_version v on v.id = l.dataset_version_id join dataset d on d.id = v.dataset_id
            where l.id = %s""", (lease_id,))
    if not row or row["tenant_id"] != tenant_id:
        raise NoSuchLease(lease_id)
    return row


def active(person_id: str, tenant_id: str, version_id: str, via: str = "console") -> dict | None:
    """The lease this person holds on this version that is in force for the way the files are being opened, or None. Worked out from the time now."""
    return db.one(
        """select * from file_lease
            where tenant_id = %s and principal = %s and dataset_version_id = %s and kind = %s and status = 'approved' and expires_at > now()""",
        (tenant_id, person_id, version_id, via))


def leased_records(person_id: str, tenant_id: str, version_id: str, via: str = "console") -> list[str] | None:
    """The files a person's lease on this version opens, or None when they hold none in force."""
    found = active(person_id, tenant_id, version_id, via)
    return list(found["records"] or []) if found else None


def request_lease(identity: dict, version_id: str, kind: str, purpose: str, records: int, hours: int) -> dict:
    sweep()
    version = _version(version_id, identity["tenant_id"])
    if kind in KINDS and kind not in ENABLED_KINDS:
        raise Refused([f"a lease for a {kind} cannot be asked for yet"])
    allowed, reasons = opa.may_request_file_lease({
        "requester": {"id": identity["id"], "roles": identity["roles"]}, "limits": access_rules.limits_for(identity["tenant_id"]),
        "request": {"principal": identity["id"], "kind": kind, "purpose": purpose, "hours": hours, "records": records}})
    if not allowed:
        raise NotPermitted(reasons)
    if not any(not c["erased"] for c in development_samples._candidates(version_id)):
        raise Refused(["this version holds no files that can be opened: it has none, or every one is about a person who was erased"])
    if not approvers_of(version["department_id"]):
        raise Refused(["this data belongs to no department, so there is nobody who can decide a request to open it"])
    try:
        row = db.execute(
            """insert into file_lease (id, tenant_id, dataset_version_id, principal, kind, purpose, requested_count, hours)
               values (%s, %s, %s, %s, %s, %s, %s, %s) returning *""",
            (str(uuid.uuid4()), identity["tenant_id"], version_id, identity["id"], kind, purpose.strip(), records, hours))
    except pgdb.errors.UniqueViolation as exc:
        raise LeaseConflict([f"you already have a lease for opening these files in the {kind}, waiting or in force"]) from exc
    return _labels([_shape(row)])[0]


def decide(identity: dict, lease_id: str, approve: bool, note: str | None, records: int | None = None, hours: int | None = None) -> dict:
    sweep()
    lease = _lease(lease_id, identity["tenant_id"])
    allowed, reasons = opa.may_decide_file_lease({
        "approver": {"id": identity["id"], "roles": identity["roles"]}, "request": {"principal": lease["principal"]},
        "dataset": {"approvers": approvers_of(lease["department_id"])}})
    if not allowed:
        raise NotPermitted(reasons)
    if lease["status"] != "requested":
        raise LeaseConflict([f"this request was already decided ({lease['status']})"])
    if not approve:
        if not (note or "").strip():
            raise Refused(["say why, in a note, when declining: it is recorded with the decision"])
        row = db.execute("update file_lease set status = 'declined', decided_by = %s, decided_at = now(), decision_note = %s "
                         "where id = %s and status = 'requested' returning *", (identity["id"], note, lease_id))
        if row is None:
            raise LeaseConflict(["this request was decided a moment ago by somebody else"])
        return _labels([_shape(row)])[0]
    count = records if records is not None else lease["requested_count"]
    length = hours if hours is not None else lease["hours"]
    if not 1 <= count <= lease["requested_count"] or not 1 <= length <= lease["hours"]:
        raise Refused([f"an approver may lower what was asked for, never raise it: {lease['requested_count']} files for {lease['hours']} hours was asked for"])
    version_id = str(lease["dataset_version_id"])
    candidates = [c for c in development_samples._candidates(version_id) if not c["erased"]]
    salt = development_samples.salt_for(version_id, lease["principal"], f"lease:{lease['kind']}")
    drawn = development_samples.draw(candidates, count, salt)
    if not drawn["chosen"]:
        raise Refused(["nothing is left to give: every file in this version is about a person who was erased"])
    row = db.execute(
        """update file_lease
              set status = 'approved', decided_by = %s, decided_at = now(), decision_note = %s, granted_count = %s, granted_hours = %s,
                  expires_at = now() + make_interval(hours => %s), method = %s, salt = %s, strata = %s, records = %s
            where id = %s and status = 'requested' returning *""",
        (identity["id"], note, len(drawn["chosen"]), length, length, METHOD, salt, json.dumps(drawn["strata"]), json.dumps(drawn["chosen"]), lease_id))
    if row is None:
        raise LeaseConflict(["this request was decided a moment ago by somebody else"])
    return _labels([_shape(row)])[0]


def end(identity: dict, lease_id: str) -> dict:
    lease = _lease(lease_id, identity["tenant_id"])
    allowed, reasons = opa.may_end_file_lease({
        "actor": {"id": identity["id"], "roles": identity["roles"]}, "request": {"principal": lease["principal"]},
        "dataset": {"approvers": approvers_of(lease["department_id"])}})
    if not allowed:
        raise NotPermitted(reasons)
    row = db.execute("update file_lease set status = 'ended', ended_by = %s, ended_at = now() where id = %s and status in ('requested', 'approved') returning *",
                     (identity["id"], lease_id))
    if row is None:
        raise LeaseConflict(["this lease has ended already"])
    return _labels([_shape(row)])[0]


def lists(identity: dict) -> dict:
    """The person's own leases, the requests waiting for them to decide as an approver of the department that owns the files, and the leases in force
    in the departments they answer for."""
    sweep()
    tenant, me = identity["tenant_id"], identity["id"]
    mine = [_shape(r) for r in db.all_rows(
        "select * from file_lease where tenant_id = %s and principal = %s order by requested_at desc limit 100", (tenant, me))]
    cache: dict = {}

    def mine_to_decide(r: dict) -> bool:
        dept = str(r["department_id"]) if r["department_id"] else None
        if dept not in cache:
            cache[dept] = approvers_of(dept)
        return me in cache[dept]

    waiting = [_shape(r) for r in db.all_rows(
        """select l.*, d.department_id from file_lease l
             join dataset_version v on v.id = l.dataset_version_id join dataset d on d.id = v.dataset_id
            where l.tenant_id = %s and l.status = 'requested' and l.principal <> %s order by l.requested_at limit 200""", (tenant, me)) if mine_to_decide(r)]
    granted = [_shape(r) for r in db.all_rows(
        """select l.*, d.department_id from file_lease l
             join dataset_version v on v.id = l.dataset_version_id join dataset d on d.id = v.dataset_id
            where l.tenant_id = %s and l.status = 'approved' and l.expires_at > now() order by l.expires_at limit 200""", (tenant,)) if mine_to_decide(r)]
    return {"mine": _labels(mine), "to_decide": _labels(waiting), "granted": _labels(granted)}


def for_version(identity: dict, version_id: str) -> dict:
    """What a version's page needs about leases on its files: this person's own lease on it (waiting or in force), the limits, and who would decide."""
    sweep()
    version = _version(version_id, identity["tenant_id"])
    own = db.all_rows(
        """select * from file_lease where tenant_id = %s and principal = %s and dataset_version_id = %s and status in ('requested', 'approved')
              and (status = 'requested' or expires_at > now()) order by requested_at desc""", (identity["tenant_id"], identity["id"], version_id))
    approvers = approvers_of(version["department_id"])
    limits = access_rules.limits_for(identity["tenant_id"])
    caps = {"console": {"records": limits["files_in_page"]["most_files"], "hours": limits["files_in_page"]["most_hours"]},
            "notebook": {"records": limits["files_in_notebook"]["most_files"], "hours": limits["files_in_notebook"]["most_hours"]}}
    return {"version_id": version_id, "leases": _labels([_shape(r) for r in own]), "kinds": list(ENABLED_KINDS), "caps": {k: caps[k] for k in ENABLED_KINDS if k in caps},
            "has_approver": bool(approvers), "approver_count": len(approvers)}
