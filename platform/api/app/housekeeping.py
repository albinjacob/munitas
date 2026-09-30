"""Storage housekeeping: what is held, what could be freed, what was.

None of this was visible anywhere but the database, and that is how three
bugs in this area survived five weeks. `scripts/admin/reclaim-storage.py` freed nothing for
over a month while reporting success, because it looked in the shared bucket
for tenants that had their own. Verification fixtures wrote objects where the
platform would never read them. The volume pool filled with throwaway test
buckets until every write failed with an error naming neither volumes nor the
pool. Each was discoverable by querying Postgres and the storage master by
hand, and each went undiscovered because nobody had a reason to.

WHAT IS SHOWN, AND TO WHOM

Two scopes, and the policy engine decides both (`may_see_housekeeping` in
platform/policy/access.rego). The split is not cosmetic.

The platform-wide view carries every tenant's storage and no tenant's
contents: ids, bucket names, counts, bytes, dates. That is telemetry, which
is what `hybridops` exists for in the policy's own words, and it keeps the
property recorded beside `platform_admin`, that running the system confers no
standing access to what it holds. Dataset names are customer metadata rather
than telemetry, so they do not appear here.

The tenant view is one organisation's own, and reclamation is the reason it
exists: freeing storage deletes that organisation's bytes, and what went,
when, why and at whose hand is a governance fact belonging to them rather
than to whoever runs the machine. Their own dataset names appear there,
because it is their own data being named.

WHAT CAN BE DONE FROM HERE, AND WHAT CANNOT

Reclaiming is offered, to `platform_admin`, with a reason that is written
beside the bytes. It always reports what it would free before it frees
anything.

Sweeping throwaway tenants is deliberately not offered, and the reason is
worth stating rather than leaving as an omission. `scripts/admin/tidy-probes.py` deletes
tenants that hold sealed versions, which means disabling the rewrite rules
that make a sealed version immutable. Those rules are the platform's central
guarantee. A long-running service that mints credentials must never be able
to switch them off, whatever the caller's role, so that act stays in a script
somebody runs deliberately. This screen reports what is waiting and names the
command.
"""

from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from . import config, db, grants, logs, opa, seaweed, storage
from .auth import current_session

log = logs.get_logger("housekeeping")

router = APIRouter(prefix="/housekeeping", tags=["housekeeping"])

# Matches scripts/admin/reclaim-storage.py's own eligibility: only a canary or retired
# tenant's bytes are ever freeable, never a production tenant's. Restated
# here because this is a separate caller, and checked by U70 against that
# script so the two cannot drift apart silently.
RECLAIMABLE_PURPOSES = ("canary", "retired")


def _refuse(reasons: list[str], status: int = 403):
    raise HTTPException(status, {"reasons": reasons})


@router.get("/storage")
def storage_report(
    tenant_id: str | None = Query(default=None),
    session: dict = Depends(current_session),
) -> dict:
    """What storage this platform holds, or what one organisation's holds.

    `tenant_id` chooses the scope, and the scope chooses the question the
    policy engine is asked, because they are different questions with
    different answers.
    """
    scope = "tenant" if tenant_id else "platform"
    permitted, reasons = opa.may_see_housekeeping({
        "scope": scope,
        "tenant_id": tenant_id,
        "viewer": {
            "id": session["id"],
            "tenant_id": session["tenant_id"],
            "roles": session["roles"],
        },
    })
    if not permitted:
        _refuse(reasons)

    if tenant_id:
        return {"scope": "tenant", "tenant_id": tenant_id,
                "reclaimed": _reclaimed_for(tenant_id)}

    return {
        "scope": "platform",
        # Where SeaweedFS keeps its files on the host. Sent once rather than
        # per organisation, because it is a property of the deployment and
        # repeating it on every row would say the same thing five times.
        "storage_path": config.STORAGE_PATH,
        "tenants": _storage_by_tenant(),
        "volumes": _volume_pool(),
        "probes_waiting": _probes_waiting(),
        # Whether allowed storage access is taking effect. The screen raises
        # it only when it needs an administrator (grants.activation_status).
        "storage_permissions": json.loads(
            json.dumps(grants.activation_status(), default=str)),
    }


