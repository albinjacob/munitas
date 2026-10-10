"""Reading a table that arrives as text (CSV, TSV, JSON lines, a JSON list), checking every value, and handing on typed Arrow batches.

One library, so a ten-row file brought in through the console and a ten-gigabyte file read by a table worker are judged by the same rules and
give the same answer. Nothing here knows about a database, a person or an organisation: what it is given is a way to open the file, the
contract's fields and the options the file was declared with.

How a value is checked: every column is read as text, then tested against the strict pattern for its kind (see types.py) a whole column at a
time, so the check is fast and every bad value is found, with its row number, in one pass. Only text that passes is converted. A refusal lists
every problem it found (up to a limit) by row, line and column and by what is wrong, and never repeats a value from the file.

Rows are numbered from 1, not counting the header. For CSV and TSV the line is given as well, because a quoted line break makes a row take two
lines. A file that is wrong in its layout (a ragged row, a byte that is not text, a header that does not match, a line that is not JSON) is
reported for that first and alone: rows cannot be numbered, or their values judged, reliably past it.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from typing import BinaryIO, Callable, Iterator

from . import types as kinds

PROBLEM_LIMIT = 100                  # problems listed; the rest are counted
HEADER_PEEK = 1 << 20                # the header is read from the first megabyte
EXPANSION_RATIO = 400                # a compressed file may not expand to more than this many times its size (checked past 64 MB)
FORMATS = ("csv", "tsv", "jsonl", "json")
COMPRESSIONS = (None, "gzip", "zstd")
_BLOCK = 8 * 1024 * 1024
_NESTED = object()                   # a JSON value that is not a scalar


class Refused(Exception):
    """The rows cannot be a table. `problems` is the list of sentences (at most PROBLEM_LIMIT plus one that counts the rest)."""

    def __init__(self, problems: list[str], total: int | None = None):
        super().__init__("; ".join(problems[:3]))
        self.problems, self.total = problems, total if total is not None else len(problems)


@dataclass(frozen=True)
class Options:
    """How the file was declared by the person who brought it in. Anything not stated has the plain default."""

    delimiter: str = ","
    encoding: str = "utf-8"
    header: bool = True
    null_values: tuple = ("",)
    compression: str | None = None
    check_keys: bool = True
    sheet: str | None = None          # for a workbook: the sheet to read, the first when it is left out

    @classmethod
    def of(cls, raw: dict | None, kind: str) -> "Options":
        raw = dict(raw or {})
        known = set(cls.__dataclass_fields__)
        extra = sorted(set(raw) - known)
        if extra:
            raise ValueError(f"the option {extra[0]!r} is not one of {', '.join(sorted(known))}")
        if kind == "tsv":
            raw.setdefault("delimiter", "\t")
        options = cls(**{**raw, "null_values": tuple(raw.get("null_values", ("",)))})
        if len(options.delimiter) != 1 or options.delimiter in '"\r\n':
            raise ValueError("the delimiter must be one character that is not a quote or a line break")
        if options.compression not in COMPRESSIONS:
            raise ValueError("the compression must be gzip, zstd or none")
        try:
            "".encode(options.encoding)
        except LookupError as exc:
            raise ValueError(f"the encoding {options.encoding!r} is not one this platform reads") from exc
        return options

    def as_dict(self) -> dict:
        return {"delimiter": self.delimiter, "encoding": self.encoding, "header": self.header, "null_values": list(self.null_values),
                "compression": self.compression, "check_keys": self.check_keys, **({"sheet": self.sheet} if self.sheet else {})}


def format_of(name: str) -> tuple[str, str | None] | None:
    """(format, compression) from a file's name, or None when the ending is not one of the text formats."""
    lowered, compression = name.lower(), None
    for ending, how in ((".gz", "gzip"), (".zst", "zstd")):
        if lowered.endswith(ending):
            lowered, compression = lowered[: -len(ending)], how
    for ending, found in ((".csv", "csv"), (".tsv", "tsv"), (".jsonl", "jsonl"), (".ndjson", "jsonl"), (".json", "json")):
        if lowered.endswith(ending):
            return found, compression
    return None


