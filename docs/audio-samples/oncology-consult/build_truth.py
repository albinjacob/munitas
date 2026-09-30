"""Build truth.json for oncology-consult-synthetic.wav.

Same shape as cardiology-consult/build_truth.py: reference_transcript is the
true spoken content in natural orthography (TTS-only pronunciation hacks
like "M R I" / "P E T" are normalized back to real spelling here, since
those letter-spacings carry no information -- they exist only to steer this
one TTS engine's pronunciation). Everything else (spelled-out numbers,
"twenty second of July", "Dr. Menon") is kept exactly as spoken, since that
IS the ground truth -- worker/align.py's Alignment is built to tolerate
reference/ASR reformatting of digit runs, not word-vs-numeral substitution,
so the reference has to already be in the numeral form ASR actually
produces for phone numbers and record numbers.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
TRANSCRIPT = HERE / "transcript.txt"

NORMALIZE = {
    "M R I": "MRI",
    "P E T": "PET",
    # Spoken digit words -> numerals, same reasoning as cardiology-consult's
    # own NORMALIZE map: ASR produces numerals for phone numbers and record
    # numbers, and Alignment matches at the character level, so a
    # spelled-out reference would register an intact identifier as
    # "destroyed by ASR" when it plainly wasn't.
    "eight nine seven six five, four three two one nine": "8976543219",
    "seven seven zero four four": "77044",
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
    targets = [
        ("PERSON_PATIENT", "Priya", 0),           # "Good afternoon, Priya."
        ("PERSON_PATIENT", "Priya Nair", 0),       # "Priya Nair, date of birth..."
        ("DATE_OF_BIRTH", "twenty second of July, eighty one", 0),
        ("ADDRESS", "fourteen, Church Street, Kochi", 0),
        ("PHONE_NUMBER", "8976543219", 0),
        ("PERSON_CLINICIAN", "Dr. Menon", 0),      # "...worried. Is that normal, Dr. Menon?"
        ("MEDICAL_RECORD_NUMBER", "77044", 0),
        ("PERSON_CLINICIAN", "Dr. Menon", 1),      # "Thank you, Dr. Menon."
        ("PERSON_PATIENT", "Priya", 2),            # "Take care, Priya." (0=greeting, 1=inside "Priya Nair")
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
