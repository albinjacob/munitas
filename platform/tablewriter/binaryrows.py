"""Reading a table that arrives in a binary column format (Parquet, Avro, ORC, Arrow, Feather, Excel), checking it against the contract, and handing on typed
Arrow batches. Only a table worker reads these: a file in one of these formats has a parser of its own, and a hostile file must not be opened inside the
API, which holds every session and decision. The API refuses the endings and sends a person to the route that makes a job.

What is checked, in this order, and refused with every problem found:

  1. What the file is: it must open as the format its ending says, with its metadata and its stripes, row groups, blocks or batches within limits (a
     footer that asks a reader to allocate without bound, a block that claims to be gigabytes, a spreadsheet that expands to hundreds of times its size).
  2. Its layout: every column the contract describes is there, nothing else is, none is named twice, and each has a type the contract's kind can be
     read from without loss (a whole number into a decimal, yes; text into a number, never; a number into text, never).
  3. Its rows: a value that does not fit its column's type, a missing value in a key or a required column, and a key that repeats, each named by row.

A column that cannot be read as what the contract says is a problem of the file, said once for the column, not once for every row. Rows are numbered from 1.
For a spreadsheet the row of the sheet is named as well, because the first row is the header.
"""
from __future__ import annotations

import io
import os
import tempfile
import zipfile
from decimal import Decimal, InvalidOperation
from typing import BinaryIO, Callable, Iterator

from . import types as kinds
from .textrows import Collector, KeyCheck, Refused

FORMATS = {".parquet": "parquet", ".avro": "avro", ".orc": "orc", ".arrow": "arrow", ".feather": "arrow", ".xlsx": "excel"}
WORDS = {"parquet": "a Parquet file", "avro": "an Avro file", "orc": "an ORC file", "arrow": "an Arrow or Feather file", "excel": "an Excel workbook (.xlsx)"}
SHEET_ROW_LIMIT = 1_048_576
EXCEL_EXPANSION_RATIO = 100            # a member of the workbook may expand to this many times its size
EXCEL_MAX_UNPACKED = 8 * 1024 ** 3     # and all of them to this much
AVRO_MAX_READ = 256 * 1024 * 1024      # no single read of an Avro file may ask for more than this: a block that claims to be gigabytes is refused


def format_of(name: str) -> str | None:
    lowered = name.lower()
    for ending, found in FORMATS.items():
        if lowered.endswith(ending):
            return found
    return None


def _types_fit(spec: dict, file_type) -> bool:
    """Whether a column of this type in the file can be read as the contract's kind without losing anything."""
    import pyarrow as pa

    k = kinds.kind(spec)
    t = file_type.value_type if pa.types.is_dictionary(file_type) else file_type
    if k == kinds.INT:
        return pa.types.is_integer(t)
    if k == kinds.FLOAT:
        return pa.types.is_floating(t) or pa.types.is_integer(t)
    if k == kinds.BOOL:
        return pa.types.is_boolean(t)
    if k == kinds.DATE:
        return pa.types.is_date(t)
    if k == kinds.TIMESTAMP:
        return pa.types.is_timestamp(t)
    if k == kinds.DECIMAL:
        return pa.types.is_decimal(t) or pa.types.is_integer(t)
    return pa.types.is_string(t) or pa.types.is_large_string(t)


def _word(file_type) -> str:
    import pyarrow as pa

    t = file_type
    if pa.types.is_integer(t):
        return "a whole number"
    if pa.types.is_floating(t):
        return "a number"
    if pa.types.is_decimal(t):
        return "a decimal"
    if pa.types.is_boolean(t):
        return "true or false"
    if pa.types.is_date(t):
        return "a date"
    if pa.types.is_timestamp(t):
        return "a date and time"
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "text"
    return "something that is not a single value"


