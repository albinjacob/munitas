"""A read-only Iceberg catalog, where a table appears only while you may read it.

WHAT IT IS

The standard Iceberg catalog API (the one DuckDB, Spark, Trino and PyIceberg
speak), served by the control plane. A person's own tool connects to it with a
token, lists the tables it may read, and opens one. Munitas decides each time,
the same way it decides every other read, and the tool then reads the files
straight from storage with a credential for that one version.

WHAT IT DOES NOT DO

It never decides anything of its own. Whether a person may read a version is
asked of the same policy engine, with the same leases, as a credential request
(`request_credential`, which also writes the audit rows and mints the key), and
which tables to list is asked of the same preview the console uses. This file is
the standard protocol wrapped round those two answers.

It is read-only on purpose. A table is written once, when its version is sealed,
and a sealed version never changes, so there is nothing for a client to write.
Every write the protocol offers is refused with a sentence saying so.

THE TOKEN

A person asks for a token (POST /iceberg/tokens) with their console session and
says what it is for. The token names the person and the purpose and nothing
else; each table it opens is still decided on its own. Only a hash is stored. It
stops working when it expires, when the person revokes it, or when the person's
appointment ends, and it is accepted by no other endpoint of this API.

WHAT "SHORT-LIVED" MEANS HERE

SeaweedFS has no temporary credentials. The key a lease is given is the lease's
own, and it stops working when the lease ends and storage permissions are next
printed (the same mechanism as every other read). The catalog adds no expiry of
its own to that key, and does not claim to.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

import psycopg.errors
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from . import access_preview, auth, config, db, grants, logs, models, storage

log = logs.get_logger("iceberg_catalog")
router = APIRouter(tags=["iceberg"])

TOKEN_PREFIX = "mct_"
_CHUNK = 500  # versions asked about at once; the preview refuses more than 1000

# What this catalog supports, in the spelling clients read from /v1/config.
# Listed so a client does not try anything else.
ENDPOINTS = [
    "GET /v1/{prefix}/namespaces",
    "GET /v1/{prefix}/namespaces/{namespace}",
    "HEAD /v1/{prefix}/namespaces/{namespace}",
    "GET /v1/{prefix}/namespaces/{namespace}/tables",
    "GET /v1/{prefix}/namespaces/{namespace}/tables/{table}",
    "HEAD /v1/{prefix}/namespaces/{namespace}/tables/{table}",
    "GET /v1/{prefix}/namespaces/{namespace}/tables/{table}/credentials",
]

READ_ONLY = ("this catalog is read-only: a table is written once, when its version is "
             "sealed, and a sealed version never changes")


class CatalogError(Exception):
    """An error in the shape Iceberg clients read: {"error": {message, type, code}}."""

    def __init__(self, status: int, kind: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status, self.kind, self.message, self.headers = status, kind, message, headers or {}


def handle_error(_request: Request, exc: CatalogError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status, headers=exc.headers,
        content={"error": {"message": exc.message, "type": exc.kind, "code": exc.status}},
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------- tokens --


@router.post("/iceberg/tokens", status_code=201)
def issue_token(body: models.CatalogTokenIn, request: Request,
                identity: dict = Depends(auth.current_session)) -> dict:
    """Give the signed-in person a token for their own tools.

    Returned once and never stored: only its hash is kept, so there is no way
    to read it back later, and a lost token is replaced, not recovered.
    """
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    hours = min(body.hours, config.CATALOG_TOKEN_MAX_HOURS)
    expires = _now() + timedelta(hours=hours)
    row = db.execute(
        """insert into catalog_token (id, token_hash, principal, tenant_id, purpose, expires_at)
           values (gen_random_uuid(), %s, %s, %s, %s, %s) returning id""",
        (_hash(token), identity["id"], identity["tenant_id"], body.purpose.strip(), expires),
    )
    log.info("catalog token issued", extra={"principal": identity["id"],
             "tenant_id": identity["tenant_id"], "purpose": body.purpose.strip()[:80]})
    return {
        "id": str(row["id"]), "token": token, "purpose": body.purpose.strip(),
        "tenant_id": identity["tenant_id"], "expires_at": expires.isoformat(),
        "hours": hours,
        # What a tool needs to connect: the address, and the warehouse name,
        # which is the organisation's own id.
        "catalog_url": str(request.base_url).rstrip("/") + "/iceberg",
        "warehouse": identity["tenant_id"],
    }


@router.get("/iceberg/tokens")
def list_tokens(identity: dict = Depends(auth.current_session)) -> dict:
    """The signed-in person's own tokens. Never the token itself."""
    rows = db.all_rows(
        """select id, purpose, created_at, expires_at, revoked_at, last_used_at
             from catalog_token where principal = %s order by created_at desc limit 100""",
        (identity["id"],),
    )
    return {"tokens": [{**r, "id": str(r["id"]),
                        "active": r["revoked_at"] is None and r["expires_at"] > _now()}
                       for r in rows]}


