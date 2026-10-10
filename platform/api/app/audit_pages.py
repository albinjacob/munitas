"""The locked data audit, a page of one list at a time, with filters.

The whole-evidence call (`locked_leases.audit_evidence`) returns the latest lines of every list and cannot reach older ones. An auditor needs to ask for what they are
looking for: what one person read, everything about one table, what happened in one week. This serves one list, newest first, the filters applied in the database,
a page at a time, with the number of lines that match, so a screen can say "26 to 50 of 1,284" and a page past the 200th line exists.

The same rules as the whole-evidence call: only the privacy auditor and the data protection officer, and never a name, a value, a file name or an identifier from any
table. Each call is recorded as a read of audit evidence, so who looked, and at what, is itself on record.

Order is by time, newest first, with the line's own key as the tie-break, so a line is on exactly one page however many share a moment.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from . import db, opa
from .person_errors import NotPermitted, Refused

SECTIONS = ("reads", "column_leases", "file_reads", "file_leases", "keys", "erasures", "small_groups", "contracts")
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25
CONTRACT_PAGE_SIZE = 5

# What each list can be narrowed by, and what "result" means for it. The screen offers only these for each list.
RESULTS = {
    "reads": ("allowed", "refused"),
    "file_reads": ("allowed", "refused"),
    "column_leases": ("requested", "approved", "declined", "revoked"),
    "file_leases": ("requested", "approved", "declined", "ended"),
    "keys": ("make_org", "make_person", "unwrap", "destroy"),
    "small_groups": ("accepted", "will_change", "undecided"),
    "erasures": (),
    "contracts": (),
}
CAN_FILTER = {
    "reads": ("person", "table", "date", "result", "q"),
    "file_reads": ("person", "table", "file", "date", "result", "q"),
    "column_leases": ("person", "table", "date", "result", "q"),
    "file_leases": ("person", "table", "file", "date", "result", "q"),
    "keys": ("person", "table", "file", "date", "result", "q"),
    "erasures": ("person", "date", "q"),
    "small_groups": ("person", "table", "date", "result", "q"),
    "contracts": ("table", "date", "q"),
}

ESCAPE = " escape '\\'"


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _when(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Refused([f"{field} must be a date and time, such as 2026-10-08T00:00:00Z"]) from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _stamp(row: dict) -> dict:
    return {k: (v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if hasattr(v, "astimezone") else v) for k, v in row.items()}


class _Where:
    """The conditions of one list, built from the filters, with their values kept apart from the SQL."""

    def __init__(self, base: str, params: list) -> None:
        self.parts, self.params = [base], list(params)

    def add(self, sql: str, *values) -> None:
        self.parts.append(sql)
        self.params.extend(values)

    def sql(self) -> str:
        return " and ".join(f"({p})" for p in self.parts)


def _dates(w: _Where, f: dict, column: str) -> None:
    if f.get("date_from"):
        w.add(f"{column} >= %s", f["date_from"])
    if f.get("date_to"):
        w.add(f"{column} < %s", f["date_to"])


def _page(select: str, source: str, w: _Where, order: str, size: int, page: int) -> tuple[list[dict], int]:
    total = db.one(f"select count(*) as n from {source} where {w.sql()}", tuple(w.params))["n"]
    rows = db.all_rows(f"select {select} from {source} where {w.sql()} order by {order} limit %s offset %s", tuple(w.params) + (size, (page - 1) * size))
    return rows, int(total)


def _names(ids: set[str]) -> dict[str, str]:
    ids = {i for i in ids if i}
    return {r["id"]: r["label"] for r in db.all_rows("select id, label from directory where id = any(%s)", (list(ids),))} if ids else {}


_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_BASIS = {"lease": "with an approver's permission", "own_sample": "from a development sample drawn for them", "synthetic": "synthetic files, with no real person in them",
          "own_development_output": "made by their own run on development data", "review": "to judge a result waiting for their decision"}


class _Line:
    """What a key line says, read from the text the platform wrote: why, and the table, file, lease, version or decision it names."""

    def __init__(self, reason: str | None = None, *, file: str | None = None, dataset: str | None = None, version: str | None = None, lease: str | None = None,
                 decision: str | None = None) -> None:
        self.reason, self.file, self.dataset, self.version, self.lease, self.decision = reason, file, dataset, version, lease, decision


def _classify(action: str, purpose: str) -> _Line:
    """The reason a key was opened, in words, and what it names so a table and a file can be shown. Only one of these sentences is passed on, never the
    platform's own text."""
    if action != "unwrap":
        return _Line()
    if (m := re.match(rf"read locked columns \(lease ({_UUID})", purpose)):
        return _Line("To read locked rows, under a lease", lease=m.group(1))
    if purpose.startswith("read locked columns"):
        return _Line("To read locked rows, as the owning department's approver")
    if (m := re.match(rf"seal a table(?: \(dataset ({_UUID})\))?$", purpose)):
        return _Line("To seal a table", dataset=m.group(1))
    if (m := re.match(rf"release check of file (.+?)(?: against its original,)? for decision ({_UUID})$", purpose)):
        return _Line("To check a result before it is released", file=m.group(1), decision=m.group(2))
    if (m := re.match(rf"lock part \d+ of (?:file|recording|output) (.+?)(?: for custom step [^()]+?)?(?: \(dataset ({_UUID})\))?$", purpose)):
        return _Line("To lock a file", file=m.group(1), dataset=m.group(2))
    if (m := re.match(rf"open record (.+) of version ({_UUID}) \((\w+)(, in a notebook)?\)$", purpose)):
        where = "in a notebook" if m.group(4) else "in the page"
        return _Line(f"To open a locked file {where}, {_BASIS.get(m.group(3), m.group(3))}", file=m.group(1), version=m.group(2))
    if re.match(r"open a (?:file|recording) for a pipeline run", purpose):
        return _Line("For a pipeline run")
    if (m := re.match(rf"\d+ people: lock a batch of a table(?: \(dataset ({_UUID})\))?", purpose)):
        return _Line("To lock a batch of a table", dataset=m.group(1))
    if re.match(rf"\d+ people: read locked columns of version {_UUID}", purpose):
        return _Line("To read locked rows")
    return _Line("For another reason")


