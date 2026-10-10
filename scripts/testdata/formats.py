"""One writer for each way of writing a table to a file.

A writer takes the table one batch at a time (so a table of fifty million rows never has to be held whole), and each variant is one writer:
the same rows as comma, semicolon, tab or pipe separated text, with a byte-order mark or in Windows-1252, compressed, as JSON lines, as one
JSON list, as Parquet, Arrow, Feather, ORC, Avro or an Excel workbook. Everything that can be made byte for byte the same on a second run is:
no file carries a time, a random marker or a machine name.
"""
from __future__ import annotations

import codecs
import gzip
import io
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.ipc as ipc
import pyarrow.orc as orc
import pyarrow.parquet as pq

EXCEL_MAX_ROWS = 1_048_575          # a sheet holds 1,048,576 rows, and the first is the header
JSON_LIST_MAX_ROWS = 100_000        # a JSON list is read whole, so the platform keeps it small
AVRO_SYNC = b"MUNITAS-TESTDATA"     # sixteen bytes, so two runs write the same file


# ---- turning a batch into text ------------------------------------------------------------------------------------------------------------


def _temporal_text(column: pa.ChunkedArray, numpy_type: str, unit: str, integer: pa.DataType) -> pa.Array:
    """A date or time column as ISO text (2026-03-31, 2026-03-31T09:30:00, with .123 for milliseconds), made by numpy in one pass: Arrow's own
    formatting does the same thing about thirty times slower."""
    whole = column.combine_chunks() if isinstance(column, pa.ChunkedArray) else column
    values = whole.cast(integer).fill_null(0).to_numpy(zero_copy_only=False).astype(numpy_type)
    text = np.datetime_as_string(values, unit=unit)
    return pa.array(text, pa.string(), mask=whole.is_null().to_numpy(zero_copy_only=False))


def stringify(table: pa.Table) -> pa.Table:
    """Every column as text, the way a text file writes it: dates as 2026-03-31, timestamps as 2026-03-31T09:30:00 (with .123 for milliseconds)."""
    columns = []
    for field_, column in zip(table.schema, table.columns):
        t = field_.type
        if pa.types.is_date(t):
            column = _temporal_text(column, "datetime64[D]", "D", pa.int32())
        elif pa.types.is_timestamp(t):
            column = _temporal_text(column, f"datetime64[{t.unit}]", t.unit, pa.int64())
        elif not pa.types.is_string(t):
            column = pc.cast(column, pa.string())
        columns.append(column)
    return pa.table(columns, names=table.schema.names)


def _escape(column: pa.ChunkedArray) -> pa.ChunkedArray:
    for old, new in (("\\", "\\\\"), ('"', '\\"'), ("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t")):
        column = pc.replace_substring(column, old, new)
    return column


def json_rows(table: pa.Table, text: pa.Table | None = None) -> pa.Array:
    """One JSON object per row, as text. Strings are quoted and escaped; numbers, decimals and nulls are not quoted. `text` is the table
    already turned into text, when the caller has it."""
    parts = []
    text = text if text is not None else stringify(table)
    for field_, column in zip(table.schema, text.columns):
        t = field_.type
        quoted = pa.types.is_string(t) or pa.types.is_date(t) or pa.types.is_timestamp(t)
        if quoted:
            fragment = pc.binary_join_element_wise(pa.scalar('"'), _escape(column), pa.scalar('"'), "")
        else:
            fragment = column
        fragment = pc.fill_null(fragment, "null")
        parts.append(pc.binary_join_element_wise(pa.scalar(f'"{field_.name}":'), fragment, ""))
    body = pc.binary_join_element_wise(*parts, ",")
    return pc.binary_join_element_wise(pa.scalar("{"), body, pa.scalar("}"), "").combine_chunks()


# ---- sinks: a file, optionally compressed, optionally re-encoded -------------------------------------------------------------------------


