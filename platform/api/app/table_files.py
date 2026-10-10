"""Reading a privacy contract and a table of rows from two files.

The console sends files, not JSON: the contract as YAML or JSON, and the rows as a CSV, a TSV, a JSON list of rows or a JSONL file of one row per
line (told apart by the file's ending). This turns them into what sealing takes, a contract and a list of rows whose values have the types the
contract declares, and refuses a bad file with every problem listed together. One attempt should show all of them, and an answer that names only
the first would turn twelve problems into twelve attempts.

Nothing here makes a key or writes a file. Sealing (`person_keys.seal_table`) does that, after the same row checks run again.
"""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import date, datetime

import yaml

from . import person_keys, privacy_contract
from .person_errors import Refused

MAX_ROWS = 20_000
MAX_BYTES = 5 * 1024 * 1024
MAX_CONTRACT_BYTES = 1024 * 1024
SHOWN = 100

# The ending of the rows file says how it is laid out. Plain text only: a file that needs a parser of its own (Parquet, Excel) is read by a worker.
FORMATS = {".csv": "csv", ".tsv": "tsv", ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl"}
OFFERED = ".csv, .tsv, .json (one list of rows) or .jsonl (one row per line)"


def layout(filename: str | None) -> str:
    """The layout of the rows file, from its name. A name with no ending is read as a CSV, which is what every caller sent before there was a choice."""
    ending = os.path.splitext((filename or "").lower())[1]
    if not ending:
        return "csv"
    if ending not in FORMATS:
        raise Refused([f"the rows file {filename!r} ends in {ending!r}, which this route does not read. Use {OFFERED}"])
    return FORMATS[ending]


def _text(raw: bytes, what: str) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise Refused([f"the {what} file is not UTF-8 text (byte {exc.start} cannot be read). Save it as UTF-8 from your editor or spreadsheet"]) from exc


def parse_contract(raw: bytes) -> dict:
    """The contract as a document, from JSON or YAML, and checked as the platform checks every contract."""
    if len(raw) > MAX_CONTRACT_BYTES:
        raise Refused(["the contract file is larger than 1 MB, which no contract is"])
    text = _text(raw, "contract")
    if not text.strip():
        raise Refused(["the contract file is empty"])
    try:
        doc = json.loads(text)
    except ValueError:
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            where = getattr(exc, "problem_mark", None)
            at = f" near line {where.line + 1}" if where else ""
            raise Refused([f"the contract file is neither valid YAML nor valid JSON{at}: {getattr(exc, 'problem', None) or 'it cannot be read'}"]) from exc
    problems = privacy_contract.check(doc)
    if problems:
        raise Refused(problems)
    return doc


def _convert(value: str, logical: str) -> tuple[object, str | None]:
    """The cell as the type the contract declares, or the reason it is not one. An empty cell is no value."""
    if value == "":
        return None, None
    try:
        if logical == "string":
            return value, None
        if logical == "integer":
            return int(value), None
        if logical == "number":
            return float(value), None
        if logical == "boolean":
            if value.lower() in ("true", "false"):
                return value.lower() == "true", None
            return None, "which is not true or false"
        if logical == "date":
            return date.fromisoformat(value).isoformat(), None
        if logical in ("timestamp", "datetime"):
            return datetime.fromisoformat(value).isoformat(), None
    except ValueError:
        return None, {"integer": "which is not a whole number", "number": "which is not a number",
                      "date": "which is not a date written like 2026-03-31",
                      "timestamp": "which is not a date and time written like 2026-03-31T09:30:00",
                      "datetime": "which is not a date and time written like 2026-03-31T09:30:00"}[logical]
    return None, f"which a CSV file cannot hold, because the contract declares the type {logical}"


def parse_rows(raw: bytes, contract: dict, kind: str = "csv") -> tuple[list[dict], list[str]]:
    """The rows, typed by the contract, and every problem found while reading them. Header problems stop here, because without a header the
    cells mean nothing. `kind` is csv or tsv; a JSON file is read by `parse_json_rows`."""
    if len(raw) > MAX_BYTES:
        raise Refused(["the rows file is larger than 5 MB. A table this big is sealed another way; split it, or ask for the large-table route"])
    text = _text(raw, "rows")
    if not text.strip():
        raise Refused(["the rows file is empty"])
    cells = list(csv.reader(io.StringIO(text, newline=""), delimiter="\t" if kind == "tsv" else ","))
    header, body = [h.strip() for h in cells[0]], [r for r in cells[1:] if any(c.strip() for c in r)]
    columns = privacy_contract.columns(contract)
    names = [c["name"] for c in columns]
    problems = []
    for name in sorted({h for h in header if header.count(h) > 1}):
        problems.append(f"the header names the column {name} more than once")
    for name in names:
        if name not in header:
            problems.append(f"the header has no column {name}, which the contract describes")
    for name in header:
        if name not in names and name:
            problems.append(f"the header has a column {name}, which the contract does not describe")
    if problems:
        raise Refused(problems)
    if not body:
        raise Refused(["the rows file has a header and no rows"])
    if len(body) > MAX_ROWS:
        raise Refused([f"the rows file has {len(body):,} rows, and this route takes at most {MAX_ROWS:,}. Send a table this big in parts instead (POST /datasets/{{id}}/table-uploads), which the console does for a file over 1 MB"])

    key = next(c["name"] for c in columns if c["primaryKey"])
    kind = {c["name"]: c for c in columns}
    rows = []
    for number, cell_row in enumerate(body, start=1):
        record = {name: (cell_row[i].strip() if i < len(cell_row) else "") for i, name in enumerate(header)}
        label = record[key] if record[key] else f"row {number}"
        if len(cell_row) != len(header):
            problems.append(f"{label}: has {len(cell_row)} values and the header has {len(header)}")
        typed = {}
        for name in names:
            value, why = _convert(record.get(name, ""), kind[name]["logicalType"])
            if why:
                problems.append(f"{label}: {name} is {record[name]!r}, {why}")
            typed[name] = value
        rows.append(typed)
    return rows, problems


def _shown(value: object) -> str:
    """A value for a message, cut short: a value from a file can be any size."""
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _convert_json(value: object, logical: str) -> tuple[object, str | None]:
    """The value as the type the contract declares, or the reason it is not one. JSON already has types, so nothing is guessed: a number where the
    contract says text is a mistake to be shown, not quietly turned into text. A missing value is no value."""
    if value is None:
        return None, None
    if isinstance(value, (dict, list)):
        return None, "which is a nested value; a column holds one value"
    if logical == "string":
        return (value, None) if isinstance(value, str) else (None, "which is not text")
    if logical == "integer":
        return (value, None) if isinstance(value, int) and not isinstance(value, bool) else (None, "which is not a whole number")
    if logical == "number":
        return (value, None) if isinstance(value, (int, float)) and not isinstance(value, bool) else (None, "which is not a number")
    if logical == "boolean":
        return (value, None) if isinstance(value, bool) else (None, "which is not true or false")
    if logical in ("date", "timestamp", "datetime"):
        if not isinstance(value, str):
            return None, "which is not written as text like 2026-03-31"
        try:
            return (date.fromisoformat(value).isoformat() if logical == "date" else datetime.fromisoformat(value).isoformat()), None
        except ValueError:
            return None, ("which is not a date written like 2026-03-31" if logical == "date"
                          else "which is not a date and time written like 2026-03-31T09:30:00")
    return None, f"which a JSON file cannot hold, because the contract declares the type {logical}"


def parse_json_rows(raw: bytes, contract: dict, kind: str) -> tuple[list[dict], list[str]]:
    """The rows of a JSON list (kind json) or of a file with one JSON row per line (kind jsonl), typed by the contract, and every problem found."""
    if len(raw) > MAX_BYTES:
        raise Refused(["the rows file is larger than 5 MB. A table this big is sealed another way; split it, or ask for the large-table route"])
    text = _text(raw, "rows")
    if not text.strip():
        raise Refused(["the rows file is empty"])
    problems: list[str] = []
    records: list[tuple[str, object]] = []
    if kind == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise Refused([f"the rows file is not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}"]) from exc
        except RecursionError as exc:
            raise Refused(["the rows file is nested too deeply to be a list of rows"]) from exc
        if not isinstance(data, list):
            raise Refused(["the rows file must be one list of rows, each row an object of column names and values. For one row per line use a .jsonl file"])
        records = [(f"row {n}", item) for n, item in enumerate(data, start=1)]
    else:
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append((f"line {number}", json.loads(line)))
            except json.JSONDecodeError as exc:
                problems.append(f"line {number} is not valid JSON: {exc.msg} at column {exc.colno}")
            except RecursionError:
                problems.append(f"line {number} is nested too deeply to be a row")
    if not records and not problems:
        raise Refused(["the rows file has no rows"])
    if len(records) > MAX_ROWS:
        raise Refused([f"the rows file has {len(records):,} rows, and this route takes at most {MAX_ROWS:,}. Send a table this big in parts instead (POST /datasets/{{id}}/table-uploads), which the console does for a file over 1 MB"])

    columns = privacy_contract.columns(contract)
    names = [c["name"] for c in columns]
    key = next(c["name"] for c in columns if c["primaryKey"])
    kinds = {c["name"]: c["logicalType"] for c in columns}
    rows = []
    for place, item in records:
        if not isinstance(item, dict):
            problems.append(f"{place} is not an object of column names and values")
            continue
        found = item.get(key)
        label = str(found) if isinstance(found, (str, int, float)) and not isinstance(found, bool) and found != "" else place
        for extra in sorted(set(item) - set(names)):
            problems.append(f"{label}: has the key {extra!r}, which the contract does not describe")
        typed = {}
        for name in names:
            value, why = _convert_json(item.get(name), kinds[name])
            if why:
                problems.append(f"{label}: {name} is {_shown(item.get(name))}, {why}")
            typed[name] = value
        rows.append(typed)
    return rows, problems


def build(contract_raw: bytes, rows_raw: bytes, filename: str | None = None) -> tuple[dict, list[dict]]:
    """A contract and typed rows, or a Refused naming everything wrong with either file. The ending of `filename` says how the rows are laid out."""
    kind = layout(filename)
    contract = parse_contract(contract_raw)
    problems = person_keys._contract_problems(contract)
    if problems:
        raise Refused(problems)
    rows, problems = parse_json_rows(rows_raw, contract, kind) if kind in ("json", "jsonl") else parse_rows(rows_raw, contract, kind)
    problems += [p for p in person_keys.row_problems(contract, rows) if p not in problems]
    if problems:
        shown = problems[:SHOWN]
        if len(problems) > SHOWN:
            shown.append(f"and {len(problems) - SHOWN:,} more problems like these")
        raise Refused(shown)
    return contract, rows
