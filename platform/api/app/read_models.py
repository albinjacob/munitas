"""Read-only queries for the console.

Separate from `main.py` for two reasons. The obvious one is that `main.py` is
already long and mixing list endpoints into it would make the write paths, which
are the ones with consequences, harder to find.

The less obvious one matters more: everything in this module is a read. Nothing
here creates a version, mints a credential, approves a lease or promotes a class.
Keeping that boundary at the file level means a change to the console's data
needs cannot quietly grow a side effect, and a reviewer can check the claim by
looking at the imports rather than by reading every function.

The queries lean on the `version_class` and `lineage` views rather than
rebuilding their joins. Those views already answer the two questions the detail
screens ask, and a second implementation of the same question is a second thing
that can disagree.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from . import auth, db, models, readability, screens

router = APIRouter(tags=["read"])


def _jsonable(rows: Any) -> Any:
    """UUIDs, timestamps and arrays into something JSON can carry."""
    return json.loads(json.dumps(rows, default=str))


# What the datasets list can be ordered by: the name a person sees, and the expression that orders it. "access" puts the most restricted first when
# ascending, because that is the order the levels are drawn in everywhere else.
_DATASET_SORTS = {
    "name": "lower(name)",
    "registered": "created_at",
    "kind": "kind",
    "format": "array_to_string(formats, ',')",
    "structure": "structure",
    "versions": "version_count",
    "access": "case widest_class when 'RAW' then 0 when 'UNDER_REVIEW' then 1 when 'OPEN_FOR_ANNOTATION' then 2 "
              "when 'OPEN_FOR_TRAINING' then 3 when 'PUBLISHED' then 4 end",
}


@router.get("/datasets")
def list_datasets(
    q: str | None = None,
    widest_class: str | None = None,
    kind: str | None = None,
    format: str | None = None,
    structure: str | None = None,
    department_id: str | None = None,
    has_versions: bool | None = None,
    state: str | None = None,
    include_deleted: bool = False,
    sort: str = "registered",
    direction: str = "desc",
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
    identity: dict = Depends(auth.current_session),
) -> dict:
    """Datasets, filtered, with the total so the caller knows what is hidden.

    Filtering happens here, not in the browser, so the list stays fast as the platform grows.

    The class shown is the widest any version has reached. A dataset whose newest version is raw but which has an older version released is not
    a raw dataset from an exposure point of view, and showing the newest would understate it.

    Where the data came from travels with each row: the licence a HuggingFace fetch found and, when that licence corrected what was registered,
    the original answer and when it changed.

    `has_versions` marks a registered dataset with nothing in it, so an empty shelf is not mistaken for a failure.

    Every dataset has a `state`, worked out from its versions. `registered`: it has no version yet. `active`: at least one of its versions still has
    data that can be read, whether that data is files or a table. `deleted`: it has versions and the data of every one of them has been deleted; its
    record, its versions and the decisions made about them are kept. The state is separate from the access level. Deleted datasets are left out of the
    list unless `include_deleted` is true or `state` asks for them, and `deleted` always says how many deleted datasets match the other filters, so a
    caller can offer them without a second request.
    """
    if state is not None and state not in ("registered", "active", "deleted"):
        raise HTTPException(422, {"reasons": ["state is registered, active or deleted"]})
    # One filter fragment, used by both queries below, so "how many rows
    # match" and "which page of them" can never quietly disagree the way two
    # hand-written copies of the same where clause could.
    filtered_cte = """
        with summary as (
          select d.id, d.name, d.tenant_id, d.created_at, d.modality,
                 d.provenance, d.department_id,
                 d.license_tag, d.license_export_unmodified, d.license_export_modified,
                 d.provenance_registered_as, d.provenance_overridden_at,
                 count(vc.dataset_version_id) as version_count,
                 count(vc.dataset_version_id) filter (where vc.reclaimed_at is null) as readable_versions,
                 max(vc.version) as latest_version,
                 count(r.dataset_version_id) as tabled_versions,
                 count(n.dataset_version_id) filter (where n.outcome in ('skipped', 'failed')) as untabled_versions,
                 array_agg(vc.current_class order by
                      case vc.current_class
                          when 'PUBLISHED' then 0 when 'OPEN_FOR_TRAINING' then 1
                          when 'OPEN_FOR_ANNOTATION' then 2
                          when 'UNDER_REVIEW' then 3 else 4 end)
                   filter (where vc.current_class is not null) as classes
          from dataset d
          left join version_class vc on vc.dataset_id = d.id
          left join iceberg_table_ref r on r.dataset_version_id = vc.dataset_version_id
          left join iceberg_projection_note n on n.dataset_version_id = vc.dataset_version_id
          where d.tenant_id = %(tenant)s
          group by d.id, d.name, d.tenant_id, d.created_at, d.modality,
                   d.provenance, d.department_id,
                   d.license_tag, d.license_export_unmodified, d.license_export_modified,
                   d.provenance_registered_as, d.provenance_overridden_at
        ),
        file_kinds as (
          -- What each dataset holds, from what was written against every file when its version was sealed. A version stored as a table counts as
          -- one table, in the format "table", and not as the files its rows came from.
          select dataset_id, array_agg(distinct kind) as kinds, array_agg(distinct fmt) as formats,
                 array_agg(distinct format_structure(kind, fmt)) as structures,
                 count(*) filter (where not is_table) as files, coalesce(sum(size), 0) as bytes
            from (
              select dv.dataset_id, manifest_kind(e) as kind, manifest_format(e) as fmt, false as is_table, coalesce((e ->> 'bytes')::bigint, 0) as size
                from dataset_version dv cross join lateral jsonb_array_elements(dv.object_manifest) e
               where dv.tenant_id = %(tenant)s and (e ->> 'key') not like '%%/iceberg/%%'
                 and not exists (select 1 from iceberg_table_ref r where r.dataset_version_id = dv.id)
                 and not exists (select 1 from iceberg_projection_note n
                                  where n.dataset_version_id = dv.id and n.outcome in ('skipped', 'failed'))
              union all
              select dv.dataset_id, 'tabular', 'table', true, 0
                from dataset_version dv
               where dv.tenant_id = %(tenant)s
                 and (exists (select 1 from iceberg_table_ref r where r.dataset_version_id = dv.id)
                      or exists (select 1 from iceberg_projection_note n
                                  where n.dataset_version_id = dv.id and n.outcome in ('skipped', 'failed')))
            ) held
           group by dataset_id
        ),
        matching as (
          select id, name, tenant_id, created_at, modality, provenance,
                 department_id, version_count, latest_version,
                 case when version_count = 0 then 'registered' when readable_versions = 0 then 'deleted' else 'active' end as state,
                 tabled_versions, untabled_versions,
                 -- What it holds. A dataset with nothing in it yet holds nothing: what its owner expected is not shown as though it were found.
                 coalesce(fk.kinds, '{}') as kinds, coalesce(fk.formats, '{}') as formats, coalesce(fk.structures, '{}') as structures,
                 case coalesce(cardinality(fk.kinds), 0) when 0 then null when 1 then fk.kinds[1] else 'multimodal' end as kind,
                 case coalesce(cardinality(fk.structures), 0) when 0 then null when 1 then fk.structures[1] else 'mixed' end as structure,
                 coalesce(fk.files, 0) as files, coalesce(fk.bytes, 0) as bytes,
                 (tabled_versions + untabled_versions > 0) as is_table,
                 (tabled_versions + untabled_versions > 0 and tabled_versions < version_count) as table_missing,
                 license_tag, license_export_unmodified, license_export_modified,
                 provenance_registered_as, provenance_overridden_at,
                 classes[1] as widest_class
          from summary left join file_kinds fk on fk.dataset_id = summary.id
          where (%(q)s::text is null or name ilike '%%' || %(q)s || '%%')
            and (%(klass)s::text is null or classes[1] = %(klass)s)
            and (%(kind)s::text is null or (%(kind)s = 'multimodal' and cardinality(fk.kinds) > 1) or (%(kind)s <> 'multimodal' and %(kind)s = any(fk.kinds)))
            and (%(format)s::text is null or %(format)s = any(fk.formats))
            and (%(structure)s::text is null
                 or (%(structure)s = 'mixed' and cardinality(fk.structures) > 1)
                 or (%(structure)s <> 'mixed' and %(structure)s = any(fk.structures)))
            and (%(dept)s::text is null or department_id::text = %(dept)s)
            and (%(has)s::boolean is null
                 or (%(has)s = true and version_count > 0)
                 or (%(has)s = false and version_count = 0))
        ),
        filtered as (
          select * from matching
           where (%(state)s::text is null or state = %(state)s)
             and (%(state)s::text is not null or %(include_deleted)s::boolean or state <> 'deleted')
        )
    """
    params = {
        "tenant": identity["tenant_id"], "q": q, "klass": widest_class,
        "kind": kind, "format": format, "structure": structure, "dept": department_id, "has": has_versions, "limit": limit, "offset": offset,
        "state": state, "include_deleted": include_deleted,
    }
    if sort not in _DATASET_SORTS or direction not in ("asc", "desc"):
        raise HTTPException(422, {"reasons": [f"sort is one of {', '.join(_DATASET_SORTS)}, and direction is asc or desc"]})
    # The order is chosen from a fixed list, never taken from the request as text, and ends on the name and the id so that two datasets that tie
    # always come in the same order and a page never repeats or skips one.
    rows = db.all_rows(
        filtered_cte + f"select * from filtered order by {_DATASET_SORTS[sort]} {direction} nulls last, lower(name), id "
        "limit %(limit)s offset %(offset)s",
        params,
    )
    total = db.one(
        filtered_cte + "select (select count(*) from filtered) as n, (select count(*) from matching where state = 'deleted') as deleted", params)

    return _jsonable({
        "datasets": rows,
        "shown": len(rows),
        "total": total["n"],
        "deleted": total["deleted"],
        "limit": limit,
        "offset": offset,
    })


@router.get("/datasets/{dataset_id}/versions")
def list_dataset_versions(
    dataset_id: str,
    identity: dict = Depends(auth.current_session),
) -> list[dict]:
    """The versions inside one dataset.

    Narrowed to the caller's own organisation, returning nothing rather than refusing.
    """
    sql = """
        select vc.*, dv.content_hash, dv.record_count, dv.created_at, dv.sealed,
               exists (select 1 from iceberg_table_ref r where r.dataset_version_id = dv.id) as table_copy,
               -- What record_count counts: the rows of a table (written, or meant to be and not written), the files of anything else.
               (exists (select 1 from iceberg_table_ref r where r.dataset_version_id = dv.id)
                or exists (select 1 from iceberg_projection_note n where n.dataset_version_id = dv.id and n.outcome in ('skipped', 'failed'))) as holds_rows
        from version_class vc
        join dataset_version dv on dv.id = vc.dataset_version_id
        where vc.dataset_id = %s
          and vc.tenant_id = %s
        order by vc.version desc
    """
    return _jsonable(db.all_rows(sql, (dataset_id, identity["tenant_id"])))


@router.get("/dataset-versions")
def list_versions(
    current_class: str | None = None,
    limit: int = Query(200, le=1000),
    identity: dict = Depends(auth.current_session),
) -> list[dict]:
    """Versions across all datasets, filterable by current class.

    Filtering on `current_class` rather than the sealed class is deliberate:
    the question people ask is "what is published right now", not "what was
    sealed as published", and answering the second when they meant the first would
    under-report exposure.
    """
    sql = """
        select vc.*, d.name as dataset_name, dv.record_count, dv.created_at
        from version_class vc
        join dataset d on d.id = vc.dataset_id
        join dataset_version dv on dv.id = vc.dataset_version_id
        where vc.tenant_id = %s
          and (%s::text is null or vc.current_class = %s)
        order by dv.created_at desc
        limit %s
    """
    return _jsonable(db.all_rows(
        sql, (identity["tenant_id"], current_class, current_class, limit)
    ))


@router.get("/dataset-versions/{version_id}/holds")
def version_holds(version_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """What one version holds: its kinds, its formats, its structure, how many files and how much, and how many rows when it is a table.

    The same facts the datasets list shows for a whole dataset, for this one version. They are worked out from what was written against each
    file when the version was sealed and, for an older version, from the file's name. `read_from` says which: "content" when every file was read,
    "name" when none was (locked files cannot be read, and an older version was sealed before files were read), "mixed" otherwise. A version
    stored as a table counts as one table in the format "table", not as the files its rows came from. File names are not returned, because a
    name can identify a person. Narrowed to the caller's own organisation, answering 404 for anything else.
    """
    version = db.one("select id, tenant_id from dataset_version where id = %s", (version_id,))
    if not version or version["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such version")
    ref = db.one("select record_count from iceberg_table_ref where dataset_version_id = %s", (version_id,))
    note = db.one("select outcome from iceberg_projection_note where dataset_version_id = %s and outcome in ('skipped', 'failed')", (version_id,))
    table = None
    # A version of locked files has a table copy too, of its list of files. That is not what the version holds: it holds the files, which are locked, so
    # they are counted by name (their content cannot be read) and the list that describes them is not counted as one of them.
    of_files = db.one("select 1 as x from file_person where dataset_version_id = %s limit 1", (version_id,)) is not None
    if (ref or note) and not of_files:
        by_format = [{"format": "table", "kind": "tabular", "structure": "structured", "files": 0, "bytes": 0}]
        files, size, read_from = 0, 0, "table"
        table = {"present": bool(ref), "rows": ref["record_count"] if ref else None}
    else:
        rows = db.all_rows(
            """select manifest_kind(e) as kind, manifest_format(e) as format, format_structure(manifest_kind(e), manifest_format(e)) as structure,
                      count(*) as files, coalesce(sum(nullif(e ->> 'bytes', '')::bigint), 0) as bytes,
                      count(*) filter (where e ->> 'kind_basis' = 'content') as from_content
                 from dataset_version dv cross join lateral jsonb_array_elements(dv.object_manifest) e
                where dv.id = %s and (e ->> 'key') not like '%%/iceberg/%%' and (not %s or (e ->> 'key') not like '%%/records.json')
                group by 1, 2, 3 order by files desc, bytes desc, format""", (version_id, of_files))
        by_format = [{"format": r["format"], "kind": r["kind"], "structure": r["structure"], "files": r["files"], "bytes": int(r["bytes"])} for r in rows]
        files, size = sum(r["files"] for r in rows), sum(int(r["bytes"]) for r in rows)
        from_content = sum(r["from_content"] for r in rows)
        read_from = None if not files else "content" if from_content == files else "name" if from_content == 0 else "mixed"
    kinds = sorted({b["kind"] for b in by_format})
    structures = sorted({b["structure"] for b in by_format})
    return _jsonable({
        "kinds": kinds, "formats": sorted({b["format"] for b in by_format}), "structures": structures,
        "kind": None if not kinds else kinds[0] if len(kinds) == 1 else "multimodal",
        "structure": None if not structures else structures[0] if len(structures) == 1 else "mixed",
        "files": files, "bytes": size, "by_format": by_format, "read_from": read_from, "table": table,
    })


@router.get("/dataset-versions/{version_id}/readability")
def version_readability(version_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """What is locked in this version and what a permission to read the whole of it is worth, in one answer for every screen and route.

    `shape` is "files" (every file locked), "columns" (a table with locked columns) or "plain" (nothing locked); `reads` is what the whole-version
    permission reads: "all", "readable_columns" or "nothing"; `says` is one sentence for the person looking, or null when nothing is locked.
    Counts only. Narrowed to the caller's own organisation, answering 404 for anything else, as the other reads of one version do.
    """
    version = db.one("select id, tenant_id from dataset_version where id = %s", (version_id,))
    if not version or version["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such version")
    found = readability.of(version_id)
    return {"version_id": version_id, **found, "says": readability.says(found)}


@router.get("/dataset-versions/{version_id}/table")
def version_table(version_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """Whether this version is also stored as a table, and if it is not, why.

    A table copy is what lets a standard tool read the rows, and what a legal export needs to hand over only the rows
    for named people. Narrowed to the caller's own organisation, answering 404 for anything else, as the other reads of
    one version do.
    """
    version = db.one("select id, tenant_id from dataset_version where id = %s", (version_id,))
    if not version or version["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such version")
    ref = db.one(
        """select namespace, table_name, snapshot_id, format_version, record_count, projected_at
             from iceberg_table_ref where dataset_version_id = %s""", (version_id,))
    if ref:
        return _jsonable({"projected": True, "outcome": "projected", "table": f"{ref['namespace']}.{ref['table_name']}",
                          "rows": ref["record_count"], "snapshot_id": ref["snapshot_id"],
                          "format_version": ref["format_version"], "projected_at": ref["projected_at"],
                          "filterable": True, "reason": None})
    note = db.one("select outcome, reason, noted_at from iceberg_projection_note where dataset_version_id = %s", (version_id,))
    if note:
        return _jsonable({"projected": False, "outcome": note["outcome"], "reason": note["reason"],
                          "filterable": False, "noted_at": note["noted_at"]})
    return {"projected": False, "outcome": "unrecorded", "filterable": False,
            "reason": "This version was sealed before the platform recorded whether a table was written for it, so "
                      "it cannot say."}


PREVIEW_MOST = 100


@router.get("/dataset-versions/{version_id}/preview")
def version_preview(version_id: str, limit: int = 20, identity: dict = Depends(auth.current_session)) -> dict:
    """The first rows of a version's table, for somebody who may read it.

    Who may is the answer the console already shows next to every version (a role that covers its class, or a lease): this asks the same question and
    gives the same answer, so a person is never shown rows by one screen that another would refuse. The rows are the first ones in the table, at most
    100 (20 unless asked for more), typed as stored: a decimal or a date is written as text so no digit is lost. Showing them is a read, so it is
    recorded whether it is allowed or refused. A version that is not stored as a table has no rows to show, and a table of people is not shown here:
    its locked columns are read through the locked-data requests, which are decided and counted row by row.
    """
    from . import access_preview, iceberg

    limit = max(1, min(int(limit), PREVIEW_MOST))
    version = db.one("select v.id, v.tenant_id, v.storage_backend, c.current_class from dataset_version v join version_class c on c.dataset_version_id = v.id "
                     "where v.id = %s", (version_id,))
    if not version or version["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such version")
    ref = db.one("select metadata_location, record_count from iceberg_table_ref where dataset_version_id = %s", (version_id,))
    if not ref:
        raise HTTPException(404, {"reasons": ["this version is not stored as a table, so there are no rows to show"]})
    mark = access_preview.access_preview(models.AccessPreviewIn(version_ids=[version_id]), identity=identity)["versions"].get(version_id, {})
    allowed = mark.get("mark") in access_preview._READABLE
    reasons = [] if allowed else ["you do not hold access to read this version's rows yet. Ask for access from the version's page, and look again once it is given"]
    db.execute(
        """insert into access_decision (principal, principal_kind, principal_roles, tenant_id, dataset_version_id, requested_class, purpose, allowed, reasons, phase)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'policy') returning principal""",
        (identity["id"], identity.get("kind", "human"), identity["roles"], version["tenant_id"], version_id, version["current_class"],
         "look at the first rows of the table", allowed, reasons))
    if not allowed:
        raise HTTPException(403, {"reasons": reasons})
    from pyiceberg.table import StaticTable

    table = StaticTable.from_metadata(ref["metadata_location"], properties=iceberg._file_io(version["tenant_id"], version["storage_backend"] or "seaweedfs"))
    rows = table.scan(limit=limit).to_arrow().to_pylist()
    return {"columns": [{"name": f.name, "type": str(f.field_type)} for f in table.schema().fields],
            "rows": json.loads(json.dumps(rows, default=str)), "shown": len(rows), "rows_total": ref["record_count"], "limit": limit}


@router.get("/dataset-versions/{version_id}/transitions")
def list_transitions(
    version_id: str,
    identity: dict = Depends(auth.current_session),
) -> list[dict]:
    """The class history of one version.

    This is what makes a promotion legible: which class it moved from and to,
    who decided, and the evidence they pointed at. A version with no transitions
    has never been promoted, and an empty list is the correct answer rather than
    a 404.

    `class_transition` carries no tenant of its own, so the version is joined for
    the check rather than the column being copied onto it. Two places file
    which organisation something belongs to is two places that can disagree.
    """
    sql = """
        select ct.id, ct.from_class, ct.to_class, ct.decided_by, who.label as decided_by_label,
               ct.decided_by_kind, ct.gate_evidence, ct.at
        from class_transition ct
        join dataset_version dv on dv.id = ct.dataset_version_id
        left join directory who on who.id = ct.decided_by
        where ct.dataset_version_id = %s
          and dv.tenant_id = %s
        order by ct.at asc
    """
    return _jsonable(db.all_rows(sql, (version_id, identity["tenant_id"])))


@router.get("/datasets/awaiting-confirmation")
def list_awaiting_confirmation(
    custodian: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    identity: dict = Depends(auth.current_session),
) -> dict:
    """Datasets whose sensitivity claim has not been agreed to yet.

    Only `asserted` claims need this: the safe default carries no claim, and
    `verified_source` is already backed by something the platform fetched
    itself. Narrowed to `custodian` for the same reason `/lease-requests` is,
    so an approver's queue lists arrivals they can actually act on. Those are
    the claims made by somebody else about data their department owns
    (`classification_confirmation_decision`). A claim made by a department's
    only approver is in nobody's queue until the department has a second
    approver, because nobody else may confirm it.

    Oldest first, with the id breaking ties so two claims made at the same
    moment never swap places between pages. `total` counts every dataset
    waiting, not just this page, so a screen can say how many are not shown.
    """
    where = """
        from dataset d
        left join department dept on dept.id = d.department_id
        where d.declaration_basis = 'asserted'
          and d.classification_confirmed_by is null
          and d.tenant_id = %s
          and (
            %s::text is null
            or (%s = any(active_department_approvers(dept.id)) and d.declared_by is distinct from %s)
          )
    """
    scope = (identity["tenant_id"], custodian, custodian, custodian)
    total = db.one("select count(*) as n " + where, scope)["n"]
    items = db.all_rows(
        """select d.id, d.name, d.declared_class, d.declared_by, d.declared_at,
                  d.provenance, dept.name as department_name, active_department_approvers(dept.id) as approvers """
        + where + " order by d.declared_at asc, d.id asc limit %s offset %s",
        scope + (limit, offset),
    )
    return {"items": _jsonable(items), "total": total, "limit": limit, "offset": offset}


@router.get("/lease-requests")
def list_lease_requests(
    state: str | None = None,
    exclude_pending: bool = False,
    principal: str | None = None,
    custodian: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    identity: dict = Depends(auth.current_session),
) -> dict:
    """Lease requests, with the department that owns what was asked for, and the total for page controls.

    `custodian` narrows to requests that person can actually decide, so a custodian's queue lists only work they can do. States come back
    together by default, because the history of how earlier requests were decided tells a reviewer whether this one is unusual.
    `exclude_pending` returns decided requests only, with a total that matches the rows.

    `version` is returned because what is granted is one version, not a dataset: two versions of the same dataset can sit at different
    sensitivities. `requested_by` and `requested_by_label` are set only when a person asked on a workload's behalf; the reading principal stays
    `lr.principal`, because a workload's identity is never inferred from who invoked it.
    """
    where = (
        "where lr.tenant_id = %(tenant)s"
        " and (%(state)s::text is null or lr.state = %(state)s)"
        " and (not %(exclude_pending)s or lr.state <> 'pending')"
        " and (%(principal)s::text is null or lr.principal = %(principal)s)"
        " and (%(custodian)s::text is null or %(custodian)s = any(active_department_approvers(dept.id)))"
    )
    params = {
        "tenant": identity["tenant_id"], "state": state,
        "exclude_pending": exclude_pending, "principal": principal,
        "custodian": custodian,
    }
    rows = db.all_rows(
        f"""
        select lr.*, d.name as dataset_name, vc.version, vc.current_class,
               dept.name as department_name, dept.custodian, active_department_approvers(dept.id) as approvers,
               req.label as requested_by_label
        from lease_request lr
        left join version_class vc on vc.dataset_version_id = lr.dataset_version_id
        left join dataset d on d.id = vc.dataset_id
        left join department dept on dept.id = d.department_id
        left join directory req on req.id = lr.requested_by
        {where}
        order by lr.created_at desc
        limit %(limit)s offset %(offset)s
        """,
        {**params, "limit": limit, "offset": offset},
    )
    total = db.one(
        f"""select count(*) as n
        from lease_request lr
        left join version_class vc on vc.dataset_version_id = lr.dataset_version_id
        left join dataset d on d.id = vc.dataset_id
        left join department dept on dept.id = d.department_id
        {where}""",
        params,
    )
    waiting_on = {str(r["dataset_version_id"]) for r in rows if r["state"] == "pending"}
    gives = {v: readability.gives(readability.of(v)) for v in waiting_on}
    for r in rows:
        if r["state"] == "pending":
            r["gives"] = gives.get(str(r["dataset_version_id"]))
    return {
        "lease_requests": _jsonable(rows),
        "shown": len(rows),
        "total": total["n"],
        "limit": limit,
        "offset": offset,
    }


@router.get("/leases")
def list_leases(
    principal: str | None = None,
    active_only: bool = False,
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    identity: dict = Depends(auth.current_session),
) -> dict:
    """Granted leases, with the total for page controls.

    `active_only` filters to leases that are neither revoked nor expired. It is off by default, because an expired lease is evidence of who had
    access last week.

    `standing` marks a lease with no expiry, granted only to a workload and lasting until revoked. `expires_at is null` means "standing"
    everywhere in the platform, and a standing lease counts as active until revoked, never as expired.

    A caller that needs to look up which lease backs which decided request asks for one large page with no `principal`.
    """
    where = (
        "where al.tenant_id = %(tenant)s"
        " and (%(principal)s::text is null or al.principal = %(principal)s)"
        " and (not %(active_only)s or (al.revoked = false"
        " and (al.expires_at is null or al.expires_at > now())))"
    )
    params = {
        "tenant": identity["tenant_id"], "principal": principal,
        "active_only": active_only,
    }
    rows = db.all_rows(
        f"""
        select al.*, d.name as dataset_name, vc.version, vc.current_class,
               req.label as requested_by_label,
               (al.revoked = false and (al.expires_at is null or al.expires_at > now())) as active,
               (al.expires_at is null) as standing
        from access_lease al
        left join version_class vc on vc.dataset_version_id = al.dataset_version_id
        left join dataset d on d.id = vc.dataset_id
        left join directory req on req.id = al.requested_by
        {where}
        order by al.created_at desc
        limit %(limit)s offset %(offset)s
        """,
        {**params, "limit": limit, "offset": offset},
    )
    total = db.one(f"select count(*) as n from access_lease al {where}", params)
    return {
        "leases": _jsonable(rows),
        "shown": len(rows),
        "total": total["n"],
        "limit": limit,
        "offset": offset,
    }


@router.get("/action-runs")
def list_action_runs(
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    identity: dict = Depends(auth.current_session),
) -> dict:
    """Runs, with how each one started, and the total for real page controls.

    `trigger_kind`/`triggered_by`/`schedule_id` say whether a human ran this by
    hand or a schedule fired it; `operator` stays the workload that executed
    it regardless, the same separation `requested_by` keeps for leases.

    Envelope shape matches `/datasets`/`/agents`: `{action_runs, shown,
    total, limit, offset}`, not a bare list, so one pagination component
    can front all three.

    For data engineers and platform administrators.
    """
    screens.require_screen("action_runs", identity)
    sql = """
        select ar.id, ar.status, ar.operator, ar.code_hash, ar.image_digest,
               ar.started_at, ar.ended_at, ar.output_version, ar.input_versions,
               ar.trigger_kind, ar.triggered_by, ar.schedule_id,
               trig.label as triggered_by_label,
               da.name as action_name, da.output_class
        from action_run ar
        join dataset_action da on da.id = ar.action_id
        left join directory trig on trig.id = ar.triggered_by
        where ar.tenant_id = %(tenant)s
        order by ar.started_at desc
        limit %(limit)s offset %(offset)s
    """
    params = {"tenant": identity["tenant_id"], "limit": limit, "offset": offset}
    rows = db.all_rows(sql, params)
    total = db.one(
        "select count(*) as n from action_run where tenant_id = %(tenant)s", params
    )
    return _jsonable({
        "action_runs": rows,
        "shown": len(rows),
        "total": total["n"],
        "limit": limit,
        "offset": offset,
    })


@router.get("/datasets/{dataset_id}/huggingface-fetch-jobs")
def list_huggingface_fetch_jobs(
    dataset_id: str,
    identity: dict = Depends(auth.current_session),
) -> list[dict]:
    """A dataset's HuggingFace fetch history, most recent first.

    What the console polls instead of holding a browser tab open on the
    fetch request itself: a `running` job is shown as in progress, a
    finished one shows its outcome, and this survives a page reload or
    navigating away and back, because it is read from the job row rather
    than from anything the browser remembered.
    """
    dataset = db.one("select tenant_id from dataset where id = %s", (dataset_id,))
    if not dataset or dataset["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such dataset")

    sql = """
        select j.id, j.repo_id, j.revision, j.path, j.status,
               j.files_total, j.files_done, j.bytes_total, j.bytes_done,
               j.error, j.started_at, j.ended_at,
               j.fetched_by, f.label as fetched_by_label
        from huggingface_fetch_job j
        left join directory f on f.id = j.fetched_by
        where j.dataset_id = %s
        order by j.started_at desc
    """
    return _jsonable(db.all_rows(sql, (dataset_id,)))


@router.get("/agents")
def list_agents(
    q: str | None = None,
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
    identity: dict = Depends(auth.current_session),
) -> dict:
    """Registered agents, most recently registered first, with the total.

    Each carries its version count and its latest version's content hash, so
    the list view can show "what's actually running" without a second
    round trip per row.

    Now the same `{agents, shown, total, limit, offset}` shape `/datasets`
    uses, for real page controls rather than a "show more" that only ever
    grows the same list -- a deliberate break from this endpoint's own
    former bare-array response, made because the console-wide pagination
    pass this is part of asked for one consistent shape everywhere, not
    because the earlier reasoning for keeping it bare was wrong at the
    time. `useAgents()` and the two tests that read this response directly
    (`activation.spec.ts`, `real-auth.spec.ts`) were updated in the same
    change.

    For data engineers and platform administrators.
    """
    screens.require_screen("agents", identity)
    filtered_cte = """
        with filtered as (
          select a.id, a.name, a.purpose, a.principal_id, a.created_at,
                 dept.name as department_name,
                 reg.label as registered_by_label,
                 count(av.id) as version_count,
                 max(av.version) as latest_version,
                 (array_agg(av.code_hash order by av.version desc))[1] as latest_code_hash
          from agent a
          left join department dept on dept.id = a.department_id
          left join directory reg on reg.id = a.registered_by
          left join agent_version av on av.agent_id = a.id
          where a.tenant_id = %(tenant)s
            and (%(q)s::text is null or a.name ilike '%%' || %(q)s || '%%')
          group by a.id, dept.name, reg.label
        )
    """
    params = {"tenant": identity["tenant_id"], "q": q, "limit": limit, "offset": offset}
    rows = db.all_rows(
        filtered_cte + "select * from filtered order by created_at desc "
        "limit %(limit)s offset %(offset)s",
        params,
    )
    total = db.one(filtered_cte + "select count(*) as n from filtered", params)
    return _jsonable({
        "agents": rows,
        "shown": len(rows),
        "total": total["n"],
        "limit": limit,
        "offset": offset,
    })


@router.get("/agents/{agent_id}")
def get_agent(
    agent_id: str,
    identity: dict = Depends(auth.current_session),
) -> dict:
    """One agent's own record, and its full version history."""
    agent = db.one(
        """select a.*, dept.name as department_name,
                  reg.label as registered_by_label
           from agent a
           left join department dept on dept.id = a.department_id
           left join directory reg on reg.id = a.registered_by
           where a.id = %s""",
        (agent_id,),
    )
    if not agent:
        raise HTTPException(404, "no such agent")
    if agent["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such agent")

    versions = db.all_rows(
        """select av.id, av.version, av.code_hash, av.source_path,
                  av.image_digest, av.model_id, av.tool_scope, av.content_hash,
                  av.sealed, av.created_at, reg.label as registered_by_label,
                  av.code_object_key is not null as sandboxed,
                  av.entrypoint, av.requested_hosts, av.tools_declared, av.model_hosting, av.data_access,
                  ega.id as egress_approval_id, ega.state as egress_state
           from agent_version av
           left join directory reg on reg.id = av.registered_by
           left join lateral (
               select id, state from agent_egress_approval
                where agent_version_id = av.id
                order by created_at desc limit 1
           ) ega on true
           where av.agent_id = %s
           order by av.version desc""",
        (agent_id,),
    )

    active = db.one(
        "select agent_version_id, deployed_at from agent_active_version where agent_id = %s",
        (agent_id,),
    )

    out = dict(agent)
    out["versions"] = versions
    out["active_version"] = active
    return _jsonable(out)


