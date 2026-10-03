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

import concurrent.futures
import hashlib
import json
import threading
from dataclasses import dataclass, field

from botocore.exceptions import BotoCoreError, ClientError

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
    rows to put in a table. `blocking` says whether a caller who asked for a
    table should be refused: true for a reason about the data (an unreadable
    records file, rows that do not fit the contract), false for a reason the
    caller cannot fix by changing the data (projection switched off, files on
    R2, a file over the size limit).
    """

    def __init__(self, message: str, blocking: bool = True):
        super().__init__(message)
        self.blocking = blocking


class Abandoned(Exception):
    """Writing was given up on while it was running (it took too long), so what it wrote is removed."""


class TableRequired(Exception):
    """A version that was asked to be a table was not sealed, because its table could not be written.

    Raised before anything is written to the register, so the version number is unused and a retry takes it."""

    def __init__(self, outcome: str, reason: str):
        super().__init__(reason)
        self.outcome, self.reason = outcome, reason


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


# Read in pieces this large when a file is only being hashed or split into lines.
CHUNK = 8 * 1024 * 1024
JSON_LIST, NDJSON, PARQUET = "json", "ndjson", "parquet"


def records_format(records_key: str) -> str:
    """Which shape the records file is in, from its name. A name that says nothing is a JSON list, which is what every
    caller sent before the other two existed."""
    lowered = records_key.lower()
    if lowered.endswith((".ndjson", ".jsonl")):
        return NDJSON
    if lowered.endswith(".parquet"):
        return PARQUET
    return JSON_LIST


def _iceberg_schema(fields: list[dict], primary_key: list[str]):
    """The Iceberg schema build() produces, available before any row has been read."""
    from pyiceberg.schema import Schema
    from pyiceberg.types import NestedField

    nested = []
    for index, spec in enumerate(fields, start=1):
        kind = spec.get("type", "string")
        doc = f"sensitivity={spec.get('sensitivity', 'none')}" + (" (JSON text)" if kind in _JSON_KINDS else "")
        nested.append(NestedField(index, spec["name"], _types(kind)[0], required=spec["name"] in primary_key, doc=doc))
    return Schema(*nested, identifier_field_ids=[n.field_id for n in nested if n.name in primary_key])


def _arrow_schema(fields: list[dict], primary_key: list[str]):
    """The Arrow schema build() produces, available before any row has been read."""
    import pyarrow as pa

    return pa.schema([pa.field(spec["name"], _types(spec.get("type", "string"))[1], nullable=spec["name"] not in primary_key)
                      for spec in fields])


def _size(n: int) -> str:
    """A size for a message. Megabytes, or kilobytes where a limit has been set small enough that megabytes would read 0."""
    return f"{n // (1024 * 1024)} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


def _names(found: list[str]) -> str:
    """Column names for a message: a few of them, each cut short. Names come from a file, so they are bounded."""
    shown = [repr(n[:40]) for n in found[:5]]
    return ", ".join(shown) + (f" and {len(found) - 5} more" if len(found) > 5 else "")


def _digest(client, bucket: str, key: str, checkpoint, count_lines: bool = False) -> tuple[str, int]:
    """The SHA-256 of the records object, read in pieces, and for newline-delimited JSON how many rows (non-blank lines)
    it holds. The hash and the count go into the table's snapshot before any row is written, so they are read first."""
    digest, rows, open_line_has_text = hashlib.sha256(), 0, False
    for chunk in client.get_object(Bucket=bucket, Key=key)["Body"].iter_chunks(CHUNK):
        checkpoint()
        digest.update(chunk)
        if count_lines:
            parts = chunk.split(b"\n")
            for index, part in enumerate(parts):
                if part.strip():
                    open_line_has_text = True
                if index < len(parts) - 1:
                    rows += open_line_has_text
                    open_line_has_text = False
    return digest.hexdigest(), rows + (1 if count_lines and open_line_has_text else 0)


