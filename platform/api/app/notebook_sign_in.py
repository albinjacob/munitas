"""A notebook signs in on behalf of a person: it shows a code, the person approves it in the console, and the notebook receives a short-lived pass.

The pass is issued, renewed and revoked by Hydra (an OAuth 2.0 server that holds no people), and this module is everything around it that is ours:

  * the notebook never talks to Hydra. It talks to the routes in `notebook_sign_in_routes`, which start the sign-in, forward the polling and the renewal, and
    answer for a person who pressed Deny (Hydra's own answer to that is to keep saying "pending" until the code runs out);
  * the person's side is three steps in the console, each asked of this platform with the console's own session: type the code shown in the notebook, be signed
    in (the console session is the sign-in, so Kratos is not asked to hand over to Hydra), and approve what the notebook will be able to do;
  * every sign-in is a row here (`notebook_sign_in`): who, what the notebook called itself, from which address, when, until when, when it was last used and
    whether the address changed. That row is what the person sees under "Your notebooks" and what an auditor reads. Hydra's own list of a person's sign-ins
    holds none of that;
  * a pass is accepted by `resolve_pass`, called from the one place every acting route already passes through (`auth._resolve_session`). It asks Hydra whether
    the token is live on every request, finds the sign-in row, and lets the request through only for the short list of routes in `ALLOWED`. Anything else, such
    as approving a request or changing a role, is refused with a reason, even for a person who may do it in the console.

Nothing in this module needs the address Hydra's public side is reached at from a browser: that is Hydra's own setting (`URLS_SELF_PUBLIC`).
"""

from __future__ import annotations

import hashlib
import re
import threading
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException, Request

from . import auth, config, db

SCOPE = "notebook"
# A person has this long to type the code, the same as Hydra's own life for it (`ttl.device_user_code`).
CODE_MINUTES = 10
# How many sign-ins one address may start in `CODE_MINUTES` (generous, because an office shares one address), and how many wrong codes one person may type in that time.
STARTS_PER_ADDRESS = 30
WRONG_CODES_PER_PERSON = 5
# A notebook that has made a request this recently may be in the middle of work, and the console says so before ending it.
IN_USE_MINUTES = 10
# What the notebook is told when it asks again too soon (RFC 8628 section 3.5). Hydra's own interval is a hint only; this enforces it.
POLL_SECONDS = 5

# The routes a notebook's pass may reach, and nothing else. This is the whole of "a notebook cannot approve anything": it is not that those routes check
# the pass, it is that the pass never gets past `resolve_pass` to them. A route is added here only when the client library calls it.
ALLOWED: tuple[tuple[str, re.Pattern], ...] = tuple((m, re.compile(p)) for m, p in (
    ("GET", r"^/auth/whoami$"),
    ("GET", r"^/datasets$"),
    ("GET", r"^/datasets/[^/]+/versions$"),
    ("POST", r"^/leases/requests$"),
    ("POST", r"^/iceberg/tokens$"),
    ("POST", r"^/derivations$"),
    ("POST", r"^/derivations/[^/]+/confirm$"),
    ("GET", r"^/derivations/[^/]+$"),
    ("GET", r"^/lineage/[^/]+$"),
    ("POST", r"^/dataset-versions/[^/]+/files-token$"),
    # A notebook may end its own sign-in (the route refuses anybody else's).
    ("POST", r"^/notebook-sign-ins/[^/]+/revoke$"),
))

NOT_THIS = ("a notebook sign-in reaches only what the client library does: finding datasets, asking for access, opening the few files you were allowed, "
            "and making a dataset from a query. Anything else, such as deciding a request, is done in the console")

_client_ready = threading.Lock()
_client_made = False


def digest(value: str) -> str:
    return hashlib.sha256(value.strip().upper().encode("utf-8")).hexdigest()


def _admin(method: str, path: str, **kw) -> httpx.Response:
    try:
        return httpx.request(method, f"{config.HYDRA_ADMIN_URL}{path}", timeout=10.0, **kw)
    except httpx.HTTPError as exc:
        raise HTTPException(503, {"reasons": [f"could not reach the sign-in service: {exc}"]}) from exc


def _public(method: str, path: str, **kw) -> httpx.Response:
    try:
        return httpx.request(method, f"{config.HYDRA_PUBLIC_URL}{path}", timeout=10.0, **kw)
    except httpx.HTTPError as exc:
        raise HTTPException(503, {"reasons": [f"could not reach the sign-in service: {exc}"]}) from exc