@router.get("/egress-approvals")
def list_egress_approvals(
    state: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    identity: dict = Depends(auth.current_session),
) -> dict:
    """Requests to approve the hosts an agent version may call, pending and
    past, with the total for real page controls.

    States come back together by default, the same choice `/gate-decisions`
    makes: a queue showing only pending work hides how the previous ones
    went, and that history is what tells a reviewer whether this one is
    unusual.

    Envelope shape matches `/datasets`/`/agents`/`/action-runs`:
    `{egress_approvals, shown, total, limit, offset}`.

    For network architects, data engineers and platform administrators.
    """
    screens.require_screen("egress_approvals", identity)
    where = " where ega.tenant_id = %s"
    args: list[Any] = [identity["tenant_id"]]
    if state:
        where += " and ega.state = %s"
        args.append(state)

    rows = db.all_rows(
        """
        select ega.id, ega.tenant_id, ega.agent_id, ega.agent_version_id,
               ega.requested_hosts, ega.state, ega.submitted_by,
               ega.decided_by, ega.decided_at, ega.decision_reason,
               ega.created_at,
               a.name as agent_name, av.version,
               sub.label as submitted_by_label, dec.label as decided_by_label
          from agent_egress_approval ega
          join agent a on a.id = ega.agent_id
          join agent_version av on av.id = ega.agent_version_id
          left join directory sub on sub.id = ega.submitted_by
          left join directory dec on dec.id = ega.decided_by
        """ + where + " order by ega.created_at desc limit %s offset %s",
        (*args, limit, offset),
    )
    total = db.one(
        "select count(*) as n from agent_egress_approval ega" + where,
        tuple(args),
    )
    return _jsonable({
        "egress_approvals": rows,
        "shown": len(rows),
        "total": total["n"],
        "limit": limit,
        "offset": offset,
    })


