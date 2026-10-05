"""Writing a version's rows as an Iceberg table. No database, no API, no platform credentials: everything it needs is
passed in, so the same code runs in the API process and in a table worker that holds nothing but a key for one folder.

What a caller provides: where the records are and how to read them (an S3 client and the S3 properties PyIceberg needs,
both made from whatever key the caller holds), the contract the rows must fit, where the table goes, what to write in its
snapshot, and the limits (Settings). What comes back is the table's location and a manifest entry for every file written.
Anything the write put under the table's folder is removed again when it does not finish.

The records file comes in one of three shapes, chosen by its name: a JSON list, one JSON row per line, or Parquet. See
Settings for what each costs in memory and why the limits are what they are.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field

from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger("tablewriter")

# Version 2: row-level deletes, and every tool this was tried against reads it.
# Version 3 adds deletion vectors and row lineage, which nothing here uses yet.
FORMAT_VERSION = 2
TABLE_DIR = "iceberg"

# Munitas contract type to Iceberg type. A list or a dictionary is stored as
# JSON text: a contract names one without saying what is inside, and inventing
# an element type would assert something nothing checked.
_JSON_KINDS = {"list", "dict"}


@dataclass(frozen=True)
class Settings:
    """The limits a write works within. The platform fills these from its configuration at each call; a worker fills
    them from its environment."""

    # Rows read and written at a time, for lines and for Parquet.
    batch_rows: int = 20_000
    # Rows in one Parquet row group, and the bytes after which rows continue in a new data file (0 leaves the library's
    # default for the first, and 512 MB for the second). The library counts file bytes uncompressed, held in memory.
    row_group_rows: int = 0
    file_bytes: int = 64 * 1024 * 1024
    # A JSON list is read whole and takes about six times its size in memory, so its limit is small. The other shapes are
    # streamed, so theirs is a limit on time and not on memory.
    max_list_bytes: int = 32 * 1024 * 1024
    max_stream_bytes: int = 2 * 1024 ** 3
    # One line of newline-delimited JSON may be no longer than this.
    max_row_bytes: int = 8 * 1024 * 1024
    # A Parquet file is read a row group at a time: a row group larger than this (uncompressed, as its own footer says) is
    # refused, and its footer is read with limits, since a hostile footer is how a reader is made to allocate without bound.
    parquet_row_group_bytes: int = 256 * 1024 * 1024
    thrift_string_bytes: int = 64 * 1024 * 1024
    thrift_container_items: int = 1_000_000


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


def _ndjson_chunks(client, bucket: str, key: str, checkpoint, cfg: "Settings"):
    """(rows, first line, last line) in batches of ICEBERG_BATCH_ROWS rows, read as the object streams in."""
    rows: list[dict] = []
    number, first, buffer = 0, 1, b""

    def take(line: bytes):
        nonlocal number, first
        number += 1
        if len(line) > cfg.max_row_bytes:
            raise Skipped(f"line {number} is longer than {_size(cfg.max_row_bytes)}")
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
        if len(buffer) > cfg.max_row_bytes:
            raise Skipped(f"line {number + len(lines) + 1} is longer than {_size(cfg.max_row_bytes)}")
        for line in lines:
            take(line)
            if len(rows) >= cfg.batch_rows:
                yield rows, first, number
                rows = []
    if buffer:
        take(buffer)
    if rows:
        yield rows, first, number


def _list_chunks(rows: list[dict], cfg: "Settings"):
    step = cfg.batch_rows
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


def _parquet_open(props: dict, bucket: str, key: str, cfg: "Settings"):
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
            handle, thrift_string_size_limit=cfg.thrift_string_bytes,
            thrift_container_size_limit=cfg.thrift_container_items)
    except Exception as exc:  # not Parquet, cut short, or a footer past the limits
        handle.close()
        raise Skipped(f"the records file is not a readable Parquet file ({type(exc).__name__})") from exc


def _parquet_check(props: dict, bucket: str, key: str, fields: list[dict], cfg: "Settings") -> int:
    """How many rows the Parquet file says it holds, after checking that it can be read at all: every column the contract
    names is there with a type that fits, nothing else is, and no row group is too large to hold at once. Reads the footer."""
    handle, parquet = _parquet_open(props, bucket, key, cfg)
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
        if biggest > cfg.parquet_row_group_bytes:
            raise Skipped(f"a row group in the Parquet file is {_size(biggest)} uncompressed, over the "
                          f"{_size(cfg.parquet_row_group_bytes)} that is read at once; "
                          "write the file with smaller row groups")
        return meta.num_rows
    finally:
        handle.close()


def _from_parquet(props: dict, bucket: str, key: str, arrow_schema, seen: dict, checkpoint, cfg: "Settings"):
    """Arrow batches from a Parquet file, one batch at a time, each cast to the contract's types. A value that does not fit
    names its column and the kind of error, never the value."""
    import pyarrow as pa

    handle, parquet = _parquet_open(props, bucket, key, cfg)
    try:
        for batch in parquet.iter_batches(batch_size=cfg.batch_rows, use_threads=False):
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


def s3_properties(endpoint: str, access_key: str, secret_key: str) -> dict:
    """The properties PyIceberg's own S3 client needs, for a key the caller holds."""
    return {
        "s3.endpoint": endpoint,
        "s3.access-key-id": access_key,
        "s3.secret-access-key": secret_key,
        "s3.region": "us-east-1",
        "s3.path-style-access": "true",
    }


