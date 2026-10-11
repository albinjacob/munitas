"""Release bars: the named bars an organisation judges a de-identified result by, and the judgement of a result under one of them.

What a bar is, and why it is the customer's to set, is in `gate_bars.py` (platform/gate_bars.py). This module keeps them. A release bar is a name an organisation gives to a set of limits
("Public release", "Project A, closed research team"). Each change adds a version and nothing is edited, with who made it, when and why, so a decision can say which bar it was judged by
and what that bar said at the time. The platform's own starting values are not stored: they are the built-in bar, "Platform default", and are what a run's own recommendation is made under.

A result is judged under a bar when somebody decides to release it, from the counts for each kind of identifier that the run recorded. A result that nothing measured stays "not measured"
under every bar, because no bar can make a measurement that was never taken. A result measured before those counts were kept can only be judged by the built-in bar, and says so.
"""

from __future__ import annotations

import json
import uuid
from datetime import timezone

import gate_bars

from . import db

DEFAULT_NAME = "Platform default"


class NotJudgeable(Exception):
    """A result cannot be judged under the chosen bar, and the reason is a sentence for the person."""


class NameTaken(Exception):
    pass


def default_bar() -> dict:
    """The built-in bar, in the shape a stored one has, so a page can show them the same way."""
    return {"id": None, "name": DEFAULT_NAME, "version": None, "bars": gate_bars.default_bars(), "reason": None, "set_by": None, "set_by_label": None, "set_at": None,
            "built_in": True, "versions": 0}


def kinds() -> list[dict]:
    """The kinds of identifier a bar can limit, with the words a page uses and whether the kind names a person directly."""
    return [{"code": code, "label": label, "group": "name a person directly"} for code, label in gate_bars.DIRECT] + \
           [{"code": code, "label": label, "group": "identify only in combination"} for code, label in gate_bars.QUASI]


def normalised(bars: dict) -> dict:
    """A bars object with every limit present, an unset one as null, and only the kinds the platform measures."""
    types = {}
    for code in gate_bars.KINDS:
        limit = (bars.get("types") or {}).get(code)
        if limit and (limit.get("recall") is not None or limit.get("max_leaks") is not None):
            types[code] = {"recall": limit.get("recall"), "max_leaks": limit.get("max_leaks")}
    return {"overall_recall": bars.get("overall_recall"), "types": types}


def _iso(moment) -> str | None:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if moment else None


_LATEST = """select p.id, p.name, p.created_by, p.created_at, v.version, v.bars, v.reason, v.set_by, v.set_at, who.label as set_by_label,
                    (select count(*) from gate_purpose_version where purpose_id = p.id) as versions
               from gate_purpose p
               join gate_purpose_version v on v.purpose_id = p.id and v.version = (select max(version) from gate_purpose_version where purpose_id = p.id)
               left join directory who on who.id = v.set_by
              where p.tenant_id = %s"""


def _shown(row: dict) -> dict:
    return {"id": str(row["id"]), "name": row["name"], "version": row["version"], "bars": row["bars"], "reason": row["reason"], "set_by": row["set_by"],
            "set_by_label": row["set_by_label"], "set_at": _iso(row["set_at"]), "built_in": False, "versions": row["versions"]}


def list_bars(tenant_id: str) -> list[dict]:
    """Every release bar of the organisation at its latest version, by name."""
    return [_shown(r) for r in db.all_rows(_LATEST + " order by lower(p.name)", (tenant_id,))]


def latest(tenant_id: str, purpose_id: str) -> dict | None:
    try:
        uuid.UUID(str(purpose_id))
    except ValueError:
        return None
    row = db.one(_LATEST + " and p.id = %s", (tenant_id, purpose_id))
    return _shown(row) if row else None