class Recoder(io.RawIOBase):
    """Passes UTF-8 text through as another encoding. A multi-byte character split between two writes is held until its end arrives."""

    def __init__(self, inner, encoding: str):
        self.inner, self.encoding = inner, encoding
        self.decoder = codecs.getincrementaldecoder("utf-8")()

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        self.inner.write(self.decoder.decode(bytes(data)).encode(self.encoding))   # strict: a character the encoding lacks is an error
        return len(data)

    def close(self) -> None:
        if not self.closed:
            self.inner.write(self.decoder.decode(b"", final=True).encode(self.encoding))
        super().close()


def open_sink(path: Path, compression: str | None):
    """The file, wrapped in its compression. Returns (outer, [everything to close, innermost last])."""
    raw = open(path, "wb")
    if compression == "gzip":
        outer = gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=6)   # mtime 0: no clock in the header
        return outer, [outer, raw]
    if compression == "zstd":
        import zstandard
        outer = zstandard.ZstdCompressor(level=3).stream_writer(raw, closefd=False)
        return outer, [outer, raw]
    return raw, [raw]


# ---- the variants -------------------------------------------------------------------------------------------------------------------------


@dataclass
class Writer:
    path: Path
    spec: object
    options: dict
    max_rows: int | None = None
    written: int = 0

    def write(self, table: pa.Table, text: pa.Table | None = None, lines: pa.Array | None = None) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


def _quote_when_needed(column: pa.ChunkedArray, delimiter: str) -> pa.ChunkedArray:
    """A value in quotes only if it holds the separator, a quote or a line break, with any quote inside it doubled. Nothing else is quoted."""
    needs = pc.or_(pc.or_(pc.match_substring(column, delimiter), pc.match_substring(column, '"')),
                   pc.or_(pc.match_substring(column, "\n"), pc.match_substring(column, "\r")))
    wrapped = pc.binary_join_element_wise(pa.scalar('"'), pc.replace_substring(column, '"', '""'), pa.scalar('"'), "")
    return pc.fill_null(pc.if_else(needs, wrapped, column), "")


class CsvWriter(Writer):
    """Text separated by a character. Two styles: `minimal` quotes a value only when it must be (what most exports do), and `strings` quotes every
    text value (what Arrow's own writer does, and some exporters)."""

    def __init__(self, path, spec, options):
        super().__init__(path, spec, options)
        self.outer, self.closers = open_sink(path, options.get("compression"))
        self.sink = self.outer
        if options["encoding"] != "utf-8":
            self.sink = Recoder(self.sink, options["encoding"])
            self.closers.insert(0, self.sink)
        if options.get("bom"):
            self.sink.write(codecs.BOM_UTF8)
        self.writer = None
        if options.get("style") == "strings":
            self.writer = pacsv.CSVWriter(self.sink, spec.schema_as_text(), write_options=pacsv.WriteOptions(
                include_header=True, delimiter=options["delimiter"], quoting_style="needed"))
        else:
            names = pa.array([c.name for c in spec.cols], pa.string())
            header = _quote_when_needed(pa.chunked_array([names]), options["delimiter"]).to_pylist()
            self.sink.write((options["delimiter"].join(header) + "\n").encode("utf-8"))

    def write(self, table, text=None, lines=None):
        if self.writer is not None:
            self.writer.write_table(text)
        else:
            delimiter = self.options["delimiter"]
            parts = [_quote_when_needed(text[name], delimiter) for name in text.schema.names]
            rows = pc.binary_join_element_wise(*parts, delimiter)
            self.sink.write(("\n".join(rows.to_pylist()) + "\n").encode("utf-8"))
        self.written += table.num_rows

    def close(self):
        if self.writer is not None:
            self.writer.close()
        for stream in self.closers:
            stream.close()


class JsonLinesWriter(Writer):
    def __init__(self, path, spec, options):
        super().__init__(path, spec, options)
        self.outer, self.closers = open_sink(path, options.get("compression"))

    def write(self, table, text=None, lines=None):
        self.outer.write(("\n".join(lines.to_pylist()) + "\n").encode("utf-8"))
        self.written += table.num_rows

    def close(self):
        for stream in self.closers:
            stream.close()