@router.get("/egress-approvals/{approval_id}")
def get_egress_approval(
    approval_id: str,
    identity: dict = Depends(auth.current_session),
) -> dict:
    """One approval request, with what a network_architect needs to decide it."""
    row = db.one(
        """select ega.*, a.name as agent_name, av.version,
                  av.model_id, av.tool_scope,
                  sub.label as submitted_by_label, dec.label as decided_by_label
             from agent_egress_approval ega
             join agent a on a.id = ega.agent_id
             join agent_version av on av.id = ega.agent_version_id
             left join directory sub on sub.id = ega.submitted_by
             left join directory dec on dec.id = ega.decided_by
            where ega.id = %s""",
        (approval_id,),
    )
    if not row:
        raise HTTPException(404, "no such egress approval")
    if row["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such egress approval")
    return _jsonable(row)


@router.get("/services")
def list_services(identity: dict = Depends(auth.current_session)) -> dict:
    """What each workload has actually been doing, across every organisation.

    Assembled from the records of action runs and access decisions, not from a status the services report about themselves, because a service
    claiming to be healthy and a service that has done something are different facts. Denials are counted separately: a workload accumulating
    refusals is either misconfigured or reaching for something it should not.

    Platform-wide, for the platform administrator only. That role runs the system and holds no standing access to any organisation's contents,
    and this is information about workloads, not customer data.
    """
    screens.require_screen("services", identity)
    runs = db.all_rows("""
        select ar.operator,
               count(*) as runs,
               count(*) filter (where ar.status = 'succeeded') as succeeded,
               count(*) filter (where ar.status = 'failed') as failed,
               max(ar.started_at) as last_run
        from action_run ar
        group by ar.operator
    """)
    decisions = db.all_rows("""
        select principal,
               count(*) filter (where phase = 'policy' and allowed) as allowed,
               count(*) filter (where phase = 'policy' and not allowed) as denied,
               max(at) as last_decision
        from access_decision
        group by principal
    """)
    return _jsonable({"runs": runs, "decisions": decisions})