def layout_problems(schema, fields: list[dict]) -> list[str]:
    """Everything wrong with the shape of the file before any row is read."""
    names = list(schema.names)
    wanted = {f["name"]: f for f in fields}
    out = [f"the file names the column {n} more than once" for n in sorted({n for n in names if names.count(n) > 1})]
    out += [f"the file has no column {n}, which the contract describes" for n in wanted if n not in names]
    out += [f"the file has a column {n}, which the contract does not describe" for n in names if n not in wanted]
    for name, spec in wanted.items():
        if name in names and names.count(name) == 1 and not _types_fit(spec, schema.field(name).type):
            out.append(f"the column {name} holds {_word(schema.field(name).type)} in the file, and the contract says {kinds.describe(spec)}")
    return out


def _fit_zone(column, spec: dict, unit: str):
    """A timestamp column of the file as the contract's: with a time zone, a file that has none is read as UTC; without one, a file that has one is read as the
    wall clock it holds in UTC. Avro and ORC do not tell the two apart, so this does not refuse either."""
    import pyarrow as pa
    import pyarrow.compute as pc

    zoned = kinds.has_zone(spec)
    has_zone = column.type.tz is not None
    if zoned and not has_zone:
        column = pc.assume_timezone(column, "UTC")
    elif not zoned and has_zone:
        column = column.cast(pa.timestamp(column.type.unit))
    return column


def typed_batch(batch, fields: list[dict], key: list[str], first_row: int, found: Collector, arrow_schema, line_of: Callable[[int], int] | None = None):
    """A batch read from the file, as a batch of the contract's types, or None when a value in it is wrong (each wrong value is added to `found`)."""
    import pyarrow as pa
    import pyarrow.compute as pc

    columns, spoiled = [], False
    for spec in fields:
        name = spec["name"]
        column = batch.column(batch.schema.get_field_index(name))
        if pa.types.is_dictionary(column.type):
            column = column.dictionary_decode()
        target = kinds.arrow_type(spec)
        if kinds.kind(spec) == kinds.TIMESTAMP:
            column = _fit_zone(column, spec, "us")
        try:
            made = pc.cast(column, target, safe=True)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            made, wrong = _one_by_one(column, target)
            for i in wrong:
                found.add(first_row + i, name, f"{name} is not {kinds.describe(spec)}", line=line_of(first_row + i) if line_of else None)
            spoiled = True
        if (name in key or spec.get("required")) and made.null_count:
            why = "it is part of the key" if name in key else "every row needs a value"
            for i in pc.indices_nonzero(pc.is_null(made)).to_pylist():
                found.add(first_row + i, name, f"{name} is empty, and {why}", line=line_of(first_row + i) if line_of else None)
            spoiled = True
        columns.append(made)
        if spoiled and found.full:
            return None
    return None if spoiled else pa.RecordBatch.from_arrays(columns, schema=arrow_schema)


def _one_by_one(column, target):
    """A column that would not convert as a whole: the values that do not, found one at a time, and the column with those left empty."""
    import pyarrow as pa

    out, wrong = [], []
    for i, value in enumerate(column.to_pylist()):
        if value is None:
            out.append(None)
            continue
        try:
            out.append(pa.array([value], column.type).cast(target, safe=True)[0].as_py())
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            wrong.append(i)
            out.append(None)
    return pa.array(out, target), wrong


def _slices(batch, size: int) -> Iterator:
    for start in range(0, batch.num_rows, size):
        yield batch.slice(start, size)


# ---- the formats ---------------------------------------------------------------------------------------------------------------------------