# ---- problems ---------------------------------------------------------------------------------------------------------------------


class Collector:
    """The problems found so far. Keeps the first PROBLEM_LIMIT and counts the rest."""

    def __init__(self, limit: int = PROBLEM_LIMIT):
        self.limit, self.total, self.items = limit, 0, []
        self.unit = "line"          # what the second number means: a line of a text file, a row of a sheet

    @property
    def full(self) -> bool:
        return self.total >= self.limit * 4      # keep looking past the limit, so the count says more than "a lot", but not for ever

    def add(self, row: int | None, column: str | None, text: str, line: int | None = None) -> None:
        self.total += 1
        if len(self.items) < self.limit:
            self.items.append({"row": row, "line": line, "column": column, "text": text})

    def __bool__(self) -> bool:
        return self.total > 0

    def render(self, lines: dict[int, int] | None = None) -> Refused:
        lines = {} if lines is None else lines
        out = []
        for p in sorted(self.items, key=lambda p: (p["row"] is None, p["row"] or 0)):
            line = p["line"] or lines.get(p["row"])
            if p["row"] is None:
                where = ""
            elif line is not None and line != p["row"]:
                where = f"row {p['row']} ({self.unit} {line}): "
            else:
                where = f"row {p['row']}: "
            out.append(where + p["text"])
        if self.total > len(out):
            out.append(f"{self.total - len(out):,} more problems were found and are not listed")
        return Refused(out, self.total)


# ---- opening a source -------------------------------------------------------------------------------------------------------------


class _Counting(io.RawIOBase):
    """A stream that counts the bytes read from it, for the expansion check."""

    def __init__(self, inner):
        self.inner, self.count = inner, 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self.inner.read(len(buffer))
        buffer[: len(data)] = data
        self.count += len(data)
        return len(data)


class _Expanded(io.RawIOBase):
    """A decompressed stream that stops when it has expanded past what a compressed file may: an absolute cap, and a ratio."""

    def __init__(self, inner, source: _Counting, limit: int):
        self.inner, self.source, self.limit, self.count = inner, source, limit, 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self.inner.read(len(buffer))
        self.count += len(data)
        if self.count > self.limit:
            raise Refused([f"the file is compressed, and it expands to more than {self.limit // (1024 * 1024):,} MB, which is more than is read"])
        if self.count > 64 * 1024 * 1024 and self.count > EXPANSION_RATIO * max(1, self.source.count):
            raise Refused([f"the compressed file expands to more than {EXPANSION_RATIO} times its size, which is not a file this platform reads"])
        buffer[: len(data)] = data
        return len(data)


def open_text_source(open_raw: Callable[[], BinaryIO], compression: str | None, limit: int) -> BinaryIO:
    """The bytes of the file, decompressed if need be, behind the expansion limits."""
    raw = open_raw()
    if compression is None:
        return raw
    counted = _Counting(raw)
    if compression == "gzip":
        inner = gzip.GzipFile(fileobj=io.BufferedReader(counted), mode="rb")
    else:
        import zstandard
        inner = zstandard.ZstdDecompressor().stream_reader(io.BufferedReader(counted))
    return io.BufferedReader(_Expanded(inner, counted, limit), buffer_size=_BLOCK)


# ---- turning columns of text into a typed batch ---------------------------------------------------------------------------------


def _bad_value_text(spec: dict) -> str:
    return f"{spec['name']} is not {kinds.describe(spec)}"