def _ndjson_chunks(client, bucket: str, key: str, checkpoint):
    """(rows, first line, last line) in batches of ICEBERG_BATCH_ROWS rows, read as the object streams in."""
    rows: list[dict] = []
    number, first, buffer = 0, 1, b""

    def take(line: bytes):
        nonlocal number, first
        number += 1
        if len(line) > config.ICEBERG_MAX_ROW_BYTES:
            raise Skipped(f"line {number} is longer than {_size(config.ICEBERG_MAX_ROW_BYTES)}")
        if not line.strip():
            return
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise Skipped(f"line {number} is not JSON") from exc
        if not isinstance(row, dict):
            raise Skipped(f"line {number} is not a row: each line must be a JSON object")
        if not rows:
            first = number
        rows.append(row)

    for chunk in client.get_object(Bucket=bucket, Key=key)["Body"].iter_chunks(CHUNK):
        checkpoint()
        *lines, buffer = (buffer + chunk).split(b"\n")
        if len(buffer) > config.ICEBERG_MAX_ROW_BYTES:
            raise Skipped(f"line {number + len(lines) + 1} is longer than {_size(config.ICEBERG_MAX_ROW_BYTES)}")
        for line in lines:
            take(line)
            if len(rows) >= config.ICEBERG_BATCH_ROWS:
                yield rows, first, number
                rows = []
    if buffer:
        take(buffer)
    if rows:
        yield rows, first, number


def _list_chunks(rows: list[dict]):
    step = config.ICEBERG_BATCH_ROWS
    for start in range(0, len(rows), step):
        yield rows[start:start + step], start + 1, min(start + step, len(rows))


def _from_rows(chunks, unit: str, fields: list[dict], primary_key: list[str], seen: dict, checkpoint):
    """Arrow batches from batches of JSON rows. A row that does not fit says which rows to look at, never what is in them."""
    for rows, first, last in chunks:
        checkpoint()
        try:
            _, table = build(fields, primary_key, rows)
        except Skipped as exc:
            raise Skipped(f"{exc} ({unit} {first} to {last})", blocking=exc.blocking) from exc
        seen["rows"] += len(rows)
        yield from table.to_batches()


def _compatible(kind: str, arrow_type) -> bool:
    """Whether a Parquet column of this type may be read as a contract field of this kind. Strict on purpose: text is never
    read as a number, and a number is never read as text, because the contract is what the table promises."""
    import pyarrow as pa

    if pa.types.is_dictionary(arrow_type):
        arrow_type = arrow_type.value_type
    if kind == "int":
        return pa.types.is_integer(arrow_type)
    if kind == "float":
        return pa.types.is_floating(arrow_type) or pa.types.is_integer(arrow_type)
    if kind == "bool":
        return pa.types.is_boolean(arrow_type)
    # A string, and a list or a dictionary, which a contract stores as JSON text.
    return pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type)


def _parquet_open(props: dict, bucket: str, key: str):
    """(handle, ParquetFile) for a Parquet records object, read through the tenant's own storage identity and with limits on
    what its footer may ask the reader to allocate."""
    from urllib.parse import urlparse

    import pyarrow.fs as pafs
    import pyarrow.parquet as pq

    endpoint = urlparse(props["s3.endpoint"])
    filesystem = pafs.S3FileSystem(
        access_key=props["s3.access-key-id"], secret_key=props["s3.secret-access-key"],
        endpoint_override=endpoint.netloc, scheme=endpoint.scheme or "http", region=props["s3.region"])
    handle = filesystem.open_input_file(f"{bucket}/{key}")
    try:
        return handle, pq.ParquetFile(
            handle, thrift_string_size_limit=config.ICEBERG_PARQUET_THRIFT_STRING_BYTES,
            thrift_container_size_limit=config.ICEBERG_PARQUET_THRIFT_CONTAINER_ITEMS)
    except Exception as exc:  # not Parquet, cut short, or a footer past the limits
        handle.close()
        raise Skipped(f"the records file is not a readable Parquet file ({type(exc).__name__})") from exc


def _parquet_check(props: dict, bucket: str, key: str, fields: list[dict]) -> int:
    """How many rows the Parquet file says it holds, after checking that it can be read at all: every column the contract
    names is there with a type that fits, nothing else is, and no row group is too large to hold at once. Reads the footer."""
    handle, parquet = _parquet_open(props, bucket, key)
    try:
        schema, meta = parquet.schema_arrow, parquet.metadata
        names = schema.names
        if len(set(names)) != len(names):
            raise Skipped("the Parquet file has two columns with the same name")
        wanted = {spec["name"]: spec.get("type", "string") for spec in fields}
        if absent := [n for n in wanted if n not in names]:
            raise Skipped(f"the Parquet file has no column for {_names(absent)}")
        if extra := [n for n in names if n not in wanted]:
            raise Skipped(f"the Parquet file has columns the contract does not describe: {_names(extra)}")
        if wrong := [n for n, kind in wanted.items() if not _compatible(kind, schema.field(n).type)]:
            raise Skipped(f"the Parquet file's column types do not match the contract for {_names(wrong)}")
        if meta.num_rows < 1:
            raise Skipped("the Parquet file has no rows")
        biggest = max(meta.row_group(i).total_byte_size for i in range(meta.num_row_groups))
        if biggest > config.ICEBERG_PARQUET_MAX_ROW_GROUP_BYTES:
            raise Skipped(f"a row group in the Parquet file is {_size(biggest)} uncompressed, over the "
                          f"{_size(config.ICEBERG_PARQUET_MAX_ROW_GROUP_BYTES)} that is read at once; "
                          "write the file with smaller row groups")
        return meta.num_rows
    finally:
        handle.close()