def _parquet(open_seekable, cfg, checkpoint) -> tuple:
    import pyarrow.parquet as pq

    handle = open_seekable()
    try:
        parquet = pq.ParquetFile(handle, thrift_string_size_limit=cfg.thrift_string_bytes, thrift_container_size_limit=cfg.thrift_container_items)
    except Exception as exc:  # noqa: BLE001 - not Parquet, cut short, or a footer past the limits
        handle.close()
        raise Refused(["the file is not a readable Parquet file: it is not Parquet, it was cut short, or its footer asks for more than is allowed"]) from exc
    meta = parquet.metadata
    if meta.num_rows < 1:
        handle.close()
        raise Refused(["the Parquet file has no rows"])
    biggest = max(meta.row_group(i).total_byte_size for i in range(meta.num_row_groups))
    if biggest > cfg.parquet_row_group_bytes:
        handle.close()
        raise Refused([f"a row group in the Parquet file is {biggest // (1024 * 1024)} MB uncompressed, over the {cfg.parquet_row_group_bytes // (1024 * 1024)} MB that "
                       "is read at once. Write the file with smaller row groups"])

    def rows():
        try:
            for batch in parquet.iter_batches(batch_size=cfg.batch_rows, use_threads=False):
                checkpoint()
                yield batch
        finally:
            handle.close()

    return parquet.schema_arrow, rows()


class _NextRow(dict):
    """A sheet's own row number for a row of data: the header is the sheet's first row, so row n is on sheet row n + 1."""

    def get(self, row, default=None):
        return None if row is None else row + 1


def _zone_database() -> None:
    """ORC keeps times against a time zone and reads the system's zone database for it. An image without one (and Windows) uses the one the tzdata package carries."""
    if "TZDIR" in os.environ or os.path.isdir("/usr/share/zoneinfo"):       # TZDIR is the ORC library's own setting, not one of the platform's
        return
    try:
        import tzdata

        os.putenv("TZDIR", os.path.join(os.path.dirname(tzdata.__file__), "zoneinfo"))
    except ImportError:
        pass


def _orc(open_seekable, cfg, checkpoint) -> tuple:
    import pyarrow.orc as orc

    _zone_database()
    handle = open_seekable()
    try:
        reader = orc.ORCFile(handle)
        schema, stripes = reader.schema, reader.nstripes
    except Exception as exc:  # noqa: BLE001
        handle.close()
        raise Refused(["the file is not a readable ORC file: it is not ORC, or it was cut short"]) from exc
    if not stripes:
        handle.close()
        raise Refused(["the ORC file has no rows"])

    def rows():
        try:
            for i in range(stripes):
                checkpoint()
                stripe = reader.read_stripe(i)
                if stripe.nbytes > cfg.parquet_row_group_bytes:
                    raise Refused([f"a stripe in the ORC file is {stripe.nbytes // (1024 * 1024)} MB, over the {cfg.parquet_row_group_bytes // (1024 * 1024)} MB that is read at "
                                   "once. Write the file with smaller stripes"])
                yield from _slices(stripe, cfg.batch_rows)
        finally:
            handle.close()

    return schema, rows()


def _arrow(open_seekable, cfg, checkpoint) -> tuple:
    import pyarrow as pa
    import pyarrow.ipc as ipc

    handle = open_seekable()
    try:
        reader = ipc.open_file(handle)
        schema, count = reader.schema, reader.num_record_batches
    except Exception as exc:  # noqa: BLE001
        handle.close()
        raise Refused(["the file is not a readable Arrow or Feather file: it is not one, or it was cut short"]) from exc
    if not count:
        handle.close()
        raise Refused(["the Arrow file has no rows"])

    def rows():
        try:
            for i in range(count):
                checkpoint()
                batch = reader.get_batch(i)
                if batch.nbytes > cfg.parquet_row_group_bytes:
                    raise Refused([f"a batch in the Arrow file is {batch.nbytes // (1024 * 1024)} MB, over the {cfg.parquet_row_group_bytes // (1024 * 1024)} MB that is read at once"])
                yield from _slices(batch, cfg.batch_rows)
        finally:
            handle.close()

    return schema, rows()


class _CappedRead(io.RawIOBase):
    """A stream that refuses a single read larger than a limit: how a block that claims to be gigabytes is stopped before anything is allocated for it."""

    def __init__(self, inner: BinaryIO, limit: int):
        self.inner, self.limit = inner, limit

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self.inner.read(min(len(buffer), self.limit))
        buffer[: len(data)] = data
        return len(data)

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0 or size > self.limit:
            raise Refused(["the Avro file asks for a block larger than is read at once, so it is not a file this platform reads"])
        return self.inner.read(size)


