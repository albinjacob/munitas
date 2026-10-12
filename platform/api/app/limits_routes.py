"""Reading the limits of an organisation, and asking what would happen to a request under them.

Anybody who is signed in may read their own organisation's limits: people should know what they may ask for. Nothing here changes a limit; until the page edits, an
operator sets one with `scripts/admin/set-limit.py`. The test uses the same decisions as the real request routes, with the same values in force, so the answer cannot
differ from what the real request would get.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import auth, opa
from . import limits as org_limits

router = APIRouter(tags=["limits"])

CAPABILITIES = {"locked_rows": "Ask to read rows of locked columns", "files_in_page": "Ask to open locked files in the page", "files_in_notebook": "Ask to open locked files in a notebook"}


class TestIn(BaseModel):
    capability: str = Field(description="locked_rows, files_in_page or files_in_notebook")
    role: str = Field(description="The role the person asking holds")
    size: int = Field(ge=0, le=10_000_000, description="How many rows (locked_rows) or files")
    length: int = Field(ge=0, le=10_000, description="How many days (locked_rows) or hours")


@router.get("/limits", summary="List the limits of the organisation")
def list_limits(identity: dict = Depends(auth.current_session)) -> dict:
    """Every limit of the caller's organisation: the value in force, the default, the most the platform allows, and who set it and why when the organisation has."""
    listed = org_limits.in_force(identity["tenant_id"])
    words = opa.role_words()
    return {"limits": list(listed.values()), "capabilities": CAPABILITIES, "roles": list(org_limits.ASKING_ROLES),
            "role_words": {role: words[role]["one"] for role in org_limits.ASKING_ROLES}}


@router.post("/limits/what-would-happen", summary="Test a request against the limits")
def what_would_happen(body: TestIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Would a request of this size, from a person holding this role, be accepted under this organisation's limits, and if not, which limit says so. Records nothing."""
    if body.capability not in CAPABILITIES:
        raise HTTPException(400, {"reasons": [f"the capability must be one of {', '.join(CAPABILITIES)}"]})
    limits = org_limits.limits_for(identity["tenant_id"])
    who = {"id": "what-would-happen", "roles": [body.role]}
    if body.capability == "locked_rows":
        allowed, reasons = opa.may_request_locked_lease({"requester": who, "limits": limits, "request": {"principal": who["id"], "purpose": "a test", "days": body.length, "rows": body.size}})
    else:
        kind = "console" if body.capability == "files_in_page" else "notebook"
        allowed, reasons = opa.may_request_file_lease({"requester": who, "limits": limits, "request": {"principal": who["id"], "kind": kind, "purpose": "a test", "hours": body.length, "records": body.size}})
    return {"allowed": bool(allowed), "reasons": reasons, "limits": limits}