def ensure_client() -> None:
    """The one client every notebook signs in as, made in Hydra if it is not there: public (a notebook holds no secret), device sign-in and renewal only."""
    global _client_made
    if _client_made:
        return
    with _client_ready:
        if _client_made:
            return
        found = _admin("GET", f"/admin/clients/{config.NOTEBOOK_CLIENT_ID}")
        if found.status_code == 404:
            made = _admin("POST", "/admin/clients", json={
                "client_id": config.NOTEBOOK_CLIENT_ID, "client_name": "Munitas notebook", "token_endpoint_auth_method": "none",
                "grant_types": ["urn:ietf:params:oauth:grant-type:device_code", "refresh_token"], "scope": f"offline_access {SCOPE}"})
            if made.status_code not in (200, 201):
                raise HTTPException(503, {"reasons": [f"the sign-in service would not accept the notebook client: {made.status_code}"]})
        elif found.status_code != 200:
            raise HTTPException(503, {"reasons": [f"could not check the notebook client: {found.status_code}"]})
        _client_made = True


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _agent(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:200]


def _hydra_refusal(r: httpx.Response) -> dict:
    try:
        return r.json()
    except ValueError:
        return {"error": "server_error", "error_description": r.text[:200]}


# The notebook's side -------------------------------------------------------------------------------------------------------------------------------------


def start(request: Request, label: str) -> dict:
    """The notebook asks to sign in. Returns the code to show, the address to open, and what to poll with. Nobody is identified yet."""
    ensure_client()
    ip = _ip(request)
    recent = db.one("select count(*) as n from notebook_sign_in where created_ip = %s and created_at > now() - make_interval(mins => %s)", (ip, CODE_MINUTES))["n"]
    if recent >= STARTS_PER_ADDRESS:
        raise HTTPException(429, {"reasons": [f"this address has started {recent} notebook sign-ins in the last {CODE_MINUTES} minutes. Wait a few minutes"]})
    r = _public("POST", "/oauth2/device/auth", data={"client_id": config.NOTEBOOK_CLIENT_ID, "scope": f"offline_access {SCOPE}"})
    if r.status_code != 200:
        raise HTTPException(503, {"reasons": [f"the sign-in service did not start a sign-in: {r.status_code}"]})
    got = r.json()
    db.execute(
        """insert into notebook_sign_in (id, user_code_hash, device_code_hash, label, created_ip, created_user_agent)
           values (gen_random_uuid(), %s, %s, %s, %s, %s) returning id""",
        (digest(got["user_code"]), digest(got["device_code"]), (label or "a notebook").strip()[:80], ip, _agent(request)))
    return {"user_code": got["user_code"], "verification_uri": got["verification_uri"], "verification_uri_complete": got["verification_uri_complete"],
            "device_code": got["device_code"], "expires_in": got["expires_in"], "interval": max(POLL_SECONDS, int(got.get("interval", POLL_SECONDS)))}