class JsonListWriter(Writer):
    def __init__(self, path, spec, options):
        super().__init__(path, spec, options, max_rows=JSON_LIST_MAX_ROWS)
        self.handle = open(path, "wb")
        self.handle.write(b"[\n")

    def write(self, table, text=None, lines=None):
        body = ",\n".join(lines.to_pylist())
        self.handle.write((("," if self.written else "") + ("\n" if self.written else "") + body).encode("utf-8"))
        self.written += table.num_rows

    def close(self):
        self.handle.write(b"\n]\n")
        self.handle.close()


class ParquetWriter(Writer):
    def __init__(self, path, spec, options):
        super().__init__(path, spec, options)
        self.writer = pq.ParquetWriter(path, spec.schema(), compression="zstd")

    def write(self, table, text=None, lines=None):
        self.writer.write_table(table, row_group_size=self.options["row_group_rows"])
        self.written += table.num_rows

    def close(self):
        self.writer.close()


class ArrowWriter(Writer):
    def __init__(self, path, spec, options):
        super().__init__(path, spec, options)
        self.sink = pa.OSFile(str(path), "wb")
        compression = options.get("compression")
        self.writer = ipc.new_file(self.sink, spec.schema(), options=ipc.IpcWriteOptions(compression=compression))

    def write(self, table, text=None, lines=None):
        self.writer.write_table(table)
        self.written += table.num_rows

    def close(self):
        self.writer.close()
        self.sink.close()


class OrcWriter(Writer):
    def __init__(self, path, spec, options):
        super().__init__(path, spec, options)
        # ORC keeps times against a time zone, and Windows has no zone database for it to read. The tzdata package carries one.
        if not os.environ.get("TZDIR"):
            try:
                import tzdata
                os.environ["TZDIR"] = os.path.join(os.path.dirname(tzdata.__file__), "zoneinfo")
            except ImportError:
                pass
        self.writer = orc.ORCWriter(str(path), compression="zstd")

    def write(self, table, text=None, lines=None):
        self.writer.write(table)
        self.written += table.num_rows

    def close(self):
        self.writer.close()


def _avro_schema(spec) -> dict:
    fields = []
    for column in spec.cols:
        t = column.type
        if pa.types.is_string(t):
            kind = "string"
        elif pa.types.is_integer(t):
            kind = "long"
        elif pa.types.is_floating(t):
            kind = "double"
        elif pa.types.is_boolean(t):
            kind = "boolean"
        elif pa.types.is_decimal(t):
            kind = {"type": "bytes", "logicalType": "decimal", "precision": t.precision, "scale": t.scale}
        elif pa.types.is_date(t):
            kind = {"type": "int", "logicalType": "date"}
        else:
            kind = {"type": "long", "logicalType": "timestamp-millis"}
        fields.append({"name": column.name, "type": ["null", kind] if column.nullable and not column.key else kind})
    return {"type": "record", "name": spec.name, "fields": fields}


class AvroWriter(Writer):
    def __init__(self, path, spec, options):
        import fastavro
        from fastavro.write import Writer as FastWriter
        super().__init__(path, spec, options)
        self.handle = open(path, "wb")
        self.writer = FastWriter(self.handle, fastavro.parse_schema(_avro_schema(spec)), codec="deflate", sync_marker=AVRO_SYNC)

    def write(self, table, text=None, lines=None):
        columns = {}
        for field_, column in zip(table.schema, table.columns):
            t = field_.type
            if pa.types.is_date(t):
                column = pc.cast(column, pa.int32())
            elif pa.types.is_timestamp(t):
                column = pc.cast(column, pa.int64())
                if t.unit == "s":
                    column = pc.multiply(column, 1000)
            columns[field_.name] = column.to_pylist()
        names = list(columns)
        for row in zip(*columns.values()):
            self.writer.write(dict(zip(names, row)))
        self.written += table.num_rows

    def close(self):
        self.writer.flush()
        self.handle.close()


