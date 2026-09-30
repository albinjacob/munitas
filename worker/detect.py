"""The detection ensemble.

Three detectors that fail differently, reconciled by span overlap. The
ensemble is the design rather than a hedge, and the reason is specific: Presidio is deterministic and auditable line by line but
blind to anything its patterns do not describe; a transformer NER model
generalises but has no idea what a medical record number is; GLiNER will find
span types nobody enumerated but is the least predictable of the three.

Agreement between two of them is a much stronger signal than confidence from any
one of them, because their errors are not correlated. That is the whole argument
for paying three times the compute.

Models load lazily and can be released, because 8 GB of VRAM will not hold these
alongside Whisper.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config

# The labels the pipeline cares about, mapped onto each detector's vocabulary.
# Kept explicit rather than inferred, because a silent mapping failure looks
# exactly like a detector that found nothing.
ENTITY_MAP_PRESIDIO = {
    "PERSON": "PERSON",
    "PHONE_NUMBER": "PHONE_NUMBER",
    "EMAIL_ADDRESS": "EMAIL",
    "DATE_TIME": "DATE",
    "LOCATION": "ADDRESS",
    "UK_NHS": "NATIONAL_ID",
    "US_SSN": "NATIONAL_ID",
    "MEDICAL_LICENSE": "MEDICAL_RECORD_NUMBER",
}

ENTITY_MAP_SPACY = {
    "PERSON": "PERSON",
    "GPE": "ADDRESS",
    "LOC": "ADDRESS",
    "FAC": "ADDRESS",
    "DATE": "DATE",
    "CARDINAL": "NUMBER",
}

GLINER_LABELS = [
    "person name", "date of birth", "medical record number",
    "phone number", "home address", "email address", "national id number",
]

ENTITY_MAP_GLINER = {
    "person name": "PERSON",
    "date of birth": "DATE_OF_BIRTH",
    "medical record number": "MEDICAL_RECORD_NUMBER",
    "phone number": "PHONE_NUMBER",
    "home address": "ADDRESS",
    "email address": "EMAIL",
    "national id number": "NATIONAL_ID",
}


@dataclass
class Candidate:
    """A span one or more detectors proposed."""

    start: int
    end: int
    text: str
    entity: str
    detectors: dict[str, float] = field(default_factory=dict)

    @property
    def votes(self) -> int:
        return len(self.detectors)

    @property
    def confidence(self) -> float:
        """Mean score, lifted by agreement.

        Agreement is worth more than any single score, so two detectors at 0.6
        outranks one at 0.9. The multiplier is a judgement call and is stated
        here rather than buried, because it is exactly the kind of constant that
        should be tuned against the ground truth in slice 3's evaluation rather
        than trusted.
        """
        if not self.detectors:
            return 0.0
        mean = sum(self.detectors.values()) / len(self.detectors)
        return min(1.0, mean * (1.0 + 0.25 * (self.votes - 1)))

    def as_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "entity": self.entity,
            "detectors": self.detectors,
            "votes": self.votes,
            "confidence": round(self.confidence, 4),
        }


class Ensemble:
    def __init__(self) -> None:
        self._presidio = None
        self._spacy = None
        self._gliner = None

    # Loading is separated from use so a caller can control when VRAM is taken.
    def load(self) -> None:
        import spacy
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        self._spacy = spacy.load(config.SPACY_MODEL)

        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": config.SPACY_MODEL}],
        })
        self._presidio = AnalyzerEngine(nlp_engine=provider.create_engine())

        from gliner import GLiNER
        self._gliner = GLiNER.from_pretrained(config.GLINER_MODEL)

    def release(self) -> None:
        """Drop the models so Whisper can have the VRAM back."""
        self._presidio = self._spacy = self._gliner = None
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def detect(self, text: str) -> list[Candidate]:
        raw: list[tuple[str, int, int, str, float]] = []
        raw.extend(self._run_presidio(text))
        raw.extend(self._run_spacy(text))
        raw.extend(self._run_gliner(text))
        return reconcile(raw, text)

    def _run_presidio(self, text: str) -> list[tuple[str, int, int, str, float]]:
        out = []
        for result in self._presidio.analyze(text=text, language="en"):
            entity = ENTITY_MAP_PRESIDIO.get(result.entity_type)
            if entity:
                out.append(("presidio", result.start, result.end, entity, float(result.score)))
        return out

    def _run_spacy(self, text: str) -> list[tuple[str, int, int, str, float]]:
        out = []
        for ent in self._spacy(text).ents:
            entity = ENTITY_MAP_SPACY.get(ent.label_)
            if entity:
                # spaCy exposes no per-entity probability, so a flat value is
                # used. Inventing a number that looked calibrated would be worse
                # than admitting there is not one.
                out.append(("spacy", ent.start_char, ent.end_char, entity, 0.70))
        return out

    def _run_gliner(self, text: str) -> list[tuple[str, int, int, str, float]]:
        out = []
        for ent in self._gliner.predict_entities(text, GLINER_LABELS, threshold=0.35):
            entity = ENTITY_MAP_GLINER.get(ent["label"])
            if entity:
                out.append(("gliner", ent["start"], ent["end"], entity, float(ent["score"])))
        return out


def reconcile(
    raw: list[tuple[str, int, int, str, float]], text: str
) -> list[Candidate]:
    """Merge overlapping spans from different detectors.

    Two detectors rarely agree on boundaries: one takes the title, another does
    not. Overlap is therefore the merge criterion rather than exact equality,
    and the merged span takes the union of the boundaries. Taking the union
    rather than the intersection is a deliberate bias towards over-masking,
    since a leaked identifier is a breach and an over-masked word is an
    annoyance.
    """
    merged: list[Candidate] = []

    for detector, start, end, entity, score in sorted(raw, key=lambda r: (r[1], r[2])):
        hit = None
        for candidate in merged:
            if start < candidate.end and candidate.start < end:
                # PERSON is deliberately permissive here: a detector calling
                # something PERSON and another calling it DATE_OF_BIRTH means
                # one of them is wrong, and merging them would hide that.
                if _compatible(candidate.entity, entity):
                    hit = candidate
                    break

        if hit is None:
            merged.append(Candidate(start, end, text[start:end], entity, {detector: score}))
        else:
            hit.start = min(hit.start, start)
            hit.end = max(hit.end, end)
            hit.text = text[hit.start:hit.end]
            hit.entity = _specific(hit.entity, entity)
            hit.detectors[detector] = max(hit.detectors.get(detector, 0.0), score)

    return sorted(merged, key=lambda c: c.start)


# A generic label and a specific one describing the same thing are compatible,
# and the specific one wins. PERSON and MEDICAL_RECORD_NUMBER are not.
_FAMILIES = {
    "PERSON": {"PERSON", "PERSON_PATIENT", "PERSON_CLINICIAN"},
    "DATE": {"DATE", "DATE_OF_BIRTH"},
    "NUMBER": {"NUMBER", "MEDICAL_RECORD_NUMBER", "NATIONAL_ID", "PHONE_NUMBER"},
    "ADDRESS": {"ADDRESS"},
    "EMAIL": {"EMAIL"},
}

_SPECIFICITY = {
    "NUMBER": 0, "DATE": 1, "PERSON": 1, "ADDRESS": 1, "EMAIL": 1,
    "PERSON_PATIENT": 2, "PERSON_CLINICIAN": 2, "DATE_OF_BIRTH": 2,
    "MEDICAL_RECORD_NUMBER": 2, "NATIONAL_ID": 2, "PHONE_NUMBER": 2,
}


def _family(entity: str) -> str | None:
    for name, members in _FAMILIES.items():
        if entity in members:
            return name
    return None


def _compatible(a: str, b: str) -> bool:
    return a == b or (_family(a) is not None and _family(a) == _family(b))


def _specific(a: str, b: str) -> str:
    return a if _SPECIFICITY.get(a, 0) >= _SPECIFICITY.get(b, 0) else b
