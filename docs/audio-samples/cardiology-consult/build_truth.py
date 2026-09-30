"""Build truth.json for cardiology-consult-synthetic.wav.

reference_transcript is the true spoken content, in natural orthography
(TTS-only pronunciation hacks like "Am-lo-di-peen" / "E C G" / "B P" are
normalized back to real spelling here, since those hyphens/letter-spacing
carry no information -- they exist only to steer this one TTS engine).
Everything else (spelled-out numbers, "fourteenth", "Dr. Das") is kept
exactly as spoken, since that IS the ground truth: worker/align.py's own
Alignment is specifically built to tolerate reference/ASR reformatting
(its self-test aligns unspaced "85145542" against ASR's spaced "8 5 1 4 5
5 4 2" and still calls it survived), so there's no need to guess the
ASR's exact output -- only to state what was actually said.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
TRANSCRIPT = HERE / "cardiology_transcript.txt"

NORMALIZE = {
    "Am-lo-di-peen": "Amlodipine",
    "E C G": "ECG",
    "B P": "BP",
    # Phone numbers and MRNs: real ASR (confirmed repeatedly against this
    # exact audio) always normalizes spoken digit words to numerals, and
    # worker/align.py's Alignment matches at the character level -- "nine"
    # and "9" share no characters, so a spelled-out reference makes an
    # intact identifier register as "destroyed by ASR" when it plainly
    # wasn't. Digit-run reformatting (spacing) is what Alignment is built
    # to tolerate (see its own self-test); word-vs-numeral is not, so the
    # reference has to already be in the form ASR actually produces.
    "nine eight seven six five, four three two one zero": "9876543210",
    "four eight two one three": "48213",
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
        ("PERSON_PATIENT", "Arjun", 0),          # "Morning, Arjun."
        ("PERSON_PATIENT", "Arjun Reddy", 0),     # "Arjun Reddy, date of birth..."
        ("DATE_OF_BIRTH", "fourteenth of March, seventy four", 0),
        ("ADDRESS", "twelve, Lakshmi Nagar, Bangalore", 0),
        ("PHONE_NUMBER", "9876543210", 0),
        ("PERSON_CLINICIAN", "Dr. Das", 0),       # "...something serious, Dr. Das?"
        ("MEDICAL_RECORD_NUMBER", "48213", 0),
        ("PERSON_CLINICIAN", "Dr. Das", 1),       # "Thank you, Dr. Das."
        ("PERSON_PATIENT", "Arjun", 2),           # "Take care, Arjun."
    ]

    spans = []
    search_from = {}  # text -> next search start, to get occurrences in order
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
    out = HERE / "cardiology-consult-synthetic.truth.json"
    out.write_text(json.dumps(truth, indent=2), encoding="utf-8")
    print()
    print("wrote", out)
    print()
    print("=== reference_transcript ===")
    print(reference)


if __name__ == "__main__":
    main()
