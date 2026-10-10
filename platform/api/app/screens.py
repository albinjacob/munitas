"""Which pages of the console a person may open.

The policy says who each page is for (platform/policy/screens.rego). This module is the API's two ways of using that answer: a route that tells the console
what the signed-in person may open, so that the menu and the "this page is for ..." sentence come from the policy and not from a list in the console, and a
helper that a route calls before it answers, so that the API refuses the same people the console would.

A route that lists data for the whole organisation, and is limited to some roles, calls `require_screen` with the page it belongs to. A route that scopes what it
returns to the caller instead of refusing (the holds a person answers for) does not.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from . import auth, opa

router = APIRouter(tags=["screens"])


def require_screen(screen: str, identity: dict) -> None:
    """Refuse with a 403 and the policy's reason unless the caller's roles may open this page. A policy engine that cannot be reached refuses too."""
    try:
        permitted, reasons = opa.may_open_screen(screen, list(identity["roles"]))
    except opa.PolicyUnavailable as exc:
        raise HTTPException(503, {"reasons": [str(exc)]}) from exc
    if not permitted:
        raise HTTPException(403, {"reasons": reasons})


@router.get("/screens")
def list_screens(identity: dict = Depends(auth.current_session)) -> dict:
    """Every page of the console, and what the signed-in person may do with each.

    Each page carries its path, the label the menu gives it, `open` (whether the caller may open it), `menu` (whether the menu offers it to the caller; it can
    be narrower than `open`, never wider) and `who` (the people it is for, in a phrase that completes "This page is for ..."). The answer is
    `{screens}`, keyed by the page's name.
    """
    try:
        return {"screens": opa.screens_for(list(identity["roles"]))}
    except opa.PolicyUnavailable as exc:
        raise HTTPException(503, {"reasons": [str(exc)]}) from exc