@router.get("/directory")
def list_directory(
    kind: str | None = None,
    purpose: str = "production",
    identity: dict = Depends(auth.current_session),
) -> list[dict]:
    """The people and workloads of the caller's own organisation.

    A session is required. Whatever role the caller holds, the list is the caller's own organisation and no other, and `purpose` does not
    change that. The platform administrator runs the platform across organisations (legal holds, closing an organisation, the services
    page), so the platform administrator is offered the people of every live organisation, or of every organisation with `purpose=all`.

    Approvers carry the departments they answer for (`approver_of`, with the first of them as `department_name`). A department counts only while
    the person can act for it right now: listed, not removed, not past the end of their cover, and still holding the data custodian role. It is
    worked out from the same function the policy uses, on every request, so a label on a screen cannot outlive the right. A person who answers
    for several departments is listed once.

    For the platform administrator, only live organisations are listed by default. Verification runs as its own principals in a test
    organisation, and those are not offered beside real people. Pass `purpose=all` to see every organisation.
    """
    # Platform staff operate across organisations; everybody else is one organisation and sees only it.
    operator = "platform_admin" in identity["roles"]
    own_organisation = None if operator else identity["tenant_id"]
    if not operator:
        purpose = "all"
    sql = """
        select d.id, d.label, d.kind, d.roles, d.tenant_id,
               t.purpose as tenant_purpose,
               ap.first_name as department_name,
               ap.first_id   as department_id,
               coalesce(ap.names, '{}'::text[]) as approver_of
        from directory d
        join tenant t on t.id = d.tenant_id
        left join lateral (
            select array_agg(dept.name order by dept.name) as names,
                   (array_agg(dept.name order by dept.name))[1] as first_name,
                   (array_agg(dept.id order by dept.name))[1] as first_id
              from department dept
             where dept.tenant_id = d.tenant_id
               and d.id = any(active_department_approvers(dept.id))
        ) ap on true
        where (%s::text is null or d.kind = %s)
          and (%s = 'all' or t.purpose = %s)
          and (%s::text is null or d.tenant_id = %s)
        order by d.kind, d.id
    """
    return _jsonable(db.all_rows(sql, (kind, kind, purpose, purpose, own_organisation, own_organisation)))


