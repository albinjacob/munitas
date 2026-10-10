"""What is true of a generated table, written down at the moment it is made, so any later reading of it can be checked against figures that
did not come from the thing being checked.

A truth file holds the row count and, for each column, how many values are missing, the smallest and largest, and the total of a number
column or the total length of a text column. For the key it holds the number of different keys and a fingerprint (the total of each
key's MD5 digest, low 64 bits), which does not depend on the order of the rows. A table about people also holds the number of different
people. `Summary` is used both to write the truth and to measure a file that was read back, so a difference is a difference in the data.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from .formats import stringify
from .tables import GENERATOR_VERSION, TableSpec

UNIT = "\x1f"


def _low64(key: str) -> int:
    return int(hashlib.md5(key.encode("utf-8")).hexdigest()[16:], 16)


def _text(value) -> str | None:
    if value is None:
        return None
    return str(value) if not isinstance(value, Decimal) else format(value, "f")


class Summary:
    def __init__(self, spec: TableSpec):
        self.spec = spec
        self.rows = 0
        self.stats = {c.name: {"nulls": 0, "min": None, "max": None, "sum": None, "chars": 0} for c in spec.cols}
        self.keys = spec.key_columns()
        self.fingerprint = 0
        self.hashes: list[np.ndarray] = []
        self.person_hashes: list[np.ndarray] = []
        self.person_column = (spec.subject or {}).get("column")

    def add(self, table: pa.Table) -> None:
        self.rows += table.num_rows
        for column in self.spec.cols:
            data, t, stat = table[column.name], column.type, self.stats[column.name]
            stat["nulls"] += data.null_count
            if data.null_count == table.num_rows:
                continue
            low_high = pc.min_max(data).as_py()
            for end, pick in (("min", min), ("max", max)):
                value = low_high[end]
                stat[end] = value if stat[end] is None else pick(stat[end], value)
            if pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t):
                total = pc.sum(data).as_py()
                stat["sum"] = total if stat["sum"] is None else stat["sum"] + total
            if pa.types.is_string(t):
                stat["chars"] += pc.sum(pc.utf8_length(data)).as_py() or 0
        if self.keys:
            text = stringify(table.select(self.keys))
            joined = pc.binary_join_element_wise(*[text[k] for k in self.keys], UNIT) if len(self.keys) > 1 else text[self.keys[0]]
            digests = np.array([_low64(k) for k in joined.to_pylist()], dtype=np.uint64)
            self.hashes.append(digests)
            self.fingerprint += int(sum(int(x) for x in digests))
        if self.person_column:
            self.person_hashes.append(np.array([_low64(k) for k in table[self.person_column].to_pylist()], dtype=np.uint64))

    def result(self) -> dict:
        columns = {}
        for column in self.spec.cols:
            stat = self.stats[column.name]
            entry = {"type": str(column.type), "nulls": stat["nulls"], "min": _text(stat["min"]), "max": _text(stat["max"])}
            if stat["sum"] is not None:
                entry["sum"] = _text(stat["sum"])
            if pa.types.is_string(column.type):
                entry["chars"] = stat["chars"]
            columns[column.name] = entry
        out = {"rows": self.rows, "columns": columns, "key": None, "people": None}
        if self.keys:
            out["key"] = {"columns": self.keys, "distinct": int(len(np.unique(np.concatenate(self.hashes)))) if self.hashes else 0,
                          "fingerprint": str(self.fingerprint)}
        if self.person_column:
            out["people"] = {"column": self.person_column,
                             "distinct": int(len(np.unique(np.concatenate(self.person_hashes)))) if self.person_hashes else 0}
        return out


def build(spec: TableSpec, seed: int, summary: dict) -> dict:
    return {"generator_version": GENERATOR_VERSION, "table": spec.name, "seed": seed, **summary}


def summarize(spec: TableSpec, table: pa.Table) -> dict:
    s = Summary(spec)
    s.add(table)
    return s.result()


def _same(a, b, column_type: str) -> bool:
    if a == b:
        return True
    if "double" in column_type and a is not None and b is not None:
        x, y = float(a), float(b)
        return abs(x - y) <= 1e-9 * max(1.0, abs(x), abs(y))
    return False


def compare(truth: dict, measured: dict) -> list[str]:
    """Every difference between the truth and a measurement of a file read back, in words. Empty means they agree."""
    out = []
    if truth["rows"] != measured["rows"]:
        out.append(f"{measured['rows']:,} rows were read and the truth says {truth['rows']:,}")
    for name, want in truth["columns"].items():
        got = measured["columns"].get(name)
        if got is None:
            out.append(f"the column {name} was not read back")
            continue
        for field in ("nulls", "min", "max", "sum", "chars"):
            if field in want and not _same(want[field], got.get(field), want["type"]):
                out.append(f"{name}: {field} is {got.get(field)!r} and the truth says {want[field]!r}")
    for part in ("key", "people"):
        if truth.get(part) and truth[part] != (measured.get(part) or {}) and any(
                truth[part].get(k) != (measured.get(part) or {}).get(k) for k in truth[part]):
            out.append(f"{part}: measured {measured.get(part)} and the truth says {truth[part]}")
    return out
