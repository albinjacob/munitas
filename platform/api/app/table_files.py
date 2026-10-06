"""Reading a privacy contract and a table of rows from two files.

The console sends files, not JSON: the contract as YAML or JSON, and the rows as a CSV. This turns them into what sealing takes, a contract and
a list of rows whose values have the types the contract declares, and refuses a bad file with every problem listed together. One attempt
should show all of them, and an answer that names only the first would turn twelve problems into twelve attempts.

Nothing here makes a key or writes a file. Sealing (`person_keys.seal_table`) does that, after the same row checks run again.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime

import yaml

from . import person_keys, privacy_contract
from .person_errors import Refused

MAX_ROWS = 20_000
MAX_BYTES = 5 * 1024 * 1024
MAX_CONTRACT_BYTES = 1024 * 1024
SHOWN = 100


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


def parse_rows(raw: bytes, contract: dict) -> tuple[list[dict], list[str]]:
    """The rows, typed by the contract, and every problem found while reading them. Header problems stop here, because without a header the
    cells mean nothing."""
    if len(raw) > MAX_BYTES:
        raise Refused(["the rows file is larger than 5 MB. A table this big is sealed another way; split it, or ask for the large-table route"])
    text = _text(raw, "rows")
    if not text.strip():
        raise Refused(["the rows file is empty"])
    cells = list(csv.reader(io.StringIO(text, newline="")))
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
        raise Refused([f"the rows file has {len(body):,} rows, and this route takes at most {MAX_ROWS:,}. A table this big is sealed another way"])

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


def build(contract_raw: bytes, rows_raw: bytes) -> tuple[dict, list[dict]]:
    """A contract and typed rows, or a Refused naming everything wrong with either file."""
    contract = parse_contract(contract_raw)
    rows, problems = parse_rows(rows_raw, contract)
    problems += [p for p in person_keys.row_problems(contract, rows) if p not in problems]
    if problems:
        shown = problems[:SHOWN]
        if len(problems) > SHOWN:
            shown.append(f"and {len(problems) - SHOWN:,} more problems like these")
        raise Refused(shown)
    return contract, rows
