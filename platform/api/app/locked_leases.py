"""Leases on locked columns, and the evidence of how locking was used.

The owning department's approvers read a table's locked columns in full. Anybody else sees real values only through a lease: a purpose,
an approver who is not the person asking, a short life and a row cap, each read logged. Who may ask, decide and end one is decided in
policy (`access.rego`), and this module asks it. It holds the lease's record and the one choice that is not policy: which rows a lease
opens.

The rows a lease opens are fixed, and they are the same on every read and under every later lease for the same person and table. They are
the first rows in the order of a hash of the table, the person and the row's id: a spread that looks arbitrary, needs nothing stored, and
cannot be changed by reading again. A person who could draw a fresh sample each time would collect the whole table.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import timezone

import pgdb

from . import db, opa
from . import limits as org_limits
from .person_errors import LeaseConflict, NoSuchLease, NoSuchVersion, NotPermitted

COLUMNS = ("id", "tenant_id", "principal", "dataset_version_id", "purpose", "days", "row_limit", "status", "requested_at",
           "decided_by", "decided_at", "decision_note", "expires_at", "revoked_by", "revoked_at")


def approvers_of(department_id: object) -> list[str]:
    """The people who answer for a department's data right now. Empty for data with no department, which nobody then answers for."""
    if not department_id:
        return []
    return list(db.one("select active_department_approvers(%s::uuid) as a", (str(department_id),))["a"])


def _version(version_id: str, tenant_id: str) -> dict:
    row = db.one(
        """select v.id, v.tenant_id, v.schema_id, d.department_id
             from dataset_version v join dataset d on d.id = v.dataset_id
            where v.id = %s""", (version_id,))
    if not row or row["tenant_id"] != tenant_id:
        raise NoSuchVersion(version_id)
    if not db.one("select 1 as x from privacy_contract where tenant_id = %s and schema_id = %s", (tenant_id, row["schema_id"])):
        raise NoSuchVersion(f"{version_id} was not sealed under a privacy contract")
    return row


def _labels(rows: list[dict]) -> list[dict]:
    """Each lease with the people it names as people: the label the directory holds, so a screen need not show an identifier."""
    ids = {v for r in rows for v in (r["principal"], r["decided_by"], r["revoked_by"]) if v}
    names = {r["id"]: r["label"] for r in db.all_rows("select id, label from directory where id = any(%s)", (list(ids),))} if ids else {}
    # The table each lease is about, by name and version, so a list of leases says which table without opening each one.
    version_ids = list({str(r["dataset_version_id"]) for r in rows if r.get("dataset_version_id")})
    tables = {str(r["id"]): r for r in db.all_rows(
        "select v.id, d.name, v.version from dataset_version v join dataset d on d.id = v.dataset_id where v.id = any(%s::uuid[])", (version_ids,))} if version_ids else {}
    return [{**r, "principal_label": names.get(r["principal"], r["principal"]),
             "decided_by_label": names.get(r["decided_by"]) if r["decided_by"] else None,
             "revoked_by_label": names.get(r["revoked_by"]) if r["revoked_by"] else None,
             "dataset_name": tables.get(str(r["dataset_version_id"]), {}).get("name"),
             "dataset_version": tables.get(str(r["dataset_version_id"]), {}).get("version")} for r in rows]


def _shape(row: dict) -> dict:
    out = {k: row[k] for k in COLUMNS}
    for k in ("id", "dataset_version_id"):
        out[k] = str(out[k])
    for k in ("requested_at", "decided_at", "expires_at", "revoked_at"):
        out[k] = out[k].astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if out[k] else None
    return out


def current_lease(tenant_id: str, principal: str, version_id: str) -> dict | None:
    """This person's approved lease on this table that has not ended and has not run out, or None."""
    return db.one(
        """select * from locked_column_lease
            where tenant_id = %s and principal = %s and dataset_version_id = %s
              and status = 'approved' and expires_at > now()
            order by expires_at desc limit 1""", (tenant_id, principal, version_id))


