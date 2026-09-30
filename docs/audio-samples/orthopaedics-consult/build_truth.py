"""Build truth.json for orthopaedics-consult-synthetic.wav.

Same shape as the other two samples' build_truth.py: reference_transcript is
the true spoken content in natural orthography; the NORMALIZE map only
touches spoken digit-runs (phone number, record number), since Alignment
matches at the character level and ASR always produces numerals for those,
never the words. No TTS-only pronunciation hacks are needed here (no
letter-spaced acronyms in this script), so the map is shorter than the
other two samples'.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
TRANSCRIPT = HERE / "transcript.txt"

NORMALIZE = {
    "nine one two three four, five six seven eight nine": "9123456789",
    "three three nine one two": "33912",
}


def spoken_lines():
    lines = []
    for raw in TRANSCRIPT.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        _, _, text = raw.partition(":")
        text = text.strip()
        for hack, real in NORMALIZE.items():
            text = text.replace(hack, real)
        lines.append(text)
    return lines


def build_reference() -> str:
    return " ".join(spoken_lines())


def main():
    reference = build_reference()

    # (entity, text, occurrence index within `reference`, 0-indexed)
    # Note the shape here is deliberately different from the other two
    # samples: identity is confirmed mid-conversation ("before I finalize
    # your file..."), not at the top, and the clinician's name appears only
    # once (the patient never asks a worried "is it serious" question in
    # this script -- the diagnosis is good news, delivered early).
    targets = [
        ("PERSON_PATIENT", "Rahul", 0),             # "Rahul, come on in."
        ("PERSON_PATIENT", "Rahul Verma", 0),        # "Rahul Verma, date of birth..."
        ("DATE_OF_BIRTH", "third of November, ninety two", 0),
        ("ADDRESS", "forty two, Karve Road, Pune", 0),
        ("PHONE_NUMBER", "9123456789", 0),
        ("MEDICAL_RECORD_NUMBER", "33912", 0),
        ("PERSON_CLINICIAN", "Dr. Kapoor", 0),       # "Understood, Dr. Kapoor."
        ("PERSON_PATIENT", "Rahul", 2),              # "Take care, Rahul." (0=greeting, 1=inside "Rahul Verma")
    ]

    spans = []
    for entity, text, occurrence in targets:
        search_from = 0
        for _ in range(occurrence + 1):
            pos = reference.index(text, search_from)
            search_from = pos + 1
        start = pos
        end = start + len(text)
        assert reference[start:end] == text, (text, reference[start:end])
        spans.append({"entity": entity, "start": start, "end": end, "text": text})
        print(f"{entity:24s} [{start:4d}:{end:4d}] {text!r}")

    truth = {
        "spans": spans,
        "reference_transcript": reference,
        "hazard": False,
    }
    out = HERE / "truth.json"
    out.write_text(json.dumps(truth, indent=2), encoding="utf-8")
    print()
    print("wrote", out)
    print()
    print("=== reference_transcript ===")
    print(reference)


if __name__ == "__main__":
    main()
