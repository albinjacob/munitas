"""Scoring a de-identification run against ground truth.

Shared by the pipeline's `verify` activity and by the verification scripts, so
the number the promotion gate refuses on is the same number the audit reports.
Two implementations that ought to agree is exactly the situation this project
keeps finding bugs in, so there is one.

The measurement is by alignment and interval overlap, never by string
comparison. Three earlier matchers each produced a confident and wrong answer,
in different directions, because a string comparison cannot distinguish:

  * the detector missed the identifier
  * the detector found part of it, leaving the rest in the clear
  * speech recognition destroyed it, so no detector could have found it

Those need different responses. The first is a detector problem, the second a
span-boundary problem, and the third is not a detector problem at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .align import Alignment, coverage

# A detector must cover this much of an identifier for the span itself to count.
FULL_COVERAGE = 0.9

# Words that identify nobody. When everything a detector left uncovered comes
# from this set, the identifier was effectively removed and the shortfall is an
# artefact of where the span boundary fell, not a leak.
#
# Written out rather than hidden behind a threshold, because it is a judgement
# and someone should be able to disagree with it. Place names are deliberately
# absent: a town narrows a person down.
HARMLESS_RESIDUE = {
    "dr", "doctor", "mr", "mrs", "ms", "miss", "prof", "professor",
    "at", "dot", "the", "and", "of", "a", "an",
    "example", "invalid", "com", "co", "uk", "org", "net", "email",
}

# Direct identifiers name a person outright. Quasi-identifiers narrow the field
# and only identify in combination. The gate treats them differently because
# leaving a town in a transcript and leaving a patient's name in one are not the
# same failure.
DIRECT = {
    "PERSON", "PERSON_PATIENT", "PERSON_CLINICIAN",
    "MEDICAL_RECORD_NUMBER", "NATIONAL_ID", "PHONE_NUMBER", "EMAIL",
}


def residue_is_harmless(residue: str) -> bool:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", residue.casefold()) if w]
    if not words:
        return True
    return all(w in HARMLESS_RESIDUE for w in words)


def uncovered_text(text: str, target: tuple[int, int],
                   spans: list[tuple[int, int]]) -> str:
    start, end = target
    covered = [False] * (end - start)
    for s, e in spans:
        for i in range(max(s, start), min(e, end)):
            covered[i - start] = True
    return "".join(text[start + i] for i, hit in enumerate(covered) if not hit).strip()


@dataclass
class ScoreCard:
    total: int = 0
    destroyed: int = 0
    covered: int = 0          # span coverage at or above FULL_COVERAGE
    effective: int = 0        # identifier removed, allowing harmless residue
    leaked: int = 0           # identifying text left in the clear
    missed: int = 0           # survived and not found at all
    direct_leaks: int = 0
    quasi_leaks: int = 0
    per_entity: dict = field(default_factory=dict)
    leak_detail: list = field(default_factory=list)
    similarity: float = 0.0

    @property
    def survived(self) -> int:
        return self.total - self.destroyed

    @property
    def recall_effective(self) -> float:
        """Of the identifiers that survived ASR, how many were removed.

        This is the detector's own performance, with transcription failures
        taken out. It is the number the gate should reason about, because a
        pipeline cannot be held responsible for what the speech model deleted,
        only for what it left behind.
        """
        return self.effective / self.survived if self.survived else 0.0

    @property
    def recall_spoken(self) -> float:
        """Of everything actually spoken, how many were removed."""
        return self.effective / self.total if self.total else 0.0

    @property
    def destruction_rate(self) -> float:
        return self.destroyed / self.total if self.total else 0.0

    def as_metrics(self) -> dict:
        return {
            "ground_truth_spans": self.total,
            "destroyed_by_asr": self.destroyed,
            "destruction_rate": round(self.destruction_rate, 4),
            "survived_asr": self.survived,
            "recall_effective": round(self.recall_effective, 4),
            "recall_spoken": round(self.recall_spoken, 4),
            "recall_strict_coverage": round(
                self.covered / self.survived if self.survived else 0.0, 4),
            "leaks_total": self.leaked,
            "leaks_direct": self.direct_leaks,
            "leaks_quasi": self.quasi_leaks,
            "not_found_at_all": self.missed,
            "asr_similarity": round(self.similarity, 4),
        }


def score_record(card: ScoreCard, reference: str, asr: str,
                 truth_spans: list[dict], candidates: list[dict],
                 record_id: str) -> None:
    """Accumulate one record into the score card.

    `record_id` names which record a leak came from. Together with the span's
    own offsets it addresses a leak without naming the person in it, which is
    what goes to MLflow: see `pseudonymised`.
    """
    alignment = Alignment(reference, asr)
    candidate_spans = [(c["start"], c["end"]) for c in candidates]

    for span in truth_spans:
        entity = span["entity"]
        stats = card.per_entity.setdefault(
            entity, {"total": 0, "destroyed": 0, "effective": 0, "leaked": 0})
        card.total += 1
        stats["total"] += 1

        mapped = alignment.map_span(span["start"], span["end"])
        if not mapped.survived:
            card.destroyed += 1
            stats["destroyed"] += 1
            continue

        target = (mapped.asr_start, mapped.asr_end)
        covered = coverage(target, candidate_spans)

        if covered >= FULL_COVERAGE:
            card.covered += 1
            card.effective += 1
            stats["effective"] += 1
            continue

        if covered <= 0:
            card.missed += 1
            card.leaked += 1
            stats["leaked"] += 1
            _record_leak(card, entity, span, asr[target[0]:target[1]], 0.0,
                         record_id)
            continue

        residue = uncovered_text(asr, target, candidate_spans)
        if residue_is_harmless(residue):
            card.effective += 1
            stats["effective"] += 1
        else:
            card.leaked += 1
            stats["leaked"] += 1
            _record_leak(card, entity, span, residue, covered, record_id)


def _record_leak(card: ScoreCard, entity: str, span: dict,
                 residue: str, covered: float, record_id: str) -> None:
    if entity in DIRECT:
        card.direct_leaks += 1
    else:
        card.quasi_leaks += 1
    if len(card.leak_detail) < 50:
        card.leak_detail.append({
            # The coordinate, which names this leak without naming the person.
            # Stable across runs because the ground truth does not change
            # between them, so two runs can be diffed leak by leak.
            "record_id": record_id,
            "span_start": span["start"],
            "span_end": span["end"],
            "entity": entity,
            "identifier": span["text"],
            "left_in_the_clear": residue,
            "coverage": round(covered, 3),
            "direct": entity in DIRECT,
        })


def pseudonymised(leak_detail: list[dict]) -> list[dict]:
    """The leak list with the personal data taken out.

    MLflow in this deployment runs unauthenticated on a published port, holds
    the storage admin key as its own credentials, and writes to one shared
    bucket for every tenant. The identifier and the residue are the personal
    data de-identification exists to remove, so they do not go there. The full
    detail goes to Postgres instead, under the tenant that owns it, where the
    reviewer who has to judge severity can reach it and nobody else can.

    What remains is what comparing two runs actually needs: which leak this is,
    what kind of thing it was, and how the redaction failed. Every leak cause
    found so far was diagnosed at exactly this level.

    To be plain about the limit: a coordinate is still linkable to a person by
    anyone holding the corpus. This is pseudonymisation, not anonymisation, and
    the result stays personal data. What it buys is that linking requires
    access to governed data rather than only the ability to reach a port.
    """
    return [
        {
            "record_id": leak["record_id"],
            "span_start": leak["span_start"],
            "span_end": leak["span_end"],
            "entity": leak["entity"],
            "coverage": leak["coverage"],
            "direct": leak["direct"],
            "failure": _failure_shape(leak),
        }
        for leak in leak_detail
    ]


def _failure_shape(leak: dict) -> str:
    """How the redaction failed, described without quoting what survived."""
    if leak["coverage"] <= 0:
        return "not detected at all"
    kept = len(leak["left_in_the_clear"].split())
    if kept <= 1:
        return "partly redacted, one token survived"
    return f"partly redacted, {kept} tokens survived"


# --------------------------------------------------------------- the gate --

# The promotion gate. Two conditions, not one, because an average hides the case that
# matters: a run can reach high recall while still leaving a handful of names in
# the clear, and a name in the clear is the failure de-identification exists to
# prevent.
#
# These values are a proposal and should be argued with. The recall figure is
# set above the 0.95 the pipeline currently reaches, so the gate refuses until
# the span-boundary problem is fixed rather than being tuned to pass today.
GATE_RECALL = 0.98
GATE_DIRECT_LEAKS = 0


def evaluate_gate(card: ScoreCard,
                  recall_threshold: float = GATE_RECALL,
                  max_direct_leaks: int = GATE_DIRECT_LEAKS) -> tuple[bool, str]:
    """Decide whether a version may be promoted, and say why if not."""
    reasons = []
    if card.recall_effective < recall_threshold:
        reasons.append(
            f"recall {card.recall_effective:.3f} is below {recall_threshold:.3f} "
            f"({card.leaked} of {card.survived} surviving identifiers left in the clear)"
        )
    if card.direct_leaks > max_direct_leaks:
        reasons.append(
            f"{card.direct_leaks} direct identifiers leaked, limit is {max_direct_leaks}"
        )
    if reasons:
        return False, "; ".join(reasons)
    return True, (
        f"recall {card.recall_effective:.3f}, {card.direct_leaks} direct leaks, "
        f"{card.quasi_leaks} quasi-identifier leaks"
    )
