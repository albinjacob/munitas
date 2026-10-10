"""The privacy contract: an Open Data Contract Standard document with the Munitas profile.

A contract says, per column, how sensitive it is and what happens to it when the table is sealed, and how to find the person each row is
about. It is written in the Open Data Contract Standard (ODCS) version 3.2.0 and the Munitas profile lives in `customProperties` with the
vendor `munitas`, because the standard rejects unknown top-level fields. The standard's own validator accepts any text in a custom
property, so the values are checked here: a misspelt treatment is the kind of mistake that would otherwise lock nothing and say nothing.

`check` returns every problem at once, as sentences a person can act on. An empty list means the contract may be used.
"""

from __future__ import annotations

import re

STANDARD_VERSION = "v3.2.0"
VENDOR = "munitas"

CLASSIFICATIONS = ("none", "quasi", "direct", "phi")
REPLACED = "replaced by the person code"
TREATMENTS = ("lock", "readable", "generalise", REPLACED)
OFFERED = ("lock", "readable", REPLACED)
SUBJECT_RULES = ("column", "manifest", "uploadField", "embeddedMetadata", "fileNamePattern", "linkKey")
SENSITIVE = ("direct", "phi")


def _profile(props: object) -> dict:
    """The munitas custom properties as a plain mapping."""
    out: dict = {}
    for item in props if isinstance(props, list) else []:
        if isinstance(item, dict) and item.get("vendor") == VENDOR and isinstance(item.get("property"), str):
            out[item["property"]] = item.get("value")
    return out


def columns(doc: dict) -> list[dict]:
    """Each column as {name, logicalType, physicalType, options, classification, treatment, primaryKey, keyPosition, required, personIdentifier,
    reason, approvedBy}. `options` is the standard's logicalTypeOptions, which says how a date or time is written and whether it has a time zone."""
    found = []
    objects = doc.get("schema") if isinstance(doc.get("schema"), list) else []
    for obj in objects[:1]:
        for prop in obj.get("properties", []) if isinstance(obj, dict) else []:
            if not isinstance(prop, dict):
                continue
            p = _profile(prop.get("customProperties"))
            found.append({
                "name": prop.get("name"), "logicalType": prop.get("logicalType") or "string",
                "physicalType": prop.get("physicalType") or "", "options": prop.get("logicalTypeOptions") or {},
                "classification": prop.get("classification"), "treatment": p.get("treatment"),
                "primaryKey": bool(prop.get("primaryKey")), "keyPosition": prop.get("primaryKeyPosition"),
                "required": prop.get("required") is True, "personIdentifier": p.get("personIdentifier") is True,
                "reason": p.get("treatmentReason"), "approvedBy": p.get("treatmentApprovedBy"),
            })
    return found


def key_columns(doc: dict) -> list[str]:
    """The columns that make up the primary key, in key order: by the position the contract gives each, else in the order they are written."""
    keys = [c for c in columns(doc) if c["primaryKey"]]
    return [c["name"] for c in sorted(keys, key=lambda c: (c["keyPosition"] if isinstance(c["keyPosition"], int) else 10 ** 6))]


_DECIMAL = re.compile(r"decimal\(\s*(\d+)\s*,\s*(\d+)\s*\)", re.I)


def table_field(col: dict) -> dict:
    """A column as the table writer's field: its kind and what the kind needs (a decimal's precision and scale, whether a timestamp has a zone).

    A `number` is a float unless the contract gives a physical type of decimal(precision, scale), which is what money should be.
    """
    logical, field = col["logicalType"], {"name": col["name"]}
    if col["required"] or col["primaryKey"]:
        field["required"] = True
    if logical == "integer":
        field["type"] = "int"
    elif logical == "number":
        found = _DECIMAL.fullmatch(col["physicalType"])
        field["type"] = "decimal" if found else "float"
        if found:
            field["precision"], field["scale"] = int(found.group(1)), int(found.group(2))
    elif logical == "boolean":
        field["type"] = "bool"
    elif logical == "date":
        field["type"] = "date"
    elif logical in ("timestamp", "datetime"):
        field["type"] = "timestamp"
        field["zone"] = bool(col["options"].get("timezone"))
    elif logical == "array":
        field["type"] = "list"
    elif logical == "object":
        field["type"] = "dict"
    else:
        field["type"] = "string"
    return field


def subject_rule(doc: dict) -> dict | None:
    value = _profile(doc.get("customProperties")).get("subject")
    return value if isinstance(value, dict) else None


DEFAULT_GROUP_SIZE = 5


def group_size_threshold(doc: dict) -> int:
    """The fewest different people a combination of readable quasi columns may hold before the report calls it small. 5 unless the contract says."""
    value = _profile(doc.get("customProperties")).get("groupSizeThreshold", DEFAULT_GROUP_SIZE)
    return value if isinstance(value, int) and not isinstance(value, bool) else DEFAULT_GROUP_SIZE