def typed_batch(texts: dict, fields: list[dict], key: list[str], first_row: int, options: Options, found: Collector, arrow_schema):
    """One batch of text columns as a typed Arrow batch, or None when a value in it is wrong (each wrong value is added to `found`).

    `texts` maps a column's name to an Arrow string array. Rows are numbered from `first_row`.
    """
    import pyarrow as pa
    import pyarrow.compute as pc

    columns, spoiled = [], False
    nulls_wanted = pa.array(list(options.null_values), pa.string())
    for spec in fields:
        name = spec["name"]
        raw = texts[name]
        is_null = pc.or_(pc.is_null(raw), pc.is_in(raw, value_set=nulls_wanted))
        bad = None
        text = kinds.pattern(spec)
        if text is not None:
            matches = pc.fill_null(pc.match_substring_regex(raw, f"^(?:{text})$"), False)
            bad = pc.and_(pc.invert(matches), pc.invert(is_null))
        if (name in key or spec.get("required")) and pc.any(is_null).as_py():
            why = "it is part of the key" if name in key else "every row needs a value"
            for index in pc.indices_nonzero(is_null).to_pylist():
                found.add(first_row + index, name, f"{name} is empty, and {why}")
            spoiled = True
        if bad is not None and pc.any(bad).as_py():
            for index in pc.indices_nonzero(bad).to_pylist():
                found.add(first_row + index, name, _bad_value_text(spec))
            spoiled = True
        if spoiled and found.full:
            return None
        clean = pc.if_else(is_null if bad is None else pc.or_(is_null, bad), pa.scalar(None, pa.string()), raw)
        try:
            columns.append(kinds.convert(clean, spec))
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            columns.append(_convert_one_by_one(clean, spec, first_row, found))
            spoiled = True
    if spoiled:
        return None
    return pa.RecordBatch.from_arrays([c.combine_chunks() if isinstance(c, pa.ChunkedArray) else c for c in columns], schema=arrow_schema)


def _convert_one_by_one(clean, spec: dict, first_row: int, found: Collector):
    """A column that passed its pattern and still would not convert (the 30th of February): find the values, so each is reported."""
    import pyarrow as pa

    good = []
    for index, value in enumerate(clean.to_pylist()):
        if value is None:
            good.append(None)
            continue
        try:
            kinds.convert(pa.array([value], pa.string()), spec)
            good.append(value)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            found.add(first_row + index, spec["name"], _bad_value_text(spec))
            good.append(None)
    return kinds.convert(pa.array(good, pa.string()), spec)


# ---- the key must not repeat ---------------------------------------------------------------------------------------------------


class KeyCheck:
    """Finds a repeated key without holding every key in memory.

    Keys are written to a number of bucket files by their hash, as the batches go by, and each bucket is read back and checked on its own, so
    memory is one bucket whatever the table's size. A small table is one bucket held in memory and never touches a disk.
    """

    def __init__(self, columns: list[str], expected_rows: int | None, scratch: str | None = None, bucket_bytes: int = 192 * 1024 * 1024):
        self.columns = columns
        estimate = (expected_rows or 0) * 90
        self.buckets = 1
        while self.buckets < 256 and estimate / self.buckets > bucket_bytes:
            self.buckets *= 2
        self.scratch = scratch
        self.memory: list = []
        self.files: list = []
        self.writers: list = []
        self.dir: str | None = None

    def _open(self) -> None:
        import pyarrow as pa
        import pyarrow.ipc as ipc

        self.dir = tempfile.mkdtemp(prefix="munitas-keys-", dir=self.scratch)
        schema = pa.schema([("row", pa.int64()), ("key", pa.string())])
        for index in range(self.buckets):
            sink = open(os.path.join(self.dir, f"{index}.arrow"), "wb")
            self.files.append(sink)
            self.writers.append(ipc.new_stream(sink, schema))

    def add(self, batch, first_row: int) -> None:
        import pyarrow as pa
        import pyarrow.compute as pc

        parts = [pc.cast(batch.column(batch.schema.get_field_index(c)), pa.string()) for c in self.columns]
        keys = parts[0] if len(parts) == 1 else pc.binary_join_element_wise(*parts, "\x1f")
        table = pa.table({"row": pa.array(range(first_row, first_row + len(keys)), pa.int64()), "key": keys})
        if self.buckets == 1:
            self.memory.append(table)
            return
        if not self.writers:
            self._open()
        where = pa.array([hash(k) % self.buckets for k in keys.to_pylist()], pa.int64())
        for index in range(self.buckets):
            chosen = pc.indices_nonzero(pc.equal(where, index))
            if len(chosen):
                self.writers[index].write_table(table.take(chosen))

    def repeats(self, found: Collector) -> None:
        """Add a problem for every row whose key an earlier row already used."""
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.ipc as ipc

        names = ", ".join(self.columns)
        for index in range(self.buckets):
            if self.buckets == 1:
                if not self.memory:
                    continue
                table = pa.concat_tables(self.memory)
            else:
                if not self.writers:
                    return
                self.writers[index].close()
                self.files[index].close()
                with open(os.path.join(self.dir, f"{index}.arrow"), "rb") as handle:
                    table = ipc.open_stream(handle).read_all()
            counts = pc.value_counts(table["key"])
            repeated = counts.filter(pc.greater(counts.field("counts"), 1)).field("values")
            if not len(repeated):
                continue
            hits = table.filter(pc.is_in(table["key"], value_set=repeated)).sort_by([("row", "ascending")])
            first: dict[str, int] = {}
            for row, key in zip(hits["row"].to_pylist(), hits["key"].to_pylist()):
                if key in first:
                    found.add(row, None, f"the key ({names}) repeats row {first[key]}")
                else:
                    first[key] = row

    def close(self) -> None:
        for writer in self.writers:
            try:
                writer.close()
            except Exception:  # noqa: BLE001 - already closed
                pass
        for sink in self.files:
            sink.close()
        self.writers, self.files = [], []
        if self.dir:
            for name in os.listdir(self.dir):
                os.remove(os.path.join(self.dir, name))
            os.rmdir(self.dir)
            self.dir = None