@router.get("/policy/roles")
def policy_roles() -> dict:
    """The class floors, the approving roles and what each role is called, read from the policy engine.

    `role_words` gives every role its console `label` and, for the roles people hold, `one` and `many` of them with their article ("a data engineer", "data
    engineers"), the same words every refusal uses. The values are read, not restated here, so they cannot disagree with the policy. They describe the shape of the policy, not any
    organisation's data: the same rules apply to every organisation, so this needs no session. That is deliberate, so the console's roles page
    can explain what each role can do to somebody who has not signed in yet.
    """
    from . import opa

    return {
        "class_order": opa.read_document("munitas/access/class_order"),
        "role_floor": opa.read_document("munitas/access/role_floor"),
        "approver_roles": opa.read_document("munitas/access/approver_roles"),
        "role_words": opa.role_words(),
    }


@router.get("/organisation")
def organisation(identity: dict = Depends(auth.current_session)) -> dict:
    """Departments, their approvers, and which datasets they own.

    An unowned dataset is reported as such rather than omitted. Thirty-nine
    datasets predate the organisation model, and nobody can approve access to
    them until somebody takes ownership. Hiding that would make the gap look
    like a smaller number of datasets rather than an unanswered question.
    """
    departments = db.all_rows("""
        select d.id, d.name, d.custodian, dir.label as custodian_label,
               (select coalesce(json_agg(json_build_object(
                          'person_id', a.person_id, 'label', p.label, 'added_at', a.added_at, 'valid_until', a.valid_until,
                          'holds_role', person_holds_live_role(a.person_id, 'data_custodian'))
                          order by a.added_at, p.label), '[]'::json)
                  from department_approver a
                  join directory p on p.id = a.person_id and p.ended_at is null
                 where a.department_id = d.id and a.removed_at is null
                   and (a.valid_until is null or a.valid_until > now())) as approvers,
               count(ds.id) as datasets
        from department d
        left join directory dir on dir.id = d.custodian
        left join dataset ds on ds.department_id = d.id
        where d.tenant_id = %s
        group by d.id, d.name, d.custodian, dir.label
        order by d.name
    """, (identity["tenant_id"],))
    unowned = db.one(
        """select count(*) as n from dataset
           where department_id is null
             and tenant_id = %s""",
        (identity["tenant_id"],),
    )
    return _jsonable({
        "departments": departments,
        "datasets_without_a_department": unowned["n"],
    })