def _from_parquet(props: dict, bucket: str, key: str, arrow_schema, seen: dict, checkpoint):
    """Arrow batches from a Parquet file, one batch at a time, each cast to the contract's types. A value that does not fit
    names its column and the kind of error, never the value."""
    import pyarrow as pa

    handle, parquet = _parquet_open(props, bucket, key)
    try:
        for batch in parquet.iter_batches(batch_size=config.ICEBERG_BATCH_ROWS, use_threads=False):
            checkpoint()
            columns = []
            for target in arrow_schema:
                column = batch.column(batch.schema.get_field_index(target.name))
                if pa.types.is_dictionary(column.type):
                    column = column.dictionary_decode()
                try:
                    column = column.cast(target.type, safe=True)
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
                    raise Skipped(f"a value in column {target.name!r} does not fit its contract type ({type(exc).__name__})") from exc
                if not target.nullable and column.null_count:
                    raise Skipped(f"a row has no value for the key field {target.name!r}")
                columns.append(column)
            seen["rows"] += batch.num_rows
            yield pa.RecordBatch.from_arrays(columns, schema=arrow_schema)
    finally:
        handle.close()


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
    cancel: "threading.Event | None" = None,
) -> Projection:
    """Write one version's records as an Iceberg table under its own prefix.

    Returns where the table is and a manifest entry for every file written, so
    the caller can put them in the version's object manifest before the content
    hash is taken. Raises Skipped when there is nothing honest to write.
    """
    if not config.ICEBERG_PROJECTION:
        raise Skipped("projection is switched off (MUNITAS_ICEBERG_PROJECTION=off)", blocking=False)

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

    def checkpoint() -> None:
        if cancel is not None and cancel.is_set():
            raise Abandoned("writing the table was given up on")

    kind = records_format(records_key)
    try:
        size = int(client.head_object(Bucket=bucket, Key=records_key)["ContentLength"])
    except Exception as exc:  # the object is simply not there, or not readable
        raise Skipped(f"the records object could not be read: {type(exc).__name__}") from exc
    limit = config.ICEBERG_MAX_BYTES if kind == JSON_LIST else config.ICEBERG_STREAM_MAX_BYTES
    if size > limit:
        advice = ("; a JSON list is read whole, so send rows this many as newline-delimited JSON or Parquet"
                  if kind == JSON_LIST else "")
        raise Skipped(f"the records file is {_size(size)}, over the {_size(limit)} "
                      f"that is written as a table{advice}", blocking=False)

    schema, arrow_schema = _iceberg_schema(fields, primary_key), _arrow_schema(fields, primary_key)
    seen = {"rows": 0}
    try:
        if kind == JSON_LIST:
            raw = client.get_object(Bucket=bucket, Key=records_key)["Body"].read()
            try:
                rows = json.loads(raw)
            except ValueError as exc:
                raise Skipped("the records object is not JSON") from exc
            if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
                raise Skipped("the records object is not a non-empty list of rows")
            records_sha256, total = hashlib.sha256(raw).hexdigest(), len(rows)
            batches = _from_rows(_list_chunks(rows), "rows", fields, primary_key, seen, checkpoint)
        elif kind == NDJSON:
            records_sha256, total = _digest(client, bucket, records_key, checkpoint, count_lines=True)
            if not total:
                raise Skipped("the records object has no rows")
            batches = _from_rows(_ndjson_chunks(client, bucket, records_key, checkpoint), "lines", fields, primary_key, seen, checkpoint)
        else:
            total = _parquet_check(props, bucket, records_key, fields)
            records_sha256, _ = _digest(client, bucket, records_key, checkpoint)
            batches = _from_parquet(props, bucket, records_key, arrow_schema, seen, checkpoint)
    except (OSError, BotoCoreError, ClientError) as exc:  # the object went away or the storage failed while it was being read
        raise Skipped(f"the records object could not be read: {type(exc).__name__}") from exc

    folder = f"{prefix}/{TABLE_DIR}/"

    try:
        checkpoint()
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
            "munitas.records-format": kind,
            "munitas.record-count": str(total),
            **_provenance(produced_by_run),
        }
        checkpoint()

        def stream():
            yield from batches
            if seen["rows"] != total:
                raise Skipped(f"the records object held {seen['rows']} rows when read and {total} when counted")

        _append_in_groups(table, stream(), arrow_schema, summary, checkpoint)
        checkpoint()
        snapshot = table.current_snapshot()
        # A permanent name for this snapshot. Written now, before the version is
        # sealed, because nothing may be written under a sealed prefix afterwards.
        table.manage_snapshots().create_tag(snapshot.snapshot_id, table_name_for(version)).commit()
        table = catalog.load_table((dataset_name, name))

        return Projection(
            namespace=dataset_name, table_name=name, location=location,
            metadata_location=table.metadata_location,
            snapshot_id=table.current_snapshot().snapshot_id,
            record_count=total, records_sha256=records_sha256,
            objects=_manifest_entries(client, bucket, f"{prefix}/{TABLE_DIR}/"),
        )
    except BaseException as exc:
        # Whatever this attempt wrote is removed, so a version sealed without its table (or not sealed at all) has nothing
        # under its prefix that its fingerprint does not cover. Anything the removal cannot reach is said so.
        if not _remove_written(client, bucket, folder):
            exc.cleanup_incomplete = True  # type: ignore[attr-defined]
        raise