def _storage_by_tenant() -> list[dict]:
    """One row per tenant: where its objects are, and what could be freed.

    `storage_reclamation` is the record of what has already gone, so a
    version appearing there is not counted as reclaimable again. That is the
    same join scripts/admin/reclaim-storage.py makes when it picks its candidates.
    """
    return db.all_rows(
        """select t.id as tenant_id,
                  t.purpose,
                  t.note,
                  -- Which backends this organisation's objects are actually
                  -- on, read from the versions themselves rather than from
                  -- the provisioning rows: a tenant has a row only for a
                  -- backend it has written to, and the question here is
                  -- where its data is, not where it could go.
                  (select array_agg(distinct dv3.storage_backend)
                     from dataset_version dv3
                    where dv3.tenant_id = t.id) as backends,
                  -- Distinguishes "has written nothing yet" from "its
                  -- storage is on another backend". r2-probe-a and
                  -- r2-probe-b live on Cloudflare R2, so they have no
                  -- SeaweedFS row, and calling that "none yet" reads as
                  -- "this organisation stores nothing", which is wrong.
                  case
                    when p.bucket is not null then p.bucket
                    when exists (select 1 from dataset_version dv2
                                  where dv2.tenant_id = t.id
                                    and dv2.storage_backend <> 'seaweedfs')
                      then '(on another backend)'
                    else '(nothing written yet)'
                  end as bucket,
                  count(dv.id) as versions,
                  count(dv.id) filter (
                      where t.purpose = any(%s) and r.dataset_version_id is null
                  ) as reclaimable_versions,
                  count(r.dataset_version_id) as reclaimed_versions,
                  coalesce(sum(r.bytes_freed), 0) as bytes_reclaimed
             from tenant t
             left join tenant_storage_provision p
                    on p.tenant_id = t.id and p.backend = 'seaweedfs'
             left join dataset_version dv on dv.tenant_id = t.id
             left join storage_reclamation r on r.dataset_version_id = dv.id
            group by t.id, t.purpose, t.note, p.bucket
            order by t.id""",
        (list(RECLAIMABLE_PURPOSES),),
    )


def _reclaimed_for(tenant_id: str) -> list[dict]:
    """What has been freed from this organisation, and why.

    Named down to the dataset, unlike the platform-wide view. This is the
    organisation's own record of its own deletions, which is exactly the
    thing it should be able to read without asking anybody.
    """
    return db.all_rows(
        """select d.name as dataset_name,
                  dv.version,
                  r.bytes_freed,
                  r.object_count,
                  r.at as reclaimed_at,
                  r.reclaimed_by,
                  r.reason
             from storage_reclamation r
             join dataset_version dv on dv.id = r.dataset_version_id
             join dataset d on d.id = dv.dataset_id
            where r.tenant_id = %s
            order by r.at desc
            limit 200""",
        (tenant_id,),
    )


def _volume_pool() -> dict:
    """How much of the storage engine's fixed volume pool is spoken for.

    A volume is a 1 GB file objects are appended into, never shared between
    buckets, and claimed on a tenant's first write rather than when its
    bucket is created. Running out fails every write with an S3
    InternalError naming neither volumes nor the pool, which reads like
    broken storage rather than a full one. It happened here at 22 buckets
    holding under a megabyte between them, so the number belongs on a screen
    rather than in somebody's memory of a bad afternoon.
    """
    try:
        response = httpx.get(f"{config.MASTER_URL}/vol/status", timeout=10.0)
        response.raise_for_status()
        status = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Reported rather than raised: the rest of this screen is answerable
        # from Postgres alone, and losing all of it because one number is
        # unavailable would be a poor trade.
        log.warning("could not read the storage volume pool",
                    extra={"reason": str(exc)})
        return {"used": None, "by_collection": [], "reason": str(exc)}

    counts: dict[str, int] = {}
    for centre in status.get("Volumes", {}).get("DataCenters", {}).values():
        for rack in centre.values():
            for node in rack.values():
                for volume in node or []:
                    name = volume.get("Collection") or "(default)"
                    counts[name] = counts.get(name, 0) + 1

    return {
        "used": sum(counts.values()),
        "by_collection": sorted(
            ({"collection": k, "volumes": v} for k, v in counts.items()),
            key=lambda row: (-row["volumes"], row["collection"]),
        ),
    }


def _probes_waiting() -> list[dict]:
    """Throwaway verification tenants still holding a bucket.

    Reported, never swept from here: removing one means deleting sealed
    versions, which means disabling the immutability rules. See this
    module's own header for why that stays in a script.
    """
    # Asked by declared purpose, not by name. Matching names was how this
    # was written before disposability was something an organisation could
    # declare, and it went wrong the moment a new probe prefix appeared: the
    # screen listed six scratch organisations in the table above while this
    # section said nothing was waiting. The platform now knows which
    # organisations are disposable, so there is no need to guess from a
    # naming convention that nothing enforces.
    return db.all_rows(
        """select t.id as tenant_id,
                  t.purpose,
                  (select count(*) from dataset_version dv
                    where dv.tenant_id = t.id) as versions
             from tenant t
            where t.purpose = 'scratch'
            order by t.id"""
    )

