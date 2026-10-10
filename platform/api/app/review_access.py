"""Who may open the files of a result that is waiting for a decision, to judge it.

A result of a pipeline run waits for somebody to release it or hold it back. To judge it, the person has to be able to look at what it holds, and
nobody else may: what a run makes is no more open than what it was made from. So the door opens a version's files to a reviewer for exactly as
long as that version has a decision waiting for them, and for a bounded number of files (`release_review_records` in the policy).

The person has to be one the policy names as able to decide, held now (a role or a live grant of it), and must be neither the person who started the
run nor the person who uploaded the version of the pipeline that made it. When the decision is made the right ends at once, because it is worked
out again on every request.

This module only answers the question. The door (`record_door`) acts on the answer and the decision (`release_review`) uses it.
"""

from __future__ import annotations

from . import db, opa


def decider_roles() -> list[str]:
    """The roles the policy names as able to decide a de-identification result."""
    return list(opa.read_document("munitas/access/decider_roles") or [])


def limit() -> int:
    """How many different files of one result a reviewer may open."""
    return int(opa.read_document("munitas/access/release_review_records"))


def waiting_decision(tenant_id: str, version_id: str) -> dict | None:
    """The decision waiting on this version, with who started the run and who uploaded the pipeline version, or None."""
    return db.one(
        """select gd.id, gd.triggered_by, gd.recommendation, gd.pipeline_run_id,
                  (select pv.uploaded_by from pipeline_run pr join pipeline_version pv on pv.id = pr.pipeline_version_id
                    where pr.id = gd.pipeline_run_id) as pipeline_uploaded_by
             from gate_decision gd
            where gd.tenant_id = %s and gd.dataset_version_id = %s and gd.state = 'pending'
            order by gd.created_at desc limit 1""", (tenant_id, version_id))


def may_review(person_id: str, tenant_id: str, version_id: str) -> dict | None:
    """The waiting decision this person may open the files of the version to judge, or None."""
    waiting = waiting_decision(tenant_id, version_id)
    if not waiting or person_id in (waiting["triggered_by"], waiting["pipeline_uploaded_by"]):
        return None
    holds = db.one("select exists (select 1 from unnest(%s::text[]) r where person_holds_live_role(%s, r)) as yes", (decider_roles(), person_id))
    return waiting if holds and holds["yes"] else None


def opened_by(person_id: str, version_id: str) -> list[str]:
    """The different files this person has opened to review this version, in the order they first opened them."""
    return [r["record_id"] for r in db.all_rows(
        """select record_id from record_read_log where principal = %s and version_id = %s and basis = 'review'
            group by record_id order by min(at)""", (person_id, version_id))]
