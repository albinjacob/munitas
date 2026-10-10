"""The contract for each table, written in the Open Data Contract Standard (v3.2.0) with the Munitas privacy profile in custom properties.

The contract is what a person hands the platform beside the rows, so it says what the platform needs to know: each column's type and
sensitivity, what happens to it when the table is sealed, which column or columns are the key, how to find the person each row is about,
and, for a table that is not about people, that it cannot be erased and why.
"""
from __future__ import annotations

import pyarrow as pa
import yaml

from .tables import REPLACED, Col, TableSpec

VENDOR = "munitas"


def _custom(prop: str, value) -> dict:
    return {"property": prop, "vendor": VENDOR, "value": value}


def _logical(col: Col) -> tuple[str, str | None, dict]:
    """(logical type, physical type if the contract should state one, logical type options)."""
    t = col.type
    if pa.types.is_string(t):
        return "string", None, {}
    if pa.types.is_integer(t):
        return "integer", None, {}
    if pa.types.is_floating(t):
        return "number", None, {}
    if pa.types.is_decimal(t):
        return "number", f"decimal({t.precision},{t.scale})", {}
    if pa.types.is_boolean(t):
        return "boolean", None, {}
    if pa.types.is_date(t):
        return "date", None, {"format": col.fmt}
    if pa.types.is_timestamp(t):
        return "timestamp", None, {"format": col.fmt, "timezone": bool(col.zone)}
    raise ValueError(f"no contract type for {t}")


def _property(col: Col) -> dict:
    logical, physical, options = _logical(col)
    prop: dict = {"id": f"{col.name}_prop", "name": col.name, "logicalType": logical}
    if physical:
        prop["physicalType"] = physical
    if options:
        prop["logicalTypeOptions"] = options
    if col.key:
        prop["primaryKey"] = True
        prop["primaryKeyPosition"] = col.key
        prop["required"] = True
    prop["classification"] = col.classification
    custom = []
    if col.person_identifier:
        custom.append(_custom("personIdentifier", True))
    custom.append(_custom("treatment", col.treatment))
    if col.reason:
        custom.append(_custom("treatmentReason", col.reason))
        custom.append(_custom("treatmentApprovedBy", col.approved_by))
    prop["customProperties"] = custom
    return prop


def contract(spec: TableSpec) -> dict:
    custom = []
    if spec.kind == "plain":
        custom += [_custom("erasable", False), _custom("notErasableReason", spec.not_erasable_reason)]
    else:
        custom.append(_custom("subject", dict(spec.subject)))
    return {
        "apiVersion": "v3.2.0", "kind": "DataContract", "id": spec.name, "name": spec.name, "version": "1.0.0", "status": "active",
        "description": {"purpose": spec.summary}, "customProperties": custom,
        "schema": [{"id": f"{spec.name}_obj", "name": spec.name, "logicalType": "object", "properties": [_property(c) for c in spec.cols]}],
    }


def to_yaml(spec: TableSpec) -> str:
    header = (f"# Test contract for the {spec.name} table. Every name and number in the data is invented.\n"
              f"# Made by scripts/testdata. {spec.summary}\n")
    return header + yaml.safe_dump(contract(spec), sort_keys=False, allow_unicode=True, width=140)