def history(tenant_id: str, purpose_id: str) -> list[dict]:
    """Every version of one bar, newest first, each with who made it, when and why."""
    return [{"version": r["version"], "bars": r["bars"], "reason": r["reason"], "set_by": r["set_by"], "set_by_label": r["set_by_label"], "set_at": _iso(r["set_at"])}
            for r in db.all_rows(
                """select v.version, v.bars, v.reason, v.set_by, v.set_at, who.label as set_by_label
                     from gate_purpose_version v join gate_purpose p on p.id = v.purpose_id
                     left join directory who on who.id = v.set_by
                    where p.tenant_id = %s and p.id = %s order by v.version desc""", (tenant_id, purpose_id))]


def create(tenant_id: str, name: str, bars: dict, reason: str, who: str) -> dict:
    """A new release bar, at version 1. The name is the organisation's own and is never changed afterwards."""
    name = " ".join(name.split())
    if name.lower() == DEFAULT_NAME.lower():
        raise NameTaken(f"{DEFAULT_NAME} is the platform's own bar and its name cannot be used for another")
    new = str(uuid.uuid4())
    with db.cursor(commit=True) as cur:
        cur.execute("select 1 from gate_purpose where tenant_id = %s and lower(name) = lower(%s)", (tenant_id, name))
        if cur.fetchone():
            raise NameTaken(f"this organisation already has a release bar called {name}")
        cur.execute("insert into gate_purpose (id, tenant_id, name, created_by) values (%s, %s, %s, %s)", (new, tenant_id, name, who))
        cur.execute("insert into gate_purpose_version (purpose_id, version, tenant_id, bars, reason, set_by) values (%s, 1, %s, %s, %s, %s)",
                    (new, tenant_id, json.dumps(normalised(bars)), reason, who))
    return latest(tenant_id, new)


def add_version(tenant_id: str, purpose_id: str, bars: dict, reason: str, who: str) -> dict | None:
    """The next version of a release bar, or None when the organisation has no such bar. Earlier versions stay as they were."""
    if latest(tenant_id, purpose_id) is None:
        return None
    with db.cursor(commit=True) as cur:
        cur.execute("select coalesce(max(version), 0) + 1 as next from gate_purpose_version where purpose_id = %s", (purpose_id,))
        next_version = cur.fetchone()["next"]
        cur.execute("insert into gate_purpose_version (purpose_id, version, tenant_id, bars, reason, set_by) values (%s, %s, %s, %s, %s, %s)",
                    (purpose_id, next_version, tenant_id, json.dumps(normalised(bars)), reason, who))
    return latest(tenant_id, purpose_id)


def judge(decision: dict, bar: dict | None) -> dict:
    """The recommendation for a pending result under a release bar (None for the built-in one), with the bar and the figures it rests on.

    Under the built-in bar this is what the run recorded. Under another it is worked out again, here, from the counts the run kept: nothing the caller sends is believed.
    """
    chosen = bar or default_bar()
    per_entity = (decision.get("metrics") or {}).get("per_entity")
    # `counted` says whether the result carries counts for each kind of identifier. A result without them (a pipeline's own check, a result measured before they were kept, one
    # nothing measured) is not judged by a bar of identifying detail, and no bar is recorded for it when it is decided.
    out = {"bar": {k: chosen[k] for k in ("id", "name", "version", "bars", "built_in")}, "counted": bool(per_entity)}
    if bar is None:
        return {**out, "recommendation": decision["recommendation"], "reason": decision["recommendation_reason"], "worked_out": False}
    if decision["recommendation"] == "not_measured":
        return {**out, "recommendation": "not_measured", "reason": decision["recommendation_reason"], "worked_out": False}
    if not per_entity:
        raise NotJudgeable("This result has no counts for each kind of identifier, so a release bar cannot judge it. It keeps the recommendation the pipeline gave it.")
    passed, reason = gate_bars.evaluate(per_entity, chosen["bars"])
    return {**out, "recommendation": "pass" if passed else "fail", "reason": reason, "worked_out": True}
