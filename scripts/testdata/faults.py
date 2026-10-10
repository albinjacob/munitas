"""Faulty copies of a made table, with an answers file saying exactly which rows were damaged and how.

Each faulty file has one kind of fault, planted in a few known rows (or, for a fault in the layout, in the header). The answers file lists, for
every file: the kind, the rows and lines that were damaged, the column, and the value that was put there. A test that brings the file in
must see the platform refuse it and name those rows, and nothing else.

Rows are numbered from 1, not counting the header. For a CSV the line is also given, because a quoted line break makes a row take two lines.
These are made from the CSV and JSON lines files of the same table, so they are the text formats' faults. The binary formats' faults come
with the worker that reads them.
"""
from __future__ import annotations

import csv
import io
import json
import random
import re
from pathlib import Path

import pyarrow as pa

from .tables import TableSpec

PER_KIND = 5
BAD_NUMBER, BAD_DATE, BAD_DECIMAL = "twelve", "31/03/2026", "12.5.3"


def positions(rows: int, kind: str, seed: int) -> list[int]:
    """Which rows to damage: spread through the file, never the first or the last, the same every time for the same seed."""
    if rows < 20:
        return []
    rng = random.Random(f"{seed}:{kind}")
    anchors = [rows // 10, rows // 4, rows // 2, (3 * rows) // 4, rows - 2]
    return sorted({min(rows - 1, max(2, a + rng.randint(-max(1, rows // 40), max(1, rows // 40)))) for a in anchors})


def _columns_of(spec: TableSpec, predicate) -> list[str]:
    return [c.name for c in spec.cols if predicate(c)]


def applicable(spec: TableSpec) -> dict[str, dict]:
    """The row faults that make sense for this table, each with the column it is planted in."""
    keys = spec.key_columns()
    numbers = _columns_of(spec, lambda c: pa.types.is_integer(c.type) and not c.key)
    decimals = _columns_of(spec, lambda c: pa.types.is_decimal(c.type))
    times = _columns_of(spec, lambda c: (pa.types.is_date(c.type) or pa.types.is_timestamp(c.type)) and not c.key)
    out: dict[str, dict] = {}
    if numbers:
        out["bad-integer"] = {"column": numbers[0], "value": BAD_NUMBER}
    if decimals:
        out["bad-decimal"] = {"column": decimals[0], "value": BAD_DECIMAL}
    if times:
        out["bad-date"] = {"column": times[0], "value": BAD_DATE}
    if keys:
        out["repeated-key"] = {"column": ",".join(keys), "value": None}
        out["empty-key"] = {"column": keys[0], "value": ""}
    if spec.subject:
        out["empty-subject"] = {"column": spec.subject["column"], "value": ""}
    out["short-row"] = {"column": None, "value": None}
    out["long-row"] = {"column": None, "value": None}
    texts = [c.name for c in spec.cols if pa.types.is_string(c.type)]
    plain_texts = [c.name for c in spec.cols if pa.types.is_string(c.type) and not c.key]
    out["invalid-byte"] = {"column": (plain_texts or texts)[0], "value": None}
    return out


# ---- CSV and TSV -------------------------------------------------------------------------------------------------------------------


def _read_csv(path: Path, delimiter: str) -> tuple[list[str], list[list[str]], list[int]]:
    """The header, every row, and the line each row starts on."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        header = next(reader)
        rows, starts, previous = [], [], reader.line_num
        for row in reader:
            starts.append(previous + 1)
            rows.append(row)
            previous = reader.line_num
    return header, rows, starts


def _serialise(fields: list[str], delimiter: str) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=delimiter, lineterminator="\n").writerow(fields)
    return buffer.getvalue()


def _write_csv(path: Path, header: list[str], rows: list[list[str]], delimiter: str, damaged: dict[int, bytes] | None = None) -> list[int]:
    """Write the file and return the line each row starts on. `damaged` maps a row number to the bytes to write for it instead."""
    damaged = damaged or {}
    line, starts = 1, []
    with open(path, "wb") as out:
        head = _serialise(header, delimiter).encode("utf-8")
        out.write(head)
        line += head.count(b"\n")
        for number, row in enumerate(rows, start=1):
            starts.append(line)
            data = damaged.get(number) or _serialise(row, delimiter).encode("utf-8")
            out.write(data)
            line += data.count(b"\n")
    return starts


def csv_faults(spec: TableSpec, clean: Path, out: Path, delimiter: str, extension: str, seed: int, only: set[str] | None = None) -> list[dict]:
    header, rows, _ = _read_csv(clean, delimiter)
    index = {name: i for i, name in enumerate(header)}
    answers = []
    for kind, how in applicable(spec).items():
        if only and kind not in only:
            continue
        chosen = positions(len(rows), kind, seed)
        if not chosen:
            continue
        copy = [list(r) for r in rows]
        damaged: dict[int, bytes] = {}
        planted = []
        for number in chosen:
            row = copy[number - 1]
            note = {"row": number}
            if kind in ("bad-integer", "bad-decimal", "bad-date", "empty-key", "empty-subject"):
                row[index[how["column"]]] = how["value"]
                note.update(column=how["column"], value=how["value"])
            elif kind == "repeated-key":
                source = 1 + (number * 7) % (number - 1)
                for name in how["column"].split(","):
                    row[index[name]] = rows[source - 1][index[name]]
                note.update(column=how["column"], also_row=source)
            elif kind == "short-row":
                del row[-1]
                note.update(fields=len(row), expected=len(header))
            elif kind == "long-row":
                row.append("extra")
                note.update(fields=len(row), expected=len(header))
            elif kind == "invalid-byte":
                fields = _serialise(row, delimiter).encode("utf-8")
                at = fields.index(row[index[how["column"]]].encode("utf-8")) if row[index[how["column"]]] else 0
                damaged[number] = fields[:at] + b"\xff" + fields[at:]
                note.update(column=how["column"], value="the byte 0xFF")
            planted.append(note)
        target = out / f"{extension}-{kind}" / f"{spec.name}.{extension}"
        target.parent.mkdir(parents=True, exist_ok=True)
        starts = _write_csv(target, header, copy, delimiter, damaged)
        for note in planted:
            note["line"] = starts[note["row"] - 1]
        answers.append({"id": f"{extension}-{kind}", "format": extension, "file": str(target), "kind": kind, "level": "row", "planted": planted,
                        "delimiter": delimiter})

    # Faults in the layout rather than in a row: a column the contract describes is missing, and a column it does not describe is present.
    for kind in ("missing-column", "extra-column"):
        if only and kind not in only:
            continue
        copy = [list(r) for r in rows]
        head = list(header)
        if kind == "missing-column":
            gone = next((c.name for c in reversed(spec.cols) if not c.key), spec.cols[-1].name)
            at = index[gone]
            head = [h for i, h in enumerate(header) if i != at]
            copy = [[v for i, v in enumerate(r) if i != at] for r in copy]
            detail = {"column": gone}
        else:
            head.append("extra")
            copy = [r + ["x"] for r in copy]
            detail = {"column": "extra"}
        target = out / f"{extension}-{kind}" / f"{spec.name}.{extension}"
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(target, head, copy, delimiter)
        answers.append({"id": f"{extension}-{kind}", "format": extension, "file": str(target), "kind": kind, "level": "file", "planted": [detail],
                        "delimiter": delimiter})
    return answers


# ---- JSON lines and JSON list -------------------------------------------------------------------------------------------------------


def _field(line: str, column: str) -> re.Match:
    """Where a column's value is in a line of JSON lines: the text after `"column":` up to the comma or brace that ends it."""
    pattern = r'"' + re.escape(column) + r'":(?P<value>"(?:[^"\\]|\\.)*"|[^,}]+)'
    return re.search(pattern, line)


def _with_value(line: str, column: str, replacement: str) -> str:
    found = _field(line, column)
    return line[:found.start("value")] + replacement + line[found.end("value"):]


def jsonl_faults(spec: TableSpec, clean: Path, out: Path, seed: int, only: set[str] | None = None) -> list[dict]:
    lines = clean.read_text(encoding="utf-8").splitlines()
    kinds = {k: v for k, v in applicable(spec).items() if k not in ("short-row", "long-row")}
    kinds["invalid-json"] = {"column": None, "value": None}
    kinds["extra-field"] = {"column": "extra", "value": "x"}
    kinds["nested-value"] = {"column": next((c.name for c in spec.cols if not c.key), spec.cols[0].name), "value": {"x": 1}}
    answers = []
    for kind, how in kinds.items():
        if only and kind not in only:
            continue
        chosen = positions(len(lines), "jsonl-" + kind, seed)
        if not chosen:
            continue
        text: list[bytes] = [line.encode("utf-8") for line in lines]
        planted = []
        for number in chosen:
            line = lines[number - 1]
            note = {"row": number, "line": number}
            if kind in ("bad-integer", "bad-decimal", "bad-date", "empty-key", "empty-subject"):
                line = _with_value(line, how["column"], json.dumps(how["value"]))
                note.update(column=how["column"], value=how["value"])
            elif kind == "nested-value":
                line = _with_value(line, how["column"], json.dumps(how["value"], separators=(",", ":")))
                note.update(column=how["column"], value="a nested value")
            elif kind == "extra-field":
                line = line[:-1] + ',"extra":"x"}'
                note.update(column="extra", value="x")
            elif kind == "repeated-key":
                source = 1 + (number * 7) % (number - 1)
                for name in how["column"].split(","):
                    line = _with_value(line, name, _field(lines[source - 1], name).group("value"))
                note.update(column=how["column"], also_row=source)
            elif kind == "invalid-json":
                line = line[:-1]
                note.update(value="the closing brace is missing")
            if kind == "invalid-byte":
                raw = line.encode("utf-8")
                found = _field(line, how["column"])
                at = len(line[:found.start("value")].encode("utf-8")) + 1
                text[number - 1] = raw[:at] + b"\xff" + raw[at:]
                note.update(column=how["column"], value="the byte 0xFF")
            else:
                text[number - 1] = line.encode("utf-8")
            planted.append(note)
        target = out / f"jsonl-{kind}" / f"{spec.name}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\n".join(text) + b"\n")
        answers.append({"id": f"jsonl-{kind}", "format": "jsonl", "file": str(target), "kind": kind, "level": "row", "planted": planted})
    return answers


def json_faults(spec: TableSpec, clean: Path, out: Path, seed: int) -> list[dict]:
    rows = json.loads(clean.read_text(encoding="utf-8"))
    answers = []
    target = out / "json-not-a-list" / f"{spec.name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")
    answers.append({"id": "json-not-a-list", "format": "json", "file": str(target), "kind": "not-a-list", "level": "file", "planted": []})
    numbers = _columns_of(spec, lambda c: pa.types.is_integer(c.type) and not c.key)
    chosen = positions(len(rows), "json-bad-integer", seed)
    if numbers and chosen:
        for number in chosen:
            rows[number - 1][numbers[0]] = BAD_NUMBER
        target = out / "json-bad-integer" / f"{spec.name}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        answers.append({"id": "json-bad-integer", "format": "json", "file": str(target), "kind": "bad-integer", "level": "row",
                        "planted": [{"row": n, "column": numbers[0], "value": BAD_NUMBER} for n in chosen]})
    return answers


def make(spec: TableSpec, folder: Path, seed: int) -> list[dict]:
    """Make the faulty copies for a table whose clean files are in `folder` (the one `generate.make` wrote), and write answers.json."""
    out = folder / "faults"
    answers = []
    if (folder / "csv" / f"{spec.name}.csv").exists():
        answers += csv_faults(spec, folder / "csv" / f"{spec.name}.csv", out, ",", "csv", seed)
    if (folder / "tsv" / f"{spec.name}.tsv").exists():
        answers += csv_faults(spec, folder / "tsv" / f"{spec.name}.tsv", out, "\t", "tsv", seed, only={"bad-integer", "short-row", "missing-column"})
    if (folder / "jsonl" / f"{spec.name}.jsonl").exists():
        answers += jsonl_faults(spec, folder / "jsonl" / f"{spec.name}.jsonl", out, seed)
    if (folder / "json" / f"{spec.name}.json").exists():
        answers += json_faults(spec, folder / "json" / f"{spec.name}.json", out, seed)
    for a in answers:
        a["file"] = str(Path(a["file"]).relative_to(folder)).replace("\\", "/")
    from . import faults_binary
    answers += faults_binary.make(spec, folder, seed)
    hostile = faults_binary.hostile(spec, folder, seed)
    (out / "answers.json").write_text(json.dumps({"table": spec.name, "faults": answers, "hostile": hostile}, indent=1) + "\n", encoding="utf-8")
    return answers


# ---- an independent scan, to check the answers ------------------------------------------------------------------------------------------


def _is_integer(text) -> bool:
    return isinstance(text, str) and text.lstrip("-").isdigit()


def scan(entry: dict, spec: TableSpec, folder: Path) -> list[int]:
    """The rows a plain reader finds damaged in a faulty file, by looking only at the file and the table's own definition. It does not read
    the answers, so agreeing with them means the answers are right."""
    path = folder / entry["file"]
    kind, fmt = entry["kind"], entry["format"]
    if fmt in ("parquet", "arrow", "orc", "avro", "xlsx"):
        from . import faults_binary
        return faults_binary.scan(entry, spec, folder)
    columns = {c.name: c for c in spec.cols}
    keys = spec.key_columns()
    found: list[int] = []
    if fmt in ("csv", "tsv"):
        delimiter = entry["delimiter"]
        seen_keys = {}
        text = path.read_bytes().decode("utf-8", errors="replace")      # a byte that is not UTF-8 becomes U+FFFD, which marks its row
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
        header_names = next(reader)
        for number, row in enumerate(reader, start=1):
            if kind == "invalid-byte" and "�" in "".join(row):
                found.append(number)
            elif kind in ("short-row", "long-row") and len(row) != len(header_names):
                found.append(number)
            elif kind in ("bad-integer", "bad-decimal", "bad-date", "empty-key", "empty-subject"):
                column = entry["planted"][0]["column"]
                value = row[header_names.index(column)] if len(row) > header_names.index(column) else ""
                if kind == "bad-integer" and not _is_integer(value) and value != "":
                    found.append(number)
                elif kind == "bad-decimal" and value.count(".") > 1:
                    found.append(number)
                elif kind == "bad-date" and "/" in value:
                    found.append(number)
                elif kind in ("empty-key", "empty-subject") and value == "":
                    found.append(number)
            elif kind == "repeated-key":
                key = tuple(row[header_names.index(k)] for k in keys)
                seen_keys.setdefault(key, []).append(number)
        if kind == "repeated-key":
            found = sorted(n for numbers in seen_keys.values() if len(numbers) > 1 for n in numbers[1:])
        if kind in ("missing-column", "extra-column"):
            expected = [c.name for c in spec.cols]
            return [] if header_names == expected else [0]
        return found
    if fmt == "jsonl":
        seen_keys = {}
        for number, line in enumerate(path.read_bytes().split(b"\n")[:-1], start=1):
            try:
                row = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                found.append(number)
                continue
            if kind == "extra-field" and "extra" in row:
                found.append(number)
            elif kind == "nested-value" and any(isinstance(v, (dict, list)) for v in row.values()):
                found.append(number)
            elif kind == "bad-integer" and isinstance(row.get(entry["planted"][0]["column"]), str):
                found.append(number)
            elif kind == "bad-decimal" and isinstance(row.get(entry["planted"][0]["column"]), str):
                found.append(number)
            elif kind == "bad-date" and "/" in str(row.get(entry["planted"][0]["column"])):
                found.append(number)
            elif kind in ("empty-key", "empty-subject") and row.get(entry["planted"][0]["column"]) == "":
                found.append(number)
            elif kind == "repeated-key":
                seen_keys.setdefault(tuple(row.get(k) for k in keys), []).append(number)
        if kind == "repeated-key":
            found = sorted(n for numbers in seen_keys.values() if len(numbers) > 1 for n in numbers[1:])
        return found
    if fmt == "json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if kind == "not-a-list":
            return [] if isinstance(data, list) else [0]
        column = entry["planted"][0]["column"]
        return [n for n, row in enumerate(data, start=1) if isinstance(row.get(column), str)]
    raise ValueError(fmt)


def check(spec: TableSpec, folder: Path) -> list[str]:
    """Every disagreement between the answers file and a plain scan of each faulty file. Empty when they agree."""
    answers = json.loads((folder / "faults" / "answers.json").read_text(encoding="utf-8"))["faults"]
    problems = []
    for entry in answers:
        planted = sorted(p["row"] for p in entry["planted"] if "row" in p)
        found = scan(entry, spec, folder)
        if entry["level"] == "file":
            if not found:
                problems.append(f"{entry['id']}: the scan found no fault in the layout")
        elif found != planted:
            problems.append(f"{entry['id']}: the answers name rows {planted} and a scan finds {found}")
        lines = {p["row"]: p["line"] for p in entry["planted"] if "line" in p}
        if entry["format"] in ("csv", "tsv") and lines:
            blob = (folder / entry["file"]).read_bytes().decode("utf-8", errors="replace")
            reader = csv.reader(io.StringIO(blob, newline=""), delimiter=entry["delimiter"])
            next(reader)
            starts, previous = [], reader.line_num
            for _ in reader:
                starts.append(previous + 1)
                previous = reader.line_num
            for row, line in lines.items():
                if starts[row - 1] != line:
                    problems.append(f"{entry['id']}: row {row} starts on line {starts[row - 1]} and the answers say {line}")
    return problems