def policy_view(lease: dict | None) -> dict | None:
    """The lease as the policy reads it, with the time in the form it compares."""
    if lease is None:
        return None
    return {"principal": lease["principal"], "revoked": lease["status"] == "revoked",
            "expires_at": lease["expires_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}


def sample_ids(version_id: str, principal: str, record_ids: list[str], limit: int) -> list[str]:
    """The rows a lease opens: the same ones every time for this person and this table."""
    def rank(record_id: str) -> str:
        return hashlib.sha256(f"{version_id}|{principal}|{record_id}".encode("utf-8")).hexdigest()
    return sorted(record_ids, key=rank)[:limit]


def locking_summary(identity: dict, version_id: str) -> dict:
    """What a screen needs to show a locked table to this person: its columns, what they may do, their lease, and the limits.

    Asks the policy the questions the routes ask and records nothing, because looking at a screen is not reading a value. Never a value.
    """
    from . import privacy_contract
    version = _version(version_id, identity["tenant_id"])
    contract = db.one("select document from privacy_contract where tenant_id = %s and schema_id = %s", (identity["tenant_id"], version["schema_id"]))
    held = current_lease(identity["tenant_id"], identity["id"], version_id)
    waiting = db.one("select * from locked_column_lease where principal = %s and dataset_version_id = %s and status = 'requested'", (identity["id"], version_id))
    decision = opa.locked_columns({"person": {"id": identity["id"], "roles": identity["roles"]},
                                   "dataset": {"approvers": approvers_of(version["department_id"])}, "lease": policy_view(held)})
    limits = org_limits.limits_for(identity["tenant_id"])
    may_ask, _ = opa.may_request_locked_lease({"requester": {"id": identity["id"], "roles": identity["roles"]}, "limits": limits,
                                               "request": {"principal": identity["id"], "purpose": "x", "days": 1, "rows": 1}})
    both = _labels([_shape(r) for r in (held, waiting) if r]) if (held or waiting) else []
    shaped = {r["id"]: r for r in both}
    return {
        "version_id": version_id,
        "records": db.one("select record_count from dataset_version where id = %s", (version_id,))["record_count"],
        "columns": [{"name": c["name"], "classification": c["classification"], "treatment": c["treatment"]}
                    for c in privacy_contract.columns(contract["document"])],
        "mode": decision.get("mode", "none") if decision.get("allow") else "none",
        # Somebody who already reads in full, holds a lease, or has a request waiting has nothing to ask for.
        "may_ask": bool(may_ask) and held is None and waiting is None and decision.get("mode") != "full",
        "lease": shaped.get(str(held["id"])) if held else None,
        "waiting": shaped.get(str(waiting["id"])) if waiting else None,
        "limits": {"days": limits["locked_rows"]["most_days"], "rows": limits["locked_rows"]["most_rows"]},
    }


def request_lease(identity: dict, version_id: str, purpose: str, days: int, rows: int) -> dict:
    version = _version(version_id, identity["tenant_id"])
    allowed, reasons = opa.may_request_locked_lease({
        "requester": {"id": identity["id"], "roles": identity["roles"]}, "limits": org_limits.limits_for(identity["tenant_id"]),
        "request": {"principal": identity["id"], "purpose": purpose, "days": days, "rows": rows}})
    if not allowed:
        raise NotPermitted(reasons)
    held = current_lease(identity["tenant_id"], identity["id"], version_id)
    if held:
        raise LeaseConflict([f"you already hold a lease on this table, which ends {held['expires_at'].astimezone(timezone.utc).isoformat()}"])
    try:
        row = db.execute(
            """insert into locked_column_lease (id, tenant_id, principal, dataset_version_id, purpose, days, row_limit)
               values (%s, %s, %s, %s, %s, %s, %s) returning *""",
            (str(uuid.uuid4()), identity["tenant_id"], identity["id"], version_id, purpose.strip(), days, rows))
    except pgdb.errors.UniqueViolation as exc:
        raise LeaseConflict(["you already have a request on this table waiting for an approver"]) from exc
    return _shape(row)


def _lease(lease_id: str, tenant_id: str) -> dict:
    row = db.one("select l.*, d.department_id from locked_column_lease l "
                 "join dataset_version v on v.id = l.dataset_version_id join dataset d on d.id = v.dataset_id "
                 "where l.id = %s", (lease_id,))
    if not row or row["tenant_id"] != tenant_id:
        raise NoSuchLease(lease_id)
    return row


def decide(identity: dict, lease_id: str, approve: bool, note: str | None) -> dict:
    lease = _lease(lease_id, identity["tenant_id"])
    allowed, reasons = opa.may_approve_locked_lease({
        "approver": {"id": identity["id"], "roles": identity["roles"]}, "request": {"principal": lease["principal"]},
        "dataset": {"approvers": approvers_of(lease["department_id"])}})
    if not allowed:
        raise NotPermitted(reasons)
    if lease["status"] != "requested":
        raise LeaseConflict([f"this request was already decided ({lease['status']})"])
    if approve and current_lease(lease["tenant_id"], lease["principal"], str(lease["dataset_version_id"])):
        raise LeaseConflict(["this person already holds a current lease on this table"])
    row = db.execute(
        """update locked_column_lease
              set status = %s, decided_by = %s, decided_at = now(), decision_note = %s,
                  expires_at = case when %s then now() + make_interval(days => days) else null end
            where id = %s and status = 'requested' returning *""",
        ("approved" if approve else "declined", identity["id"], note, approve, lease_id))
    if row is None:
        raise LeaseConflict(["this request was decided a moment ago by somebody else"])
    return _shape(row)


def revoke(identity: dict, lease_id: str) -> dict:
    lease = _lease(lease_id, identity["tenant_id"])
    allowed, reasons = opa.may_revoke_locked_lease({
        "actor": {"id": identity["id"], "roles": identity["roles"]}, "request": {"principal": lease["principal"]},
        "dataset": {"approvers": approvers_of(lease["department_id"])}})
    if not allowed:
        raise NotPermitted(reasons)
    row = db.execute(
        """update locked_column_lease set status = 'revoked', revoked_by = %s, revoked_at = now()
            where id = %s and status = 'approved' and expires_at > now() returning *""", (identity["id"], lease_id))
    if row is None:
        raise LeaseConflict(["only a lease that is in force can be ended"])
    return _shape(row)


def list_leases(identity: dict) -> dict:
    """The person's own leases, and the requests waiting for them to decide as an approver of the department that owns the data."""
    mine = [_shape(r) for r in db.all_rows(
        "select * from locked_column_lease where tenant_id = %s and principal = %s order by requested_at desc limit 100",
        (identity["tenant_id"], identity["id"]))]
    waiting = []
    rows = db.all_rows(
        """select l.*, d.department_id from locked_column_lease l
             join dataset_version v on v.id = l.dataset_version_id join dataset d on d.id = v.dataset_id
            where l.tenant_id = %s and l.status = 'requested' and l.principal <> %s order by l.requested_at limit 200""",
        (identity["tenant_id"], identity["id"]))
    cache: dict = {}

    def mine_to_decide(r: dict) -> bool:
        dept = str(r["department_id"]) if r["department_id"] else None
        if dept not in cache:
            cache[dept] = approvers_of(dept)
        return identity["id"] in cache[dept]

    for r in rows:
        if mine_to_decide(r):
            waiting.append(_shape(r))
    # The leases in force in the departments this person answers for, so an approver can end one early.
    granted = [_shape(r) for r in db.all_rows(
        """select l.*, d.department_id from locked_column_lease l
             join dataset_version v on v.id = l.dataset_version_id join dataset d on d.id = v.dataset_id
            where l.tenant_id = %s and l.status = 'approved' and l.expires_at > now() order by l.expires_at limit 200""",
        (identity["tenant_id"],)) if mine_to_decide(r)]
    return {"mine": _labels(mine), "to_decide": _labels(waiting), "granted": _labels(granted)}


def audit_evidence(identity: dict, limit: int = 200) -> dict:
    """How locking was used, for the privacy auditor: reads and refusals, key uses, leases, contract treatments and erasures.

    Never a name, a value or an identifier. Person codes appear, which mean nothing alone. A tombstone's free-text reason is left out,
    because it is written by a person and may name somebody. The auditor's own read is recorded like any other.
    """
    allowed, reasons = opa.may_read_audit_evidence({"person": {"id": identity["id"], "roles": identity["roles"]}})
    db.execute(
        """insert into access_decision (principal, principal_kind, principal_roles, tenant_id, purpose, allowed, reasons, phase)
           values (%s, %s, %s, %s, 'read audit evidence', %s, %s, 'policy') returning principal""",
        (identity["id"], identity.get("kind", "human"), identity["roles"], identity["tenant_id"], allowed, reasons))
    if not allowed:
        raise NotPermitted(reasons)
    t, n = identity["tenant_id"], max(1, min(limit, 500))
    stamp = lambda r: {k: (v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if hasattr(v, "astimezone") else v) for k, v in r.items()}  # noqa: E731
    reads = db.all_rows(
        """select at, principal, principal_roles, dataset_version_id, purpose, allowed, reasons, locked_column_lease_id
             from access_decision where tenant_id = %s and purpose like 'read locked columns%%' order by at desc limit %s""", (t, n))
    keys = db.all_rows("select at, person_code, action, actor, purpose from key_use_log where tenant_id = %s order by at desc limit %s", (t, n))
    leases = db.all_rows(
        "select " + ", ".join(COLUMNS) + " from locked_column_lease where tenant_id = %s order by requested_at desc limit %s", (t, n))
    contracts = db.all_rows(
        """select p.name, p.version, p.schema_id, p.created_at, p.document from privacy_contract p
            where p.tenant_id = %s order by p.created_at desc limit %s""", (t, n))
    sizes = db.all_rows(
        """select dataset_version_id, threshold, columns, people, groups, smallest_group, groups_below, people_in_small_groups, advice,
                  decision, decided_by, decided_at, created_at
             from group_size_report where tenant_id = %s order by created_at desc limit %s""", (t, n))
    erasures = db.all_rows(
        "select at, record_id, requested_by from tombstone where tenant_id = %s and record_id like 'person:%%' order by at desc limit %s", (t, n))

    # Locked files: every opening of one, and every refusal to open a file of a version that holds locked files. A file is named by neither its name nor its
    # identifier here, only by how many were opened, in which table and version, and why the platform let the person.
    from . import audit_pages, file_leases
    file_opens = db.all_rows(
        """select l.at, l.principal, l.version_id as dataset_version_id, l.basis, l.via, l.file_lease_id, l.record_id from record_read_log l
            where l.tenant_id = %s and l.locked order by l.at desc limit %s""", (t, n))
    file_refusals = db.all_rows(
        """select a.at, a.principal, a.dataset_version_id, a.reasons, a.record_id from access_decision a
            where a.tenant_id = %s and a.purpose = 'open a record' and not a.allowed
              and exists (select 1 from file_person fp where fp.dataset_version_id = a.dataset_version_id)
            order by a.at desc limit %s""", (t, n))
    file_lease_rows = db.all_rows(
        "select " + ", ".join(file_leases.COLUMNS) + " from file_lease where tenant_id = %s order by requested_at desc limit %s", (t, n))

    # The key log names a person as "person <id>", the key service as itself. Show the people by their label.
    who_of = lambda actor: actor[len("person "):] if actor.startswith("person ") else actor  # noqa: E731
    ids = ({r["principal"] for r in reads} | {who_of(r["actor"]) for r in keys} | {r["requested_by"] for r in erasures}
           | {r["decided_by"] for r in sizes if r["decided_by"]} | {r["principal"] for r in file_opens} | {r["principal"] for r in file_refusals})
    names = {r["id"]: r["label"] for r in db.all_rows("select id, label from directory where id = any(%s)", (list(ids),))} if ids else {}

    def named(row: dict, field: str, as_field: str) -> dict:
        return {**row, as_field: names.get(row[field], row[field]) if row[field] else None}

    # The tables sealed under each contract, so the page names tables and not only the contract's own name.
    sealed_under: dict[str, list[str]] = {}
    for r in db.all_rows(
            """select v.schema_id, d.name, v.version from dataset_version v join dataset d on d.id = v.dataset_id
                where v.tenant_id = %s and v.schema_id = any(%s::uuid[]) order by v.created_at desc""",
            (t, [str(c["schema_id"]) for c in contracts])) if contracts else []:
        sealed_under.setdefault(str(r["schema_id"]), []).append(f'{r["name"]}, version {r["version"]}')

    def treatments(doc: dict) -> list[dict]:
        from . import privacy_contract
        return [{"name": c["name"], "classification": c["classification"], "treatment": c["treatment"]} for c in privacy_contract.columns(doc)]

    version_ids = list({str(r["dataset_version_id"]) for r in file_opens + file_refusals + reads + sizes if r["dataset_version_id"]})
    tables = {str(r["id"]): f'{r["name"]}, version {r["version"]}' for r in db.all_rows(
        "select v.id, d.name, v.version from dataset_version v join dataset d on d.id = v.dataset_id where v.id = any(%s::uuid[])", (version_ids,))} if version_ids else {}
    file_events = sorted(
        [{"at": r["at"], "principal_label": names.get(r["principal"], r["principal"]), "table": tables.get(str(r["dataset_version_id"])), "allowed": True,
          "basis": r["basis"], "via": r["via"], "lease_id": str(r["file_lease_id"]) if r["file_lease_id"] else None, "reason": None, "file": r["record_id"]} for r in file_opens]
        + [{"at": r["at"], "principal_label": names.get(r["principal"], r["principal"]), "table": tables.get(str(r["dataset_version_id"])), "allowed": False,
            "basis": None, "via": None, "lease_id": None, "reason": (r["reasons"] or [None])[0], "file": r["record_id"]} for r in file_refusals],
        key=lambda e: e["at"], reverse=True)[:n]
    shaped_file_leases = file_leases._labels([file_leases._shape(r) for r in file_lease_rows])

    return {
        "file_reads": [stamp(e) for e in file_events],
        "file_leases": [{k: v for k, v in l.items() if k in ("id", "requested_at", "principal_label", "kind", "purpose", "requested_count", "hours", "status", "granted_count",
                                                            "granted_hours", "decided_by_label", "decided_at", "ended_by_label", "ended_at", "expires_at", "dataset_name", "dataset_version")}
                        for l in shaped_file_leases],
        "reads": [stamp({**named(r, "principal", "principal_label"), "dataset_version_id": str(r["dataset_version_id"]) if r["dataset_version_id"] else None,
                         "table": tables.get(str(r["dataset_version_id"])) if r["dataset_version_id"] else None,
                         "locked_column_lease_id": str(r["locked_column_lease_id"]) if r["locked_column_lease_id"] else None}) for r in reads],
        "key_uses": [stamp({**r, "actor_label": names.get(who_of(r["actor"]), r["actor"])}) for r in audit_pages.describe_keys(keys, t)],
        "leases": _labels([_shape(r) for r in leases]),
        "contracts": [{"name": c["name"], "version": c["version"], "schema_id": str(c["schema_id"]),
                       "tables": sealed_under.get(str(c["schema_id"]), [])[:5], "table_count": len(sealed_under.get(str(c["schema_id"]), [])),
                       "sealed_under_since": stamp({"at": c["created_at"]})["at"], "treatments": treatments(c["document"])} for c in contracts],
        "erasures": [stamp(named(r, "requested_by", "requested_by_label")) for r in erasures],
        "group_sizes": [stamp({**named(r, "decided_by", "decided_by_label"), "dataset_version_id": str(r["dataset_version_id"]), "table": tables.get(str(r["dataset_version_id"])), "columns": list(r["columns"])}) for r in sizes],
    }