def poll(device_code: str) -> tuple[int, dict]:
    """The notebook asks whether the person has approved. Forwarded to Hydra, except where this platform knows better: denied, too soon, or run out."""
    row = db.one("select id, status, created_at, last_poll_at from notebook_sign_in where device_code_hash = %s", (digest(device_code),))
    if row is None:
        return 400, {"error": "invalid_grant", "error_description": "this code was not started here, or has been used"}
    if row["status"] == "denied":
        return 400, {"error": "access_denied", "error_description": "the person refused this sign-in"}
    now = datetime.now(timezone.utc)
    if row["status"] == "pending" and now - row["created_at"] > timedelta(minutes=CODE_MINUTES):
        db.execute("update notebook_sign_in set status = 'expired' where id = %s returning id", (row["id"],))
        return 400, {"error": "expired_token", "error_description": "nobody approved this sign-in in time. Start again"}
    if row["last_poll_at"] and (now - row["last_poll_at"]).total_seconds() < POLL_SECONDS - 0.5:
        return 400, {"error": "slow_down", "error_description": f"ask no more often than every {POLL_SECONDS} seconds"}
    db.execute("update notebook_sign_in set last_poll_at = now() where id = %s returning id", (row["id"],))
    r = _public("POST", "/oauth2/token", data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": config.NOTEBOOK_CLIENT_ID, "device_code": device_code})
    return r.status_code, _hydra_refusal(r)


def renew(refresh_token: str) -> tuple[int, dict]:
    """The notebook renews its pass. Hydra replaces the renewal token; using an old one again ends the sign-in, which Hydra does and the notebook is told."""
    r = _public("POST", "/oauth2/token", data={"grant_type": "refresh_token", "client_id": config.NOTEBOOK_CLIENT_ID, "refresh_token": refresh_token})
    return r.status_code, _hydra_refusal(r)


# The person's side --------------------------------------------------------------------------------------------------------------------------------------


def accept_code(identity: dict, device_challenge: str, user_code: str) -> dict:
    """The person types the code the notebook is showing. Only a code this platform started is accepted, and only a few wrong ones are allowed."""
    ensure_client()
    failed = db.one("select count(*) as n from notebook_code_attempt where principal = %s and not ok and at > now() - make_interval(mins => %s)",
                    (identity["id"], CODE_MINUTES))["n"]
    if failed >= WRONG_CODES_PER_PERSON:
        raise HTTPException(429, {"reasons": [f"{failed} wrong codes in the last {CODE_MINUTES} minutes. Wait, then read the code from the notebook again"]})
    row = db.one("select id, status, created_at from notebook_sign_in where user_code_hash = %s", (digest(user_code),))
    fresh = row is not None and row["status"] == "pending" and datetime.now(timezone.utc) - row["created_at"] <= timedelta(minutes=CODE_MINUTES)
    db.execute("insert into notebook_code_attempt (principal, ok) values (%s, %s) returning at", (identity["id"], fresh))
    if not fresh:
        raise HTTPException(400, {"reasons": ["that is not a code a notebook is waiting on. Check it matches the one in the notebook, which lasts "
                                              f"{CODE_MINUTES} minutes"]})
    r = _admin("PUT", "/admin/oauth2/auth/requests/device/accept", params={"device_challenge": device_challenge}, json={"user_code": user_code.strip().upper()})
    if r.status_code != 200:
        raise HTTPException(400, {"reasons": ["the sign-in service did not accept that code. Start again from the notebook"]})
    db.execute("update notebook_sign_in set principal = %s, tenant_id = %s, code_accepted_at = now() where id = %s returning id",
               (identity["id"], identity["tenant_id"], row["id"]))
    return {"redirect_to": r.json()["redirect_to"]}


def login(identity: dict, login_challenge: str) -> dict:
    """The sign-in step Hydra sends the browser to: the person is already signed in to the console, so that session is the sign-in. Refused when the person
    now signed in is not the one who typed the code."""
    got = _admin("GET", "/admin/oauth2/auth/requests/login", params={"login_challenge": login_challenge})
    if got.status_code != 200:
        raise HTTPException(400, {"reasons": ["this sign-in request is not good any more. Start again from the notebook"]})
    request = got.json()
    if request["client"]["client_id"] != config.NOTEBOOK_CLIENT_ID:
        raise HTTPException(400, {"reasons": ["this sign-in is not for a notebook"]})
    waiting = db.one("select id from notebook_sign_in where principal = %s and status = 'pending' and code_accepted_at is not null order by code_accepted_at desc limit 1",
                     (identity["id"],))
    if waiting is None:
        raise HTTPException(403, {"reasons": ["you have not typed a code a notebook is waiting on. Start again from the notebook"]})
    done = _admin("PUT", "/admin/oauth2/auth/requests/login/accept", params={"login_challenge": login_challenge}, json={"subject": identity["id"], "remember": False})
    if done.status_code != 200:
        raise HTTPException(400, {"reasons": ["the sign-in service did not accept the sign-in. Start again from the notebook"]})
    return {"redirect_to": done.json()["redirect_to"]}


def _consent_request(identity: dict, consent_challenge: str) -> dict:
    """Hydra's own record of the approval being asked for, checked to be for this person."""
    got = _admin("GET", "/admin/oauth2/auth/requests/consent", params={"consent_challenge": consent_challenge})
    if got.status_code != 200:
        raise HTTPException(400, {"reasons": ["this approval is not good any more. Start again from the notebook"]})
    request = got.json()
    if request.get("subject") != identity["id"]:
        raise HTTPException(403, {"reasons": ["this approval is for somebody else"]})
    return request


def consent_details(identity: dict, consent_challenge: str) -> dict:
    """What the person is asked to approve: which notebook, from where, and what it will be able to do. Never shows a token."""
    _consent_request(identity, consent_challenge)
    row = db.one("select id, label, created_ip, created_user_agent, created_at from notebook_sign_in "
                 "where principal = %s and status = 'pending' and code_accepted_at is not null order by code_accepted_at desc limit 1", (identity["id"],))
    if row is None:
        raise HTTPException(403, {"reasons": ["there is no notebook waiting for your approval"]})
    return {"sign_in_id": str(row["id"]), "label": row["label"], "address": row["created_ip"], "program": row["created_user_agent"],
            "asked_at": row["created_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "hours": config.NOTEBOOK_SIGN_IN_HOURS,
            "may": ["find the datasets and tables you may read", "ask for access in your name", "open the few files you were allowed to open in a notebook",
                    "make a new dataset from a query over datasets you may read"],
            "may_not": ["decide anybody's request, including your own", "change roles, departments or settings", "read anything you cannot read in the console"]}


def decide(identity: dict, consent_challenge: str, approve: bool) -> dict:
    """The person approves or refuses. Approving gives the notebook a pass that lasts `NOTEBOOK_SIGN_IN_HOURS` at most and is renewed in short pieces."""
    details = consent_details(identity, consent_challenge)
    request_id = _consent_request(identity, consent_challenge)["consent_request_id"]
    if not approve:
        r = _admin("PUT", "/admin/oauth2/auth/requests/consent/reject", params={"consent_challenge": consent_challenge},
                   json={"error": "access_denied", "error_description": "the person refused this sign-in"})
        db.execute("update notebook_sign_in set status = 'denied', ended_at = now(), ended_by = %s where id = %s returning id", (identity["id"], details["sign_in_id"]))
        if r.status_code != 200:
            raise HTTPException(400, {"reasons": ["the sign-in service did not record the refusal. The sign-in is refused here all the same"]})
        return {"redirect_to": r.json()["redirect_to"], "approved": False}
    r = _admin("PUT", "/admin/oauth2/auth/requests/consent/accept", params={"consent_challenge": consent_challenge},
               json={"grant_scope": [SCOPE, "offline_access"], "remember": False, "session": {"access_token": {"sign_in": details["sign_in_id"]}}})
    if r.status_code != 200:
        raise HTTPException(400, {"reasons": ["the sign-in service did not accept the approval. Start again from the notebook"]})
    db.execute("""update notebook_sign_in set status = 'approved', approved_at = now(), consent_request_id = %s,
                         expires_at = now() + make_interval(hours => %s) where id = %s returning id""",
               (request_id, config.NOTEBOOK_SIGN_IN_HOURS, details["sign_in_id"]))
    return {"redirect_to": r.json()["redirect_to"], "approved": True}


def _shape(row: dict) -> dict:
    out = {k: row[k] for k in ("id", "principal", "label", "status", "created_ip", "last_ip", "address_changes")}
    out["id"] = str(out["id"])
    for k in ("created_at", "approved_at", "expires_at", "last_used_at", "ended_at"):
        out[k] = row[k].astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if row[k] else None
    now = datetime.now(timezone.utc)
    out["in_force"] = row["status"] == "approved" and row["expires_at"] is not None and row["expires_at"] > now
    out["in_use"] = out["in_force"] and row["last_used_at"] is not None and now - row["last_used_at"] <= timedelta(minutes=IN_USE_MINUTES)
    out["in_use_minutes"] = IN_USE_MINUTES
    return out


def list_for(identity: dict, everyone: bool) -> list[dict]:
    """A person's own notebook sign-ins, or everybody's in the organisation for an auditor. Newest first."""
    if everyone:
        rows = db.all_rows("select * from notebook_sign_in where tenant_id = %s and status <> 'pending' order by created_at desc limit 200", (identity["tenant_id"],))
    else:
        rows = db.all_rows("select * from notebook_sign_in where principal = %s and status <> 'pending' order by created_at desc limit 200", (identity["id"],))
    names = {r["id"]: r["label"] for r in db.all_rows("select id, label from directory where id = any(%s)", ([r["principal"] for r in rows if r["principal"]],))} if rows else {}
    return [{**_shape(r), "person": names.get(r["principal"], r["principal"])} for r in rows]


def revoke(identity: dict, sign_in_id: str) -> dict:
    """The person ends one of their notebook sign-ins. The pass and the renewal token stop at once, and the row says who and when."""
    row = db.one("select * from notebook_sign_in where id = %s and principal = %s", (sign_in_id, identity["id"]))
    if row is None:
        raise HTTPException(404, "no such notebook sign-in")
    if row["status"] != "approved":
        raise HTTPException(409, {"reasons": [f"this sign-in is already {row['status']}"]})
    if row["consent_request_id"]:
        # Hydra ends the one approval by its own id, and the person's other notebooks keep working. The id must be sent alone: with the person and the client
        # as well, Hydra refuses the call (tried 2026-10-08, docs/internal/dev/hydra-spike/NOTES.md).
        r = _admin("DELETE", "/admin/oauth2/auth/sessions/consent", params={"consent_request_id": row["consent_request_id"]})
        ended_ids = [row["id"]]
    else:
        # A sign-in recorded before the id was kept can only be ended with the rest: Hydra then ends every notebook sign-in of the person, and the rows say so.
        r = _admin("DELETE", "/admin/oauth2/auth/sessions/consent", params={"subject": identity["id"], "client": config.NOTEBOOK_CLIENT_ID})
        ended_ids = [x["id"] for x in db.all_rows("select id from notebook_sign_in where principal = %s and status = 'approved'", (identity["id"],))]
    if r.status_code not in (200, 204):
        raise HTTPException(503, {"reasons": ["the sign-in service did not end the sign-in. It is still in force: try again"]})
    db.execute("update notebook_sign_in set status = 'revoked', ended_at = now(), ended_by = %s where id = any(%s) and status = 'approved' returning id",
               (identity["id"], ended_ids))
    return _shape(db.one("select * from notebook_sign_in where id = %s", (sign_in_id,)))


def revoke_all(identity: dict) -> dict:
    """The person ends every notebook sign-in they have, at once: for a lost laptop, a leaked pass, or not knowing which. Returns which were ended."""
    rows = db.all_rows("select * from notebook_sign_in where principal = %s and status = 'approved' and expires_at > now()", (identity["id"],))
    r = _admin("DELETE", "/admin/oauth2/auth/sessions/consent", params={"subject": identity["id"], "client": config.NOTEBOOK_CLIENT_ID})
    if r.status_code not in (200, 204):
        raise HTTPException(503, {"reasons": ["the sign-in service did not end the sign-ins. They are still in force: try again"]})
    db.execute("update notebook_sign_in set status = 'revoked', ended_at = now(), ended_by = %s where principal = %s and status = 'approved' returning id",
               (identity["id"], identity["id"]))
    return {"ended": len(rows), "sign_ins": [{"id": str(x["id"]), "label": x["label"]} for x in rows]}


# A pass presented to the platform ------------------------------------------------------------------------------------------------------------------------


def is_pass(request: Request) -> bool:
    return request.headers.get("authorization", "").lower().startswith("bearer ory_at_")


def resolve_pass(request: Request, refuse_closing: bool) -> dict:
    """The identity behind a notebook's pass, in the shape a console session gives, or the refusal. Asked on every request, so ending a sign-in stops the next one."""
    token = request.headers["authorization"][7:].strip()
    r = _admin("POST", "/admin/oauth2/introspect", data={"token": token})
    got = r.json() if r.status_code == 200 else {}
    if not got.get("active") or got.get("client_id") != config.NOTEBOOK_CLIENT_ID or SCOPE not in str(got.get("scope", "")).split():
        raise HTTPException(401, {"reasons": ["this notebook sign-in is not in force. Sign in again from the notebook"]})
    sign_in_id = (got.get("ext") or {}).get("sign_in")
    row = db.one("select * from notebook_sign_in where id = %s and status = 'approved' and expires_at > now()", (sign_in_id,)) if sign_in_id else None
    if row is None or row["principal"] != got.get("sub"):
        raise HTTPException(401, {"reasons": ["this notebook sign-in has ended. Sign in again from the notebook"]})
    path = request.url.path
    if not any(m == request.method and p.match(path) for m, p in ALLOWED):
        raise HTTPException(403, {"reasons": [NOT_THIS]})
    person = auth.identity_for(row["principal"])
    if not person:
        raise HTTPException(401, {"reasons": ["this notebook sign-in is for somebody who is not registered"]})
    if person.get("ended_at"):
        raise HTTPException(403, {"reasons": [f"{person['label']}'s appointment ended on {person['ended_at']:%Y-%m-%d}, so this sign-in cannot act"]})
    if refuse_closing:
        why = auth.closed_refusal(person["phase"], person["roles"])
        if why:
            raise HTTPException(403, {"reasons": [why]})
    ip, agent = _ip(request), _agent(request)
    changed = row["first_ip"] is not None and ip != row["first_ip"]
    db.execute("""update notebook_sign_in set last_used_at = now(), last_ip = %s, last_user_agent = %s, first_ip = coalesce(first_ip, %s),
                         first_user_agent = coalesce(first_user_agent, %s), address_changes = address_changes + %s where id = %s returning id""",
               (ip, agent, ip, agent, 1 if changed and ip != row["last_ip"] else 0, row["id"]))
    return {"id": person["id"], "tenant_id": person["tenant_id"], "label": person["label"], "kind": person["kind"], "roles": person["roles"], "phase": person["phase"],
            "session_id": str(row["id"]), "authenticated_at": row["approved_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            # Says the caller is a notebook's pass, not a console session, so a token made from it can be tied to the sign-in it came from.
            "via_notebook_sign_in": str(row["id"])}