def describe_keys(rows: list[dict], tenant_id: str) -> list[dict]:
    """Key log lines with a plain `reason`, the `table` where it can be found and the `file` where one was opened or locked, in place of the platform's own
    text. A line written before the table was put on it has none, and says so by having no table."""
    lines = [_classify(r["action"], r["purpose"] or "") for r in rows]

    def lookup(sql: str, ids: set[str]) -> dict[str, str]:
        ids = {i for i in ids if i}
        return {str(r["id"]): str(r["dataset_version_id"]) for r in db.all_rows(sql, (tenant_id, list(ids)))} if ids else {}

    via_lease = lookup("select id, dataset_version_id from locked_column_lease where tenant_id = %s and id = any(%s::uuid[])", {l.lease for l in lines if l.lease})
    via_decision = lookup("select id, dataset_version_id from gate_decision where tenant_id = %s and id = any(%s::uuid[])", {l.decision for l in lines if l.decision})
    versions = {l.version for l in lines if l.version} | set(via_lease.values()) | set(via_decision.values())
    tables = _tables(versions)
    datasets = {str(r["id"]): r["name"] for r in db.all_rows("select id, name from dataset where tenant_id = %s and id = any(%s::uuid[])", (tenant_id, list({l.dataset for l in lines if l.dataset})))} \
        if any(l.dataset for l in lines) else {}
    out = []
    for r, line in zip(rows, lines):
        version = line.version or via_lease.get(line.lease or "") or via_decision.get(line.decision or "")
        table = tables.get(version) if version else datasets.get(line.dataset or "")
        out.append({**{k: v for k, v in r.items() if k != "purpose"}, "reason": line.reason, "table": table, "file": line.file})
    return out


def _tables(version_ids: set[str]) -> dict[str, str]:
    ids = [str(v) for v in version_ids if v]
    return {str(r["id"]): f'{r["name"]}, version {r["version"]}' for r in db.all_rows(
        "select v.id, d.name, v.version from dataset_version v join dataset d on d.id = v.dataset_id where v.id = any(%s::uuid[])", (ids,))} if ids else {}


