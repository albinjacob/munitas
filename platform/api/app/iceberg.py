"""A sealed tabular version, also written as an Iceberg table.

WHY

A version's records are a JSON list sitting in object storage. Only this
platform knows how to read them. Written as an Iceberg table the same rows can
be opened by DuckDB, Spark, Trino or a notebook, which is what makes the data
usable by the tools people already have. Nothing about who may read them
changes: the table lives inside the version's own storage prefix, so the grant
that covers the version covers the table.

WHAT THIS DOES NOT DO

It never decides who may read anything, and it never changes a sealed version.
The version row, the policy engine and the lease remain the authority; this
module only produces another representation of data the version already holds,
and records where it put it (`iceberg_table_ref`).

TWO RULES THAT SHAPE THE CODE

* One table per version, never one per dataset. A credential is granted for one
  version's prefix. A table spanning versions would put other versions' files
  inside the table a reader opens, and the reader's credential would not
  cover them.

* Nothing is written under a sealed prefix after sealing. So the table is
  written completely before the version row exists (its tag included), its
  files are listed in the version's object manifest and therefore in its
  content hash, and no later step moves a branch or a tag.

Everything here is imported lazily so that the API starts, and its schema can be
generated, on a machine without pyarrow.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from . import config, db, logs, storage

log = logs.get_logger("iceberg")

# Version 2: row-level deletes, and every tool this was tried against reads it.
# Version 3 adds deletion vectors and row lineage, which nothing here uses yet.
FORMAT_VERSION = 2
TABLE_DIR = "iceberg"

# Munitas contract type to Iceberg type. A list or a dictionary is stored as
# JSON text: a contract names one without saying what is inside, and inventing
# an element type would assert something nothing checked.
_JSON_KINDS = {"list", "dict"}


class Skipped(Exception):
    """The version is not written as a table, for a reason stated plainly.

    Not an error in the platform: most versions are made of files and have no
    rows to put in a table. Callers log the reason and seal the version anyway.
    """


@dataclass
class Projection:
    namespace: str
    table_name: str
    location: str
    metadata_location: str
    snapshot_id: int
    record_count: int
    records_sha256: str
    format_version: int = FORMAT_VERSION
    # One manifest entry per file written, in the shape object_manifest uses.
    objects: list[dict] = field(default_factory=list)


def table_name_for(version: int) -> str:
    return f"v{version}"


def table_location(backend: str, tenant_id: str, prefix: str) -> str:
    return f"s3://{storage.bucket_for(backend, tenant_id)}/{prefix}/{TABLE_DIR}"


def _types(kind: str):
    """(Iceberg type, Arrow type) for a contract's type name."""
    import pyarrow as pa
    from pyiceberg.types import BooleanType, DoubleType, LongType, StringType

    return {
        "string": (StringType(), pa.string()),
        "float": (DoubleType(), pa.float64()),
        "int": (LongType(), pa.int64()),
        "bool": (BooleanType(), pa.bool_()),
    }.get(kind, (StringType(), pa.string()))


def build(fields: list[dict], primary_key: list[str], rows: list[dict]):
    """The Iceberg schema and the Arrow table for these rows.

    Raises Skipped when the rows cannot honestly be a table: a primary-key
    value missing, or a value that does not fit the type its contract names.
    The worker validates rows against the contract before sealing, so this is
    a second check on the way to a different format, not the first.
    """
    import pyarrow as pa
    from pyiceberg.schema import Schema
    from pyiceberg.types import NestedField

    nested, arrow_fields, columns = [], [], {}
    for index, spec in enumerate(fields, start=1):
        name, kind = spec["name"], spec.get("type", "string")
        iceberg_type, arrow_type = _types(kind)
        required = name in primary_key
        sensitivity = spec.get("sensitivity", "none")
        doc = f"sensitivity={sensitivity}" + (" (JSON text)" if kind in _JSON_KINDS else "")
        nested.append(NestedField(index, name, iceberg_type, required=required, doc=doc))
        arrow_fields.append(pa.field(name, arrow_type, nullable=not required))

        values = []
        for row in rows:
            value = row.get(name)
            if value is not None and kind in _JSON_KINDS:
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if value is None and required:
                raise Skipped(f"a row has no value for the key field {name!r}")
            values.append(value)
        columns[name] = values

    schema = Schema(*nested, identifier_field_ids=[
        n.field_id for n in nested if n.name in primary_key])
    try:
        table = pa.table(
            {f.name: pa.array(columns[f.name], f.type) for f in arrow_fields},
            schema=pa.schema(arrow_fields),
        )
    except (pa.ArrowInvalid, pa.ArrowTypeError) as exc:
        # The error's own text can quote a value from a row, so only its kind is kept.
        raise Skipped(f"a row does not match its contract's field types ({type(exc).__name__})") from exc
    return schema, table