# ---- reading CSV and TSV --------------------------------------------------------------------------------------------------------


def _header_names(source: BinaryIO, options: Options) -> list[str]:
    text = io.TextIOWrapper(io.BytesIO(source.read(HEADER_PEEK)), encoding=options.encoding, newline="", errors="replace")
    row = next(csv.reader(text, delimiter=options.delimiter), None)
    if row is None:
        raise Refused(["the file is empty"])
    if row and row[0].startswith("﻿"):
        row[0] = row[0][1:]
    return row


def _layout_problems(names: list[str], fields: list[dict]) -> list[str]:
    wanted = [f["name"] for f in fields]
    out = [f"the header names the column {n} more than once" for n in sorted({n for n in names if names.count(n) > 1})]
    out += [f"the header has no column {n}, which the contract describes" for n in wanted if n not in names]
    out += [f"the header has a column {n}, which the contract does not describe" for n in names if n not in wanted and n]
    return out


def _records(open_raw, options: Options, limit: int):
    """A plain reader over the file: (csv reader, the stream to close). Used only to find where a problem is, never to read the table."""
    source = open_text_source(open_raw, options.compression, limit)
    text = io.TextIOWrapper(source, encoding=options.encoding, newline="", errors="replace")
    reader = csv.reader(text, delimiter=options.delimiter)
    if options.header:
        next(reader, None)
    return reader, source


def _scan(open_raw, options: Options, limit: int, test: Callable[[list[str]], str | None], found: Collector) -> None:
    """Read the file one record at a time and add a problem, with its row and the line it starts on, for each record `test` says is wrong."""
    reader, source = _records(open_raw, options, limit)
    try:
        previous, row = reader.line_num, 0
        for record in reader:
            row += 1
            said = test(record)
            if said:
                found.add(row, None, said, line=previous + 1)
                if found.full:
                    break
            previous = reader.line_num
    finally:
        source.close()


def _lines_of(found: Collector, open_raw, options: Options, limit: int) -> dict[int, int]:
    """The line each problem's row starts on, found by one plain read as far as the last of them."""
    wanted = sorted({p["row"] for p in found.items if p["row"] is not None and p["line"] is None})
    lines: dict[int, int] = {}
    if not wanted:
        return lines
    reader, source = _records(open_raw, options, limit)
    try:
        previous, row, index = reader.line_num, 0, 0
        for _ in reader:
            row += 1
            if wanted[index] == row:
                lines[row] = previous + 1
                index += 1
                if index == len(wanted):
                    break
            previous = reader.line_num
    finally:
        source.close()
    return lines