def audit_page(identity: dict, section: str, *, person: str | None = None, table: str | None = None, file: str | None = None, date_from: str | None = None,
               date_to: str | None = None, result: str | None = None, q: str | None = None, page: int = 1, page_size: int | None = None) -> dict:
    """One page of one list of the locked data audit, newest first, and how many lines match the filters."""
    from . import file_leases, locked_leases, privacy_contract

    allowed, reasons = opa.may_read_audit_evidence({"person": {"id": identity["id"], "roles": identity["roles"]}})
    # Every read of a list is one line with its time: which list, what was searched for, and which page. Nothing is left out, because each list discloses
    # different evidence and the access log has to show which of it a person was shown.
    searched = {k: v for k, v in (("person", person), ("table", table), ("file", file), ("from", date_from), ("to", date_to), ("result", result), ("words", q)) if v and str(v).strip()}
    what = ", ".join(f"{k}={str(v).strip()[:60]}" for k, v in searched.items())
    purpose = "read audit evidence" if section not in SECTIONS else f"read audit evidence: {section}{f' ({what})' if what else ''}{f' page {page}' if page > 1 else ''}"
    db.execute(
        """insert into access_decision (principal, principal_kind, principal_roles, tenant_id, purpose, allowed, reasons, phase)
           values (%s, %s, %s, %s, %s, %s, %s, 'policy') returning principal""",
        (identity["id"], identity.get("kind", "human"), identity["roles"], identity["tenant_id"], purpose, allowed, reasons))
    if not allowed:
        raise NotPermitted(reasons)
    if section not in SECTIONS:
        raise Refused([f"the list must be one of {', '.join(SECTIONS)}"])
    if result and RESULTS[section] and result not in RESULTS[section]:
        raise Refused([f"for this list the result must be one of {', '.join(RESULTS[section])}"])
    size = max(1, min(page_size if page_size is not None else (CONTRACT_PAGE_SIZE if section == "contracts" else DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    number = max(1, page)
    t = identity["tenant_id"]
    f = {"person": (person or "").strip() or None, "table": (table or "").strip() or None, "file": (file or "").strip() or None, "q": (q or "").strip() or None,
         "date_from": _when(date_from, "the start date"), "date_to": _when(date_to, "the end date")}

    def narrow(w: _Where, *, text: list[str]) -> None:
        if f["q"]:
            w.add(" or ".join(f"{c} ilike %s{ESCAPE}" for c in text), *([_like(f["q"])] * len(text)))

    if section == "reads":
        w = _Where("a.tenant_id = %s and a.purpose like 'read locked columns%%'", [t])
        _dates(w, f, "a.at")
        if f["person"]:
            w.add(f"p.label ilike %s{ESCAPE} or a.principal ilike %s{ESCAPE}", _like(f["person"]), _like(f["person"]))
        if f["table"]:
            w.add(f"ds.name ilike %s{ESCAPE}", _like(f["table"]))
        if result:
            w.add("a.allowed = %s", result == "allowed")
        narrow(w, text=["a.purpose", "a.reasons::text"])
        rows, total = _page(
            "a.at, a.principal, a.principal_roles, a.dataset_version_id, a.purpose, a.allowed, a.reasons, a.locked_column_lease_id",
            "access_decision a left join directory p on p.id = a.principal left join dataset_version v on v.id = a.dataset_version_id left join dataset ds on ds.id = v.dataset_id",
            w, "a.at desc, a.id desc", size, number)
        names, tables = _names({r["principal"] for r in rows}), _tables({r["dataset_version_id"] for r in rows})
        out = [_stamp({**r, "principal_label": names.get(r["principal"], r["principal"]), "dataset_version_id": str(r["dataset_version_id"]) if r["dataset_version_id"] else None,
                       "table": tables.get(str(r["dataset_version_id"])) if r["dataset_version_id"] else None,
                       "locked_column_lease_id": str(r["locked_column_lease_id"]) if r["locked_column_lease_id"] else None}) for r in rows]

    elif section == "column_leases":
        w = _Where("l.tenant_id = %s", [t])
        if f["person"]:
            w.add(f"p.label ilike %s{ESCAPE} or l.principal ilike %s{ESCAPE}", _like(f["person"]), _like(f["person"]))
        if f["table"]:
            w.add(f"ds.name ilike %s{ESCAPE}", _like(f["table"]))
        _dates(w, f, "l.requested_at")
        if result:
            w.add("l.status = %s", result)
        narrow(w, text=["l.purpose"])
        rows, total = _page(", ".join(f"l.{c}" for c in locked_leases.COLUMNS),
                            "locked_column_lease l left join directory p on p.id = l.principal join dataset_version v on v.id = l.dataset_version_id join dataset ds on ds.id = v.dataset_id",
                            w, "l.requested_at desc, l.id desc", size, number)
        out = locked_leases._labels([locked_leases._shape(r) for r in rows])

    elif section == "file_reads":
        w = _Where("true", [])
        _dates(w, f, "u.at")
        if f["person"]:
            w.add(f"p.label ilike %s{ESCAPE} or u.principal ilike %s{ESCAPE}", _like(f["person"]), _like(f["person"]))
        if f["table"]:
            w.add(f"ds.name ilike %s{ESCAPE}", _like(f["table"]))
        if result:
            w.add("u.allowed = %s", result == "allowed")
        if f["file"]:
            w.add(f"u.file ilike %s{ESCAPE}", _like(f["file"]))
        narrow(w, text=["u.reason", "u.basis", "u.via"])
        union = """(select l.at, l.principal, l.version_id as dataset_version_id, true as allowed, l.basis, l.via, l.file_lease_id::text as lease_id, null::text as reason, 'a' || l.id::text as ord,
                           l.record_id as file
                      from record_read_log l where l.tenant_id = %s and l.locked
                    union all
                    select a.at, a.principal, a.dataset_version_id, false, null, null, null, a.reasons[1], 'b' || lpad(a.id::text, 20, '0'), a.record_id
                      from access_decision a where a.tenant_id = %s and a.purpose = 'open a record' and not a.allowed
                       and exists (select 1 from file_person fp where fp.dataset_version_id = a.dataset_version_id)) u
                   left join directory p on p.id = u.principal left join dataset_version v on v.id = u.dataset_version_id left join dataset ds on ds.id = v.dataset_id"""
        # The two copies of the tenant are the first two parameters, ahead of the filters.
        w.params = [t, t] + w.params
        rows, total = _page("u.at, u.principal, u.dataset_version_id, u.allowed, u.basis, u.via, u.lease_id, u.reason, u.file", union, w, "u.at desc, u.ord desc", size, number)
        names, tables = _names({r["principal"] for r in rows}), _tables({r["dataset_version_id"] for r in rows})
        out = [_stamp({"at": r["at"], "principal_label": names.get(r["principal"], r["principal"]), "table": tables.get(str(r["dataset_version_id"])), "allowed": r["allowed"],
                       "basis": r["basis"], "via": r["via"], "lease_id": r["lease_id"], "reason": r["reason"], "file": r["file"]}) for r in rows]

    elif section == "file_leases":
        w = _Where("f.tenant_id = %s", [t])
        if f["person"]:
            w.add(f"p.label ilike %s{ESCAPE} or f.principal ilike %s{ESCAPE}", _like(f["person"]), _like(f["person"]))
        if f["table"]:
            w.add(f"ds.name ilike %s{ESCAPE}", _like(f["table"]))
        _dates(w, f, "f.requested_at")
        if result:
            w.add("f.status = %s", result)
        if f["file"]:
            w.add(f"exists (select 1 from jsonb_array_elements_text(coalesce(f.records, '[]'::jsonb)) r(v) where r.v ilike %s{ESCAPE})", _like(f["file"]))
        narrow(w, text=["f.purpose"])
        rows, total = _page(", ".join(f"f.{c}" for c in file_leases.COLUMNS),
                            "file_lease f left join directory p on p.id = f.principal join dataset_version v on v.id = f.dataset_version_id join dataset ds on ds.id = v.dataset_id",
                            w, "f.requested_at desc, f.id desc", size, number)
        keep = ("id", "requested_at", "principal_label", "kind", "purpose", "requested_count", "hours", "status", "granted_count", "granted_hours", "decided_by_label",
                "decided_at", "ended_by_label", "ended_at", "expires_at", "dataset_name", "dataset_version")
        out = [{k: v for k, v in l.items() if k in keep} for l in file_leases._labels([file_leases._shape(r) for r in rows])]

    elif section == "keys":
        w = _Where("k.tenant_id = %s", [t])
        if f["person"]:
            w.add(f"p.label ilike %s{ESCAPE} or k.actor ilike %s{ESCAPE}", _like(f["person"]), _like(f["person"]))
        _dates(w, f, "k.at")
        if result:
            w.add("k.action = %s", result)
        if f["file"]:
            w.add(f"k.purpose ~ '^(lock part [0-9]+ of (file|recording|output) |release check of file |open record )' and k.purpose ilike %s{ESCAPE}", _like(f["file"]))
        narrow(w, text=["k.person_code"])
        source = "key_use_log k left join directory p on p.id = case when k.actor like 'person %%' then substr(k.actor, 8) else k.actor end"
        if f["table"]:
            # The table is not a column of the key log. `key_use_dataset` holds the dataset each line is about, read from its text when it was written.
            w.add(f"exists (select 1 from key_use_dataset kd join dataset ds on ds.id = kd.dataset_id where kd.key_use_id = k.id and ds.name ilike %s{ESCAPE})", _like(f["table"]))
        rows, total = _page("k.at, k.person_code, k.action, k.actor, k.purpose", source, w, "k.at desc, k.id desc", size, number)
        who_of = lambda actor: actor[len("person "):] if actor.startswith("person ") else actor  # noqa: E731
        names = _names({who_of(r["actor"]) for r in rows})
        out = [_stamp({**r, "actor_label": names.get(who_of(r["actor"]), r["actor"])}) for r in describe_keys(rows, t)]

    elif section == "erasures":
        w = _Where("t.tenant_id = %s and t.record_id like 'person:%%'", [t])
        if f["person"]:
            w.add(f"p.label ilike %s{ESCAPE} or t.requested_by ilike %s{ESCAPE}", _like(f["person"]), _like(f["person"]))
        _dates(w, f, "t.at")
        narrow(w, text=["t.record_id"])
        rows, total = _page("t.at, t.record_id, t.requested_by", "tombstone t left join directory p on p.id = t.requested_by", w, "t.at desc, t.record_id desc", size, number)
        names = _names({r["requested_by"] for r in rows})
        out = [_stamp({**r, "requested_by_label": names.get(r["requested_by"], r["requested_by"]) if r["requested_by"] else None}) for r in rows]

    elif section == "small_groups":
        w = _Where("g.tenant_id = %s", [t])
        if f["person"]:
            w.add(f"p.label ilike %s{ESCAPE} or g.decided_by ilike %s{ESCAPE}", _like(f["person"]), _like(f["person"]))
        if f["table"]:
            w.add(f"ds.name ilike %s{ESCAPE}", _like(f["table"]))
        _dates(w, f, "g.created_at")
        if result:
            w.add("g.decision is null" if result == "undecided" else "g.decision = %s", *([] if result == "undecided" else [result]))
        narrow(w, text=["array_to_string(g.columns, ',')"])
        rows, total = _page("g.dataset_version_id, g.threshold, g.columns, g.people, g.groups, g.smallest_group, g.groups_below, g.people_in_small_groups, g.advice, g.decision, g.decided_by, g.decided_at, g.created_at",
                            "group_size_report g left join directory p on p.id = g.decided_by join dataset_version v on v.id = g.dataset_version_id join dataset ds on ds.id = v.dataset_id",
                            w, "g.created_at desc, g.id desc", size, number)
        names, tables = _names({r["decided_by"] for r in rows}), _tables({r["dataset_version_id"] for r in rows})
        out = [_stamp({**r, "decided_by_label": names.get(r["decided_by"]) if r["decided_by"] else None, "dataset_version_id": str(r["dataset_version_id"]),
                       "table": tables.get(str(r["dataset_version_id"])), "columns": list(r["columns"])}) for r in rows]

    else:  # contracts
        w = _Where("c.tenant_id = %s", [t])
        if f["table"]:
            w.add(f"exists (select 1 from dataset_version v join dataset ds on ds.id = v.dataset_id where v.schema_id = c.schema_id and v.tenant_id = c.tenant_id and ds.name ilike %s{ESCAPE})", _like(f["table"]))
        _dates(w, f, "c.created_at")
        narrow(w, text=["c.name", "c.document::text"])
        rows, total = _page("c.name, c.version, c.schema_id, c.created_at, c.document", "privacy_contract c", w, "c.created_at desc, c.id desc", size, number)
        under: dict[str, list[str]] = {}
        counts: dict[str, int] = {}
        # When the list was searched by a table, the tables that matched come first, so the one asked about is the one named even when newer tables share
        # the contract.
        first = f"(d.name ilike %s{ESCAPE}) desc, " if f["table"] else ""
        asked = [_like(f["table"])] if f["table"] else []
        for r in db.all_rows(f"""select v.schema_id, d.name, v.version from dataset_version v join dataset d on d.id = v.dataset_id
                                  where v.tenant_id = %s and v.schema_id = any(%s::uuid[]) order by {first}v.created_at desc""",
                             (t, [str(c["schema_id"]) for c in rows], *asked)) if rows else []:
            key = str(r["schema_id"])
            counts[key] = counts.get(key, 0) + 1
            if len(under.setdefault(key, [])) < 5:
                under[key].append(f'{r["name"]}, version {r["version"]}')
        out = [{"name": c["name"], "version": c["version"], "schema_id": str(c["schema_id"]), "tables": under.get(str(c["schema_id"]), []), "table_count": counts.get(str(c["schema_id"]), 0),
                "sealed_under_since": _stamp({"at": c["created_at"]})["at"],
                "treatments": [{"name": col["name"], "classification": col["classification"], "treatment": col["treatment"]} for col in privacy_contract.columns(c["document"])]} for c in rows]

    return {"section": section, "rows": out, "total": total, "page": number, "page_size": size, "pages": max(1, -(-total // size)),
            "can_filter": list(CAN_FILTER[section]), "results": list(RESULTS[section])}
