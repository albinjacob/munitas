"""Which versions the signed-in person can read, before they try.

The console used to answer this in the browser from the role floors alone,
which was a second copy of the policy that ignored leases and organisations.
This asks the policy instead, once for a whole list (`preview` in
access.rego), and the policy builds its answer from the same parts `allow`
is built from, so the preview cannot claim something the real check would
refuse.

Nothing is recorded in access_decision. Nothing is being attempted, and a
log that gained a row every time somebody opened the Datasets screen would
bury the refusals people actually met. The agent preflight in agents.py
takes the same position for the same reason.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from . import auth, db, models, opa

router = APIRouter()

MAX_VERSIONS = 1000

_READABLE = ("role", "lease")
_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


def locked_files_mark(mark: dict, file_lease: dict | None, custodian: dict) -> dict:
    """The mark of a version of locked files: what the person holds of it, not what their role or a lease on the whole version would cover.

    A role opens nothing of locked files and a lease on the whole version reads bytes nobody can use, so neither is mentioned. What counts is an
    approver's permission to open a few of the files: in force, waiting, or not yet asked for.
    """
    decider = custodian.get("labels") or ", ".join(custodian.get("approvers") or []) or None
    if file_lease and file_lease["status"] == "approved":
        until = file_lease["expires_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return {"mark": "lease", "purpose": file_lease["purpose"], "until": until, "locked": True}
    if file_lease:
        return {"mark": "pending", "decider_label": decider, "locked": True}
    if not custodian.get("approvers"):
        return {"mark": "no_approver", "locked": True}
    return {"mark": "ask", "decider_label": decider, "locked": True}


def choose_mark(*, reclaimed: bool, answer: dict, pending: bool,
                lease_rows: dict[str, dict], decider_label: str | None,
                has_custodian: bool) -> dict:
    """One mark per version, in a fixed order: the first that applies wins.

    `answer` is the policy's preview for this version. `lease_rows` holds the
    database rows behind its lease ids, read only for when a lease ended,
    which the policy does not need to know.
    """
    if reclaimed:
        return {"mark": "removed"}
    if answer["by_role"]:
        return {"mark": "role"}
    current = answer["current_leases"]
    if current:
        # A standing lease (no end) outlasts any bounded one.
        best = max(current, key=lambda l: (l["expires_at"] is None, l["expires_at"] or ""))
        return {"mark": "lease", "purpose": best["purpose"], "until": best["expires_at"]}
    if pending:
        return {"mark": "pending", "decider_label": decider_label}
    ended = answer["ended_leases"]
    if ended:
        def ended_at(lease: dict) -> datetime | None:
            row = lease_rows.get(lease["id"], {})
            return row.get("revoked_at") if lease["ended"] == "revoked" else row.get("expires_at")

        last = max(ended, key=lambda l: ended_at(l) or _EARLIEST)
        return {"mark": "ended", "ended": last["ended"], "ended_at": ended_at(last),
                "purpose": last["purpose"], "decider_label": decider_label}
    if has_custodian:
        return {"mark": "ask", "decider_label": decider_label}
    return {"mark": "no_approver"}


@router.post("/access-preview", response_model=models.AccessPreview)
def access_preview(body: models.AccessPreviewIn,
                   identity: dict = Depends(auth.current_session)) -> dict:
    """Mark each version with whether the signed-in person can read it.

    Who is asked about comes from the session, never the body. Versions are
    those listed plus every version of the datasets listed, limited to the
    person's own organisation: an id from elsewhere is left out rather than
    reported, the same non-disclosure the rest of the API keeps.
    """
    # Counted before the query as well as after: a thousand and one unknown
    # ids would otherwise resolve to nothing and pass.
    if len(body.dataset_ids) + len(body.version_ids) > MAX_VERSIONS:
        raise HTTPException(422, {"reasons": [
            f"at most {MAX_VERSIONS} datasets and versions at once"]})
    versions = db.all_rows(
        """select dataset_version_id, dataset_id, tenant_id, current_class, reclaimed_at
             from version_class
            where tenant_id = %s
              and (dataset_id = any(%s::uuid[]) or dataset_version_id = any(%s::uuid[]))""",
        (identity["tenant_id"], body.dataset_ids, body.version_ids),
    )
    if len(versions) > MAX_VERSIONS:
        raise HTTPException(422, {"reasons": [
            f"{len(versions)} versions asked about; at most {MAX_VERSIONS} at once"]})
    if not versions:
        return {"versions": {}, "datasets": {}}

    ids = [str(v["dataset_version_id"]) for v in versions]
    # Versions of locked files: what a role covers does not open them, so their mark comes from the permission to open a few files instead.
    locked_ids = {str(r["dataset_version_id"]) for r in db.all_rows(
        "select distinct dataset_version_id from file_person where dataset_version_id = any(%s::uuid[])", (ids,))}
    file_leases_of = {str(r["dataset_version_id"]): r for r in db.all_rows(
        """select distinct on (dataset_version_id) dataset_version_id, status, purpose, expires_at
             from file_lease
            where principal = %s and dataset_version_id = any(%s::uuid[])
              and (status = 'requested' or (status = 'approved' and expires_at > now()))
            order by dataset_version_id, (status = 'approved') desc, expires_at desc nulls last""",
        (identity["id"], list(locked_ids)))} if locked_ids else {}
    lease_rows = db.all_rows(
        """select id, dataset_version_id, purpose, pattern, approved_by, revoked, expires_at, revoked_at
             from access_lease
            where principal = %s and dataset_version_id = any(%s::uuid[])""",
        (identity["id"], ids),
    )
    pending = {str(r["dataset_version_id"]) for r in db.all_rows(
        """select dataset_version_id from lease_request
            where principal = %s and state = 'pending' and dataset_version_id = any(%s::uuid[])""",
        (identity["id"], ids),
    )}
    custodians = {str(r["dataset_version_id"]): r for r in db.all_rows(
        """select vc.dataset_version_id, vc.approvers,
                  (select string_agg(p.label, ', ' order by p.label) from directory p where p.id = any(vc.approvers)) as labels
             from version_custodian vc
            where vc.dataset_version_id = any(%s::uuid[])""",
        (ids,),
    )}

    try:
        answers = opa.preview({
            "principal": {
                "id": identity["id"],
                "tenant": identity["tenant_id"],
                "roles": identity["roles"],
                "leases": [db.policy_lease(r) for r in lease_rows],
            },
            "versions": [
                {"version_id": str(v["dataset_version_id"]), "tenant": v["tenant_id"],
                 "visibility_class": v["current_class"]}
                for v in versions
            ],
        })
    except opa.PolicyUnavailable as exc:
        raise HTTPException(503, {"reasons": [str(exc)]}) from exc

    by_id = {str(r["id"]): r for r in lease_rows}
    marks: dict[str, dict] = {}
    datasets: dict[str, dict] = {}
    for v in versions:
        vid = str(v["dataset_version_id"])
        answer = answers.get(vid)
        if not answer or not answer["same_tenant"]:
            continue
        custodian = custodians.get(vid) or {}
        mark = choose_mark(
            reclaimed=v["reclaimed_at"] is not None,
            answer=answer,
            pending=vid in pending,
            lease_rows=by_id,
            decider_label=custodian.get("labels") or ", ".join(custodian.get("approvers") or []) or None,
            has_custodian=bool(custodian.get("approvers")),
        )
        if vid in locked_ids and mark["mark"] != "removed":
            mark = locked_files_mark(mark, file_leases_of.get(vid), custodian)
        marks[vid] = mark
        count = datasets.setdefault(str(v["dataset_id"]), {"readable": 0, "total": 0})
        count["total"] += 1
        count["readable"] += mark["mark"] in _READABLE
    return {"versions": marks, "datasets": datasets}
