"""Faulty copies of a table in the binary formats, and hostile files that are not tables at all, with an answers file.

The text formats' faults are in faults.py. A binary format is typed, so what can be wrong with it is different: a key that repeats, a missing value in a
key, a decimal with more places than the contract allows, a column that is not there, one that should not be, one of the wrong type, and, in a workbook, a
cell of text where the contract says a number. The hostile files are what a person might send by accident or on purpose: a file cut short, a file that is
not the format its ending says, a workbook that expands to hundreds of times its size, a workbook with a macro, an Avro file whose block claims to be gigabytes.

Rows are numbered from 1. For a workbook the sheet's own row is given as the `line`, because the first row of the sheet is the header.
"""
from __future__ import annotations

import dataclasses
import io
import json
import os
import random
import struct
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from . import formats
from .faults import positions
from .tables import Col, TableSpec

BINARY = ("parquet", "orc", "arrow", "avro", "xlsx")
EXTENSION = {"parquet": "parquet", "orc": "orc", "arrow": "arrow", "avro": "avro", "xlsx": "xlsx"}


def _variant(name: str) -> str:
    return {"parquet": "parquet", "orc": "orc", "arrow": "arrow", "avro": "avro", "xlsx": "xlsx"}[name]


def _write(spec: TableSpec, table: pa.Table, variant: str, path: Path) -> None:
    """`spec` here is the definition of the file as written, which is not always the contract: a faulty file is written under the definition it breaks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = formats.VARIANTS[_variant(variant)].open(path, spec)
    writer.write(table.cast(spec.schema()))
    writer.close()


def _with_cols(spec: TableSpec, cols: tuple) -> TableSpec:
    return dataclasses.replace(spec, cols=cols)


def _replace_column(table: pa.Table, name: str, values: list, type_: pa.DataType) -> pa.Table:
    return table.set_column(table.schema.get_field_index(name), pa.field(name, type_), pa.array(values, type_))


def make(spec: TableSpec, folder: Path, seed: int) -> list[dict]:
    """The binary faulty copies for a table whose clean Parquet file is in `folder`, and the answers. Written beside the text ones, in `faults/`."""
    clean_path = folder / "parquet" / f"{spec.name}.parquet"
    if not clean_path.exists():
        return []
    clean = pq.read_table(clean_path).cast(spec.schema())
    out = folder / "faults"
    keys = spec.key_columns()
    decimals = [c for c in spec.cols if pa.types.is_decimal(c.type)]
    ints = [c for c in spec.cols if pa.types.is_integer(c.type) and not c.key]
    answers = []

    def add(variant: str, kind: str, level: str, planted: list[dict], file_spec: TableSpec, table: pa.Table) -> None:
        target = out / f"{variant}-{kind}" / f"{spec.name}.{EXTENSION[variant]}"
        _write(file_spec, table, variant, target)
        answers.append({"id": f"{variant}-{kind}", "format": variant, "file": str(target.relative_to(folder)).replace("\\", "/"), "kind": kind, "level": level,
                        "planted": planted})

    for variant in BINARY:
        line = (lambda n: n + 1) if variant == "xlsx" else None
        if keys:
            chosen = positions(clean.num_rows, f"{variant}-repeated-key", seed)
            if chosen:
                table, planted = clean, []
                for number in chosen:
                    source = 1 + (number * 7) % (number - 1)
                    for name in keys:
                        values = table[name].to_pylist()
                        values[number - 1] = values[source - 1]
                        table = _replace_column(table, name, values, clean.schema.field(name).type)
                    planted.append({"row": number, "column": ",".join(keys), "also_row": source, **({"line": line(number)} if line else {})})
                add(variant, "repeated-key", "row", planted, spec, table)
            chosen = positions(clean.num_rows, f"{variant}-empty-key", seed)
            if chosen:
                loose = _with_cols(spec, tuple(dataclasses.replace(c, key=0, nullable=True) if c.name == keys[0] else c for c in spec.cols))
                values = clean[keys[0]].to_pylist()
                for number in chosen:
                    values[number - 1] = None
                table = _replace_column(clean, keys[0], values, clean.schema.field(keys[0]).type)
                add(variant, "empty-key", "row", [{"row": n, "column": keys[0], **({"line": line(n)} if line else {})} for n in chosen], loose, table)
        if decimals and variant != "xlsx":
            c = decimals[0]
            chosen = positions(clean.num_rows, f"{variant}-too-many-decimals", seed)
            if chosen:
                wider = pa.decimal128(c.type.precision + 4, c.type.scale + 2)
                values = [None if v is None else v for v in clean[c.name].to_pylist()]
                from decimal import Decimal
                for number in chosen:
                    values[number - 1] = (values[number - 1] or Decimal(0)) + Decimal(5).scaleb(-(c.type.scale + 1))      # half a unit of the last place the contract allows
                loose = _with_cols(spec, tuple(dataclasses.replace(x, type=wider) if x.name == c.name else x for x in spec.cols))
                table = _replace_column(clean, c.name, values, wider)
                add(variant, "too-many-decimals", "row", [{"row": n, "column": c.name} for n in chosen], loose, table)
        if ints and variant != "xlsx":
            c = ints[0]
            as_text = _with_cols(spec, tuple(dataclasses.replace(x, type=pa.string()) if x.name == c.name else x for x in spec.cols))
            table = _replace_column(clean, c.name, [None if v is None else str(v) for v in clean[c.name].to_pylist()], pa.string())
            add(variant, "wrong-type", "file", [{"column": c.name}], as_text, table)
        gone = next((c for c in reversed(spec.cols) if not c.key and c.type == clean.schema.field(c.name).type), None)
        if gone is not None and len(spec.cols) > 1:
            fewer = _with_cols(spec, tuple(c for c in spec.cols if c.name != gone.name))
            add(variant, "missing-column", "file", [{"column": gone.name}], fewer, clean.drop_columns([gone.name]))
        more = _with_cols(spec, spec.cols + (Col("extra", pa.string()),))
        add(variant, "extra-column", "file", [{"column": "extra"}], more, clean.append_column(pa.field("extra", pa.string()), pa.array(["x"] * clean.num_rows)))

    # a workbook's cell of text where the contract says a number: a typed format cannot hold one, a sheet can
    if ints:
        c = ints[0]
        chosen = positions(clean.num_rows, "xlsx-text-in-number", seed)
        if chosen:
            import xlsxwriter

            target = out / "xlsx-text-in-number" / f"{spec.name}.xlsx"
            target.parent.mkdir(parents=True, exist_ok=True)
            book = xlsxwriter.Workbook(str(target), {"constant_memory": True, "strings_to_numbers": False})
            sheet = book.add_worksheet()
            for i, col in enumerate(spec.cols):
                sheet.write_string(0, i, col.name)
            fmt_date, fmt_stamp = book.add_format({"num_format": "yyyy-mm-dd"}), book.add_format({"num_format": "yyyy-mm-dd hh:mm:ss.000"})
            rows = {col.name: clean[col.name].to_pylist() for col in spec.cols}
            for r in range(clean.num_rows):
                for i, col in enumerate(spec.cols):
                    value = rows[col.name][r]
                    if value is None:
                        continue
                    if col.name == c.name and (r + 1) in chosen:
                        sheet.write_string(r + 1, i, "twelve")
                    elif pa.types.is_date(col.type):
                        sheet.write_datetime(r + 1, i, value, fmt_date)
                    elif pa.types.is_timestamp(col.type):
                        sheet.write_datetime(r + 1, i, value, fmt_stamp)
                    elif pa.types.is_decimal(col.type):
                        sheet.write_number(r + 1, i, float(value))
                    elif isinstance(value, str):
                        sheet.write_string(r + 1, i, value)
                    else:
                        sheet.write_number(r + 1, i, value)
            book.close()
            answers.append({"id": "xlsx-text-in-number", "format": "xlsx", "file": str(target.relative_to(folder)).replace("\\", "/"), "kind": "text-in-number",
                            "level": "row", "planted": [{"row": n, "column": c.name, "line": n + 1} for n in chosen]})
    return answers


# ---- hostile files -------------------------------------------------------------------------------------------------------------------------


def hostile(spec: TableSpec, folder: Path, seed: int) -> list[dict]:
    """Files that are not tables, each named for what is wrong with it. Every one must be refused in words, quickly, without the worker using more than a
    little memory or failing, and a table sent after it must still be written."""
    out = folder / "hostile"
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(f"{seed}:hostile")
    made = []

    def note(name: str, ending: str, expect: str) -> Path:
        path = out / f"{name}.{ending}"
        made.append({"id": name, "file": str(path.relative_to(folder)).replace("\\", "/"), "ending": ending, "expect": expect})
        return path

    for variant in BINARY:
        source = folder / variant / f"{spec.name}.{EXTENSION[variant]}"
        if not source.exists():
            continue
        data = source.read_bytes()
        note(f"{variant}-truncated", EXTENSION[variant], "readable").write_bytes(data[: max(64, int(len(data) * 0.6))])
        note(f"{variant}-garbage", EXTENSION[variant], "readable").write_bytes(bytes(rng.randrange(256) for _ in range(20_000)))
        note(f"{variant}-a-text-file", EXTENSION[variant], "readable").write_bytes(b"ward,admissions\nA,12\nB,30\n" * 50)

    bomb = note("xlsx-expands-to-hundreds-of-times-its-size", "xlsx", "expands")
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<workbook/>")
        with z.open("xl/worksheets/sheet1.xml", "w", force_zip64=True) as sheet:
            chunk = b" " * (8 * 1024 * 1024)
            for _ in range(50):
                sheet.write(chunk)
    macro = note("xlsx-with-a-macro", "xlsx", "macro")
    with zipfile.ZipFile(macro, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/vbaProject.bin", b"\x00" * 64)

    avro = folder / "avro" / f"{spec.name}.avro"
    if avro.exists():
        data = avro.read_bytes()
        marker_at = data.index(b"MUNITAS-TESTDATA") + 16          # the end of the header, where the first block starts
        block = note("avro-claims-a-huge-block", "avro", "block")
        block.write_bytes(data[:marker_at] + _varint(1) + _varint(4_000_000_000) + b"\x00" * 128)
    return made


def _varint(value: int) -> bytes:
    """An Avro long: zig-zag, then seven bits at a time."""
    value = (value << 1) ^ (value >> 63)
    out = bytearray()
    while value & ~0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


# ---- an independent scan, to check the answers ----------------------------------------------------------------------------------------------


def _read(path: Path, variant: str):
    """The file's column names, its rows as python lists by column, and its column types as words, read by a reader of its own."""
    if variant in ("parquet", "arrow", "orc"):
        if variant == "parquet":
            table = pq.read_table(path)
        elif variant == "orc":
            import pyarrow.orc as orc
            table = orc.read_table(str(path))
        else:
            import pyarrow.ipc as ipc
            with pa.memory_map(str(path)) as source:
                table = ipc.open_file(source).read_all()
        return table.schema.names, {n: table[n].to_pylist() for n in table.schema.names}, {f.name: str(f.type) for f in table.schema}
    if variant == "avro":
        import fastavro
        with open(path, "rb") as handle:
            reader = fastavro.reader(handle)
            names = [f["name"] for f in reader.writer_schema["fields"]]
            types = {f["name"]: str(f["type"]) for f in reader.writer_schema["fields"]}
            columns = {n: [] for n in names}
            for record in reader:
                for n in names:
                    columns[n].append(record[n])
        return names, columns, types
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True, data_only=True)
    rows = book[book.sheetnames[0]].iter_rows(values_only=True)
    names = [str(h) for h in next(rows)]
    columns = {n: [] for n in names}
    for row in rows:
        for n, v in zip(names, row):
            columns[n].append(v)
    book.close()
    return names, columns, {}