@router.post("/iceberg/tokens/{token_id}/revoke")
def revoke_token(token_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """End one of the signed-in person's own tokens now."""
    row = db.execute(
        """update catalog_token set revoked_at = coalesce(revoked_at, now())
            where id::text = %s and principal = %s returning id""",
        (token_id, identity["id"]),
    )
    if not row:
        # Not found and not yours look the same, so a token id cannot be probed.
        raise CatalogError(404, "NoSuchTokenException", "no such token")
    return {"id": token_id, "revoked": True}


def catalog_principal(request: Request) -> dict:
    """Who is calling, from the token alone. One refusal for every way a token
    can be bad, so the answer reveals nothing about which one it was."""
    header = request.headers.get("authorization", "")
    refusal = CatalogError(
        401, "NotAuthorizedException",
        "send the token you were given as 'Authorization: Bearer <token>'. It may "
        "have expired or been revoked: ask for a new one in the console")
    if not header.lower().startswith("bearer "):
        raise refusal
    row = db.one(
        """select id, principal, tenant_id, purpose, expires_at, revoked_at
             from catalog_token where token_hash = %s""",
        (_hash(header[7:].strip()),),
    )
    if not row or row["revoked_at"] is not None or row["expires_at"] <= _now():
        raise refusal
    person = auth.identity_for(row["principal"])
    # An ended appointment stops here, the same as it does for a session.
    if not person or person.get("ended_at"):
        raise refusal
    # A closing organisation's people can do nothing, and that includes reading
    # through a token they made earlier.
    if auth.closed_refusal(person.get("phase"), person["roles"]):
        raise refusal
    # A retired organisation's records stay readable but nothing more may be
    # written to it, and that includes this timestamp. Reading must not depend
    # on being able to record that it happened.
    try:
        db.execute("update catalog_token set last_used_at = now() where id = %s", (row["id"],))
    except psycopg.errors.ReadOnlySqlTransaction:
        pass
    return {**person, "purpose": row["purpose"], "token_id": str(row["id"])}


# --------------------------------------------------------------- the catalog --


def _own_warehouse(principal: dict, prefix: str) -> None:
    """A token opens its own organisation's tables and nobody else's. Another
    organisation's name is answered as if it did not exist."""
    if prefix != principal["tenant_id"]:
        raise CatalogError(404, "NoSuchNamespaceException", f"no such warehouse: {prefix}")


def _readable(principal: dict, version_ids: list[str]) -> set[str]:
    """Which of these versions the person can read right now, asked of the
    same preview the console uses (nothing is recorded: nothing is attempted)."""
    readable: set[str] = set()
    for start in range(0, len(version_ids), _CHUNK):
        chunk = version_ids[start:start + _CHUNK]
        answer = access_preview.access_preview(
            models.AccessPreviewIn(version_ids=chunk), identity=principal)
        readable |= {vid for vid, mark in answer["versions"].items()
                     if mark["mark"] in access_preview._READABLE}
    return readable


def _tables(principal: dict, namespace: str | None = None) -> list[dict]:
    """The projected versions this person may read, newest last."""
    rows = db.all_rows(
        """select dataset_version_id::text as version_id, namespace, table_name
             from iceberg_table_ref
            where tenant_id = %s and (%s::text is null or namespace = %s)
            order by namespace, projected_at""",
        (principal["tenant_id"], namespace, namespace),
    )
    ok = _readable(principal, [r["version_id"] for r in rows])
    return [r for r in rows if r["version_id"] in ok]


@router.get("/iceberg/v1/config")
def catalog_config(principal: dict = Depends(catalog_principal)) -> dict:
    """The warehouse is the organisation: whatever name a client asks for, the
    token's own organisation is what it gets."""
    return {"defaults": {}, "overrides": {"prefix": principal["tenant_id"]},
            "endpoints": ENDPOINTS}


@router.get("/iceberg/v1/{prefix}/namespaces")
def list_namespaces(prefix: str, principal: dict = Depends(catalog_principal)) -> dict:
    _own_warehouse(principal, prefix)
    names = sorted({t["namespace"] for t in _tables(principal)})
    return {"namespaces": [[n] for n in names]}


# GET and HEAD are registered as two routes, not one route for both methods: a route
# for several methods takes its operation id from an unordered set, so the published
# API reference would change from one start of the API to the next.
@router.head("/iceberg/v1/{prefix}/namespaces/{namespace}")
@router.get("/iceberg/v1/{prefix}/namespaces/{namespace}")
def get_namespace(prefix: str, namespace: str, request: Request,
                  principal: dict = Depends(catalog_principal)):
    _own_warehouse(principal, prefix)
    if not _tables(principal, namespace):
        raise CatalogError(404, "NoSuchNamespaceException", f"no such namespace: {namespace}")
    if request.method == "HEAD":
        return Response(status_code=204)
    return {"namespace": [namespace], "properties": {}}


@router.get("/iceberg/v1/{prefix}/namespaces/{namespace}/tables")
def list_tables(prefix: str, namespace: str, principal: dict = Depends(catalog_principal)) -> dict:
    _own_warehouse(principal, prefix)
    tables = _tables(principal, namespace)
    if not tables:
        raise CatalogError(404, "NoSuchNamespaceException", f"no such namespace: {namespace}")
    return {"identifiers": [{"namespace": [namespace], "name": t["table_name"]} for t in tables]}


@router.get("/iceberg/v1/{prefix}/namespaces/{namespace}/views")
def list_views(prefix: str, namespace: str, principal: dict = Depends(catalog_principal)) -> dict:
    """There are no views. Answered, not refused, because some clients ask."""
    _own_warehouse(principal, prefix)
    return {"identifiers": []}


def _metadata(ref: dict, version: dict) -> dict:
    """The table's metadata document, read from the version's own prefix."""
    bucket, key = ref["metadata_location"].removeprefix("s3://").split("/", 1)
    client = storage.admin_client_for(version["storage_backend"], ref["tenant_id"])
    return json.loads(client.get_object(Bucket=bucket, Key=key)["Body"].read())


def _vended(key: dict, ref: dict, prefix: str, namespace: str, table: str) -> dict:
    """The properties a client needs to read this table's files: where storage
    is, the key this person's access gave them, when that key stops working, and
    where to ask for the next one. A client that reads for longer than the key
    lasts has to ask again, and this is what tells it how."""
    return {
        "s3.endpoint": config.PUBLIC_S3_ENDPOINT,
        "s3.access-key-id": key["access_key"],
        "s3.secret-access-key": key["secret_key"],
        "s3.region": "us-east-1",
        "s3.path-style-access": "true",
        "s3.session-token-expires-at-ms": str(key["expires_at"] * 1000),
        "client.refresh-credentials-endpoint":
            f"v1/{prefix}/namespaces/{namespace}/tables/{table}/credentials",
    }


def _open(principal: dict, ref: dict) -> dict:
    """Decide whether this person may read this table now, record the decision,
    and mint the key that goes with it.

    The decision and its audit rows are request_credential's, the function
    behind POST /credentials, so a table opened here is recorded exactly as the
    same version read any other way would be. The key is not the one that
    function returns, which lasts as long as a lease does: it is the next key in
    the catalog's rolling series (grants.catalog_key_for), which lasts at most
    CATALOG_KEY_SECONDS. Every call decides afresh, so a refresh after access
    has ended is refused here, which is what ends a long read.
    """
    version = db.one("select storage_backend from dataset_version where id = %s", (ref["version_id"],))
    if version["storage_backend"] != "seaweedfs":
        raise CatalogError(501, "UnsupportedOperationException",
                           f"tables on {version['storage_backend']!r} storage are not served by this catalog yet")

    # Imported here: main imports this module to register it, so a module-level
    # import would be circular. By the time a request arrives both are loaded.
    from . import main as platform

    from fastapi import HTTPException
    request = models.CredentialRequest(
        principal=principal["id"], principal_kind="human",
        roles=principal["roles"] or ["notebook_explore"], tenant_id=principal["tenant_id"],
        dataset_version_id=ref["version_id"], purpose=principal["purpose"], decide_only=True,
    )
    try:
        grant = platform.request_credential(request)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        reasons = "; ".join(detail.get("reasons") or [str(exc.detail)])
        if exc.status_code == 403:
            raise CatalogError(403, "ForbiddenException", reasons) from exc
        raise CatalogError(503 if exc.status_code == 503 else exc.status_code,
                           "ServiceUnavailableException", reasons) from exc
    # A key rests on the person's live lease when there is one, so revoking the
    # lease ends it at once, not at the end of its time.
    lease = db.one(
        """select id::text as id from access_lease
            where principal = %s and dataset_version_id = %s and revoked = false
              and expires_at > now() order by expires_at desc limit 1""",
        (principal["id"], ref["version_id"]))
    key = grants.catalog_key_for(principal["id"], principal["tenant_id"], ref["version_id"],
                                 lease["id"] if lease else None)
    if key["needs_print"]:
        try:
            grants.reconcile(trigger="request")
        except Exception as exc:
            raise CatalogError(503, "ServiceUnavailableException",
                               "access is approved and takes effect once storage permissions are "
                               "updated, which is in progress. Try again shortly",
                               headers={"Retry-After": "5"}) from exc
    # The decision row was written when it was made. This is the other half of
    # the pair every credential request leaves: that a key was in fact issued.
    platform._record_decision(
        request, db.one("select * from version_class where dataset_version_id = %s", (ref["version_id"],)),
        "grant", True, ["a catalog key was issued"])
    return key


def _table_ref(principal: dict, prefix: str, namespace: str, table: str) -> dict:
    _own_warehouse(principal, prefix)
    ref = db.one(
        """select dataset_version_id::text as version_id, tenant_id, metadata_location, location
             from iceberg_table_ref where tenant_id = %s and namespace = %s and table_name = %s""",
        (principal["tenant_id"], namespace, table),
    )
    if not ref:
        raise CatalogError(404, "NoSuchTableException", f"table does not exist: {namespace}.{table}")
    return ref


@router.head("/iceberg/v1/{prefix}/namespaces/{namespace}/tables/{table}")
@router.get("/iceberg/v1/{prefix}/namespaces/{namespace}/tables/{table}")
def load_table(prefix: str, namespace: str, table: str, request: Request,
               principal: dict = Depends(catalog_principal)):
    """Open one table: decide, record the decision, then hand over the key.

    The decision, the audit rows and the key all come from request_credential,
    the function behind POST /credentials, so a table opened here is recorded
    exactly as the same version read any other way would be.
    """
    ref = _table_ref(principal, prefix, namespace, table)
    version = db.one("select storage_backend from dataset_version where id = %s", (ref["version_id"],))
    key = _open(principal, ref)
    if request.method == "HEAD":
        return Response(status_code=204)
    return {
        "metadata-location": ref["metadata_location"],
        "metadata": _metadata(ref, version),
        "config": _vended(key, ref, prefix, namespace, table),
    }


@router.get("/iceberg/v1/{prefix}/namespaces/{namespace}/tables/{table}/credentials")
def load_credentials(prefix: str, namespace: str, table: str,
                     principal: dict = Depends(catalog_principal)) -> dict:
    """The next key for a table a client already has open. Decided again, in
    full, every time: this is where a read that outlasts its key either carries
    on or is told, with the reason, that access has ended."""
    ref = _table_ref(principal, prefix, namespace, table)
    key = _open(principal, ref)
    return {"storage-credentials": [{
        "prefix": ref["location"],
        "config": _vended(key, ref, prefix, namespace, table),
    }]}


# ------------------------------------------------------------- refusals --


@router.post("/iceberg/v1/oauth/tokens")
def oauth_tokens() -> JSONResponse:
    """Not offered: send the token from POST /iceberg/tokens directly. Answered
    in the shape OAuth clients expect, so the failure is legible."""
    return JSONResponse(status_code=400, content={
        "error": "unsupported_grant_type",
        "error_description": "this catalog takes a bearer token issued by POST /iceberg/tokens, "
                             "not an OAuth exchange"})


def _refuse_writes(rest: str, principal: dict = Depends(catalog_principal)):
    raise CatalogError(403, "ForbiddenException", READ_ONLY)


# One route per method, each with its own operation id. A single route serving
# all four would take its id from a set, whose order differs between processes,
# so the published API reference would change from one start to the next.
for _method in ("post", "put", "patch", "delete"):
    router.add_api_route("/iceberg/v1/{rest:path}", _refuse_writes, methods=[_method.upper()],
                         operation_id=f"iceberg_refuse_{_method}")