# The same set scripts/admin/reclaim-storage.py frees, in the same words, because two
# definitions of "what may be freed" is one too many. The filters are inside
# the query rather than applied afterwards, so a production tenant is never
# returned rather than being returned and then skipped: a mistake in the loop
# below cannot reach one. A version already in storage_reclamation is excluded,
# which is what makes running this twice harmless.
_ELIGIBLE_SQL = """
    select dv.id, dv.tenant_id, dv.storage_prefix, dv.created_at,
           d.name as dataset_name, dv.version
      from dataset_version dv
      join dataset d on d.id = dv.dataset_id
      join tenant t on t.id = dv.tenant_id
      left join storage_reclamation sr on sr.dataset_version_id = dv.id
     where t.purpose = any(%(reclaimable)s)
       and sr.dataset_version_id is null
       and dv.created_at < now() - make_interval(days => %(days)s)
       and (%(tenant)s::text is null or dv.tenant_id = %(tenant)s)
       -- A retired tenant is only ever swept when it is named. Winding down a
       -- closed organisation is somebody's decision, not a button's default.
       and (t.purpose = 'canary' or %(tenant)s::text is not null)
     order by dv.created_at asc
"""


class ReclaimRequest(BaseModel):
    reason: str
    older_than_days: int = 1
    tenant_id: str | None = None
    # Defaulting to a dry run is the point. The caller asks for the real thing
    # deliberately, having seen the list, rather than discovering that the
    # default destroyed something.
    dry_run: bool = True


@router.post("/reclaim")
def free_storage(
    body: ReclaimRequest, session: dict = Depends(current_session)
) -> dict:
    """Free the stored objects of old throwaway versions, or say what would go.

    Seeing that bytes could be freed and freeing them are different questions
    with different answers, so this asks the policy engine its own
    (`may_free_storage`), not the one the screen was drawn with.

    The rows survive. Only the objects go, and a `storage_reclamation` row is
    written for each, carrying the reason given here. A sealed version whose
    bytes vanish with nothing saying so is indistinguishable from one that
    never had any, which is why the reason is required rather than optional.
    """
    permitted, reasons = opa.may_free_storage({
        "actor": {"id": session["id"], "roles": session["roles"]},
        "reason": body.reason,
    })
    if not permitted:
        _refuse(reasons)

    candidates = db.all_rows(_ELIGIBLE_SQL, {
        "reclaimable": list(RECLAIMABLE_PURPOSES),
        "days": body.older_than_days,
        "tenant": body.tenant_id,
    })

    freed: list[dict] = []
    total_bytes = 0
    total_objects = 0

    for version in candidates:
        bucket = storage.bucket_for("seaweedfs", version["tenant_id"])
        client = seaweed.admin_client(version["tenant_id"])
        objects = _objects_under(client, bucket, version["storage_prefix"])
        size = sum(o["Size"] for o in objects)
        freed.append({
            "dataset_name": version["dataset_name"],
            "version": version["version"],
            "tenant_id": version["tenant_id"],
            "bucket": bucket,
            "objects": len(objects),
            "bytes": size,
        })
        total_bytes += size
        total_objects += len(objects)

        if body.dry_run:
            continue

        if objects:
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": o["Key"]} for o in objects]},
            )
        # Written even when nothing was found, because the row is the only
        # remaining evidence those bytes existed at all.
        db.execute(
            """insert into storage_reclamation
                 (dataset_version_id, tenant_id, bytes_freed, object_count,
                  reclaimed_by, reason)
               values (%s, %s, %s, %s, %s, %s)
               on conflict (dataset_version_id) do nothing""",
            (version["id"], version["tenant_id"], size, len(objects),
             session["id"], body.reason),
        )

    log.info("storage reclaimed" if not body.dry_run else "reclaim rehearsed",
             extra={"principal": session["id"], "count": len(freed),
                    "reason": body.reason,
                    "outcome": "dry-run" if body.dry_run else "freed"})

    return {
        "dry_run": body.dry_run,
        "versions": len(freed),
        "objects": total_objects,
        "bytes": total_bytes,
        "freed": freed,
    }


def _objects_under(client, bucket: str, prefix: str) -> list[dict]:
    """Every object under one version's prefix, in that tenant's own bucket.

    The client is the tenant's own ingest identity, not the platform
    super-key, so this cannot reach another tenant's objects even if the
    bucket name it was handed were wrong.
    """
    found: list[dict] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix.rstrip("/") + "/"}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        found.extend(page.get("Contents", []))
        if not page.get("IsTruncated"):
            return found
        token = page.get("NextContinuationToken")