def _file_io(tenant_id: str, backend: str) -> dict:
    """Properties for the S3 client PyIceberg uses, from this tenant's own
    storage identity (never the platform super-key), exactly as the plain
    object writes in this API do."""
    from . import grants

    if backend != "seaweedfs":
        raise Skipped(f"only SeaweedFS-backed versions are written as tables so far, not {backend!r}")
    # Called first for what it does on a first write: it makes the bucket and
    # activates the tenant's identity, which the keys below depend on.
    storage.admin_client_for(backend, tenant_id)
    identity = grants.identity_for_tenant_ingest(tenant_id)
    return {
        "s3.endpoint": config.S3_ENDPOINT,
        "s3.access-key-id": identity["access_key"],
        "s3.secret-access-key": identity["secret_key"],
        "s3.region": "us-east-1",
        "s3.path-style-access": "true",
    }


def _provenance(produced_by_run: str | None) -> dict[str, str]:
    """What produced these rows, read from the register, never supplied by a
    caller. Absent for a version nothing produced, rather than made up."""
    if not produced_by_run:
        return {}
    run = db.one(
        "select code_hash, image_digest, input_versions from action_run where id = %s",
        (produced_by_run,),
    )
    if not run:
        return {}
    return {
        "munitas.produced-by-run": str(produced_by_run),
        "munitas.code-hash": run["code_hash"],
        "munitas.image-digest": run["image_digest"],
        "munitas.input-versions": ",".join(str(v) for v in (run["input_versions"] or [])),
    }


def project(
    *,
    tenant_id: str,
    backend: str,
    dataset_id: str,
    dataset_name: str,
    version_id: str,
    version: int,
    prefix: str,
    schema_id: str,
    records_key: str,
    produced_by_run: str | None,
) -> Projection:
    """Write one version's records as an Iceberg table under its own prefix.

    Returns where the table is and a manifest entry for every file written, so
    the caller can put them in the version's object manifest before the content
    hash is taken. Raises Skipped when there is nothing honest to write.
    """
    if not config.ICEBERG_PROJECTION:
        raise Skipped("projection is switched off (MUNITAS_ICEBERG_PROJECTION=off)")

    contract = db.one(
        "select name, fields, primary_key from schema_contract where id = %s", (schema_id,))
    if not contract:
        raise Skipped("the version names a schema contract that does not exist")
    fields = contract["fields"]
    if isinstance(fields, str):
        fields = json.loads(fields)
    primary_key = list(contract["primary_key"] or [])

    props = _file_io(tenant_id, backend)
    client = storage.admin_client_for(backend, tenant_id)
    bucket = storage.bucket_for(backend, tenant_id)

    try:
        raw = client.get_object(Bucket=bucket, Key=records_key)["Body"].read()
    except Exception as exc:  # the object is simply not there, or not readable
        raise Skipped(f"the records object could not be read: {type(exc).__name__}") from exc
    try:
        rows = json.loads(raw)
    except ValueError as exc:
        raise Skipped("the records object is not JSON") from exc
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        raise Skipped("the records object is not a non-empty list of rows")
    records_sha256 = hashlib.sha256(raw).hexdigest()

    schema, arrow = build(fields, primary_key, rows)

    from pyiceberg.catalog.memory import InMemoryCatalog

    location = table_location(backend, tenant_id, prefix)
    name = table_name_for(version)
    # A catalog that exists only for this write. The pointer that matters is the
    # one kept in iceberg_table_ref, in the register, with the version.
    catalog = InMemoryCatalog("munitas-writer", **props, warehouse=f"{location}/warehouse")
    catalog.create_namespace(dataset_name)
    table = catalog.create_table(
        (dataset_name, name), schema=schema, location=location,
        properties={"format-version": str(FORMAT_VERSION),
                    **({"write.parquet.row-group-limit": str(config.ICEBERG_ROW_GROUP_ROWS)}
                       if config.ICEBERG_ROW_GROUP_ROWS else {}),
                    **({"write.target-file-size-bytes": str(config.ICEBERG_FILE_BYTES)}
                       if config.ICEBERG_FILE_BYTES else {})},
    )
    summary = {
        "munitas.tenant": tenant_id,
        "munitas.dataset-id": dataset_id,
        "munitas.dataset-version-id": version_id,
        "munitas.version": str(version),
        "munitas.contract": contract["name"],
        "munitas.records-key": records_key,
        "munitas.records-sha256": records_sha256,
        "munitas.record-count": str(len(rows)),
        **_provenance(produced_by_run),
    }
    table.append(arrow, snapshot_properties=summary)
    snapshot = table.current_snapshot()
    # A permanent name for this snapshot. Written now, before the version is
    # sealed, because nothing may be written under a sealed prefix afterwards.
    table.manage_snapshots().create_tag(snapshot.snapshot_id, table_name_for(version)).commit()
    table = catalog.load_table((dataset_name, name))

    return Projection(
        namespace=dataset_name, table_name=name, location=location,
        metadata_location=table.metadata_location,
        snapshot_id=table.current_snapshot().snapshot_id,
        record_count=len(rows), records_sha256=records_sha256,
        objects=_manifest_entries(client, bucket, f"{prefix}/{TABLE_DIR}/"),
    )


