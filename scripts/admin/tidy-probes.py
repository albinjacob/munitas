"""Remove the throwaway tenants the verification suite leaves behind.

Every run of verify/ mints probe tenants on purpose: a fixed name cannot
prove "provisioning happens on first write" twice, so each run invents a
fresh one. They are cheap individually and they accumulate forever, along
with an empty bucket each, until something removes them. This is that
something.

Two kinds of rubbish, and it is worth being precise about which:

  1. Probe tenants with nothing sealed in them. Safe to delete outright,
     because the immutability rules that protect a sealed version have
     nothing to protect here.
  2. Their buckets, one SeaweedFS collection each, drawing from one fixed
     volume pool. verify/v56_per_tenant_storage.py's own comment records
     what exhausting that pool looks like: writes failing with an S3
     error that names nothing about volumes.

Deliberately not in scope: freeing bytes from sealed versions that are
still current. That is scripts/admin/reclaim-storage.py's job, it records what it freed
in storage_reclamation, and this script would be a second, quieter way to
do the same thing.

    python scripts/admin/tidy-probes.py                              # dry run, changes nothing
    python scripts/admin/tidy-probes.py --apply                      # actually delete
    python scripts/admin/tidy-probes.py --min-age-hours 24 --apply   # only ones a full day old

A production tenant is never a candidate, by purpose, and no flag overrides
that. `--min-age-hours` exists for the unattended case (see
`worker/housekeeping_activities.py`, which imports `run_sweep` below rather
than re-implementing it, so there is exactly one place that decides what
counts as a probe): a verify run that is still mid-flight has a tenant only
seconds old, and a scheduled sweep with no age floor could delete out from
under it. A human running this by hand, right after watching a suite finish,
has no such risk and can leave the default of 0.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from datetime import timedelta
from pathlib import Path

import boto3
import psycopg
from botocore.config import Config
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ports_config import PORTS  # noqa: E402

# A second, independent lock beside the `purpose = 'scratch'` filter and the
# database's own refuse_late_disposability trigger (which is what actually
# stops a real organisation from being relabelled into this set). Nothing
# this platform seeds or registers through the console ever uses one of
# these ids, so this can only ever fire on a bug in the query below it, not
# on real data -- which is the point of checking it a second, unrelated way
# rather than trusting one query alone (CLAUDE.md: "do not depend on a
# single literal signal"). The docstring above used to claim this existed
# already; it did not, until this change.
NEVER_TOUCH = {"health", "finance", "canary"}

PG_DSN = os.environ.get(
    "PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform"
)
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", f"http://localhost:{PORTS['seaweedfs_s3']}")
ADMIN_KEY = os.environ.get("S3_ADMIN_KEY", "munitas-admin")
ADMIN_SECRET = os.environ.get("S3_ADMIN_SECRET", "munitas-admin-secret")

# The deletion machinery lives in scripts/admin/nuke-tenant.py, whose filename is not an
# importable module name. Loaded by path rather than reimplemented: that
# file works out which tables are tenant-scoped by querying the catalogue,
# precisely so a second hand-maintained copy of that list cannot go stale
# here.
_nuke_path = Path(__file__).parent / "nuke-tenant.py"
_spec = importlib.util.spec_from_file_location("nuke_tenant", _nuke_path)
nuke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nuke)


def s3():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=ADMIN_KEY,
        aws_secret_access_key=ADMIN_SECRET,
        config=Config(signature_version="s3v4", retries={"max_attempts": 2}),
        region_name="us-east-1",
    )


def probe_tenants(conn, min_age_hours: int = 0) -> list[dict]:
    """Every tenant declared disposable, sealed versions included, old enough
    to be safe to touch unattended.

    Holding a sealed version used to disqualify one, on the reasoning that
    deleting it means disabling the immutability rules and an unattended
    tidy-up is the last place that should happen. That was the wrong line
    to draw. The scripts these tenants belong to seal versions as part of
    what they prove, so the rule kept every probe from the moment it did
    its job, and the buckets piled up until SeaweedFS ran out of volumes
    and every write failed with an InternalError naming nothing about
    volumes. U65 died that way.

    Selected by declared purpose rather than by name. `scratch` means an
    organisation said it was disposable while it was still empty, which the
    database enforces (see schema.sql's refuse_late_disposability), so a
    real organisation cannot be relabelled into this set after the fact.

    `min_age_hours` is the second question, separate from purpose: a tenant
    can be legitimately `scratch` and also seconds old, because a verify run
    is using it right now. A human running this by hand right after a suite
    finishes has no reason to wait; a scheduled sweep has no way to know
    whether anything is still mid-flight, so it waits out a floor instead.
    """
    rows = conn.execute(
        """select t.id, t.purpose, t.created_at,
                  (select count(*) from dataset_version dv
                    where dv.tenant_id = t.id) as versions
             from tenant t
            where t.purpose = 'scratch'
              and t.created_at < now() - %(min_age)s::interval
            order by t.id""",
        {"min_age": timedelta(hours=min_age_hours)},
    ).fetchall()
    # The NEVER_TOUCH guard is deliberately not folded into this query: it is
    # meant to catch the query itself being wrong, so it is checked
    # separately below, against each row this returns, not baked into the
    # same statement it is supposed to be a check on.
    return [r for r in rows if r["id"] not in NEVER_TOUCH]


def objects_under(client, bucket: str, prefix: str) -> list[dict]:
    found: list[dict] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        try:
            page = client.list_objects_v2(**kwargs)
        except Exception:
            return found  # No such bucket, which is already the wanted state.
        found.extend(page.get("Contents", []))
        if not page.get("IsTruncated"):
            return found
        token = page.get("NextContinuationToken")


def empty_bucket(client, bucket: str) -> int:
    # nuke-tenant.py's, so there is one way to remove a tenant's bucket and
    # it raises when the bucket survives rather than passing over it.
    return nuke.empty_and_delete_bucket(client, bucket)


def delete_tenant(conn, tenant: str) -> dict[str, int]:
    """One disposable tenant and every row scoped to it.

    Nothing is switched off to do this, which is the whole point of the
    `scratch` purpose. The immutability rules on dataset_version exempt an
    organisation that declared itself disposable before it held anything, so
    a sealed version here deletes the way any other row does, and every
    other organisation's history stays as immutable during this call as it
    was before it.

    This used to disable those rules for the duration, the way
    scripts/admin/nuke-tenant.py still does for the tenants it is pointed at by hand. A
    guarantee that gets switched off by whatever tidies up is not a
    guarantee, which is what moved the decision to the declaration.

    Re-checks purpose and id against the same two guards `probe_tenants`
    already applied, immediately before deleting, inside the row lock this
    query takes. Belt and braces against the gap between selecting a row and
    acting on it: nothing in this platform currently changes a tenant's
    purpose after creation, but a check that only ever runs once, earlier,
    is exactly the kind of thing that stops being true silently.
    """
    row = conn.execute(
        "select purpose from tenant where id = %s for update", (tenant,)
    ).fetchone()
    if row is None:
        return {}  # Already gone; nothing to do.
    if row["purpose"] != "scratch" or tenant in NEVER_TOUCH:
        raise RuntimeError(
            f"refusing to delete {tenant!r}: purpose is now {row['purpose']!r} "
            "(expected 'scratch') or it is on the NEVER_TOUCH list -- "
            "this changed between selection and deletion"
        )
    tables = nuke.tenant_scoped_tables(conn) + list(nuke.INDIRECT_TABLES)
    deleted = nuke.delete_all(conn, tables, tenant)
    conn.execute("delete from tenant where id = %s", (tenant,))
    return deleted


def run_sweep(min_age_hours: int = 0, apply: bool = False) -> dict:
    """Find, and optionally remove, every probe tenant old enough to touch.

    The one place this decision is made, imported by both the CLI below and
    `worker/housekeeping_activities.py`'s scheduled sweep, so a hand run and
    an automatic one can never quietly diverge on what counts as a probe.
    Returns a plain-data summary, deliberately, since a Temporal activity's
    return value is what lands in the workflow's durable history.
    """
    client = s3()
    found: list[dict] = []
    removed: list[str] = []
    freed_objects = 0

    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn:
        sweep = probe_tenants(conn, min_age_hours=min_age_hours)
        for c in sweep:
            buckets = [r["bucket"] for r in conn.execute(
                "select bucket from tenant_storage_provision "
                "where tenant_id = %s and backend = 'seaweedfs'", (c["id"],)).fetchall()]
            counts = {b: len(objects_under(client, b, "")) for b in buckets}
            found.append({
                "id": c["id"],
                "purpose": c["purpose"],
                "created_at": c["created_at"].isoformat(),
                "versions": c["versions"],
                "objects": counts,
            })
            if not apply:
                continue
            for b in buckets:
                freed_objects += empty_bucket(client, b)
            delete_tenant(conn, c["id"])
            removed.append(c["id"])

        if apply:
            conn.commit()

    return {
        "found": found,
        "removed": removed,
        "freed_objects": freed_objects,
        "applied": apply,
        "min_age_hours": min_age_hours,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="actually delete; without this nothing changes")
    parser.add_argument("--min-age-hours", type=float, default=0,
                        help="skip anything younger than this (default: 0, "
                             "i.e. everything found); see this module's own "
                             "docstring for why an unattended run should not "
                             "use the default")
    args = parser.parse_args()

    result = run_sweep(min_age_hours=args.min_age_hours, apply=args.apply)

    print(f"Probe tenants: {len(result['found'])} found "
          f"(min age {args.min_age_hours}h).")
    for c in result["found"]:
        where = ", ".join(f"{n} object(s) in {b}" for b, n in c["objects"].items()) or "no bucket"
        print(f"  {c['id']} (purpose={c['purpose']}, created {c['created_at']}, "
              f"{c['versions']} version(s)): {where}")

    if not args.apply:
        print("\nDry run. Nothing changed. Pass --apply to act.")
        return 0

    print(f"\nRemoved {len(result['removed'])} tenant(s) and "
          f"{result['freed_objects']} object(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
