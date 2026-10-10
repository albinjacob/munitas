"""Read a generated file back with a reader that is not the one that wrote it, into a table of the types the table is meant to have.

The checks compare what comes out of here with the truth file, so the two are measurements of the same data made by different code:
text files are read by Arrow's own CSV reader or by Python's JSON reader, Parquet, Arrow, ORC by Arrow, Avro by fastavro and Excel by
openpyxl. Whatever the file holds is read as text first and converted to the table's types in one place, so a value that does not convert
is an error here and not a quiet change.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.ipc as ipc
import pyarrow.orc as orc
import pyarrow.parquet as pq

from .formats import Variant
from .tables import TableSpec


def _typed(spec: TableSpec, columns: dict[str, pa.Array]) -> pa.Table:
    """Convert columns of text (or of the right type already) to the table's types."""
    out = []
    for column in spec.cols:
        data = columns[column.name]
        if data.type != column.type:
            if pa.types.is_timestamp(column.type) and pa.types.is_string(data.type):
                data = pc.cast(data, pa.timestamp("ms")).cast(column.type)
            else:
                data = pc.cast(data, column.type)
        out.append(data)
    return pa.table(out, schema=spec.schema())


def _csv(path: Path, variant: Variant, spec: TableSpec) -> pa.Table:
    options = variant.options
    parse = pacsv.ParseOptions(delimiter=options["delimiter"])
    read = pacsv.ReadOptions(encoding=options["encoding"] if options["encoding"] != "utf-8" else "utf8", block_size=1 << 24)
    convert = pacsv.ConvertOptions(column_types={c.name: pa.string() for c in spec.cols}, strings_can_be_null=True, quoted_strings_can_be_null=False)
    table = pacsv.read_csv(str(path), read_options=read, parse_options=parse, convert_options=convert)
    return _typed(spec, {c.name: table[c.name].combine_chunks() for c in spec.cols})


def _json_values(raw) -> dict:
    return raw


def _jsonl(path: Path, variant: Variant, spec: TableSpec) -> pa.Table:
    import gzip
    import io
    if variant.options.get("compression") == "gzip":
        stream = io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8")
    elif variant.options.get("compression") == "zstd":
        import zstandard
        stream = io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(open(path, "rb")), encoding="utf-8")
    else:
        stream = open(path, encoding="utf-8")
    columns = {c.name: [] for c in spec.cols}
    with stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line, parse_float=str, parse_int=str)
            for name in columns:
                columns[name].append(row.get(name))
    return _typed(spec, {k: pa.array(v, pa.string()) for k, v in columns.items()})


def _json_list(path: Path, variant: Variant, spec: TableSpec) -> pa.Table:
    rows = json.loads(path.read_text(encoding="utf-8"), parse_float=str, parse_int=str)
    return _typed(spec, {c.name: pa.array([r.get(c.name) for r in rows], pa.string()) for c in spec.cols})


def _parquet(path: Path, variant: Variant, spec: TableSpec) -> pa.Table:
    return pq.read_table(path).cast(spec.schema())


def _arrow(path: Path, variant: Variant, spec: TableSpec) -> pa.Table:
    with pa.memory_map(str(path)) as source:
        return ipc.open_file(source).read_all().cast(spec.schema())


def _orc(path: Path, variant: Variant, spec: TableSpec) -> pa.Table:
    return orc.read_table(str(path)).cast(spec.schema())


def _avro(path: Path, variant: Variant, spec: TableSpec) -> pa.Table:
    import fastavro
    columns = {c.name: [] for c in spec.cols}
    with open(path, "rb") as handle:
        for record in fastavro.reader(handle):
            for name in columns:
                columns[name].append(record[name])
    arrays = {}
    for column in spec.cols:
        values = columns[column.name]
        if pa.types.is_timestamp(column.type):
            values = [None if v is None else v.replace(tzinfo=None) for v in values]
            arrays[column.name] = pa.array(values, pa.timestamp("ms")).cast(column.type)
        else:
            arrays[column.name] = pa.array(values, column.type)
    return pa.table([arrays[c.name] for c in spec.cols], schema=spec.schema())


def _excel(path: Path, variant: Variant, spec: TableSpec) -> pa.Table:
    import datetime
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True, data_only=True)
    sheet = book[book.sheetnames[0]]
    rows = sheet.iter_rows(values_only=True)
    header = next(rows)
    assert list(header) == [c.name for c in spec.cols], f"the header is {header}"
    columns = {c.name: [] for c in spec.cols}
    for row in rows:
        for column, value in zip(spec.cols, row):
            columns[column.name].append(value)
    arrays = {}
    for column in spec.cols:
        t, values = column.type, columns[column.name]
        if pa.types.is_date(t):
            values = [None if v is None else v.date() if isinstance(v, datetime.datetime) else v for v in values]
        elif pa.types.is_decimal(t):
            values = [None if v is None else Decimal(repr(float(v))).quantize(Decimal(1).scaleb(-t.scale)) for v in values]
        elif pa.types.is_timestamp(t):
            values = [None if v is None else v.replace(microsecond=round(v.microsecond / 1000) * 1000 % 1000000) for v in values]
        elif pa.types.is_integer(t):
            values = [None if v is None else int(v) for v in values]
        arrays[column.name] = pa.array(values, t)
    book.close()
    return pa.table([arrays[c.name] for c in spec.cols], schema=spec.schema())


READERS = {"csv": _csv, "jsonl": _jsonl, "json": _json_list, "parquet": _parquet, "arrow": _arrow, "orc": _orc, "avro": _avro, "excel": _excel}


def read_back(path: Path, variant: Variant, spec: TableSpec) -> pa.Table:
    return READERS[variant.kind](path, variant, spec)
