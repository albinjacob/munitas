"""Surrogate substitution, in text and in audio.

Surrogates, not silence. The argument is worth stating because the cheap
option looks safer and is not.

Silencing a name leaves a hole. The hole is audible, its duration correlates with
the length of the name, and a model trained on silenced audio learns that a
particular acoustic gap precedes the word "was admitted". Worse, the pattern of
holes is itself information: a recording with eleven holes is a recording of
someone whose name came up eleven times. Replacing the name with a different
name of similar duration leaves the recording sounding like a recording.

Two rules govern the substitution, and they are in tension on purpose:

  * **Consistent within a record.** If Aoife Brennan appears six times she
    becomes the same surrogate all six times, or the transcript stops being a
    coherent conversation and becomes useless for training dialogue models.

  * **Inconsistent across records.** The same real name in two encounters
    becomes two different surrogates. Consistency across the corpus would
    rebuild a pseudonymous identifier: an attacker could count occurrences and
    link records, which is precisely what de-identification is meant to prevent.

The mapping from real value to surrogate is never stored. It exists only in
memory for the duration of one record. Storing it would create exactly the
re-identification key the whole exercise exists to destroy, and a stored mapping
is one leaked backup away from undoing every guarantee above it.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

SURROGATE_GIVEN = [
    "Martin", "Clara", "Peter", "Ruth", "Daniel", "Helen", "Simon", "Alice",
    "George", "Naomi", "Owen", "Freya", "Colin", "Iris", "Malcolm", "Tessa",
]
SURROGATE_FAMILY = [
    "Hartley", "Downes", "Mercer", "Kelsall", "Bramley", "Ottway", "Rennick",
    "Salter", "Vaughan", "Peckham", "Ludlow", "Ashby", "Corbin", "Tarrant",
]
SURROGATE_STREETS = [
    "Hazelmere Avenue", "Dunstan Road", "Corfield Street", "Whitmoor Lane",
    "Brackenbury Way", "Elmsleigh Close",
]
SURROGATE_TOWNS = [
    "Netherton", "Farnley Hill", "Ockbrook", "Wrenbury", "Stavely", "Ilderton",
]


def is_identifying_date(text: str) -> bool:
    """Is this a date that identifies someone, or a duration that describes care?

    This exists because of a real failure. Presidio's DATE_TIME fires on "today",
    "three weeks", "every morning" and "the last three", and substituting a date
    surrogate for those turned "it's been going on about three weeks" into
    "it's been going on 14 November 1945". That is not over-masking, which the
    design accepts as the safer error. It is fabrication: it destroys the
    clinical fact and replaces it with a false one that reads as true.

    So a date is only treated as an identifier when it carries a four-digit
    year. A bare year is the part that narrows an individual; a duration is
    care, and the reason the record was written.

    The cost is stated rather than hidden: "born on the fourth of March" with no
    year spoken will now pass through. That is a real gap, and the right fix is
    a detector that understands the difference rather than a regular expression
    that approximates it.
    """
    return bool(_YEAR.search(text))


_YEAR = __import__("re").compile(r"\b(19|20)\d{2}\b")


def should_redact(span: dict) -> tuple[bool, str]:
    """Decide whether a candidate is an identifier worth substituting."""
    entity = span["entity"]
    text = span["text"]

    if entity in ("DATE", "DATE_OF_BIRTH") and not is_identifying_date(text):
        return False, "date without a year, so a duration rather than an identifier"

    # A bare number that no detector could type is not evidence of anything.
    if entity == "NUMBER":
        return False, "untyped number"

    if len(text.strip()) < 2:
        return False, "too short to identify"

    return True, "identifier"


@dataclass
class Substitution:
    start: int
    end: int
    original_length: int
    surrogate: str
    entity: str

    def as_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "original_length": self.original_length,
            "surrogate": self.surrogate,
            "entity": self.entity,
        }


class SurrogateFactory:
    """Per-record surrogate generator.

    Seeded from the record id and a per-run salt. The record id alone would make
    surrogates reproducible across runs, which sounds convenient and is the
    linkage risk described above: anyone with two exports could match them.
    """

    def __init__(self, record_id: str, run_salt: str) -> None:
        seed = hashlib.sha256(f"{record_id}|{run_salt}".encode()).digest()
        self.rng = random.Random(int.from_bytes(seed[:8], "big"))
        self._assigned: dict[tuple[str, str], str] = {}

    def for_value(self, original: str, entity: str) -> str:
        """Same original, same surrogate, within this record only."""
        key = (entity, original.casefold())
        if key not in self._assigned:
            self._assigned[key] = self._make(original, entity)
        return self._assigned[key]

    def _make(self, original: str, entity: str) -> str:
        rng = self.rng
        if entity in ("PERSON", "PERSON_PATIENT"):
            return f"{rng.choice(SURROGATE_GIVEN)} {rng.choice(SURROGATE_FAMILY)}"
        if entity == "PERSON_CLINICIAN":
            return f"Doctor {rng.choice(SURROGATE_FAMILY)}"
        if entity in ("DATE_OF_BIRTH", "DATE"):
            # Shifted, not blanked. A date range still supports age-based
            # analysis, which is often the reason the field was collected.
            return f"{rng.randint(1, 28)} {rng.choice(['January', 'March', 'June', 'September', 'November'])} {rng.randint(1938, 2006)}"
        if entity == "MEDICAL_RECORD_NUMBER":
            digits = "".join(str(rng.randint(0, 9)) for _ in range(8))
            # Preserve the spoken shape, so a transcript that read digits one at
            # a time still reads that way after substitution.
            if " " in original:
                spacing = [len(part) for part in original.split()]
                out, cursor = [], 0
                for width in spacing:
                    out.append(digits[cursor:cursor + width] or str(rng.randint(0, 9)))
                    cursor += width
                return " ".join(out)
            return digits
        if entity == "NATIONAL_ID":
            return f"{rng.randint(100, 999)} {rng.randint(100, 999)} {rng.randint(1000, 9999)}"
        if entity == "PHONE_NUMBER":
            return f"0{rng.randint(1000, 1999)} {rng.randint(100000, 999999)}"
        if entity == "ADDRESS":
            return f"{rng.randint(1, 180)} {rng.choice(SURROGATE_STREETS)}, {rng.choice(SURROGATE_TOWNS)}"
        if entity == "EMAIL":
            return f"{rng.choice(SURROGATE_GIVEN).lower()}.{rng.choice(SURROGATE_FAMILY).lower()}@example.invalid"
        return f"[{entity}]"


def redact_text(
    text: str, spans: list[dict], record_id: str, run_salt: str
) -> tuple[str, list[Substitution]]:
    """Replace each span with a surrogate, right to left.

    Right to left matters: replacing left to right invalidates every offset
    after the first substitution whose surrogate differs in length from the
    original, and the failure is silent because the result is still a string.
    """
    factory = SurrogateFactory(record_id, run_salt)
    kept = [s for s in spans if should_redact(s)[0]]
    ordered = sorted(kept, key=lambda s: s["start"], reverse=True)

    out = text
    substitutions: list[Substitution] = []
    for span in ordered:
        original = text[span["start"]:span["end"]]
        surrogate = factory.for_value(original, span["entity"])
        out = out[:span["start"]] + surrogate + out[span["end"]:]
        substitutions.append(Substitution(
            start=span["start"],
            end=span["end"],
            original_length=len(original),
            surrogate=surrogate,
            entity=span["entity"],
        ))

    return out, list(reversed(substitutions))


def redact_audio(
    audio, sample_rate: int, time_spans: list[tuple[float, float]]
):
    """Overwrite identified regions of the waveform.

    The MVP writes low-level noise at matched duration rather than synthesised
    surrogate speech. That is a real limitation and is stated plainly: it removes
    the identifier and preserves the timeline, so downstream alignment still
    works, but it does not preserve the acoustic character of speech the way
    rendering a surrogate name with Piper would.

    The gap between the two is measurable and is exactly the sort of thing the
    design claims matters. Nothing here demonstrates that silencing skews a
    speech model; that needs a training run and a held-out evaluation, which
    8 GB of VRAM will not support.
    """
    import numpy as np

    out = np.array(audio, copy=True)
    for start, end in time_spans:
        first = max(0, int(start * sample_rate))
        last = min(len(out), int(end * sample_rate))
        if last <= first:
            continue
        # Noise at roughly the level of room tone. Not silence, because a run of
        # exact zeros is trivially detectable and marks where the identifier was.
        span = last - first
        rng = np.random.default_rng(abs(hash((first, last))) % (2**32))
        noise = rng.normal(0, 180, span).astype(np.int16)
        out[first:last] = noise

    return out