@router.get("/summary")
def summary(identity: dict = Depends(auth.current_session)) -> dict:
    """Counts for the landing page.

    Denied decisions are counted separately and deliberately. A dashboard that
    reports only totals makes a rising denial rate invisible, and a rising denial
    rate is the earliest signal this system can give that something is wrong.

    Scoped to the caller's own tenant. A count that silently spanned every
    tenant would tell somebody looking at their own organisation that it
    holds eighty datasets when it holds two.
    """
    t = identity["tenant_id"]
    counts = db.one("""
        select
          (select count(*) from dataset where tenant_id = %(t)s) as datasets,
          (select count(*) from dataset_version where tenant_id = %(t)s) as versions,
          (select count(*) from class_transition ct
             join dataset_version dv on dv.id = ct.dataset_version_id
             where dv.tenant_id = %(t)s) as promotions,
          (select count(*) from lease_request
             where state = 'pending'
               and tenant_id = %(t)s) as pending_requests,
          (select count(*) from access_lease
             where revoked = false and (expires_at is null or expires_at > now())
               and tenant_id = %(t)s) as active_leases,
          (select count(*) from access_decision
             where tenant_id = %(t)s) as decisions,
          (select count(*) from access_decision
             where allowed = false
               and tenant_id = %(t)s) as denials,
          (select count(*) from tombstone where tenant_id = %(t)s) as tombstones
    """, {"t": t})
    by_class = db.all_rows("""
        select current_class, count(*) as n
        from version_class
        where tenant_id = %(t)s
        group by current_class
    """, {"t": t})
    return _jsonable({
        **counts,
        "versions_by_class": {r["current_class"]: r["n"] for r in by_class},
    })


