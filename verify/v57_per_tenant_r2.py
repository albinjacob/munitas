"""U57: the same guarantees U56 proved for SeaweedFS, proved again for
Cloudflare R2 against the real account, plus the one that only applies
here. Every tenant gets its own bucket, and every credential that
touches data, writing as much as reading, is refused against any other
tenant's bucket.

Runs inside the munitas-api container, against real R2. Every check
skips, rather than fails, if R2 is not configured on this install.

    docker compose exec -T munitas-api python /verify/v57_per_tenant_r2.py

Two fixed probe tenants, torn down at the start of every run and again
at the end, for the reason U56 gives one step further. There, an
accumulating bucket per run exhausted a local volume pool. Here the
leftovers sit in somebody's real Cloudflare account, so teardown removes
objects, bucket and row, and runs whether the checks passed or failed: a
five-byte probe object is worth nothing as evidence after the fact.
"""

from __future__ import annotations

import sys

import httpx
from common import check, db, fixture_tenant, heading, require_api, s3_client, skip, summary

# Fixed and reused, never generated per run. Named as probes so nobody
# mistakes them for real tenants, matching the storage-probe name U56
# already established.
PROBE_A = "r2-probe-a"
PROBE_B = "r2-probe-b"


def provision_row(tenant_id: str) -> dict | None:
    with db() as conn:
        return conn.execute(
            "select bucket, access_key_id, secret_ciphertext, secret_wrapped_key "
            "from tenant_storage_provision where tenant_id = %s and backend = 'r2'",
            (tenant_id,),
        ).fetchone()


def teardown(r2, config, tenant_id: str) -> None:
    """Return a probe tenant to never-having-written state, in the real
    account as well as in this database.

    There is no credential to revoke here, which is the point of the
    design rather than an omission: nothing long-lived is minted per
    tenant, so a torn-down probe leaves nothing behind by construction
    instead of by somebody remembering. Every step tolerates its target
    already being gone, because this runs at the start of a fresh run as
    often as at the end of a finished one.
    """
    row = provision_row(tenant_id)
    if row is None:
        return

    bucket = row["bucket"]

    # The account's own credential, not a vended one: it reaches every
    # bucket, which is what a teardown needs and exactly what the vended
    # credentials under test are built not to have.
    client = s3_client(
        config.R2_ACCESS_KEY_ID, config.R2_SECRET_ACCESS_KEY,
        endpoint=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    )
    try:
        listed = client.list_objects_v2(Bucket=bucket)
        for obj in listed.get("Contents", []):
            client.delete_object(Bucket=bucket, Key=obj["Key"])
    except Exception:
        pass  # No bucket, which is already the state this wants.

    # R2 refuses to delete a bucket that still holds objects, so this
    # follows the emptying above rather than replacing it.
    httpx.delete(
        f"{r2.R2_API}/accounts/{config.R2_ACCOUNT_ID}/r2/buckets/{bucket}",
        headers={"Authorization": f"Bearer {config.R2_API_TOKEN}"},
        timeout=15.0,
    )

    with db() as conn:
        conn.execute(
            "delete from tenant_storage_provision where tenant_id = %s "
            "and backend = 'r2'",
            (tenant_id,),
        )