def _append_in_groups(table, batches, arrow_schema, summary: dict, checkpoint) -> None:
    """Append a stream of Arrow batches to the table as one snapshot, writing one data file's worth of rows at a time.

    Not table.append(reader). The library documents that as bounded in memory, and it is only when the source is slower
    than the writer: it hands each file-sized group of the stream to a thread pool's map(), which takes every group the
    source produces without waiting for the writes. A Parquet source is fast, so the groups pile up in memory (measured
    here: Arrow held 359 MB of a 2-million-row file at once, and the process peaked at 943 MB). Writing a group and letting
    it go before reading the next is what bounds it. It is what the library's own append does for a table, step for step:
    one snapshot producer, data files appended to it, one commit at the end. The two underscored names are the ones
    Transaction.append itself calls, and the check that exercises this runs them for real.
    """
    import itertools

    import pyarrow as pa
    from pyiceberg.io.pyarrow import _check_pyarrow_schema_compatible, _dataframe_to_data_files

    _check_pyarrow_schema_compatible(table.schema(), provided_schema=arrow_schema, format_version=FORMAT_VERSION)
    target = config.ICEBERG_FILE_BYTES or 512 * 1024 * 1024
    counter = itertools.count(0)
    with table.transaction() as transaction:
        with transaction._append_snapshot_producer(summary) as append_files:

            def write(group: list) -> None:
                for data_file in _dataframe_to_data_files(
                        table_metadata=transaction.table_metadata, write_uuid=append_files.commit_uuid,
                        df=pa.Table.from_batches(group, schema=arrow_schema), io=table.io, counter=counter):
                    append_files.append_data_file(data_file)

            group, held = [], 0
            for batch in batches:
                group.append(batch)
                held += batch.nbytes
                if held >= target:
                    write(group)
                    group, held = [], 0
                    checkpoint()
            if group:
                write(group)


def _remove_written(client, bucket: str, folder: str) -> bool:
    """Delete everything under the table's folder. True when nothing is left."""
    try:
        paginator = client.get_paginator("list_objects_v2")
        keys = [item["Key"] for page in paginator.paginate(Bucket=bucket, Prefix=folder) for item in page.get("Contents", [])]
        for start in range(0, len(keys), 1000):
            client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys[start:start + 1000]]})
        return True
    except Exception as exc:  # reported by the caller as an incomplete clean-up
        log.error("a failed table write could not be cleaned up", extra={"error_type": type(exc).__name__, "reason": folder[:80]})
        return False


def _manifest_entries(client, bucket: str, key_prefix: str) -> list[dict]:
    """Every object under the table's folder, as object_manifest entries."""
    entries = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=key_prefix):
        for item in page.get("Contents", []):
            digest, length = hashlib.sha256(), 0
            for chunk in client.get_object(Bucket=bucket, Key=item["Key"])["Body"].iter_chunks(CHUNK):
                digest.update(chunk)
                length += len(chunk)
            entries.append({"key": item["Key"], "bytes": length, "sha256": digest.hexdigest()})
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
