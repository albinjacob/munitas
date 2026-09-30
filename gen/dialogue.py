"""Consultation templates, and the span bookkeeping that makes them useful.

The important mechanic is in `Turn.render`. A turn is a list of literal strings
and `Slot` objects, and the text is built by walking that list while tracking the
running offset. So a span's character offsets are known by construction rather
than found afterwards with `str.find`.

That distinction is the whole reason this file exists. Searching for the value
after the fact silently mislabels every corpus where a name appears twice, or
where a surname is also a common word, and this corpus contains both on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Slot:
    """A place where an identifier goes, and the label it should carry."""

    key: str      # which value from the record, e.g. "patient_name"
    entity: str   # ground-truth label, e.g. "PERSON_PATIENT"


@dataclass
class Span:
    start: int
    end: int
    entity: str
    text: str
    turn_index: int
    speaker: str

    def as_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "entity": self.entity,
            "text": self.text,
            "turn_index": self.turn_index,
            "speaker": self.speaker,
        }


@dataclass
class Turn:
    speaker: str
    parts: list[object] = field(default_factory=list)

    def render(self, values: dict[str, str], offset: int, index: int) -> tuple[str, list[Span]]:
        """Build this turn's text and the spans inside it.

        `offset` is where this turn starts in the full transcript, so the spans
        come out already positioned in the document rather than in the turn.
        """
        text_parts: list[str] = []
        spans: list[Span] = []
        cursor = offset

        for part in self.parts:
            if isinstance(part, Slot):
                value = values[part.key]
                spans.append(
                    Span(
                        start=cursor,
                        end=cursor + len(value),
                        entity=part.entity,
                        text=value,
                        turn_index=index,
                        speaker=self.speaker,
                    )
                )
                text_parts.append(value)
                cursor += len(value)
            else:
                text_parts.append(str(part))
                cursor += len(str(part))

        return "".join(text_parts), spans


# Shorthand so the templates below stay readable.
P = Slot("patient_name", "PERSON_PATIENT")
C = Slot("clinician_name", "PERSON_CLINICIAN")
DOB = Slot("date_of_birth", "DATE_OF_BIRTH")
MRN = Slot("mrn_spoken", "MEDICAL_RECORD_NUMBER")
PHONE = Slot("phone", "PHONE_NUMBER")
ADDR = Slot("address", "ADDRESS")
EMAIL = Slot("email", "EMAIL")
NHS = Slot("nhs_number", "NATIONAL_ID")


def registration_call() -> list[Turn]:
    """Front-desk style. Identifiers dense and read aloud, the worst case."""
    return [
        Turn("receptionist", ["Good morning, you're through to the surgery. Can I take your full name please?"]),
        Turn("patient", ["Yes, it's ", P, "."]),
        Turn("receptionist", ["Thank you ", P, ". And your date of birth?"]),
        Turn("patient", ["The ", DOB, "."]),
        Turn("receptionist", ["Lovely. Do you have your record number to hand?"]),
        Turn("patient", ["I do, it's ", MRN, "."]),
        Turn("receptionist", ["And can I just confirm the address we hold, ", ADDR, "?"]),
        Turn("patient", ["That's right, yes."]),
        Turn("receptionist", ["And a contact number?"]),
        Turn("patient", [PHONE, ", that's the mobile."]),
        Turn("receptionist", ["Perfect. ", C, " can see you on Thursday at ten past two."]),
    ]


def consultation() -> list[Turn]:
    """Clinical conversation. Identifiers sparse, buried in clinical language."""
    return [
        Turn("clinician", ["Come in, take a seat. I'm ", C, ". What's brought you in today?"]),
        Turn("patient", ["It's this ", Slot("condition", "NONE"), ", it's been going on about three weeks now."]),
        Turn("clinician", ["Three weeks. And are you still taking the ", Slot("medication", "NONE"), "?"]),
        Turn("patient", ["Every morning, yes."]),
        Turn("clinician", ["Right. Let me pull up the notes. ", P, ", born ", DOB, ", is that correct?"]),
        Turn("patient", ["That's me."]),
        Turn("clinician", ["Good. I'm going to refer you across to the chest clinic. They'll write to you at ", ADDR, "."]),
        Turn("patient", ["Could they email instead? It's ", EMAIL, "."]),
        Turn("clinician", ["I'll note that. And the number ending in the last three digits of ", PHONE, " is still current?"]),
        Turn("patient", ["It is."]),
        Turn("clinician", ["Then I'll see you in six weeks."]),
    ]


def discharge_summary_dictation() -> list[Turn]:
    """One speaker, formal register. The easiest case, included as a baseline."""
    return [
        Turn("clinician", ["Discharge summary. Patient ", P, ", NHS number ", NHS, ", date of birth ", DOB, "."]),
        Turn("clinician", ["Admitted under my care with ", Slot("condition", "NONE"), "."]),
        Turn("clinician", ["Record number ", MRN, ". Discharged home to ", ADDR, "."]),
        Turn("clinician", ["Follow up arranged. Dictated by ", C, "."]),
    ]


def handover_with_interruptions() -> list[Turn]:
    """Overlapping, hesitant speech. Identifiers split by fillers and restarts.

    This is where word-level timings earn their place: the identifier is not a
    contiguous clean utterance, so masking it by string match alone leaves
    fragments behind.
    """
    return [
        Turn("nurse", ["So the next one is, sorry, let me find it, ", P, "."]),
        Turn("clinician", ["Which bay?"]),
        Turn("nurse", ["Bay four. Date of birth is, hang on, ", DOB, "."]),
        Turn("clinician", ["And the number?"]),
        Turn("nurse", ["It's ", MRN, ", I think. Might be a seven at the end, the writing's poor."]),
        Turn("clinician", ["I'll check it against the wristband."]),
        Turn("nurse", ["Next of kin is on ", PHONE, ". They've been called already."]),
    ]


TEMPLATES = {
    "registration_call": registration_call,
    "consultation": consultation,
    "discharge_dictation": discharge_summary_dictation,
    "handover": handover_with_interruptions,
}


def render_dialogue(turns: Iterable[Turn], values: dict[str, str]) -> tuple[str, list[Span], list[dict]]:
    """Render turns into a transcript, its spans, and per-turn boundaries.

    Turn boundaries are returned because audio is rendered per turn. Aligning a
    span to a time range means knowing which turn it fell in and where in that
    turn it started, and reconstructing that later from the flat text is
    guesswork.
    """
    lines: list[str] = []
    spans: list[Span] = []
    boundaries: list[dict] = []
    offset = 0

    for index, turn in enumerate(turns):
        prefix = f"{turn.speaker}: "
        offset += len(prefix)

        text, turn_spans = turn.render(values, offset, index)
        spans.extend(turn_spans)

        boundaries.append({
            "turn_index": index,
            "speaker": turn.speaker,
            "text": text,
            "char_start": offset,
            "char_end": offset + len(text),
        })

        lines.append(prefix + text)
        offset += len(text) + 1  # the newline

    transcript = "\n".join(lines)

    # Spans that do not point at their own text mean the offset arithmetic
    # drifted. Fail loudly here rather than shipping a corpus whose answer key
    # is quietly wrong, which would make every recall number meaningless.
    for span in spans:
        actual = transcript[span.start:span.end]
        if actual != span.text:
            raise AssertionError(
                f"span offset drift: expected {span.text!r} at "
                f"{span.start}:{span.end}, found {actual!r}"
            )

    return transcript, spans, boundaries
