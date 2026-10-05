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

from . import auth, db

router = APIRouter(tags=["read"])


def _jsonable(rows: Any) -> Any:
    """UUIDs, timestamps and arrays into something JSON can carry."""
    return json.loads(json.dumps(rows, default=str))


@router.get("/datasets")
def list_datasets(
    q: str | None = None,
    widest_class: str | None = None,
    modality: str | None = None,
    has_versions: bool | None = None,
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
    identity: dict = Depends(auth.current_session),
) -> dict:
    """Datasets, filtered, with the total so the caller knows what is hidden.

    Filtering happens here rather than in the browser because a console that
    fetches everything and hides most of it is a console that gets slower as the
    platform gets more useful.

    The class shown is the widest any version has reached. A dataset whose newest
    version is raw but which has an older version released is not a raw dataset
    from an exposure point of view, and showing the newest would understate it.

    Where the data came from travels with each row, with the licence a
    HuggingFace fetch found and, when that licence corrected what was
    registered, the original answer and when it changed. The console showed
    a correction only at the moment of the fetch, so anybody arriving later
    saw the corrected answer and no sign it had ever been different.

    `has_versions` exists because a registered dataset with nothing in it is a
    real and easily missed state: somebody created it and never uploaded, and it
    otherwise sits in the list looking like a failure rather than an empty shelf.
    """
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
        filtered as (
          select id, name, tenant_id, created_at, modality, provenance,
                 department_id, version_count, latest_version,
                 tabled_versions, untabled_versions,
                 (tabled_versions + untabled_versions > 0) as is_table,
                 (tabled_versions + untabled_versions > 0 and tabled_versions < version_count) as table_missing,
                 license_tag, license_export_unmodified, license_export_modified,
                 provenance_registered_as, provenance_overridden_at,
                 classes[1] as widest_class
          from summary
          where (%(q)s::text is null or name ilike '%%' || %(q)s || '%%')
            and (%(klass)s::text is null or classes[1] = %(klass)s)
            and (%(modality)s::text is null or %(modality)s = any(modality))
            and (%(has)s::boolean is null
                 or (%(has)s = true and version_count > 0)
                 or (%(has)s = false and version_count = 0))
        )
    """
    params = {
        "tenant": identity["tenant_id"], "q": q, "klass": widest_class,
        "modality": modality, "has": has_versions, "limit": limit, "offset": offset,
    }
    rows = db.all_rows(
        filtered_cte + "select * from filtered order by created_at desc "
        "limit %(limit)s offset %(offset)s",
        params,
    )
    total = db.one(filtered_cte + "select count(*) as n from filtered", params)

    return _jsonable({
        "datasets": rows,
        "shown": len(rows),
        "total": total["n"],
        "limit": limit,
        "offset": offset,
    })


@router.get("/datasets/{dataset_id}/versions")
def list_dataset_versions(
    dataset_id: str,
    identity: dict = Depends(auth.current_session),
) -> list[dict]:
    """The versions inside one dataset.

    Narrowed to the caller's own organisation, returning nothing rather than
    refusing, for the reason given on `get_version` in `main.py`.
    """
    sql = """
        select vc.*, dv.content_hash, dv.record_count, dv.created_at, dv.sealed,
               exists (select 1 from iceberg_table_ref r where r.dataset_version_id = dv.id) as table_copy
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
    the check rather than the column being copied onto it. Two places recording
    which organisation something belongs to is two places that can disagree.
    """
    sql = """
        select ct.id, ct.from_class, ct.to_class, ct.decided_by,
               ct.decided_by_kind, ct.gate_evidence, ct.at
        from class_transition ct
        join dataset_version dv on dv.id = ct.dataset_version_id
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
    """Lease requests, with the department that owns what was asked for, and
    the total for real page controls.

    `custodian` narrows to requests that person can actually decide. Without it
    a custodian's queue showed every pending request in the platform under a
    heading naming their own department, including requests for data owned by
    somebody else and for data owned by nobody. A queue that lists work you
    cannot do is worse than an empty one.

    States are returned together by default. A queue showing only pending
    requests hides how the previous ones were decided, and that history is what
    tells a reviewer whether this one is unusual.

    `exclude_pending` exists for the console's own "what you have decided"
    lists, which want a page of history without the still-open queue mixed
    in. Filtering `state != "pending"` in the browser after the fact was
    the old approach; it silently mis-paginates the moment only one page's
    worth of rows is fetched at a time, since a pending row taking one of
    the 15 slots on a page means fewer than 15 decided rows actually show,
    and the reported total would count pending rows a "decided" page never
    displays. A real query parameter keeps the count and the rows in
    agreement, the same fix `/access-decisions`'s `resource` filter
    already made for the same shape of problem.

    `version` is returned because what is granted is one version, not a dataset.
    Two versions of the same dataset can sit at different sensitivities, so a
    request naming only the dataset asks the custodian to approve something
    they cannot see the shape of.

    `requested_by`/`requested_by_label` are set only when a human asked on a
    workload's behalf; the reading principal stays `lr.principal` regardless,
    since a workload's identity is never inferred from who invoked it.
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
    """Granted leases, with the total for real page controls.

    `active_only` filters to leases that are neither revoked nor expired. It is
    off by default, because an expired lease is evidence of who had access last
    week and deleting it from view would break the audit story.

    `standing` marks a lease with no expiry, granted only ever to a workload
    and lasting until revoked. `expires_at is null` is what "standing" means
    everywhere in this system, including here: `active` treats a standing
    lease as active until revoked, never as expired, which a bare
    `expires_at > now()` would get backwards (NULL, not true) rather than
    right.

    Called two shapes of way: `ResearcherHome`'s own "access you have been
    given" paginates one principal's leases directly; `CustodianHome` calls
    it with no `principal` and a large `limit` purely to build a lookup map
    (which lease backs which decided request), never rendered as a list in
    its own right -- that caller keeps asking for everything in one page
    rather than paginating, since a lookup missing a later page's entries
    would silently under-report which grants are still active.
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
    """
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
    """
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
                  av.entrypoint, av.requested_hosts,
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
    """
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

    Assembled from `action_run` and `access_decision` rather than from a status
    the services report about themselves. A service claiming to be healthy and a
    service that has successfully done something are different facts, and only
    the second is worth an administrator's attention.

    Denials are counted separately. A workload accumulating refusals is either
    misconfigured or reaching for something it should not, and both matter.

    Platform-wide, not one organisation's own: `platform_admin` runs the
    system and holds no standing access to any tenant's contents (the same
    property `housekeeping.py`'s platform view keeps), and this is
    telemetry about workloads, not customer data, so seeing it across every
    tenant does not cross that line. The console's own nav already hides
    this behind the same role; this is that check actually enforced.
    """
    if "platform_admin" not in identity["roles"]:
        raise HTTPException(403, {"reasons": [
            "only platform_admin may see workload activity across every organisation"
        ]})
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
    """The people and workloads the organisation registered.

    Deliberately not scoped to the caller's own tenant: `IdentityContext`'s
    own identity-resolution seam needs to find a session-resolved person in
    whichever organisation they actually belong to, before their tenant is
    known client-side, and every other organisation's roster is what a
    custodian-chooser needs to render honestly (see the docstring below).
    Authentication is still required -- only a real session may read it.

    This is the answer to "who exists", and it is the platform's answer rather
    than the console's. Before this endpoint the console carried its own list,
    which meant it could offer a custodian the database had never heard of and
    every approval that person made would be refused by a foreign key.

    Approvers carry the departments they answer for (`approver_of`, with the
    first of them as `department_name`), because a custodian who answers for no
    department can approve nothing and the console should be able to say so.
    A person who answers for several departments is listed once: listing them
    once per department is how a chooser ends up offering the same person as
    two different people.

    Only live tenants by default. Verification runs as its own principals in the
    canary tenant, and offering those alongside real people would put "Canary
    engineer" on the login screen of a customer's console. Pass `purpose=all` to
    see every tenant, which is what an operator investigating the platform
    itself wants.
    """
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
              join department_approver a on a.department_id = dept.id and a.removed_at is null
                                        and (a.valid_until is null or a.valid_until > now())
             where a.person_id = d.id and dept.tenant_id = d.tenant_id
        ) ap on true
        where (%s::text is null or d.kind = %s)
          and (%s = 'all' or t.purpose = %s)
        order by d.kind, d.id
    """
    return _jsonable(db.all_rows(sql, (kind, kind, purpose, purpose)))


@router.get("/policy/roles")
def policy_roles() -> dict:
    """The class floors and the approving roles, read from the policy engine.

    Not restated here. `role_floor` and `approver_roles` live in
    platform/policy/access.rego, and anything that copies them acquires the
    ability to disagree with them.

    The shape of the policy, not any tenant's data -- the same rules apply
    to every organisation, so this needs no session at all, unlike every
    other endpoint in this file. Left unauthenticated on purpose: the
    console's own `/roles` page is designed to explain what each role can do
    to somebody who has not signed in yet (see `web/src/features/roles/
    Roles.tsx`'s own module comment), and item 24's original security review
    (which made every other endpoint here session-gated) never intended to
    catch a page with no tenant data behind it -- confirmed live: the
    unauthenticated case broke outright ("no active session") until this was
    corrected.
    """
    from . import opa

    return {
        "class_order": opa.read_document("munitas/access/class_order"),
        "role_floor": opa.read_document("munitas/access/role_floor"),
        "approver_roles": opa.read_document("munitas/access/approver_roles"),
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
                          'person_id', a.person_id, 'label', p.label, 'added_at', a.added_at, 'valid_until', a.valid_until)
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
               ar.approved_by, ar.approved_at, ar.lease_request_id, ar.findings,
               ar.execution_mode
        from agent_run ar
        join agent_version av on av.id = ar.agent_version_id
        left join directory req on req.id = ar.requested_by
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


@router.get("/gate-decisions")
def list_gate_decisions(
    state: str | None = None,
    reviewer: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    identity: dict = Depends(auth.current_session),
) -> dict:
    """De-identification gate decisions, pending and past, with the total
    for real page controls.

    States come back together by default, the same choice the lease queue
    makes: a queue showing only what is pending hides how the previous ones
    went, and that history is what tells a reviewer whether this one is
    unusual.

    `reviewer` narrows to decisions that person may actually make, which today
    means excluding runs they started themselves. A queue listing work you
    cannot do is worse than an empty one.

    The leak identifiers are deliberately not in this list, only their count. A
    queue is read at a glance and over shoulders, and the personal data lives
    one further, deliberate click away.

    Envelope shape matches every other list endpoint: `{gate_decisions,
    shown, total, limit, offset}`. The total query filters `gate_decision`
    alone, without the two joins the row query needs: `state` and
    `reviewer` are both columns on `gd` itself, so the joins exist only to
    fetch display columns, never to narrow which rows match.
    """
    where = " where gd.tenant_id = %s"
    args: list[Any] = [identity["tenant_id"]]
    if state:
        where += " and gd.state = %s"
        args.append(state)
    if reviewer:
        where += " and (gd.triggered_by is null or gd.triggered_by <> %s)"
        args.append(reviewer)

    rows = db.all_rows(
        """
        select gd.id, gd.tenant_id, gd.dataset_version_id, gd.to_class,
               gd.score_card_id, gd.metrics, gd.recommendation,
               gd.recommendation_reason, gd.state, gd.triggered_by,
               gd.decided_by, gd.decided_at, gd.decision_reason, gd.created_at,
               gd.pipeline_run_id,
               d.name as dataset_name, dv.version, dv.visibility_class,
               trig.label as triggered_by_label,
               dec.label as decided_by_label,
               (select count(*) from gate_leak gl
                 where gl.gate_decision_id = gd.id) as leak_count
          from gate_decision gd
          join dataset_version dv on dv.id = gd.dataset_version_id
          join dataset d on d.id = dv.dataset_id
          left join directory trig on trig.id = gd.triggered_by
          left join directory dec on dec.id = gd.decided_by
        """ + where + " order by gd.created_at desc limit %s offset %s",
        (*args, limit, offset),
    )
    total = db.one("select count(*) as n from gate_decision gd" + where, tuple(args))
    return _jsonable({
        "gate_decisions": rows,
        "shown": len(rows),
        "total": total["n"],
        "limit": limit,
        "offset": offset,
    })


@router.get("/gate-decisions/{decision_id}")
def get_gate_decision(
    decision_id: str,
    identity: dict = Depends(auth.current_session),
) -> dict:
    """One decision, with the evidence a reviewer needs to make it.

    The leak list carries the identifier and what survived redaction, which is
    personal data and the reason this endpoint exists at all: judging severity
    is the whole job, and a count cannot tell a partial postcode from a
    patient's full name.

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
    out["leaks"] = db.all_rows(
        """select record_id, span_start, span_end, entity, identifier,
                  left_in_the_clear, coverage, direct
             from gate_leak where gate_decision_id = %s
            order by direct desc, coverage asc""",
        (decision_id,),
    )
    out["steps"] = db.all_rows(
        """select ar.id, ar.status, ar.started_at, ar.ended_at,
                  da.name as action_name
             from action_run ar
             join dataset_action da on da.id = ar.action_id
            where ar.pipeline_run_id = %s
            order by ar.started_at""",
        (row["pipeline_run_id"],),
    ) if row["pipeline_run_id"] else []
    return _jsonable(out)