_AVRO_TYPES = {"string": "string", "long": "int64", "int": "int32", "double": "float64", "float": "float32", "boolean": "bool"}


def _avro_type(field: dict):
    """The Arrow type an Avro field is read as, or None when it is nothing a single column holds (a record, an array, a map, an enum, fixed bytes)."""
    import pyarrow as pa

    t = field["type"]
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        t = non_null[0] if len(non_null) == 1 else None
    if isinstance(t, str):
        return getattr(pa, _AVRO_TYPES[t])() if t in _AVRO_TYPES else None
    if isinstance(t, dict):
        logical = t.get("logicalType")
        if logical == "decimal" and t.get("type") == "bytes":
            return pa.decimal128(int(t["precision"]), int(t.get("scale", 0)))
        if logical == "date":
            return pa.date32()
        if logical in ("timestamp-millis", "timestamp-micros"):
            return pa.timestamp("ms" if logical.endswith("millis") else "us", tz="UTC")
        if t.get("type") in _AVRO_TYPES and not logical:
            return getattr(pa, _AVRO_TYPES[t["type"]])()
    return None


def _avro(open_sequential, cfg, checkpoint) -> tuple:
    import fastavro
    import pyarrow as pa

    stream = _CappedRead(open_sequential(), AVRO_MAX_READ)
    try:
        reader = fastavro.reader(io.BufferedReader(stream, buffer_size=1 << 20))
        schema = reader.writer_schema
    except Refused:
        raise
    except Exception as exc:  # noqa: BLE001
        raise Refused(["the file is not a readable Avro file: it is not Avro, or it was cut short"]) from exc
    if not isinstance(schema, dict) or schema.get("type") != "record":
        raise Refused(["the Avro file does not hold one record per row"])
    arrow_fields, unsupported = [], []
    for field in schema["fields"]:
        found = _avro_type(field)
        if found is None:
            unsupported.append(field["name"])
        else:
            arrow_fields.append(pa.field(field["name"], found))
    if unsupported:
        raise Refused([f"the column {n} holds something that is not a single value (a record, a list, a map or an enum), which a table column does not" for n in unsupported])
    arrow = pa.schema(arrow_fields)

    def rows():
        columns = {f.name: [] for f in arrow}
        count = 0
        try:
            for record in reader:
                for f in arrow:
                    columns[f.name].append(record.get(f.name))
                count += 1
                if count >= cfg.batch_rows:
                    checkpoint()
                    yield pa.RecordBatch.from_arrays([pa.array(columns[f.name], f.type) for f in arrow], schema=arrow)
                    columns, count = {f.name: [] for f in arrow}, 0
        except Refused:
            raise
        except (OSError, ValueError, TypeError, EOFError, KeyError, UnicodeError, fastavro.read.SchemaResolutionError) as exc:
            raise Refused(["the Avro file could not be read to its end: it was cut short or damaged"]) from exc
        if count:
            yield pa.RecordBatch.from_arrays([pa.array(columns[f.name], f.type) for f in arrow], schema=arrow)

    return arrow, rows()