class ExcelWriter(Writer):
    def __init__(self, path, spec, options):
        import xlsxwriter
        super().__init__(path, spec, options, max_rows=EXCEL_MAX_ROWS)
        self.book = xlsxwriter.Workbook(str(path), {"constant_memory": True, "strings_to_numbers": False, "strings_to_formulas": False,
                                                    "strings_to_urls": False})
        self.book.set_properties({"title": spec.name, "author": "scripts/testdata", "created": datetime(2026, 10, 9, 12, 0, 0)})
        self.sheet = self.book.add_worksheet(spec.name[:31])
        self.formats = {"date": self.book.add_format({"num_format": "yyyy-mm-dd"}), "stamp": self.book.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"}),
                        "stamp_ms": self.book.add_format({"num_format": "yyyy-mm-dd hh:mm:ss.000"})}
        for number, column in enumerate(spec.cols):
            self.sheet.write_string(0, number, column.name)
        self.row = 1

    def write(self, table, text=None, lines=None):
        import decimal
        kinds = []
        for field_ in table.schema:
            t = field_.type
            kinds.append("date" if pa.types.is_date(t) else ("stamp_ms" if pa.types.is_timestamp(t) and t.unit == "ms" else "stamp" if pa.types.is_timestamp(t) else "plain"))
        columns = [column.to_pylist() for column in table.columns]
        for values in zip(*columns):
            for number, (value, kind) in enumerate(zip(values, kinds)):
                if value is None:
                    continue
                if kind != "plain":
                    self.sheet.write_datetime(self.row, number, value, self.formats[kind])
                elif isinstance(value, decimal.Decimal):
                    self.sheet.write_number(self.row, number, float(value))
                elif isinstance(value, str):
                    self.sheet.write_string(self.row, number, value)
                else:
                    self.sheet.write_number(self.row, number, value)
            self.row += 1
        self.written += table.num_rows

    def close(self):
        self.book.close()


@dataclass(frozen=True)
class Variant:
    name: str
    extension: str                    # what the file is called, which is also how the platform tells formats apart
    kind: str                         # which writer
    options: dict = field(default_factory=dict)
    note: str = ""

    def applies(self, rows: int) -> str | None:
        """Why this variant is not made for a table this long, or None when it is."""
        if self.kind == "excel" and rows > EXCEL_MAX_ROWS:
            return f"a sheet holds at most {EXCEL_MAX_ROWS:,} rows"
        if self.kind == "json" and rows > JSON_LIST_MAX_ROWS:
            return f"a JSON list is read whole, so it stays under {JSON_LIST_MAX_ROWS:,} rows"
        return None

    def open(self, path: Path, spec) -> Writer:
        cls = {"csv": CsvWriter, "jsonl": JsonLinesWriter, "json": JsonListWriter, "parquet": ParquetWriter, "arrow": ArrowWriter,
               "orc": OrcWriter, "avro": AvroWriter, "excel": ExcelWriter}[self.kind]
        return cls(path, spec, dict(self.options))


def _csv(name, ext, delimiter=",", **more):
    return Variant(name, ext, "csv", {"delimiter": delimiter, "encoding": "utf-8", "header": True, "bom": False, "compression": None, "style": "minimal", **more})


VARIANTS: dict[str, Variant] = {v.name: v for v in (
    _csv("csv", "csv"),
    _csv("csv-quoted", "csv", style="strings"),
    _csv("csv-bom", "csv", bom=True),
    _csv("csv-cp1252", "csv", encoding="cp1252"),
    _csv("csv-semicolon", "csv", ";"),
    _csv("csv-pipe", "csv", "|"),
    _csv("tsv", "tsv", "\t"),
    _csv("csv-gzip", "csv.gz", compression="gzip"),
    _csv("csv-zstd", "csv.zst", compression="zstd"),
    Variant("jsonl", "jsonl", "jsonl", {"compression": None}),
    Variant("jsonl-gzip", "jsonl.gz", "jsonl", {"compression": "gzip"}),
    Variant("jsonl-zstd", "jsonl.zst", "jsonl", {"compression": "zstd"}),
    Variant("json", "json", "json"),
    Variant("parquet", "parquet", "parquet", {"row_group_rows": 100_000}),
    Variant("parquet-small-groups", "parquet", "parquet", {"row_group_rows": 10_000}),
    Variant("arrow", "arrow", "arrow", {"compression": None}),
    Variant("feather", "feather", "arrow", {"compression": "zstd"}),
    Variant("orc", "orc", "orc"),
    Variant("avro", "avro", "avro"),
    Variant("xlsx", "xlsx", "excel"),
)}

TEXT_KINDS = ("csv",)