def _manifest_entries(client, bucket: str, key_prefix: str) -> list[dict]:
    """Every object under the table's folder, as object_manifest entries."""
    entries = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=key_prefix):
        for item in page.get("Contents", []):
            body = client.get_object(Bucket=bucket, Key=item["Key"])["Body"].read()
            entries.append({"key": item["Key"], "bytes": len(body),
                            "sha256": hashlib.sha256(body).hexdigest()})
    return sorted(entries, key=lambda e: e["key"])


def record(version_id: str, tenant_id: str, dataset_id: str, projection: Projection) -> None:
    """Write the pointer. Called once, straight after the version row."""
    db.execute(
        """insert into iceberg_table_ref
             (dataset_version_id, tenant_id, dataset_id, namespace, table_name,
              location, metadata_location, snapshot_id, format_version,
              record_count, records_sha256)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (version_id, tenant_id, dataset_id, projection.namespace,
         projection.table_name, projection.location, projection.metadata_location,
         projection.snapshot_id, projection.format_version,
         projection.record_count, projection.records_sha256),
    )


def try_project(**kwargs) -> tuple[Projection | None, tuple[str, str] | None]:
    """project(), but a version that cannot be written as a table is still
    sealed. Returns the projection, or None and the outcome and reason to be
    written down beside the version (record_note), so the console can say why.
    The reason is a sentence of ours and a kind of error only, never contents:
    a library's message can quote a value from a row."""
    where = {"tenant_id": kwargs.get("tenant_id"),
             "dataset_version_id": kwargs.get("version_id")}
    try:
        return project(**kwargs), None
    except Skipped as why:
        log.info("version not written as an Iceberg table", extra={**where, "reason": str(why)})
        return None, ("skipped", f"The table was not written: {why}.")
    except Exception as exc:  # a storage or library failure must never block a seal
        log.error("writing a version as an Iceberg table failed; sealing without it",
                  extra={**where, "error_type": type(exc).__name__})
        return None, ("failed", f"Writing the table failed ({type(exc).__name__}), so the version was sealed without it.")


NOT_REQUESTED = ("not_requested",
                 "This version was sealed as files, and no table of rows was named for it, so no table was written.")


def record_note(version_id: str, tenant_id: str, why: tuple[str, str]) -> None:
    """Write down why a version has no table copy. Once, straight after the version row."""
    db.execute(
        """insert into iceberg_projection_note (dataset_version_id, tenant_id, outcome, reason)
           values (%s, %s, %s, %s) on conflict (dataset_version_id) do nothing returning dataset_version_id""",
        (version_id, tenant_id, why[0], why[1]))
