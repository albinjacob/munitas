"""The standards a de-identified result is held to, and the one function that judges a result against them.

A standard is the customer's decision, not the platform's: how much of what was found may be left in a result depends on who it is released to and on what kind of identifier it is.
So a standard is a set of limits, one overall and one for each kind of identifier, and a customer keeps several of them under names. The platform supplies the starting
values in `default_limits()` and puts nothing underneath them.

    {"overall_recall": 0.98,
     "types": {"PERSON": {"recall": null, "max_leaks": 0}, "DATE": {"recall": 0.9, "max_leaks": null}}}

  overall_recall   the share of identifiers that survived transcription and were removed, over all kinds together (null: no overall limit)
  types            for a kind of identifier: the least share removed (`recall`) and the most left in the clear (`max_leaks`). Either may be null. A kind that is not listed has no limit.

What is judged is the per-kind count a run records: `{"PERSON": {"total": 12, "destroyed": 1, "effective": 10, "leaked": 1}, ...}`. `destroyed` are the identifiers that the speech model had
already lost before the pipeline saw them, so a kind's recall is `effective / (total - destroyed)`: the pipeline is answerable for what it left, not for what was never there.

This file ships twice, because the API image and the worker are built from different folders (`worker/gate_standards.py` is an identical copy; scripts/check_pins_agree.py fails when they
differ). It has no dependencies, so it runs the same in both.
"""

from __future__ import annotations

GATE_RECALL = 0.98

# Kinds of identifier, in the order a page lists them, with the words a page uses. The first group names a person directly; the second narrows the field and identifies only in
# combination. A leak of the first group is the failure de-identification exists to prevent, which is why the starting standard allows none of them.
DIRECT = (
    ("PERSON", "A person's name"),
    ("MEDICAL_RECORD_NUMBER", "A record number issued by the organisation"),
    ("NATIONAL_ID", "A national identity number"),
    ("PHONE_NUMBER", "A phone number"),
    ("EMAIL", "An email address"),
)
QUASI = (
    ("DATE_OF_BIRTH", "A date of birth"),
    ("DATE", "A date"),
    ("ADDRESS", "An address or a place"),
    ("NUMBER", "Another number"),
)
LABELS = dict(DIRECT + QUASI)
DIRECT_KINDS = tuple(code for code, _ in DIRECT)
KINDS = tuple(LABELS)

# An answer key may name a name by the part the person plays (the person a file is about, a member of staff). For a standard that is one kind, a person's name: a reader of a standard does
# not care how the key happened to label it, and a limit on names must cover all of them. The counts a run keeps stay under the key's own labels; they are added together here.
ALIASES = {"PERSON_PATIENT": "PERSON", "PERSON_CLINICIAN": "PERSON"}
# Every label the scoring treats as naming a person directly, as the key writes them: the kinds above and the ones folded into them.
DIRECT_ENTITY_CODES = DIRECT_KINDS + tuple(ALIASES)


def merged(per_entity: dict) -> dict:
    """The counts for each kind a standard can limit: the labels in ALIASES are added to the kind they stand for."""
    out: dict = {}
    for code, stats in per_entity.items():
        into = out.setdefault(ALIASES.get(code, code), {"total": 0, "destroyed": 0, "effective": 0, "leaked": 0})
        for field in into:
            into[field] += stats[field]
    return out


def default_limits() -> dict:
    """The platform's starting values: the overall limit of the stated proposal, and no direct identifier left in the clear."""
    return {
        "overall_recall": GATE_RECALL,
        "types": {code: {"recall": None, "max_leaks": 0} for code in DIRECT_KINDS},
    }


