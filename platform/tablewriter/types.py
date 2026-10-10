"""The kinds of column a table can have, and what each one accepts when it is written as text.

A contract field says its kind (`type`) and, where the kind needs it, more: a decimal says its `precision` and `scale`, a timestamp says
whether it carries a time zone (`zone`). Everything that depends on a kind lives here, so the reader, the writer and the contract agree.

Text is checked against a strict pattern before anything is converted. Arrow's own CSV conversion is lenient (it reads 0x1F as 31, accepts
a space before a number and 1e2 as a decimal, and treats the text nan as a missing value), and a platform that says a column holds whole
numbers must not quietly accept those. The patterns are ASCII only, with no sign on a whole number except a minus, and no exponent on a
decimal.
"""
from __future__ import annotations

import re

# What the contract calls each kind of column. `list` and `dict` are kept as JSON text: a contract names one without saying what is inside.
STRING, INT, FLOAT, BOOL, DATE, TIMESTAMP, DECIMAL, LIST, DICT = "string", "int", "float", "bool", "date", "timestamp", "decimal", "list", "dict"
KINDS = (STRING, INT, FLOAT, BOOL, DATE, TIMESTAMP, DECIMAL, LIST, DICT)
JSON_KINDS = (LIST, DICT)
MAX_DECIMAL_PRECISION = 38
MAX_INT_DIGITS = 18          # every whole number of this many digits fits in 64 bits

_DATE = r"\d{4}-\d{2}-\d{2}"
_TIME = r"\d{2}:\d{2}:\d{2}(\.\d{1,6})?"


def kind(spec: dict) -> str:
    found = spec.get("type", STRING)
    return found if found in KINDS else STRING


def precision_scale(spec: dict) -> tuple[int, int]:
    p, s = int(spec.get("precision", 18)), int(spec.get("scale", 2))
    if not (1 <= p <= MAX_DECIMAL_PRECISION and 0 <= s <= p):
        raise ValueError(f"a decimal needs a precision from 1 to {MAX_DECIMAL_PRECISION} and a scale from 0 up to it, not {p} and {s}")
    return p, s


def has_zone(spec: dict) -> bool:
    return bool(spec.get("zone"))


def pattern(spec: dict) -> str | None:
    """The whole text of a valid value, or None for a kind that accepts any text."""
    k = kind(spec)
    if k == INT:
        return rf"-?\d{{1,{MAX_INT_DIGITS}}}"
    if k == FLOAT:
        return r"-?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?"
    if k == BOOL:
        return r"(?i:true|false)"
    if k == DATE:
        return _DATE
    if k == TIMESTAMP:
        zone = r"(Z|[+-]\d{2}:\d{2})"
        return rf"{_DATE}[T ]{_TIME}{zone}" if has_zone(spec) else rf"{_DATE}[T ]{_TIME}"
    if k == DECIMAL:
        p, s = precision_scale(spec)
        whole = f"\\d{{1,{p - s}}}" if p > s else "0"
        return rf"-?{whole}(\.\d{{1,{s}}})?" if s else rf"-?{whole}"
    return None


def describe(spec: dict) -> str:
    """What a value of this kind looks like, in words a person can act on. Never contains a value from the file."""
    k = kind(spec)
    if k == INT:
        return f"a whole number of at most {MAX_INT_DIGITS} digits"
    if k == FLOAT:
        return "a number"
    if k == BOOL:
        return "true or false"
    if k == DATE:
        return "a date written like 2026-03-31"
    if k == TIMESTAMP:
        return ("a date and time with a time zone, written like 2026-03-31T09:30:00Z or 2026-03-31T09:30:00+01:00" if has_zone(spec)
                else "a date and time written like 2026-03-31T09:30:00, with no time zone")
    if k == DECIMAL:
        p, s = precision_scale(spec)
        return f"a number with at most {p - s} digits before the point and at most {s} after it" if s else f"a whole number of at most {p} digits"
    return "text"


def arrow_type(spec: dict):
    import pyarrow as pa

    k = kind(spec)
    return {INT: pa.int64(), FLOAT: pa.float64(), BOOL: pa.bool_(), DATE: pa.date32()}.get(k) or _arrow_other(spec, k)


def _arrow_other(spec: dict, k: str):
    import pyarrow as pa

    if k == TIMESTAMP:
        return pa.timestamp("us", tz="UTC") if has_zone(spec) else pa.timestamp("us")
    if k == DECIMAL:
        p, s = precision_scale(spec)
        return pa.decimal128(p, s)
    return pa.string()


def iceberg_type(spec: dict):
    from pyiceberg.types import (BooleanType, DateType, DecimalType, DoubleType, LongType, StringType, TimestampType, TimestamptzType)

    k = kind(spec)
    if k == TIMESTAMP:
        return TimestamptzType() if has_zone(spec) else TimestampType()
    if k == DECIMAL:
        return DecimalType(*precision_scale(spec))
    return {INT: LongType(), FLOAT: DoubleType(), BOOL: BooleanType(), DATE: DateType()}.get(k, StringType())


def compiled(spec: dict):
    text = pattern(spec)
    return re.compile(text) if text else None


def convert(texts, spec: dict):
    """Text that already matches `pattern` as a column of this kind. Raises pyarrow.ArrowInvalid for a value the pattern cannot
    catch, such as the 30th of February, which the caller then looks for one value at a time."""
    import pyarrow as pa
    import pyarrow.compute as pc

    k = kind(spec)
    if k in (STRING, LIST, DICT):
        return texts
    if k == BOOL:
        return pc.equal(pc.utf8_lower(texts), "true")
    if k == TIMESTAMP:
        shaped = pc.replace_substring(texts, " ", "T", max_replacements=1)
        return pc.cast(shaped, arrow_type(spec))
    return pc.cast(texts, arrow_type(spec))
