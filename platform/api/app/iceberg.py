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

The writing itself is in the tablewriter library (platform/tablewriter), which has no
database and no platform credentials, so the same code also runs in a table worker. What stays
here is what needs the register: the contract, the tenant's own storage identity, the
provenance of the run, the pointer written beside the version, and the rules for what a failed
write means for a seal.

Everything here is imported lazily so that the API starts, and its schema can be
generated, on a machine without pyarrow.
"""

from __future__ import annotations

import concurrent.futures
import json
import threading

import tablewriter
from tablewriter import (  # noqa: F401  (re-exported: callers and checks name these here)
    CHUNK, FORMAT_VERSION, JSON_LIST, NDJSON, PARQUET, TABLE_DIR, Abandoned, Projection, Skipped, build,
    records_format)

from . import config, db, logs, storage

log = logs.get_logger("iceberg")


class TableRequired(Exception):
    """A version that was asked to be a table was not sealed, because its table could not be written.

    Raised before anything is written to the register, so the version number is unused and a retry takes it."""

    def __init__(self, outcome: str, reason: str):
        super().__init__(reason)
        self.outcome, self.reason = outcome, reason


def table_name_for(version: int) -> str:
    return f"v{version}"


def table_location(backend: str, tenant_id: str, prefix: str) -> str:
    return f"s3://{storage.bucket_for(backend, tenant_id)}/{prefix}/{TABLE_DIR}"


def settings() -> tablewriter.Settings:
    """The limits a write works within, read from configuration at each call."""
    return tablewriter.Settings(
        batch_rows=config.ICEBERG_BATCH_ROWS, row_group_rows=config.ICEBERG_ROW_GROUP_ROWS, file_bytes=config.ICEBERG_FILE_BYTES,
        max_list_bytes=config.ICEBERG_MAX_BYTES, max_stream_bytes=config.ICEBERG_STREAM_MAX_BYTES,
        max_row_bytes=config.ICEBERG_MAX_ROW_BYTES, parquet_row_group_bytes=config.ICEBERG_PARQUET_MAX_ROW_GROUP_BYTES,
        thrift_string_bytes=config.ICEBERG_PARQUET_THRIFT_STRING_BYTES,
        thrift_container_items=config.ICEBERG_PARQUET_THRIFT_CONTAINER_ITEMS)


def _file_io(tenant_id: str, backend: str) -> dict:
    """Properties for the S3 client PyIceberg uses, from this tenant's own
    storage identity (never the platform super-key), exactly as the plain
    object writes in this API do."""
    from . import grants

    if backend != "seaweedfs":
        raise Skipped(f"only SeaweedFS-backed versions are written as tables so far, not {backend!r}", blocking=False)
    # Called first for what it does on a first write: it makes the bucket and
    # activates the tenant's identity, which the keys below depend on.
    storage.admin_client_for(backend, tenant_id)
    identity = grants.identity_for_tenant_ingest(tenant_id)
    return tablewriter.s3_properties(config.S3_ENDPOINT, identity["access_key"], identity["secret_key"])


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


def summary_base(*, tenant_id: str, dataset_id: str, version_id: str, version: int, produced_by_run: str | None) -> dict:
    """What every snapshot says about where it came from, from the register and never from a caller."""
    return {
        "munitas.tenant": tenant_id,
        "munitas.dataset-id": dataset_id,
        "munitas.dataset-version-id": version_id,
        "munitas.version": str(version),
        **_provenance(produced_by_run),
    }


def contract_of(schema_id: str) -> dict:
    """The contract the rows must fit, or Skipped when the version names one that does not exist."""
    contract = db.one("select name, fields, primary_key from schema_contract where id = %s", (schema_id,))
    if not contract:
        raise Skipped("the version names a schema contract that does not exist")
    fields = contract["fields"]
    if isinstance(fields, str):
        fields = json.loads(fields)
    return {"name": contract["name"], "fields": fields, "primary_key": list(contract["primary_key"] or [])}


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
    cancel: "threading.Event | None" = None,
) -> Projection:
    """Write one version's records as an Iceberg table under its own prefix, in this process.

    Returns where the table is and a manifest entry for every file written, so
    the caller can put them in the version's object manifest before the content
    hash is taken. Raises Skipped when there is nothing honest to write.
    """
    if not config.ICEBERG_PROJECTION:
        raise Skipped("projection is switched off (MUNITAS_ICEBERG_PROJECTION=off)", blocking=False)
    contract = contract_of(schema_id)
    props = _file_io(tenant_id, backend)
    return tablewriter.write_table(
        props=props, client=storage.admin_client_for(backend, tenant_id), bucket=storage.bucket_for(backend, tenant_id),
        prefix=prefix, location=table_location(backend, tenant_id, prefix), namespace=dataset_name,
        table_name=table_name_for(version), contract=contract, records_key=records_key,
        summary_base=summary_base(tenant_id=tenant_id, dataset_id=dataset_id, version_id=version_id, version=version,
                                  produced_by_run=produced_by_run),
        cfg=settings(), cancel=cancel)


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


def try_project(**kwargs) -> tuple[Projection | None, tuple[str, str, bool] | None]:
    """project() under a deadline, for a version that is sealed all the same unless the caller asked for a table.

    Returns the projection, or None and the outcome, the reason and whether it blocks a caller who asked for a table
    (enforce), to be written down beside the version (record_note), so the console can say why. The reason is a
    sentence of ours and a kind of error only, never contents: a library's message can quote a value from a row.
    """
    where = {"tenant_id": kwargs.get("tenant_id"),
             "dataset_version_id": kwargs.get("version_id")}
    cancel = threading.Event()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="iceberg-write")
    future = pool.submit(project, cancel=cancel, **kwargs)
    try:
        return future.result(timeout=config.ICEBERG_TIMEOUT_SECONDS), None
    except concurrent.futures.TimeoutError:
        # The writer cannot be killed, so it is told to stop at its next step and to remove what it wrote.
        cancel.set()
        log.error("writing a version as an Iceberg table took too long; giving up", extra=where)
        return None, ("failed", f"Writing the table did not finish within {config.ICEBERG_TIMEOUT_SECONDS} seconds, so it was given up on.", True)
    except Skipped as why:
        log.info("version not written as an Iceberg table", extra={**where, "reason": str(why)})
        return None, ("skipped", f"The table was not written: {why}." + _cleanup_note(why), why.blocking)
    except Exception as exc:  # a storage or library failure
        log.error("writing a version as an Iceberg table failed", extra={**where, "error_type": type(exc).__name__})
        return None, ("failed", f"Writing the table failed ({type(exc).__name__})." + _cleanup_note(exc), True)
    finally:
        pool.shutdown(wait=False)


def _cleanup_note(exc: BaseException) -> str:
    return (" Some files it wrote could not be removed, and a person who runs the platform needs to look."
            if getattr(exc, "cleanup_incomplete", False) else "")


NOT_REQUESTED = ("not_requested",
                 "This version was sealed as files, and no table of rows was named for it, so no table was written.", False)


def enforce(why: tuple[str, str, bool] | None, table_required: bool | None) -> None:
    """Refuse the seal when a table was asked for and could not be written, before anything reaches the register."""
    if not why or not why[2]:
        return
    required = config.ICEBERG_FAIL_CLOSED if table_required is None else table_required
    if required:
        raise TableRequired(why[0], why[1])


def record_note(version_id: str, tenant_id: str, why: tuple) -> None:
    """Write down why a version has no table copy. Once, straight after the version row."""
    db.execute(
        """insert into iceberg_projection_note (dataset_version_id, tenant_id, outcome, reason)
           values (%s, %s, %s, %s) on conflict (dataset_version_id) do nothing returning dataset_version_id""",
        (version_id, tenant_id, why[0], why[1]))