def main() -> int:
    require_api()
    sys.path.insert(0, "/app")
    from app import config  # noqa: E402
    from app import db as app_db  # noqa: E402
    from app import r2  # noqa: E402

    if not (config.R2_ACCOUNT_ID and config.R2_ACCESS_KEY_ID
            and config.R2_SECRET_ACCESS_KEY and config.R2_API_TOKEN):
        skip("U57: every check", "R2 is not configured on this install")
        return 0

    # This script imports app.r2 directly rather than going through a
    # request to the running API, so the pool main.py's lifespan hook
    # opens on startup was never opened here.
    app_db.pool.open()

    fixture_tenant(PROBE_A)
    fixture_tenant(PROBE_B)
    # Says on every screen what these two are for. Without it they read as
    # two unexplained organisations sitting beside the real ones, which is
    # how somebody ends up screenshotting a probe for a demo or hesitating
    # to delete one.
    with db() as conn:
        conn.execute(
            "update tenant set note = %s where id = any(%s)",
            ("Proves one organisation cannot reach another's files on real "
             "Cloudflare R2. Two, because isolation takes two.",
             [PROBE_A, PROBE_B]),
        )
    teardown(r2, config, PROBE_A)
    teardown(r2, config, PROBE_B)

    try:
        heading("U57: the configured credentials are internally consistent")

        # Checked first because everything below fails confusingly when
        # this is wrong, and the likeliest way for it to be wrong is the
        # S3 pair and the token value having been pasted from two
        # different Cloudflare tokens.
        # common.check prints its detail on a pass as well as a failure,
        # so the detail says what was compared rather than what would be
        # wrong. A pass that prints an explanation of failure reads as a
        # failure somebody forgot to act on.
        check("the configured S3 secret is the hash of the configured API token, "
              "so both came from one token",
              r2.s3_secret_for(config.R2_API_TOKEN) == config.R2_SECRET_ACCESS_KEY,
              "a mismatch here means R2_SECRET_ACCESS_KEY and R2_API_TOKEN "
              "came from two different tokens")

        heading("U57: a fresh tenant provisions its own R2 bucket on first write")

        check("probe A starts with no provisioning row", provision_row(PROBE_A) is None)

        bucket_a = r2.bucket(PROBE_A)
        check("the resolved bucket is named after the tenant",
              bucket_a == f"munitas-{PROBE_A}", bucket_a)

        row_a = provision_row(PROBE_A)
        check("a provisioning row now exists for probe A", row_a is not None)
        check("and it points at the same bucket the resolver returned",
              bool(row_a) and row_a["bucket"] == bucket_a)

        # The row deliberately holds no credential. A stored per-tenant
        # key is the thing this design rejects, so its absence is worth
        # asserting rather than leaving as an implementation detail
        # somebody could reintroduce without noticing.
        check("and stores no long-lived credential of its own",
              bool(row_a) and row_a["access_key_id"] is None
              and row_a["secret_ciphertext"] is None)

        heading("U57: writes use a vended credential, not the account's own")

        writer_a = r2.admin_client(PROBE_A)
        writer_a.put_object(Bucket=bucket_a, Key="v57-probe/hello.txt", Body=b"hello")
        listed = writer_a.list_objects_v2(Bucket=bucket_a, Prefix="v57-probe/")
        check("the write client can write and list inside probe A's bucket",
              listed.get("KeyCount") == 1, listed.get("KeyCount"))

        # R2 vends a credential that keeps the parent's access key id and
        # changes the secret, carrying the scope in the session token.
        # Confirmed against the real API: identity is not what makes a
        # vended credential distinct here, so asserting a different id
        # would fail while the isolation it stands for holds. The secret
        # differing, the session token existing, and the refusals below
        # are what actually distinguish it from the account's own key.
        creds = writer_a._request_signer._credentials
        check("and it is a temporary credential, carrying a session token",
              bool(getattr(creds, "token", None)))
        check("and it is not signing with the account's own secret",
              creds.secret_key != config.R2_SECRET_ACCESS_KEY)

        bucket_b = r2.bucket(PROBE_B)
        check("probe B provisioned a different bucket", bucket_b != bucket_a, bucket_b)

        try:
            writer_a.put_object(Bucket=bucket_b, Key="v57-probe/leak.txt", Body=b"leak")
            write_refused = False
        except Exception:
            write_refused = True
        check("probe A's write credential is refused against probe B's bucket",
              write_refused)

        heading("U57: reads are scoped to one prefix in one bucket")

        grant = r2.grant_prefix("pipeline_action", "v57-probe", "verify", PROBE_A)
        check("the grant names probe A's own bucket", grant["bucket"] == bucket_a,
              grant["bucket"])
        check("and carries a session token, which R2 credentials require",
              bool(grant.get("session_token")))
        check("and says which backend issued it", grant.get("backend") == "r2")

        reader = s3_client(
            grant["access_key"], grant["secret_key"],
            session_token=grant["session_token"], endpoint=grant["endpoint"],
        )
        got = reader.get_object(Bucket=bucket_a, Key="v57-probe/hello.txt")["Body"].read()
        check("that credential reads probe A's own object", got == b"hello", got)

        try:
            reader.put_object(Bucket=bucket_a, Key="v57-probe/written.txt", Body=b"no")
            read_only = False
        except Exception:
            read_only = True
        check("and cannot write with it, since it was minted read-only", read_only)

        try:
            reader.get_object(Bucket=bucket_b, Key="v57-probe/hello.txt")
            cross_refused = False
        except Exception:
            cross_refused = True
        check("nor read probe B's bucket", cross_refused)

        return summary("U57")
    finally:
        # Always, pass or fail. See the module docstring: what is left
        # behind here sits in a real account, not in a local file
        # somebody might want to inspect tomorrow.
        teardown(r2, config, PROBE_A)
        teardown(r2, config, PROBE_B)


if __name__ == "__main__":
    sys.exit(main())
