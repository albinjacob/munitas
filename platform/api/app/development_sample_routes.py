"""The routes for development samples: asking for one, deciding a request, ending a sample, and declaring files synthetic.

Thin on purpose. Who may do what, and why a request is refused, live in `development_samples` and the policy.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import auth, development_samples
from .person_errors import AlreadyDecided, LeaseConflict, NoSuchSample, NoSuchVersion, NotPermitted, Refused

router = APIRouter(tags=["development samples"])


class DevelopmentSampleRequestIn(BaseModel):
    kind: str = Field(description="'develop' to draw files for building a pipeline version, or 'debug' to name the files that went wrong")
    purpose: str
    days: int
    records: int = Field(description="How many files the sample holds")
    named_records: list[str] | None = Field(default=None, description="For a debug sample, the identifiers of the files")


class DevelopmentSampleDecisionIn(BaseModel):
    note: str | None = None
    records: int | None = Field(default=None, description="An approver may lower the number asked for, never raise it")
    days: int | None = Field(default=None, description="An approver may lower the days asked for, never raise them")


class SyntheticDeclarationIn(BaseModel):
    note: str = Field(description="Why these files hold no real person. Recorded with the declaration.")


def _refusals(call):
    try:
        return call()
    except NotPermitted as exc:
        raise HTTPException(403, {"reasons": exc.reasons}) from exc
    except (LeaseConflict, AlreadyDecided) as exc:
        raise HTTPException(409, {"reasons": exc.reasons}) from exc
    except Refused as exc:
        raise HTTPException(400, {"reasons": exc.reasons}) from exc
    except (NoSuchVersion, NoSuchSample) as exc:
        raise HTTPException(404, "no such version or sample") from exc


@router.get("/dataset-versions/{version_id}/development-samples")
def development_samples_of(version_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """What a screen needs on a version of files: the caller's samples, the requests waiting for them as an approver, what they may do, the limits.

    Never a file or a person. An approver sees how many files came from each length band, and nothing more.
    """
    return _refusals(lambda: development_samples.for_version(identity, version_id))


@router.post("/dataset-versions/{version_id}/development-samples", status_code=201)
def request_development_sample(version_id: str, body: DevelopmentSampleRequestIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Ask for a sample of these files for building a pipeline version: a purpose, up to 14 days, up to 50 files. A department approver decides."""
    return _refusals(lambda: development_samples.request_sample(identity, version_id, body.kind, body.purpose, body.days, body.records, body.named_records))


@router.post("/development-samples/{sample_id}/approve")
def approve_development_sample(sample_id: str, body: DevelopmentSampleDecisionIn | None = None, identity: dict = Depends(auth.current_session)) -> dict:
    """Approve a request, and the platform draws the files. Only an approver of the department that owns them, and never the person who asked.

    The approver may lower the number or the days asked for, and cannot choose the files.
    """
    return _refusals(lambda: development_samples.decide(identity, sample_id, True, body.note if body else None,
                                                        body.records if body else None, body.days if body else None))


@router.post("/development-samples/{sample_id}/decline")
def decline_development_sample(sample_id: str, body: DevelopmentSampleDecisionIn | None = None, identity: dict = Depends(auth.current_session)) -> dict:
    """Decline a request, with a note saying why."""
    return _refusals(lambda: development_samples.decide(identity, sample_id, False, body.note if body else None))


@router.post("/development-samples/{sample_id}/end")
def end_development_sample(sample_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """End a sample early: the department's approver, or the person it was drawn for."""
    return _refusals(lambda: development_samples.end(identity, sample_id))


@router.post("/dataset-versions/{version_id}/declare-synthetic", status_code=201)
def declare_synthetic(version_id: str, body: SyntheticDeclarationIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Declare a version synthetic, so that it holds no real person and any pipeline version in development may run on it. An approver of the owning department."""
    return _refusals(lambda: development_samples.declare_synthetic(identity, version_id, body.note))
