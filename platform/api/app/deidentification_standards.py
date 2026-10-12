"""De-identification standards: the named standards an organisation judges a de-identified result by, and the judgement of a result under one of them.

What a standard is, and why it is the customer's to set, is in `gate_standards.py` (platform/gate_standards.py). This module keeps them. A de-identification standard is a name an organisation gives to a set of limits
("Public release", "Project A, closed research team"). Each change adds a version and nothing is edited, with who made it, when and why, so a decision can say which standard it was judged by
and what that standard said at the time. The platform's own starting values are not stored: they are the built-in standard, "Platform default", and are what a run's own recommendation is made under.

A result is judged under a standard when somebody decides to release it, from the counts for each kind of identifier that the run recorded. A result that nothing measured stays "not measured"
under every standard, because no standard can make a measurement that was never taken. A result measured before those counts were kept can only be judged by the built-in standard, and says so.
"""

from __future__ import annotations

import json
import uuid
from datetime import timezone

import gate_standards

from . import db

DEFAULT_NAME = "Platform default"


class NotJudgeable(Exception):
    """A result cannot be judged under the chosen standard, and the reason is a sentence for the person."""


class NameTaken(Exception):
    pass


def default_standard() -> dict:
    """The built-in standard, in the shape a stored one has, so a page can show them the same way."""
    return {"id": None, "name": DEFAULT_NAME, "version": None, "limits": gate_standards.default_limits(), "reason": None, "set_by": None, "set_by_label": None, "set_at": None,
            "built_in": True, "versions": 0}


def kinds() -> list[dict]:
    """The kinds of identifier a standard can limit, with the words a page uses and whether the kind names a person directly."""
    return [{"code": code, "label": label, "group": "name a person directly"} for code, label in gate_standards.DIRECT] + \
           [{"code": code, "label": label, "group": "identify only in combination"} for code, label in gate_standards.QUASI]


def normalised(limits: dict) -> dict:
    """A limits object with every limit present, an unset one as null, and only the kinds the platform measures."""
    types = {}
    for code in gate_standards.KINDS:
        limit = (limits.get("types") or {}).get(code)
        if limit and (limit.get("recall") is not None or limit.get("max_leaks") is not None):
            types[code] = {"recall": limit.get("recall"), "max_leaks": limit.get("max_leaks")}
    return {"overall_recall": limits.get("overall_recall"), "types": types}


def _iso(moment) -> str | None:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if moment else None


_LATEST = """select p.id, p.name, p.created_by, p.created_at, v.version, v.limits, v.reason, v.set_by, v.set_at, who.label as set_by_label,
                    (select count(*) from deidentification_standard_version where standard_id = p.id) as versions
               from deidentification_standard p
               join deidentification_standard_version v on v.standard_id = p.id and v.version = (select max(version) from deidentification_standard_version where standard_id = p.id)
               left join directory who on who.id = v.set_by
              where p.tenant_id = %s"""


def _shown(row: dict) -> dict:
    return {"id": str(row["id"]), "name": row["name"], "version": row["version"], "limits": row["limits"], "reason": row["reason"], "set_by": row["set_by"],
            "set_by_label": row["set_by_label"], "set_at": _iso(row["set_at"]), "built_in": False, "versions": row["versions"]}


def list_standards(tenant_id: str) -> list[dict]:
    """Every de-identification standard of the organisation at its latest version, by name."""
    return [_shown(r) for r in db.all_rows(_LATEST + " order by lower(p.name)", (tenant_id,))]


def latest(tenant_id: str, standard_id: str) -> dict | None:
    try:
        uuid.UUID(str(standard_id))
    except ValueError:
        return None
    row = db.one(_LATEST + " and p.id = %s", (tenant_id, standard_id))
    return _shown(row) if row else None


