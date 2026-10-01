"""Creating sealed dataset versions.

Extracted so that ingestion and the pipeline seal versions the same way. Two
implementations of "make a sealed version" would be two sets of rules about
immutability, prefixes and manifests, and the one that drifted would be the one
nobody was testing.

Everything here writes. Nothing reads except to work out the next version
number, which is the opposite boundary from `read_models.py` and equally worth
keeping visible.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from . import db, iceberg


def register_contract(tenant_id: str, name: str, fields: list[dict],
                      primary_key: list[str]) -> dict:
    """Create a schema contract, or return the existing one unchanged.

    Returns `{"id", "content_hash", "created"}`, which is also exactly what
    POST /schema-contracts answers, so that endpoint is a thin wrapper rather
    than a second implementation.

    Content addressed and never edited, so registering the same shape twice
    returns the same row. The tenant is part of the hash because the row is
    scoped to a tenant while `content_hash` is unique across the whole table:
    without it the first tenant to register a shape took the hash and every
    other tenant's attempt failed on the unique constraint.

    One function rather than one per caller. The worker registers
    `encounter_raw` through POST /schema-contracts and the audio seal path
    registers the same contract directly, and if those two computed the digest
    differently the same contract would exist twice under one name, with the
    pipeline and the console each holding a different id for it.
    """
    digest = content_hash({"tenant": tenant_id, "fields": fields, "pk": primary_key})
    existing = db.one(
        "select id from schema_contract where content_hash = %s", (digest,)
    )
    if existing:
        return {"id": str(existing["id"]), "content_hash": digest, "created": False}

    row = db.execute(
        """insert into schema_contract
             (id, tenant_id, name, fields, primary_key, content_hash)
           values (%s, %s, %s, %s, %s, %s)
           returning id""",
        (str(uuid.uuid4()), tenant_id, name, json.dumps(fields), primary_key, digest),
    )
    return {"id": str(row["id"]), "content_hash": digest, "created": True}


def next_version(tenant_id: str, dataset_id: str) -> dict:
    """Where the next version's objects must be written.

    A producer has to write its objects before it can seal a version, but the
    prefix belongs to the version, which does not exist yet. Without this the
    producer invents a prefix, the sealed version points somewhere else, and a
    prefix-scoped credential grants access to nothing. That failure is silent:
    the version looks right and so does the grant.

    A reservation in name only. Two producers racing on one dataset would both
    be told the same answer and one would lose. Single producer per dataset is
    the assumption, and a real deployment needs an allocation that takes a lock.
    """
    row = db.one(
        "select coalesce(max(version), 0) as v from dataset_version where dataset_id = %s",
        (dataset_id,),
    )
    version = row["v"] + 1
    return {
        "version": version,
        "storage_prefix": f"{tenant_id}/{dataset_id}/v{version}",
        "reserved": False,
    }


def content_hash(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def seal(
    *,
    tenant_id: str,
    dataset_id: str,
    schema_id: str,
    visibility_class: str,
    storage_backend: str,
    object_manifest: list[dict],
    record_count: int = 0,
    produced_by_run: str | None = None,
    records_key: str | None = None,
) -> dict:
    """Create a sealed version.

    Sealed on creation, which is why there is no update anywhere in this file.
    The prefix encodes tenant, dataset and version and never the class, so a
    later promotion changes a grant rather than moving bytes.

    `storage_backend` is required, not defaulted, so every caller states its
    choice explicitly rather than one silently inheriting SeaweedFS forever.
    It is pinned here, once, and never re-derived from the dataset's or
    tenant's own current default afterward.
    """
    reserved = next_version(tenant_id, dataset_id)
    prefix = reserved["storage_prefix"]

    # See main.create_version: a tabular version is written as an Iceberg table
    # before the row exists, and its files join the manifest the hash covers.
    version_id = str(uuid.uuid4())
    manifest = list(object_manifest)
    projection = None
    if records_key:
        dataset = db.one("select name from dataset where id = %s", (dataset_id,))
        if dataset:
            projection = iceberg.try_project(
                tenant_id=tenant_id, backend=storage_backend, dataset_id=dataset_id,
                dataset_name=dataset["name"], version_id=version_id,
                version=reserved["version"], prefix=prefix, schema_id=schema_id,
                records_key=records_key, produced_by_run=produced_by_run)
            if projection:
                manifest += projection.objects
    digest = content_hash(
        {"manifest": manifest, "count": record_count, "prefix": prefix}
    )

    row = db.execute(
        """insert into dataset_version
             (id, tenant_id, dataset_id, version, visibility_class,
              storage_prefix, storage_backend, object_manifest, schema_id,
              produced_by_run, record_count, content_hash, iceberg_snapshot_id, sealed)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
           returning id""",
        (version_id, tenant_id, dataset_id, reserved["version"],
         visibility_class, prefix, storage_backend, json.dumps(manifest),
         schema_id, produced_by_run, record_count, digest,
         projection.snapshot_id if projection else None),
    )
    version_id = str(row["id"])
    if projection:
        iceberg.record(version_id, tenant_id, dataset_id, projection)

    if produced_by_run:
        db.execute(
            "update action_run set output_version = %s, status = 'succeeded', "
            "ended_at = now() where id = %s",
            (version_id, produced_by_run),
        )

    return {
        "id": version_id,
        "version": reserved["version"],
        "storage_prefix": prefix,
        "content_hash": digest,
        "sealed": True,
    }
