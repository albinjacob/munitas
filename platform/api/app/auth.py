"""A real, verifiable session, resolved to a real `directory` row.

`current_session` is a FastAPI dependency; `GET /auth/whoami` is the
endpoint that proves the mechanism on its own. The human-decision endpoints
built on `current_session` are `approve_lease`, `reject_lease`,
`revoke_lease`, `request_lease`, and the agent `deploy`/`start_run`/
`approve_run` trio.
`request_credential`'s `body.principal` and the other workload-facing
identity fields are still caller-supplied on purpose: workloads have no
Kratos session to present, and migrating that is a separate, larger piece
of work, not this one.

The trust chain, and why each link is the one it is
-----------------------------------------------------
1. A cookie or bearer token arrives with the request. Forwarded verbatim to
   Kratos's own `/sessions/whoami`, the same way Ory's own reverse-proxy
   pattern (oathkeeper) does it: this module never parses or verifies a
   session token itself, because that is exactly the kind of security-
   sensitive parsing that belongs in the identity provider, not reimplemented
   here.
2. Kratos returns the session's `identity.id` if the session is valid, a
   value Kratos assigns itself and that a signed-in person can never edit
   through their own settings flow (unlike the identity's `traits`, which are
   self-service editable once that flow is turned on). That is why the link
   to `directory` is keyed on `identity.id`, not on a trait like email: a
   trait-based link would let a compromised or later-editable field silently
   repoint who a session resolves to.
3. `directory.kratos_identity_id` is set exactly once, by an administrator
   running infra/kratos/seed-identities.py, never by a self-service flow.
   A verified `identity.id` with no matching row is refused rather than
   resolved to nobody-in-particular: that shape (a real session, no
   directory row) should not be reachable given registration is disabled,
   and if it ever is, failing closed is the only safe response.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from . import config, db

router = APIRouter(tags=["auth"])


def current_session(request: Request) -> dict:
    """The directory row behind this request's session, or a 401.

    A plain function taking `Request`, used with FastAPI's `Depends(...)`
    rather than folded into the endpoint below, so the same check can be
    reused on other endpoints as they migrate to requiring it, without
    duplicating the whoami call and the directory lookup at each call site.
    """
    forward = {}
    if cookie := request.headers.get("cookie"):
        forward["Cookie"] = cookie
    if auth := request.headers.get("authorization"):
        forward["Authorization"] = auth
    if not forward:
        raise HTTPException(401, {"reasons": ["no session cookie or bearer token was sent"]})

    try:
        who = httpx.get(
            f"{config.KRATOS_PUBLIC_URL}/sessions/whoami",
            headers=forward,
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            503, {"reasons": [f"could not reach the identity provider: {exc}"]}
        ) from exc

    if who.status_code != 200:
        raise HTTPException(401, {"reasons": ["no active session"]})

    session = who.json()
    identity_id = session["identity"]["id"]

    # `roles` here is computed, not the raw column: `directory.roles` is
    # only ever this person's permanent baseline (set once, at seeding), and
    # a `role_grant` approved through POST /people/role-requests/{id}/decide
    # used to have no effect anywhere, because nothing projected it back
    # onto this column. Reading the union live, on every request, means a
    # grant applies the moment it is approved and stops applying the moment
    # it is revoked or its `expires_at` passes -- no cache to invalidate, no
    # sweep job to expire it, the same "current state is a live read, never
    # a stored pointer" choice `agent_active_version` already makes for a
    # deployed version. `role_grant_live`'s own partial index
    # (`where not revoked`) exists for exactly this query.
    person = db.one(
        """select d.id, d.tenant_id, d.label, d.kind, d.ended_at,
                  array(
                      select distinct role from (
                          select unnest(d.roles) as role
                          union
                          select role from role_grant
                           where principal = d.id
                             and not revoked
                             and expires_at > now()
                      ) effective
                  ) as roles
             from directory d
            where d.kratos_identity_id = %s""",
        (identity_id,),
    )
    if not person:
        # A real, Kratos-verified session with no linked directory row.
        # Registration is disabled (kratos.yml), so every identity that
        # exists was created by an administrator alongside a directory link
        # in the same script; this branch is a safety net for that
        # invariant being violated, not a path anything is expected to take.
        raise HTTPException(
            401,
            {"reasons": [
                "this login is not connected to any registered directory row"
            ]},
        )

    # Ended appointments stop here, at the one place every acting endpoint
    # already passes through. Signing in is the identity provider's question
    # and a deactivated identity never reaches this line; this is the other
    # half, so somebody who has left stops acting even in the window before
    # that system is updated. Their row and every approval it names survive:
    # nobody is deleted, so nothing becomes unattributable.
    if person.get("ended_at"):
        raise HTTPException(
            403,
            {"reasons": [
                f"{person['label']}'s appointment ended on "
                f"{person['ended_at']:%Y-%m-%d}, so this session cannot act"
            ]},
        )

    return {
        "id": person["id"],
        "tenant_id": person["tenant_id"],
        "label": person["label"],
        "kind": person["kind"],
        "roles": person["roles"],
        "session_id": session["id"],
        "authenticated_at": session["authenticated_at"],
    }


def identity_for(person_id: str) -> dict | None:
    """The same identity current_session builds, for a person already known by
    id, when no session is involved (a catalog token names its holder).

    Roles are the live union current_session reads, so a role granted a minute
    ago applies and one revoked a minute ago does not. An ended appointment is
    returned as-is, with `ended_at` set, and the caller refuses it: this only
    answers who the person is, never whether they may act.
    """
    person = db.one(
        """select d.id, d.tenant_id, d.label, d.kind, d.ended_at,
                  array(
                      select distinct role from (
                          select unnest(d.roles) as role
                          union
                          select role from role_grant
                           where principal = d.id
                             and not revoked
                             and expires_at > now()
                      ) effective
                  ) as roles
             from directory d
            where d.id = %s""",
        (person_id,),
    )
    return person


@router.get("/auth/whoami")
def whoami(identity: dict = Depends(current_session)) -> dict:
    """Prove the mechanism: a real session resolves to a real directory row.

    Nothing downstream reads this endpoint yet. It exists so the login flow
    can be verified end to end (console logs in, calls this, sees its own
    identity come back) before anything is wired to depend on it.
    """
    return identity