def history(tenant_id: str, standard_id: str) -> list[dict]:
    """Every version of one standard, newest first, each with who made it, when and why."""
    return [{"version": r["version"], "limits": r["limits"], "reason": r["reason"], "set_by": r["set_by"], "set_by_label": r["set_by_label"], "set_at": _iso(r["set_at"])}
            for r in db.all_rows(
                """select v.version, v.limits, v.reason, v.set_by, v.set_at, who.label as set_by_label
                     from deidentification_standard_version v join deidentification_standard p on p.id = v.standard_id
                     left join directory who on who.id = v.set_by
                    where p.tenant_id = %s and p.id = %s order by v.version desc""", (tenant_id, standard_id))]


def create(tenant_id: str, name: str, limits: dict, reason: str, who: str) -> dict:
    """A new de-identification standard, at version 1. The name is the organisation's own and is never changed afterwards."""
    name = " ".join(name.split())
    if name.lower() == DEFAULT_NAME.lower():
        raise NameTaken(f"{DEFAULT_NAME} is the platform's own standard and its name cannot be used for another")
    new = str(uuid.uuid4())
    with db.cursor(commit=True) as cur:
        cur.execute("select 1 from deidentification_standard where tenant_id = %s and lower(name) = lower(%s)", (tenant_id, name))
        if cur.fetchone():
            raise NameTaken(f"this organisation already has a de-identification standard called {name}")
        cur.execute("insert into deidentification_standard (id, tenant_id, name, created_by) values (%s, %s, %s, %s)", (new, tenant_id, name, who))
        cur.execute("insert into deidentification_standard_version (standard_id, version, tenant_id, limits, reason, set_by) values (%s, 1, %s, %s, %s, %s)",
                    (new, tenant_id, json.dumps(normalised(limits)), reason, who))
    return latest(tenant_id, new)


def add_version(tenant_id: str, standard_id: str, limits: dict, reason: str, who: str) -> dict | None:
    """The next version of a de-identification standard, or None when the organisation has no such standard. Earlier versions stay as they were."""
    if latest(tenant_id, standard_id) is None:
        return None
    with db.cursor(commit=True) as cur:
        cur.execute("select coalesce(max(version), 0) + 1 as next from deidentification_standard_version where standard_id = %s", (standard_id,))
        next_version = cur.fetchone()["next"]
        cur.execute("insert into deidentification_standard_version (standard_id, version, tenant_id, limits, reason, set_by) values (%s, %s, %s, %s, %s, %s)",
                    (standard_id, next_version, tenant_id, json.dumps(normalised(limits)), reason, who))
    return latest(tenant_id, standard_id)


def judge(decision: dict, standard: dict | None) -> dict:
    """The recommendation for a pending result under a de-identification standard (None for the built-in one), with the standard and the figures it rests on.

    Under the built-in standard this is what the run recorded. Under another it is worked out again, here, from the counts the run kept: nothing the caller sends is believed.
    """
    chosen = standard or default_standard()
    per_entity = (decision.get("metrics") or {}).get("per_entity")
    # `counted` says whether the result carries counts for each kind of identifier. A result without them (a pipeline's own check, a result measured before they were kept, one
    # nothing measured) is not judged by a standard of identifying detail, and no standard is recorded for it when it is decided.
    out = {"standard": {k: chosen[k] for k in ("id", "name", "version", "limits", "built_in")}, "counted": bool(per_entity)}
    if standard is None:
        return {**out, "recommendation": decision["recommendation"], "reason": decision["recommendation_reason"], "worked_out": False}
    if decision["recommendation"] == "not_measured":
        return {**out, "recommendation": "not_measured", "reason": decision["recommendation_reason"], "worked_out": False}
    if not per_entity:
        raise NotJudgeable("This result has no counts for each kind of identifier, so a de-identification standard cannot judge it. It keeps the recommendation the pipeline gave it.")
    passed, reason = gate_standards.evaluate(per_entity, chosen["limits"])
    return {**out, "recommendation": "pass" if passed else "fail", "reason": reason, "worked_out": True}