def scan(entry: dict, spec: TableSpec, folder: Path) -> list[int]:
    """The rows a plain reader finds damaged in a binary faulty file, from the file and the table's own definition, without reading the answers."""
    variant, kind = entry["format"], entry["kind"]
    names, columns, types = _read(folder / entry["file"], variant)
    keys = spec.key_columns()
    if entry["level"] == "file":
        wanted = [c.name for c in spec.cols]
        if names != wanted:
            return [0]
        column = entry["planted"][0]["column"]
        return [0] if kind == "wrong-type" and ("string" in types.get(column, "") or "'string'" in types.get(column, "")) else []
    found = []
    count = len(next(iter(columns.values())))
    if kind == "repeated-key":
        seen: dict = {}
        for i in range(count):
            key = tuple(columns[k][i] for k in keys)
            if key in seen:
                found.append(i + 1)
            seen.setdefault(key, i + 1)
    elif kind == "empty-key":
        found = [i + 1 for i in range(count) if columns[keys[0]][i] is None]
    elif kind == "too-many-decimals":
        name = entry["planted"][0]["column"]
        scale = next(c.type.scale for c in spec.cols if c.name == name)
        found = [i + 1 for i in range(count) if columns[name][i] is not None and -columns[name][i].as_tuple().exponent > scale
                 and columns[name][i] != columns[name][i].quantize(columns[name][i].__class__(1).scaleb(-scale))]
    elif kind == "text-in-number":
        name = entry["planted"][0]["column"]
        found = [i + 1 for i in range(count) if isinstance(columns[name][i], str)]
    return found