def csv_batches(open_raw: Callable[[], BinaryIO], options: Options, fields: list[dict], key: list[str], limit: int, arrow_schema,
                expected_rows: int | None = None, checkpoint: Callable[[], None] = lambda: None, scratch: str | None = None) -> Iterator:
    """Typed batches of a CSV or TSV file.

    Raises Refused, with every problem found, when the layout or any value is wrong. Batches are yielded as they are made, so a caller that
    appends them must not commit until this has finished without raising: the repeated-key check runs at the end.
    """
    import pyarrow as pa
    import pyarrow.csv as pacsv

    if options.header:
        peek = open_text_source(open_raw, options.compression, limit)
        try:
            names = _header_names(peek, options)
        finally:
            peek.close()
        layout = _layout_problems(names, fields)
        if layout:
            raise Refused(layout)
        skip = 1
    else:
        names, skip = [f["name"] for f in fields], 0

    ragged = []

    def handler(row):
        ragged.append(row.number)
        return "skip"

    source = open_text_source(open_raw, options.compression, limit)
    found, row = Collector(), 1
    keys = KeyCheck(key, expected_rows, scratch) if key and options.check_keys else None
    try:
        try:
            reader = pacsv.open_csv(
                source,
                read_options=pacsv.ReadOptions(block_size=_BLOCK, column_names=names, skip_rows=skip, encoding=options.encoding),
                parse_options=pacsv.ParseOptions(delimiter=options.delimiter, quote_char='"', double_quote=True, escape_char=False,
                                                 newlines_in_values=True, invalid_row_handler=handler),
                convert_options=pacsv.ConvertOptions(column_types={n: pa.string() for n in names}, strings_can_be_null=False, null_values=[],
                                                     quoted_strings_can_be_null=False))
            while True:
                checkpoint()
                try:
                    batch = reader.read_next_batch()
                except StopIteration:
                    break
                if ragged:
                    break
                typed = typed_batch({n: batch.column(i) for i, n in enumerate(names)}, fields, key, row, options, found, arrow_schema)
                if typed is not None:
                    if keys is not None:
                        keys.add(typed, row)
                    yield typed
                row += batch.num_rows
                if found.full:
                    break
        except (pa.ArrowInvalid, UnicodeError):
            raise _unreadable(open_raw, options, limit) from None
        if ragged:
            problems = Collector()
            width = len(names)
            _scan(open_raw, options, limit, lambda r: f"it has {len(r)} values and the header has {width}" if len(r) != width else None, problems)
            raise problems.render()
        if keys is not None and not found:
            keys.repeats(found)
        if found:
            raise found.render(_lines_of(found, open_raw, options, limit))
    finally:
        source.close()
        if keys is not None:
            keys.close()


def _unreadable(open_raw, options: Options, limit: int) -> Refused:
    """The file is not text in its declared encoding. Find the rows that hold bytes that are not."""
    found = Collector()
    _scan(open_raw, options, limit, lambda r: (f"it holds a byte that is not text in the encoding {options.encoding}"
                                               if any("�" in value for value in r) else None), found)
    if not found:
        found.add(None, None, f"the file could not be read as {options.encoding} text")
    return found.render()


# ---- reading JSON lines and a JSON list ---------------------------------------------------------------------------------------


def _json_fits(value, spec: dict) -> bool:
    """Whether a JSON value is of the JSON type its column's kind is written as: a number for a number, true or false for a boolean, a string
    for text, a date or a time. A number is never read as text and text is never read as a number, because the contract is what the table promises."""
    k = kinds.kind(spec)
    if k in (kinds.INT, kinds.FLOAT, kinds.DECIMAL):
        return isinstance(value, (int, Decimal)) and not isinstance(value, bool)
    if k == kinds.BOOL:
        return isinstance(value, bool)
    return isinstance(value, str)


def _json_word(value) -> str:
    if isinstance(value, bool):
        return "true or false"
    return "a number" if isinstance(value, (int, float, Decimal)) else "text"


