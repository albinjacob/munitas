"""How many different people share each combination of a table's readable quasi-identifying columns.

A column such as age or postcode names nobody, but a rare combination of them can single one person out: the only person aged 40 to 49 in
one postcode. When a table is sealed the platform counts, for each combination of the readable quasi columns, how many different people it
holds, and reports the people who sit in a group smaller than the contract's threshold. It counts people and not rows, because a person
with six visits is one person to find, however many rows they have.

The report is advice. It never changes a treatment, never stops a seal, and holds counts and column names only, so it can be shown to
people who may not read the table. For each column it says what locking that column would leave: the owner decides.
"""

from __future__ import annotations

import json
import uuid
from datetime import timezone

from . import db, locked_leases, opa, privacy_contract
from .person_errors import AlreadyDecided, NoSuchVersion, NotPermitted, Refused

COLUMNS = ("id", "tenant_id", "dataset_version_id", "threshold", "columns", "people", "groups", "smallest_group", "groups_below",
           "people_in_small_groups", "advice", "created_at", "decision", "decided_by", "decided_at", "decision_note")


def _count(rows: list[tuple[str, dict]], columns: list[str], threshold: int) -> dict:
    """Groups by the values of `columns`, counting different people in each."""
    groups: dict[tuple, set[str]] = {}
    if columns:
        for person, row in rows:
            groups.setdefault(tuple(json.dumps(row.get(c), sort_keys=True, default=str) for c in columns), set()).add(person)
    small = [people for people in groups.values() if len(people) < threshold]
    return {"groups": len(groups), "smallest_group": min((len(p) for p in groups.values()), default=None),
            "groups_below": len(small), "people_in_small_groups": len(set().union(*small)) if small else 0}


def compute(rows: list[tuple[str, dict]], columns: list[str], threshold: int) -> dict:
    """The report for these rows, each a (person code, row). Values are used to group and are never kept."""
    found = _count(rows, columns, threshold)
    advice = []
    if found["people_in_small_groups"]:
        for column in columns:
            after = _count(rows, [c for c in columns if c != column], threshold)["people_in_small_groups"]
            if after < found["people_in_small_groups"]:
                advice.append({"lock": column, "people_in_small_groups_after": after})
        advice.sort(key=lambda a: (a["people_in_small_groups_after"], a["lock"]))
    return {"threshold": threshold, "columns": columns, "people": len({person for person, _ in rows}), **found, "advice": advice}


def record(tenant_id: str, version_id: str, report: dict) -> dict:
    row = db.execute(
        """insert into group_size_report (id, tenant_id, dataset_version_id, threshold, columns, people, groups, smallest_group, groups_below,
                                          people_in_small_groups, advice)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *""",
        (str(uuid.uuid4()), tenant_id, version_id, report["threshold"], report["columns"], report["people"], report["groups"],
         report["smallest_group"], report["groups_below"], report["people_in_small_groups"], json.dumps(report["advice"])))
    return _shape(row)


def _shape(row: dict) -> dict:
    out = {k: row[k] for k in COLUMNS}
    person = db.one("select label from directory where id = %s", (row["decided_by"],)) if row["decided_by"] else None
    out["decided_by_label"] = person["label"] if person else row["decided_by"]
    for k in ("id", "dataset_version_id"):
        out[k] = str(out[k])
    for k in ("created_at", "decided_at"):
        out[k] = out[k].astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if out[k] else None
    out["columns"] = list(out["columns"])
    return out


def _version(version_id: str, tenant_id: str) -> dict:
    row = db.one("select v.id, v.tenant_id, d.department_id from dataset_version v join dataset d on d.id = v.dataset_id where v.id = %s", (version_id,))
    if not row or row["tenant_id"] != tenant_id:
        raise NoSuchVersion(version_id)
    return row


def get_report(identity: dict, version_id: str) -> dict:
    version = _version(version_id, identity["tenant_id"])
    allowed, reasons = opa.may_read_group_size_report({
        "person": {"id": identity["id"], "roles": identity["roles"]}, "dataset": {"approvers": locked_leases.approvers_of(version["department_id"])}})
    if not allowed:
        raise NotPermitted(reasons)
    row = db.one("select * from group_size_report where dataset_version_id = %s", (version_id,))
    if not row:
        raise NoSuchVersion(f"{version_id} has no group-size report")
    return _shape(row)


def decide(identity: dict, version_id: str, decision: str, note: str) -> dict:
    version = _version(version_id, identity["tenant_id"])
    allowed, reasons = opa.may_decide_group_size_report({
        "person": {"id": identity["id"], "roles": identity["roles"]}, "dataset": {"approvers": locked_leases.approvers_of(version["department_id"])}})
    if not allowed:
        raise NotPermitted(reasons)
    if decision not in ("accepted", "will_change") or not (note or "").strip():
        raise Refused(["a decision is 'accepted' or 'will_change', and it needs a note saying why, which is recorded with it"])
    row = db.execute(
        """update group_size_report set decision = %s, decided_by = %s, decided_at = now(), decision_note = %s
            where dataset_version_id = %s and decision is null returning *""", (decision, identity["id"], note.strip(), version_id))
    if row is None:
        if not db.one("select 1 as x from group_size_report where dataset_version_id = %s", (version_id,)):
            raise NoSuchVersion(f"{version_id} has no group-size report")
        raise AlreadyDecided(["this report was already decided"])
    return _shape(row)


def threshold_of(contract: dict) -> int:
    return privacy_contract.group_size_threshold(contract)
