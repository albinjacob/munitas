"""Which storage backend a request actually goes to.

The only new thing this module does is read a storage_backend value and
call the matching implementation. Every real decision (is this allowed,
what prefix, what role) is made by its callers exactly as before; this
just stands between them and seaweed.py so a caller no longer has to
know which backend it is talking to.
"""

from __future__ import annotations

from . import r2, seaweed


def mint_read_credential(
    backend: str, role: str, prefix: str, purpose: str, tenant_id: str
) -> tuple[dict, str]:
    """Returns (credential dict, an audit-log-friendly action string).

    SeaweedFS's own grant_prefix/credentials_for pair is reconciled into
    this same two-value shape here, rather than changing seaweed.py's own
    interface, which stays exactly as every other caller already expects.
    """
    if backend == "r2":
        creds = r2.grant_prefix(role, prefix, purpose, tenant_id)
        return creds, f"r2 temporary credential for {prefix}"
    action = seaweed.grant_prefix(role, prefix, tenant_id)
    creds = seaweed.credentials_for(role, tenant_id)
    creds["backend"] = "seaweedfs"
    creds.setdefault("session_token", None)
    return creds, action


def mint_write_credential(
    backend: str, role: str, prefix: str, purpose: str, tenant_id: str
) -> tuple[dict, str]:
    """Returns (credential dict, an audit-log-friendly action string), the
    write-side counterpart to mint_read_credential above.

    Scoped to `backend == "seaweedfs"` only, refusing clearly for anything
    else. That matches worker/platform_client.py's own SERVED_BACKENDS =
    ("seaweedfs",), already in force for the real pipeline's writes -- this
    adds no new gap, since no real R2 pipeline write exists today for it to
    scope.
    """
    if backend != "seaweedfs":
        raise seaweed.StorageUnavailable(
            f"write credentials are only minted for seaweedfs, not {backend!r}. "
            "No real pipeline write exists against any other backend today"
        )
    action = seaweed.grant_prefix_write(role, prefix, tenant_id)
    creds = seaweed.credentials_for(role, tenant_id)
    creds["backend"] = "seaweedfs"
    creds.setdefault("session_token", None)
    return creds, action


def grant_prefix(backend: str, role: str, prefix: str, purpose: str,
                 tenant_id: str):
    """Grant a role read access under one prefix, in the right backend.

    The promotion path called seaweed.grant_prefix directly, so promoting an
    R2-backed version created its grants in SeaweedFS and left the version
    unreadable where its bytes actually are. Backend first, matching
    admin_client_for and bucket_for.

    The two backends answer differently by nature: SeaweedFS mutates a
    long-lived identity per role and returns a description of what it did, R2
    mints a temporary credential and returns it. A caller that only needs the
    grant to have happened can ignore the difference, which is why this returns
    whatever the backend gave rather than flattening the two.
    """
    if backend == "r2":
        return r2.grant_prefix(role, prefix, purpose, tenant_id)
    return seaweed.grant_prefix(role, prefix, tenant_id)


def admin_client_for(backend: str, tenant_id: str):
    if backend == "r2":
        return r2.admin_client(tenant_id)
    return seaweed.admin_client(tenant_id)


def bucket_for(backend: str, tenant_id: str) -> str:
    if backend == "r2":
        return r2.bucket(tenant_id)
    return seaweed.bucket(tenant_id)