def _json_text(value):
    """A JSON value as the text the pattern for its column's kind is checked against, or `_NESTED` for a value that is not a scalar."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return _NESTED


def json_batches(open_raw: Callable[[], BinaryIO], shape: str, options: Options, fields: list[dict], key: list[str], limit: int, row_bytes: int,
                 batch_rows: int, arrow_schema, expected_rows: int | None = None, checkpoint: Callable[[], None] = lambda: None,
                 scratch: str | None = None) -> Iterator:
    """Typed batches of JSON lines (shape "jsonl") or of one JSON list ("json"). Same contract as csv_batches.

    A record that is not a JSON object, a line that is not JSON, a column the contract does not have and a value that is not a scalar are
    layout problems: they are reported alone, and values are not judged past the first of them.
    """
    import pyarrow as pa

    names = [f["name"] for f in fields]
    layout, found = Collector(), Collector()
    keys = KeyCheck(key, expected_rows, scratch) if key and options.check_keys else None
    columns: dict[str, list] = {n: [] for n in names}
    state = {"first": 1, "row": 0}

    def parse(raw):
        return json.loads(raw, parse_float=Decimal)

    def flush():
        count = len(columns[names[0]])
        typed = None
        if count and not layout:
            typed = typed_batch({n: pa.array(columns[n], pa.string()) for n in names}, fields, key, state["first"], options, found, arrow_schema)
            if typed is not None and keys is not None:
                keys.add(typed, state["first"])
        state["first"] += count
        for n in names:
            columns[n] = []
        return typed

    def take(record, line: int | None) -> None:
        state["row"] += 1
        row = state["row"]
        if not isinstance(record, dict):
            layout.add(row, None, "it is not a set of columns and values", line=line)
            return
        for extra in sorted(set(record) - set(names)):
            layout.add(row, extra, f"the column {extra} is not in the contract", line=line)
        values = {}
        for spec in fields:
            raw = record.get(spec["name"])
            if kinds.kind(spec) in kinds.JSON_KINDS and isinstance(raw, (dict, list)):
                values[spec["name"]] = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)     # a list or dictionary column holds JSON text
                continue
            value = _json_text(raw)
            if value is _NESTED:
                layout.add(row, spec["name"], f"{spec['name']} is a nested value, and a column holds one value", line=line)
                value = None
            elif raw is not None and not _json_fits(raw, spec):
                layout.add(row, spec["name"], f"{spec['name']} is {_json_word(raw)}, and the contract says {kinds.describe(spec)}", line=line)
                value = None
            values[spec["name"]] = value
        for n in names:
            columns[n].append(values[n])

    try:
        if shape == "json":
            source = open_text_source(open_raw, options.compression, limit)
            raw = source.read()
            source.close()
            try:
                rows = parse(raw.decode(options.encoding))
            except (ValueError, UnicodeError):
                raise Refused(["the file is not JSON, or is not text in its declared encoding"]) from None
            if not isinstance(rows, list):
                raise Refused(["the rows file must be one list of rows, each row an object of column names and values. For one row per line use a .jsonl file"])
            for record in rows:
                take(record, None)
                if len(columns[names[0]]) >= batch_rows:
                    ready = flush()
                    if ready is not None:
                        yield ready
                if layout.full or found.full:
                    break
        else:
            source = open_text_source(open_raw, options.compression, limit)
            try:
                for line_number, line in enumerate(source, start=1):
                    checkpoint()
                    if len(line) > row_bytes:
                        raise Refused([f"line {line_number} is longer than {row_bytes // (1024 * 1024)} MB"])
                    if not line.strip():
                        continue
                    try:
                        record = parse(line.decode(options.encoding))
                    except UnicodeError:
                        state["row"] += 1
                        layout.add(state["row"], None, f"it holds a byte that is not text in the encoding {options.encoding}", line=line_number)
                        continue
                    except ValueError:
                        state["row"] += 1
                        layout.add(state["row"], None, "it is not valid JSON", line=line_number)
                        continue
                    take(record, line_number)
                    if len(columns[names[0]]) >= batch_rows:
                        ready = flush()
                        if ready is not None:
                            yield ready
                    if layout.full or found.full:
                        break
            finally:
                source.close()
        ready = flush()
        if ready is not None:
            yield ready
        if layout:
            raise layout.render()
        if keys is not None and not found:
            keys.repeats(found)
        if found:
            raise found.render()
    finally:
        if keys is not None:
            keys.close()