def _check_workbook(path: str) -> None:
    """A workbook is a zip. Refused before anything in it is read: one that is not a zip, one with a macro, encrypted, or that expands past what a
    spreadsheet reasonably does."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if not any(m.filename == "xl/workbook.xml" for m in members):
                raise Refused(["the file is not an Excel workbook (.xlsx): it has no workbook in it"])
            if any(m.filename.lower().endswith("vbaproject.bin") for m in members):
                raise Refused(["the workbook holds a macro, which this platform does not open. Save it as a workbook without macros (.xlsx)"])
            if any(m.flag_bits & 0x1 for m in members):
                raise Refused(["the workbook is encrypted, so it cannot be read"])
            total = sum(m.file_size for m in members)
            worst = max((m.file_size / max(1, m.compress_size) for m in members), default=0)
            if total > EXCEL_MAX_UNPACKED or worst > EXCEL_EXPANSION_RATIO:
                raise Refused([f"the workbook expands to {total // (1024 * 1024):,} MB, and one part of it to {int(worst)} times its size, which is more than a workbook "
                               "holds. It is not a file this platform reads"])
    except zipfile.BadZipFile as exc:
        raise Refused(["the file is not an Excel workbook (.xlsx): it is not a zip file, or it was cut short"]) from exc


def _cell(value, spec: dict):
    """A spreadsheet cell as the contract's kind, or (None, False) when it cannot be. Numbers in a sheet are all floating point, so a whole number
    or a decimal is accepted only when the digits written are exactly what the contract says."""
    import datetime

    k = kinds.kind(spec)
    if value is None or (isinstance(value, str) and value == "" and k != kinds.STRING):
        return None, True
    if k in (kinds.STRING, kinds.LIST, kinds.DICT):
        return (value, True) if isinstance(value, str) else (None, False)
    if k == kinds.BOOL:
        return (value, True) if isinstance(value, bool) else (None, False)
    if isinstance(value, bool):
        return None, False
    if k == kinds.INT:
        if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
            return int(value), abs(int(value)) < 10 ** kinds.MAX_INT_DIGITS
        return None, False
    if k == kinds.FLOAT:
        return (float(value), True) if isinstance(value, (int, float)) else (None, False)
    if k == kinds.DECIMAL:
        if not isinstance(value, (int, float, Decimal)):
            return None, False
        try:
            scale = kinds.precision_scale(spec)[1]
            made = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
            return (made, made == made.quantize(Decimal(1).scaleb(-scale)))
        except (InvalidOperation, ValueError):
            return None, False
    if k == kinds.DATE:
        if isinstance(value, datetime.datetime):
            return value.date(), value.time() == datetime.time(0)
        return (value, True) if isinstance(value, datetime.date) else (None, False)
    if k == kinds.TIMESTAMP:
        if isinstance(value, datetime.datetime):
            if kinds.has_zone(spec):
                return (value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)), True
            return value.replace(tzinfo=None), True
        return None, False
    return None, False


def _excel(open_sequential, cfg, checkpoint, fields: list[dict], key: list[str], found: Collector, arrow_schema, scratch: str | None, sheet: str | None) -> Iterator:
    import pyarrow as pa
    import pyarrow.compute as pc

    path, book, rows = None, None, None
    try:
        with tempfile.NamedTemporaryFile(prefix="munitas-xlsx-", suffix=".xlsx", dir=scratch, delete=False) as out:
            path = out.name
            source, written = open_sequential(), 0
            while True:
                chunk = source.read(1 << 22)
                if not chunk:
                    break
                written += len(chunk)
                if written > cfg.max_stream_bytes:
                    raise Refused(["the workbook is larger than is read"])
                out.write(chunk)
            source.close()
        _check_workbook(path)
        from openpyxl import load_workbook
        from openpyxl.xml import DEFUSEDXML

        if not DEFUSEDXML:
            raise Refused(["workbooks cannot be read on this platform until the safe XML reader is installed"])
        try:
            book = load_workbook(path, read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001
            raise Refused(["the file is not a readable Excel workbook (.xlsx)"]) from exc
        if sheet and sheet not in book.sheetnames:
            raise Refused([f"the workbook has no sheet called {sheet}. It has {', '.join(book.sheetnames[:10])}"])
        rows = book[sheet or book.sheetnames[0]].iter_rows(values_only=True)
        header = next(rows, None)
        if header is None:
            raise Refused(["the first sheet of the workbook is empty"])
        names = ["" if h is None else str(h) for h in header]
        wanted = {f["name"]: f for f in fields}
        layout = [f"the sheet names the column {n} more than once" for n in sorted({n for n in names if n and names.count(n) > 1})]
        layout += [f"the sheet has no column {n}, which the contract describes" for n in wanted if n not in names]
        layout += [f"the sheet has a column {n}, which the contract does not describe" for n in names if n and n not in wanted]
        if layout:
            raise Refused(layout)
        index = {n: i for i, n in enumerate(names) if n}
        columns, count, first = {f["name"]: [] for f in fields}, 0, 1
        found.unit = "sheet row"
        for number, row in enumerate(rows, start=1):
            if number > SHEET_ROW_LIMIT:
                raise Refused([f"the sheet has more than {SHEET_ROW_LIMIT:,} rows, which a sheet cannot hold"])
            if row is None or all(c is None for c in row):
                continue
            for spec in fields:
                value = row[index[spec["name"]]] if index[spec["name"]] < len(row) else None
                made, ok = _cell(value, spec)
                if not ok:
                    found.add(first + count, spec["name"], f"{spec['name']} is not {kinds.describe(spec)}", line=first + count + 1)
                    made = None
                columns[spec["name"]].append(made)
            count += 1
            if count >= cfg.batch_rows:
                checkpoint()
                yield first, pa.RecordBatch.from_arrays([pa.array(columns[f["name"]], kinds.arrow_type(f)) for f in fields], schema=arrow_schema)
                first += count
                columns, count = {f["name"]: [] for f in fields}, 0
        if count:
            yield first, pa.RecordBatch.from_arrays([pa.array(columns[f["name"]], kinds.arrow_type(f)) for f in fields], schema=arrow_schema)
    finally:
        if rows is not None:
            rows.close()                      # the sheet is read lazily, and its open stream keeps the file in use
        if book is not None:
            book.close()                      # a file that is still open cannot be removed on every system
        if path and os.path.exists(path):
            os.remove(path)


# ---- the one entry ------------------------------------------------------------------------------------------------------------------------


def batches(kind: str, *, open_seekable, open_sequential, fields: list[dict], key: list[str], cfg, arrow_schema, check_keys: bool, expected_rows: int | None,
            checkpoint: Callable[[], None] = lambda: None, scratch: str | None = None, sheet: str | None = None) -> Iterator:
    """Typed batches of a binary file. Raises Refused, with every problem found, when the file, its layout or any row is wrong; the repeated-key check runs
    at the end, so a caller that appends the batches must not commit until this has finished without raising."""
    found = Collector()
    keys = KeyCheck(key, expected_rows, scratch) if key and check_keys else None
    try:
        if kind == "excel":
            for first, batch in _excel(open_sequential, cfg, checkpoint, fields, key, found, arrow_schema, scratch, sheet):
                for i, name, why in _needed(batch, fields, key):
                    found.add(first + i, name, f"{name} is empty, and {why}", line=first + i + 1)
                if not found:
                    if keys is not None:
                        keys.add(batch, first)
                    yield batch
            if keys is not None and not found:
                keys.repeats(found)
            if found:
                raise found.render(_NextRow())
            return
        schema, source = {"parquet": _parquet, "orc": _orc, "arrow": _arrow}[kind](open_seekable, cfg, checkpoint) if kind != "avro" else _avro(open_sequential, cfg, checkpoint)
        problems = layout_problems(schema, fields)
        if problems:
            raise Refused(problems)
        row = 1
        for raw in source:
            typed = typed_batch(raw, fields, key, row, found, arrow_schema)
            if typed is not None:
                if keys is not None:
                    keys.add(typed, row)
                yield typed
            row += raw.num_rows
            if found.full:
                break
        if keys is not None and not found:
            keys.repeats(found)
        if found:
            raise found.render()
    finally:
        if keys is not None:
            keys.close()


def _needed(batch, fields: list[dict], key: list[str]):
    """The empty values in the columns that must have one, for a batch already in the contract's types: (row in the batch, column, why)."""
    import pyarrow.compute as pc

    out = []
    for spec in fields:
        name = spec["name"]
        if name in key or spec.get("required"):
            column = batch.column(batch.schema.get_field_index(name))
            why = "it is part of the key" if name in key else "every row needs a value"
            out += [(i, name, why) for i in pc.indices_nonzero(pc.is_null(column)).to_pylist()]
    return out
