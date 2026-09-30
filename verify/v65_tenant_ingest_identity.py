"""U65: ingestion and agent-upload write through a tenant's own SeaweedFS
identity, never through the platform's unscoped munitas-admin key.

Runs inside the munitas-api container, where app.seaweed, app.grants and
app.db are importable and SeaweedFS is reachable at its container-internal
endpoint.

    docker compose exec -T munitas-api python /verify/v65_tenant_ingest_identity.py

A fresh probe tenant per run, the same reason v56_per_tenant_storage.py's own
docstring gives: a fixed name shared across runs can exhaust SeaweedFS's fixed
volume pool.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from common import check, db, fixture_tenant, heading, require_api, summary

PROBE_A = f"ingest-probe-a-{uuid.uuid4().hex[:8]}"
PROBE_B = f"ingest-probe-b-{uuid.uuid4().hex[:8]}"


def main() -> int:
    require_api()
    sys.path.insert(0, "/app")
    from app import db as app_db  # noqa: E402
    from app import grants, seaweed  # noqa: E402

    app_db.pool.open()
    fixture_tenant(PROBE_A)
    fixture_tenant(PROBE_B)

    heading("U65: a tenant's ingest identity writes and reads its own bucket")

    client_a = seaweed.admin_client(PROBE_A)
    bucket_a = seaweed.bucket(PROBE_A)
    client_a.put_object(Bucket=bucket_a, Key="u65-probe/hello.txt", Body=b"hello")
    got = client_a.get_object(Bucket=bucket_a, Key="u65-probe/hello.txt")["Body"].read()
    check("a fresh tenant's ingest identity writes and reads its own object",
          got == b"hello", got)

    heading("U65: it is refused against another tenant's own bucket")

    # Called for what it creates: tenant B's own bucket and ingest identity.
    seaweed.admin_client(PROBE_B)
    bucket_b = seaweed.bucket(PROBE_B)
    check("the two probe tenants provisioned different buckets",
          bucket_a != bucket_b, f"{bucket_a} vs {bucket_b}")

    try:
        client_a.put_object(Bucket=bucket_b, Key="u65-probe/intrude.txt", Body=b"no")
        check("tenant A's identity is refused writing into tenant B's bucket",
              False, "the write succeeded")
    except Exception as exc:
        code = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        check("tenant A's identity is refused writing into tenant B's bucket",
              code in (401, 403), f"HTTP {code}: {type(exc).__name__}")

    heading("U65: two tenants get two distinct identities")

    with db() as conn:
        row_a = conn.execute(
            "select access_key_id from storage_identity where tenant_id = %s "
            "and lease_id is null and agent_run_id is null",
            (PROBE_A,),
        ).fetchone()
        row_b = conn.execute(
            "select access_key_id from storage_identity where tenant_id = %s "
            "and lease_id is null and agent_run_id is null",
            (PROBE_B,),
        ).fetchone()
    check("both tenants have their own storage_identity row",
          row_a is not None and row_b is not None)
    check("and the access keys are genuinely different",
          bool(row_a) and bool(row_b) and row_a["access_key_id"] != row_b["access_key_id"],
          f"{row_a} vs {row_b}")

    heading("U65: minting again reuses the identity, it does not remint")

    key_before = row_a["access_key_id"] if row_a else None
    seaweed.admin_client(PROBE_A)
    with db() as conn:
        row_again = conn.execute(
            "select access_key_id from storage_identity where tenant_id = %s "
            "and lease_id is null and agent_run_id is null",
            (PROBE_A,),
        ).fetchone()
    check("a second admin_client call for the same tenant returns the same key",
          bool(row_again) and row_again["access_key_id"] == key_before)

    heading("U65: the super-key now backs only bucket provisioning")

    # The API image's Dockerfile does WORKDIR /app then COPY api/app ./app,
    # so the package lives at /app/app inside the container and is imported
    # as `from app import ...`, which every other in-container probe in this
    # and the prior plan already relies on. The source file itself is
    # therefore at /app/app/seaweed.py, one level below the workdir.
    source = Path("/app/app/seaweed.py").read_text()
    # A zero-argument def line (`def _admin_boto_client():`) contains the
    # literal substring `_admin_boto_client()` too, so it is excluded by line
    # rather than trusted to not match the same pattern a call site does.
    calls = [
        line for line in source.splitlines()
        if "_admin_boto_client()" in line and not line.strip().startswith("def ")
    ]
    check("_admin_boto_client is called from exactly one place in the source "
          "(the def line itself is excluded, not just assumed not to match)",
          len(calls) == 1, f"{len(calls)} call sites found: {calls}")

    heading("U65: a retired tenant cannot mint a fresh ingest identity")

    # Matches v33_retired_tenant.py's own fixture exactly: retirement is
    # tenant.purpose = 'retired', read by refuse_write_to_retired_tenant()
    # (platform/schema.sql), not a retired_at column, which this table does
    # not have.
    retired = f"ingest-probe-retired-{uuid.uuid4().hex[:8]}"
    fixture_tenant(retired)
    with db() as conn:
        conn.execute("update tenant set purpose = 'retired' where id = %s", (retired,))
    import psycopg.errors
    try:
        grants.identity_for_tenant_ingest(retired)
        check("minting for a retired tenant is refused", False, "it succeeded")
    except psycopg.errors.ReadOnlySqlTransaction as exc:
        # The trigger raises with errcode='read_only_sql_transaction', which
        # is what this specific exception class corresponds to; a bare
        # `except Exception` would also catch an unrelated bug and misreport
        # it as this check passing.
        check("minting for a retired tenant is refused", True, str(exc)[:120])

    heading("U65: a deleted organisation's ingest identity leaves the document")

    # Reconcile used to carry every identity without a storage_identity row
    # over from the live document as though it were a role. Deleting an
    # organisation deletes its row, so its ingest identity, key and all, was
    # carried over for good, with a List on every bucket: 59 of them on this
    # machine. Nothing is carried over now: roles come from the policy.
    gone = fixture_tenant(f"ingest-probe-gone-{uuid.uuid4().hex[:8]}")
    minted = grants.identity_for_tenant_ingest(gone)
    seaweed.bucket(gone)
    grants.reconcile()
    names = {i["name"] for i in seaweed.load_identities()["identities"]}
    check("while the organisation exists, its ingest identity is in the document",
          minted["identity_name"] in names, minted["identity_name"])
    with db() as conn:
        # The row nuke-tenant.py removes with everything else of this tenant's.
        conn.execute("delete from storage_identity where tenant_id = %s", (gone,))
    grants.reconcile()
    after = seaweed.load_identities()["identities"]
    check("once its row is gone, the identity is gone from the document",
          minted["identity_name"] not in {i["name"] for i in after},
          minted["identity_name"])
    keys = {c.get("accessKey") for i in after for c in i.get("credentials", [])}
    check("and so is its key", minted["access_key"] not in keys, minted["access_key"])
    roles = grants.identity_names()
    check("every role and the admin are still there",
          roles <= {i["name"] for i in after}, sorted(roles))

    return summary("U65")


if __name__ == "__main__":
    sys.exit(main())