def counted_columns(doc: dict) -> list[str]:
    """The columns a group-size report counts: quasi-identifying and left readable. A locked one cannot single anybody out."""
    return [c["name"] for c in columns(doc) if c["classification"] == "quasi" and c["treatment"] == "readable"]


def erasable(doc: dict) -> bool:
    return _profile(doc.get("customProperties")).get("erasable", True) is not False


def is_plain(doc: dict) -> bool:
    """A table that is not about people: it says it cannot be erased and names nobody to find. Nothing in it is locked under a person's key."""
    return not erasable(doc) and subject_rule(doc) is None


def check(doc: object) -> list[str]:
    """Every reason this contract cannot be used. Empty when it can."""
    if not isinstance(doc, dict):
        return ["the contract must be a document with fields, not a list or a piece of text"]
    problems: list[str] = []
    if doc.get("apiVersion") != STANDARD_VERSION:
        problems.append(f"the contract must be written in Open Data Contract Standard {STANDARD_VERSION}, and this one says {doc.get('apiVersion')!r}")
    if doc.get("kind") != "DataContract":
        problems.append("the contract's kind must be DataContract")
    for field in ("name", "version"):
        if not doc.get(field) or not isinstance(doc.get(field), str):
            problems.append(f"the contract has no {field}")
    objects = doc.get("schema")
    if not isinstance(objects, list) or len(objects) != 1:
        return problems + ["the contract must describe exactly one table in its schema"]

    cols = columns(doc)
    if not cols:
        return problems + ["the table has no columns"]
    names = [c["name"] for c in cols]
    seen: set = set()
    for c in cols:
        name = c["name"]
        if not isinstance(name, str) or not name:
            problems.append("a column has no name")
            continue
        if name in seen:
            problems.append(f"column {name} is described twice")
        seen.add(name)
        if c["classification"] not in CLASSIFICATIONS:
            problems.append(f"column {name}: its classification {c['classification']!r} must be one of {', '.join(CLASSIFICATIONS)}")
        treatment = c["treatment"]
        if treatment not in TREATMENTS:
            problems.append(f"column {name}: the treatment {treatment!r} must be one of lock, readable or {REPLACED}")
            continue
        if treatment == "generalise":
            problems.append(f"column {name}: generalise is not available in this version, so choose lock or readable")
        if treatment == REPLACED and not c["personIdentifier"]:
            problems.append(f"column {name}: only the column that identifies the person can be {REPLACED}")
        if treatment == "readable" and c["classification"] in SENSITIVE and not (c["reason"] and c["approvedBy"]):
            problems.append(f"column {name} is classified {c['classification']} and is left readable, which needs a recorded reason and the person who approved it")
    keys = [c["name"] for c in cols if c["primaryKey"]]
    if not is_plain(doc) and len(keys) != 1:
        problems.append("exactly one column must be the primary key, because each row's id is how it is found again" + (f" (found {len(keys)})" if keys else ""))
    for c in cols:
        if c["logicalType"] == "number" and c["physicalType"] and c["physicalType"].lower().startswith("decimal") and not _DECIMAL.fullmatch(c["physicalType"]):
            problems.append(f"column {c['name']}: the physical type {c['physicalType']!r} must be written like decimal(12,2)")
        elif (c["logicalType"] == "number" and _DECIMAL.fullmatch(c["physicalType"] or "")
              and not 0 <= int(_DECIMAL.fullmatch(c["physicalType"]).group(2)) <= int(_DECIMAL.fullmatch(c["physicalType"]).group(1)) <= 38):
            problems.append(f"column {c['name']}: a decimal needs a precision from 1 to 38 and a scale from 0 up to it")

    profile = _profile(doc.get("customProperties"))
    if "erasable" in profile and not isinstance(profile["erasable"], bool):
        problems.append("erasable must be true or false")
    if "groupSizeThreshold" in profile:
        size = profile["groupSizeThreshold"]
        if not isinstance(size, int) or isinstance(size, bool) or not 2 <= size <= 100:
            problems.append("groupSizeThreshold must be a whole number from 2 to 100: the fewest different people a combination may hold")
    sensitive = [c["name"] for c in cols if c["classification"] in SENSITIVE]
    subject = subject_rule(doc)
    if erasable(doc):
        if sensitive and subject is None:
            problems.append("the table has " + ", ".join(sensitive) + " but the contract has no subject rule saying how to find the person each row is about")
    elif not profile.get("notErasableReason"):
        problems.append("a table that cannot be erased needs a notErasableReason saying why")
    if subject is not None:
        kind, found_by = subject.get("kind"), subject.get("foundBy")
        if not isinstance(kind, str) or not kind.strip():
            problems.append("the subject rule needs a kind: the system or number the identifier comes from")
        if found_by not in SUBJECT_RULES:
            problems.append(f"the subject rule's foundBy {found_by!r} must be one of {', '.join(SUBJECT_RULES)}")
        elif found_by == "column" and subject.get("column") not in names:
            problems.append(f"the subject rule names the column {subject.get('column')!r}, which the table does not have")
    return problems