def check_limits(limits: object) -> list[str]:
    """What is wrong with a limits object, in words. An empty list means it can be used."""
    if not isinstance(limits, dict):
        return ["the limits must be an object"]
    problems = []
    extra = sorted(set(limits) - {"overall_recall", "types"})
    if extra:
        problems.append("unknown limits: " + ", ".join(extra))
    overall = limits.get("overall_recall")
    if overall is not None and not _is_share(overall):
        problems.append("the overall recall must be a number from 0 to 1, or empty")
    types = limits.get("types", {})
    if not isinstance(types, dict):
        return problems + ["the limits for each kind of identifier must be an object"]
    for kind, limit in types.items():
        if kind not in LABELS:
            problems.append(f"{kind} is not a kind of identifier the platform measures")
            continue
        if not isinstance(limit, dict) or set(limit) - {"recall", "max_leaks"}:
            problems.append(f"{LABELS[kind]}: a limit holds only a recall and a most-left-in-the-clear")
            continue
        recall, most = limit.get("recall"), limit.get("max_leaks")
        if recall is not None and not _is_share(recall):
            problems.append(f"{LABELS[kind]}: the recall must be a number from 0 to 1, or empty")
        if most is not None and (isinstance(most, bool) or not isinstance(most, int) or most < 0):
            problems.append(f"{LABELS[kind]}: the most left in the clear must be a whole number, zero or more, or empty")
    return problems


def _is_share(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1


# The sentences a recommendation is made of. Both ways a result is judged (a standard, and the run's own two figures) use these, so one thing is never said two ways.
def pct(share: float) -> str:
    """A measured share, always with one decimal: 97.3%, 100.0%."""
    return f"{share * 100:.1f}%"


def asked(share: float) -> str:
    """A share a standard asks for, as a person would write it: 98%, 99.5%."""
    return f"{share * 100:.2f}".rstrip("0").rstrip(".") + "%"


def say_overall_short(recall: float, least: float, leaked: int, survived: int) -> str:
    return f"Removed {pct(recall)} of identifying detail, below the {asked(least)} asked ({leaked} of {survived} left in the clear)"


def say_kind_short(label: str, kind_recall: float, least: float, leaked: int, kept: int) -> str:
    return f"{label.lower()}: removed {pct(kind_recall)}, below the {asked(least)} asked ({leaked} of {kept} left in the clear)"


def say_too_many(label: str | None, leaked: int, most: int) -> str:
    allowed = "none are allowed" if most == 0 else f"at most {most} are allowed"
    return f"{label.lower()}: {leaked} left in the clear, and {allowed}" if label else f"{leaked} that name a person directly left in the clear, and {allowed}"


def say_clear(recall: float, direct: int, other: int) -> str:
    return f"Removed {pct(recall)} of identifying detail. Left in the clear: {direct} that name a person directly, {other} that identify only in combination."


def evaluate(per_entity: dict, limits: dict) -> tuple[bool, str]:
    """Judge a run's per-kind counts against a limits object. Returns whether it passes, and why in one sentence: every limit that was missed, or the figures if none was."""
    per_entity = merged(per_entity)
    survived = sum(s["total"] - s["destroyed"] for s in per_entity.values())
    effective = sum(s["effective"] for s in per_entity.values())
    leaked = sum(s["leaked"] for s in per_entity.values())
    recall = effective / survived if survived else 0.0
    reasons = []

    overall = limits.get("overall_recall")
    if overall is not None and recall < overall:
        reasons.append(say_overall_short(recall, overall, leaked, survived))

    for kind in KINDS:
        limit = (limits.get("types") or {}).get(kind)
        stats = per_entity.get(kind)
        if not limit or not stats:
            continue
        kept = stats["total"] - stats["destroyed"]
        kind_recall = stats["effective"] / kept if kept else 1.0
        least, most = limit.get("recall"), limit.get("max_leaks")
        if least is not None and kind_recall < least:
            reasons.append(say_kind_short(LABELS[kind], kind_recall, least, stats["leaked"], kept))
        if most is not None and stats["leaked"] > most:
            reasons.append(say_too_many(LABELS[kind], stats["leaked"], most))

    if reasons:
        return False, "; ".join(reasons)
    direct = sum(per_entity.get(k, {}).get("leaked", 0) for k in DIRECT_KINDS)
    return True, say_clear(recall, direct, leaked - direct)
