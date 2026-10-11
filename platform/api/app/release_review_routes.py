"""The route that shows a reviewer the evidence for a result waiting for their decision.

Thin on purpose: the checks, the limit on files a reviewer may open and what is recorded with the decision live in `release_review` and
`review_access`. The same function answers here and when the decision is made, so the page cannot say "you may release this" and be refused.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from . import auth, db, gate_purposes, opa, person_keys, release_bar_routes, release_review, review_access
from .person_errors import NoSuchVersion

router = APIRouter(tags=["release review"])


@router.get("/gate-decisions/{decision_id}/review")
def get_review(decision_id: str, release_bar_id: str | None = None, identity: dict = Depends(auth.current_session)) -> dict:
    """The files of the version a decision is about, with the checks the platform ran on each, what the caller has opened so far, and whether
    anything stops the caller releasing it now under the release bar named (the platform's built-in one when none is). Opens nothing for the caller
    to read; the files are opened through the file door."""
    row = db.one("select * from gate_decision where id = %s", (decision_id,))
    if not row or row["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such gate decision")
    try:
        recommendation = gate_purposes.judge(row, release_bar_routes.resolve(identity["tenant_id"], release_bar_id))["recommendation"]
    except gate_purposes.NotJudgeable as exc:
        raise HTTPException(409, {"reasons": [str(exc)]}) from exc
    try:
        service = person_keys.key_service()
    except RuntimeError as exc:
        raise HTTPException(503, {"reasons": [f"the key service is not set up: {exc}"]}) from exc
    try:
        found = release_review.evidence(service, identity, row)
    except NoSuchVersion as exc:
        raise HTTPException(404, "no such gate decision") from exc
    uploaded_by = db.one("select pv.uploaded_by from pipeline_run pr join pipeline_version pv on pv.id = pr.pipeline_version_id where pr.id = %s",
                         (row["pipeline_run_id"],)) if row["pipeline_run_id"] else None
    allowed, reasons = opa.may_decide_gate({"decider": {"id": identity["id"], "roles": identity["roles"]},
                                            "gate": {"triggered_by": row["triggered_by"], "pipeline_uploaded_by": uploaded_by["uploaded_by"] if uploaded_by else None}})
    pending = row["state"] == "pending"
    return {"decision_id": decision_id, "version_id": found["version_id"], "state": row["state"], "files": found["files"], "checks_failed": found["failed"],
            "opened": found["opened"], "limit": found["limit"], "may_review": bool(pending and allowed and review_access.may_review(identity["id"], identity["tenant_id"], found["version_id"])),
            "blockers": release_review.blockers(found, row, recommendation) if pending and allowed else [], "not_allowed": [] if allowed else reasons,
            "recorded": (row["handoff"] or {}).get("review")}