@router.get("/agents/{agent_id}/runs")
def list_agent_runs(
    agent_id: str,
    identity: dict = Depends(auth.current_session),
) -> list[dict]:
    """An agent's run history, most recent first."""
    agent = db.one("select tenant_id from agent where id = %s", (agent_id,))
    if not agent:
        raise HTTPException(404, "no such agent")
    if agent["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such agent")

    sql = """
        select ar.id, ar.agent_version_id, av.version as agent_version,
               ar.status, ar.purpose, ar.requested_by, req.label as requested_by_label,
               ar.tool_calls, ar.halted_reason, ar.error, ar.started_at, ar.ended_at,
               ar.approved_by, apr.label as approved_by_label, ar.approved_at, ar.lease_request_id, ar.findings,
               ar.execution_mode,
               rv.outcome as review_outcome, rv.reviewed_by, rvw.label as reviewed_by_label, rv.reviewed_at, rv.reason as review_reason, rv.seconds_taken as review_seconds
        from agent_run ar
        join agent_version av on av.id = ar.agent_version_id
        left join directory req on req.id = ar.requested_by
        left join directory apr on apr.id = ar.approved_by
        left join agent_output_review rv on rv.run_id = ar.id
        left join directory rvw on rvw.id = rv.reviewed_by
        where ar.agent_id = %s
        order by ar.started_at desc
    """
    return _jsonable(db.all_rows(sql, (agent_id,)))


@router.get("/agents/runs/{run_id}")
def get_agent_run(
    run_id: str,
    identity: dict = Depends(auth.current_session),
) -> dict:
    """One run, and every access decision its tool calls produced."""
    run = db.one(
        """select ar.*, av.version as agent_version, av.code_hash,
                  a.name as agent_name, req.label as requested_by_label
           from agent_run ar
           join agent_version av on av.id = ar.agent_version_id
           join agent a on a.id = ar.agent_id
           left join directory req on req.id = ar.requested_by
           where ar.id = %s""",
        (run_id,),
    )
    if not run:
        raise HTTPException(404, "no such run")
    if run["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such run")

    decisions = db.all_rows(
        """select id, at, dataset_version_id, requested_class, allowed, reasons, phase
           from access_decision
           where agent_run_id = %s
           order by at""",
        (run_id,),
    )

    out = dict(run)
    # Never leaves the server: this is what proves a credential request
    # naming this run actually came from the run's own code, not merely
    # something that looked up its id. `ar.*` picks it up along with every
    # other column, so it is stripped here rather than the query being
    # rewritten to name every other field by hand.
    out.pop("run_secret", None)
    out["decisions"] = decisions
    return _jsonable(out)


def _gate_deciders(tenant_id: str) -> list[dict]:
    """The people who may decide a de-identification result in this organisation right now: they hold a deciding role (as the policy names
    them) or a live grant of one, and have not left. Whether one of them started a given run is applied per row."""
    from . import opa
    roles = list(opa.read_document("munitas/access/decider_roles") or [])
    return db.all_rows(
        """select d.id, d.label from directory d
            where d.tenant_id = %s and d.kind = 'human' and d.ended_at is null
              and exists (select 1 from unnest(%s::text[]) r where person_holds_live_role(d.id, r))
            order by d.label, d.id""", (tenant_id, roles))


def _say_who_decides(row: dict, deciders: list[dict], caller: str) -> None:
    """Add `waiting_on` (pending only: the deciders other than whoever started the run) and `can_decide` (whether the caller is one of them)."""
    # Not the person who started the run, and not the person who uploaded the version of the pipeline that made it.
    out = {row.get("triggered_by"), row.pop("pipeline_uploaded_by", None)}
    eligible = [{"id": p["id"], "label": p["label"]} for p in deciders if p["id"] not in out]
    row["waiting_on"] = eligible if row["state"] == "pending" else []
    row["can_decide"] = row["state"] == "pending" and any(p["id"] == caller for p in eligible)