def s3_client(props: dict):
    """A plain S3 client for the same key, for the reads, listings and removals around a write."""
    import boto3
    from botocore.config import Config

    return boto3.client("s3", endpoint_url=props["s3.endpoint"], aws_access_key_id=props["s3.access-key-id"],
                        aws_secret_access_key=props["s3.secret-access-key"], region_name=props["s3.region"],
                        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))


def write_table(
    *,
    props: dict,
    client,
    bucket: str,
    prefix: str,
    location: str,
    namespace: str,
    table_name: str,
    contract: dict,
    records_key: str,
    summary_base: dict,
    cfg: Settings,
    cancel: "threading.Event | None" = None,
    manifest: bool = True,
    cleanup: bool = True,
) -> Projection:
    """Write the records at `records_key` as an Iceberg table at `location`, which is inside `prefix`.

    `contract` is {"name", "fields", "primary_key"}. `summary_base` is what the caller wants on the snapshot, which the
    count, the hash and the shape of the records are added to. Raises Skipped when there is nothing honest to write.

    `manifest` and `cleanup` are the two things this does that LIST the table's folder in bulk: a manifest entry for every file
    written, and the removal of what a failed write left (which also deletes several objects in one request, a bucket-wide
    permission). A caller whose key is limited to one folder passes False for both, and whoever holds a key that can do those
    takes over: it lists and hashes the files, and clears the folder when the write did not finish. (Making the table itself
    needs a list of object names, which the library does to be sure a metadata file is new.)
    """
    fields = contract["fields"]
    if isinstance(fields, str):
        fields = json.loads(fields)
    primary_key = list(contract["primary_key"] or [])

    def checkpoint() -> None:
        if cancel is not None and cancel.is_set():
            raise Abandoned("writing the table was given up on")

    kind = records_format(records_key)
    try:
        size = int(client.head_object(Bucket=bucket, Key=records_key)["ContentLength"])
    except Exception as exc:  # the object is simply not there, or not readable
        raise Skipped(f"the records object could not be read: {type(exc).__name__}") from exc
    limit = cfg.max_list_bytes if kind == JSON_LIST else cfg.max_stream_bytes
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
            batches = _from_rows(_list_chunks(rows, cfg), "rows", fields, primary_key, seen, checkpoint)
        elif kind == NDJSON:
            records_sha256, total = _digest(client, bucket, records_key, checkpoint, count_lines=True)
            if not total:
                raise Skipped("the records object has no rows")
            batches = _from_rows(_ndjson_chunks(client, bucket, records_key, checkpoint, cfg), "lines", fields, primary_key, seen, checkpoint)
        else:
            total = _parquet_check(props, bucket, records_key, fields, cfg)
            records_sha256, _ = _digest(client, bucket, records_key, checkpoint)
            batches = _from_parquet(props, bucket, records_key, arrow_schema, seen, checkpoint, cfg)
    except (OSError, BotoCoreError, ClientError) as exc:  # the object went away or the storage failed while it was being read
        raise Skipped(f"the records object could not be read: {type(exc).__name__}") from exc

    folder = f"{prefix}/{TABLE_DIR}/"

    try:
        checkpoint()
        from pyiceberg.catalog.memory import InMemoryCatalog

        # A catalog that exists only for this write. The pointer that matters is the
        # one the caller keeps with the version.
        catalog = InMemoryCatalog("munitas-writer", **props, warehouse=f"{location}/warehouse")
        catalog.create_namespace(namespace)
        table = catalog.create_table(
            (namespace, table_name), schema=schema, location=location,
            properties={"format-version": str(FORMAT_VERSION),
                        **({"write.parquet.row-group-limit": str(cfg.row_group_rows)} if cfg.row_group_rows else {}),
                        **({"write.target-file-size-bytes": str(cfg.file_bytes)} if cfg.file_bytes else {})},
        )
        summary = {
            **summary_base,
            "munitas.contract": contract["name"],
            "munitas.records-key": records_key,
            "munitas.records-sha256": records_sha256,
            "munitas.records-format": kind,
            "munitas.record-count": str(total),
        }
        checkpoint()

        def stream():
            yield from batches
            if seen["rows"] != total:
                raise Skipped(f"the records object held {seen['rows']} rows when read and {total} when counted")

        _append_in_groups(table, stream(), arrow_schema, summary, checkpoint, cfg)
        checkpoint()
        snapshot = table.current_snapshot()
        # A permanent name for this snapshot. Written now, before the version is
        # sealed, because nothing may be written under a sealed prefix afterwards.
        table.manage_snapshots().create_tag(snapshot.snapshot_id, table_name).commit()
        table = catalog.load_table((namespace, table_name))

        return Projection(
            namespace=namespace, table_name=table_name, location=location,
            metadata_location=table.metadata_location,
            snapshot_id=table.current_snapshot().snapshot_id,
            record_count=total, records_sha256=records_sha256,
            objects=_manifest_entries(client, bucket, folder) if manifest else [],
        )
    except BaseException as exc:
        # Whatever this attempt wrote is removed, so a version sealed without its table (or not sealed at all) has nothing
        # under its prefix that its fingerprint does not cover. Anything the removal cannot reach is said so.
        if cleanup and not _remove_written(client, bucket, folder):
            exc.cleanup_incomplete = True  # type: ignore[attr-defined]
        raise


def _append_in_groups(table, batches, arrow_schema, summary: dict, checkpoint, cfg: "Settings") -> None:
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
    target = cfg.file_bytes or 512 * 1024 * 1024
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