@router.get("/gate-decisions")
def list_gate_decisions(
    state: str | None = None,
    reviewer: str | None = None,
    started_by: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    identity: dict = Depends(auth.current_session),
) -> dict:
    """De-identification gate decisions, pending and past, with the total for page controls.

    All states come back together by default, because a queue showing only what is pending hides how the previous ones went. `reviewer`
    narrows to decisions that person may actually make, which means excluding runs they started themselves. `started_by` narrows to the runs
    one person started.

    Every row says who it is waiting on (`waiting_on`, pending rows only) and whether the caller may decide it (`can_decide`). Both come from
    the rule the decision itself is checked against: the roles the policy names as deciders, held right now, and never the person who started
    the run.

    The leak identifiers are not in this list, only their count, because a queue is read at a glance and the personal data is one deliberate
    click further. The answer is `{gate_decisions, shown, total, limit, offset}`.
    """
    where = " where gd.tenant_id = %s"
    args: list[Any] = [identity["tenant_id"]]
    if state:
        where += " and gd.state = %s"
        args.append(state)
    if reviewer:
        where += " and (gd.triggered_by is null or gd.triggered_by <> %s)"
        args.append(reviewer)
    if started_by:
        where += " and gd.triggered_by = %s"
        args.append(started_by)

    rows = db.all_rows(
        """
        select gd.id, gd.tenant_id, gd.dataset_version_id, gd.to_class,
               gd.score_card_id, gd.metrics, gd.recommendation,
               gd.recommendation_reason, gd.state, gd.triggered_by,
               gd.decided_by, gd.decided_at, gd.decision_reason, gd.created_at,
               gd.pipeline_run_id,
               (select pv.uploaded_by from pipeline_run pr join pipeline_version pv on pv.id = pr.pipeline_version_id
                 where pr.id = gd.pipeline_run_id) as pipeline_uploaded_by,
               d.name as dataset_name, dv.version, dv.visibility_class,
               trig.label as triggered_by_label,
               dec.label as decided_by_label,
               (select count(*) from gate_leak gl
                 where gl.gate_decision_id = gd.id and not gl.practice) as leak_count
          from gate_decision gd
          join dataset_version dv on dv.id = gd.dataset_version_id
          join dataset d on d.id = dv.dataset_id
          left join directory trig on trig.id = gd.triggered_by
          left join directory dec on dec.id = gd.decided_by
        """ + where + " order by gd.created_at desc limit %s offset %s",
        (*args, limit, offset),
    )
    deciders = _gate_deciders(identity["tenant_id"])
    for row in rows:
        _say_who_decides(row, deciders, identity["id"])
    total = db.one("select count(*) as n from gate_decision gd" + where, tuple(args))
    return _jsonable({
        "gate_decisions": rows,
        "shown": len(rows),
        "total": total["n"],
        "limit": limit,
        "offset": offset,
    })


def _run_that_produced(pipeline_run_id: str) -> dict | None:
    """Which run made a result, and what it was made with: the pipeline and its version (who uploaded it, who approved its code to read files),
    who started the run and how, when it ran, and the version of the data it was given. Everything a reviewer needs to trace a result back."""
    run = db.one(
        """select pr.id, pr.trigger_kind, pr.started_at, pr.ended_at, pr.source_version_id, trig.label as triggered_by_label,
                  p.id as pipeline_id, p.name as pipeline_name, pv.id as pipeline_version_id, pv.version as pipeline_version,
                  pv.created_at as uploaded_at, up.label as uploaded_by_label,
                  sd.name as source_dataset_name, sv.version as source_version
             from pipeline_run pr
             left join directory trig on trig.id = pr.triggered_by
             left join pipeline_version pv on pv.id = pr.pipeline_version_id
             left join pipeline p on p.id = pv.pipeline_id
             left join directory up on up.id = pv.uploaded_by
             left join dataset_version sv on sv.id = pr.source_version_id
             left join dataset sd on sd.id = sv.dataset_id
            where pr.id = %s""", (pipeline_run_id,))
    if not run:
        return None
    approval = db.one(
        """select a.approved_at, d.label as approved_by_label from pipeline_version_approval a join directory d on d.id = a.approved_by
            where a.pipeline_version_id = %s and a.withdrawn_at is null""", (run["pipeline_version_id"],)) if run["pipeline_version_id"] else None
    run["approved_at"] = approval["approved_at"] if approval else None
    run["approved_by_label"] = approval["approved_by_label"] if approval else None
    return run


@router.get("/gate-decisions/{decision_id}")
def get_gate_decision(
    decision_id: str,
    identity: dict = Depends(auth.current_session),
) -> dict:
    """One decision, with the evidence a reviewer needs to make it.

    The leak list carries the identifier and what survived redaction, which is
    personal data and the reason this endpoint exists at all: judging severity
    is the whole job, and a count cannot tell a partial postcode from a
    person's full name.

    `steps` is every action run of the pipeline run that produced this, which
    is what the correlation id is for. It stays correct once work after the
    promotion runs in a workflow of its own.
    """
    row = db.one(
        """select gd.*, d.name as dataset_name, dv.version,
                  dv.visibility_class, dv.storage_prefix,
                  trig.label as triggered_by_label, dec.label as decided_by_label
             from gate_decision gd
             join dataset_version dv on dv.id = gd.dataset_version_id
             join dataset d on d.id = dv.dataset_id
             left join directory trig on trig.id = gd.triggered_by
             left join directory dec on dec.id = gd.decided_by
            where gd.id = %s""",
        (decision_id,),
    )
    if not row:
        raise HTTPException(404, "no such gate decision")
    # Absent rather than refused, the same shape every other cross-tenant read
    # here takes: saying "forbidden" would confirm the row exists.
    if row["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such gate decision")

    out = dict(row)
    uploader = db.one("select pv.uploaded_by from pipeline_run pr join pipeline_version pv on pv.id = pr.pipeline_version_id where pr.id = %s",
                      (row["pipeline_run_id"],)) if row["pipeline_run_id"] else None
    # A run that was not started from an uploaded pipeline version (a built-in one) has nobody who uploaded it.
    out["pipeline_uploaded_by"] = uploader["uploaded_by"] if uploader else None
    _say_who_decides(out, _gate_deciders(identity["tenant_id"]), identity["id"])
    out["leaks"] = db.all_rows(
        """select record_id, span_start, span_end, entity, identifier,
                  left_in_the_clear, coverage, direct, practice
             from gate_leak where gate_decision_id = %s
            order by practice, direct desc, coverage asc""",
        (decision_id,),
    )
    out["run"] = _run_that_produced(row["pipeline_run_id"]) if row["pipeline_run_id"] else None
    out["steps"] = db.all_rows(
        """select ar.id, ar.status, ar.started_at, ar.ended_at,
                  da.name as action_name, ar.params ->> 'step' as step_name
             from action_run ar
             join dataset_action da on da.id = ar.action_id
            where ar.pipeline_run_id = %s
            order by ar.started_at""",
        (row["pipeline_run_id"],),
    ) if row["pipeline_run_id"] else []
    return _jsonable(out)
