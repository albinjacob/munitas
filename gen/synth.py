"""Generate a synthetic consultation corpus carrying its own ground truth.

Ground truth is the entire reason this exists. The real encounter artefacts have
no answer key, so recall cannot be scored against them directly: you can see
what a detector found, never what it missed. A synthetic corpus knows where every
identifier is, because it put them there.

Output per record:

  <id>.txt   the transcript as a human would read it
  <id>.json  the record: values, spans with exact offsets, turn boundaries,
             and the ASR hazard flags

Audio is rendered separately by render.py, which needs Piper and its voices.
Text and ground truth are produced here and stand alone, so a machine with no
Piper still gets a scoreable corpus for the text-scored half of V14.

Usage:
    python -m gen.synth --count 60        (writes to MUNITAS_DATA/synthetic)
"""

from __future__ import annotations

import os
import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from .dialogue import TEMPLATES, render_dialogue
from .identifiers import CONDITIONS, MEDICATIONS, Faker

# Surnames that are also ordinary words. A detector that leans on capitalisation
# does fine on these in text and badly once ASR has lowercased everything, which
# is exactly the divergence V14 is meant to measure.
HOMOGRAPH_SURNAMES = {"Bell", "Church", "Rivers", "Sparrow", "Frost", "Song", "Read"}


@dataclass
class Hazard:
    """A reason this record is expected to be hard for speech recognition.

    Recorded per record so V14 can report recall split by hazard rather than as
    one averaged number. An average hides the whole finding: the gap between
    text-scored and audio-scored recall lives almost entirely in these records.
    """

    kind: str
    detail: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail}


def build_record(index: int, seed: int, template_name: str, hazardous: bool) -> dict:
    fake = Faker(seed)

    patient = fake.patient_name()
    mrn = fake.mrn()
    hazards: list[Hazard] = []

    if hazardous:
        # Force the combinations that break transcription. Chosen deliberately
        # rather than sampled, so the hazardous subset is actually hazardous
        # instead of merely labelled that way.
        family = fake.rng.choice(sorted(HOMOGRAPH_SURNAMES))
        patient = f"{fake.pick(['Aoife', 'Siobhan', 'Eoghan', 'Grainne', 'Padraig'])} {family}"
        mrn_spoken = " ".join(mrn)  # digit by digit, the easiest to drop or merge
        hazards.append(Hazard(
            "homograph_surname",
            f"{family!r} is an ordinary word, so a lowercased transcript gives no capitalisation cue",
        ))
        hazards.append(Hazard(
            "irish_orthography",
            f"{patient.split()[0]!r} is commonly mis-transcribed phonetically",
        ))
        hazards.append(Hazard(
            "digit_by_digit_mrn",
            "an 8 digit record number read one digit at a time, where a single dropped digit still looks plausible",
        ))
    else:
        mrn_spoken = fake.mrn_spoken(mrn)

    values = {
        "patient_name": patient,
        "clinician_name": fake.clinician_name(),
        "date_of_birth": fake.date_of_birth(),
        "mrn": mrn,
        "mrn_spoken": mrn_spoken,
        "phone": fake.phone(),
        "address": fake.address(),
        "email": fake.email(patient),
        "nhs_number": fake.nhs_style_number(),
        "condition": fake.pick(CONDITIONS),
        "medication": fake.pick(MEDICATIONS),
    }

    transcript, spans, boundaries = render_dialogue(TEMPLATES[template_name](), values)

    # Slots labelled NONE are clinical content, not identifiers. They are
    # deliberately not in the answer key: a detector that flags a medication
    # name as PHI should be scored as a false positive, not rewarded.
    identifier_spans = [s for s in spans if s.entity != "NONE"]

    record_id = f"synth-{index:04d}"
    return {
        "record_id": record_id,
        "template": template_name,
        "seed": seed,
        "asr_hazard": hazardous,
        "hazards": [h.as_dict() for h in hazards],
        "values": values,
        "transcript": transcript,
        "spans": [s.as_dict() for s in identifier_spans],
        "turns": boundaries,
        "entity_counts": _counts(identifier_spans),
    }


def _counts(spans) -> dict[str, int]:
    out: dict[str, int] = {}
    for span in spans:
        out[span.entity] = out.get(span.entity, 0) + 1
    return out


def generate(count: int, out_dir: Path, seed: int = 20260802, hazard_fraction: float = 0.3) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    templates = sorted(TEMPLATES)
    records = []

    for i in range(count):
        template = templates[i % len(templates)]
        # Deterministic rather than random, so the hazardous subset is the same
        # set of records on every run and results stay comparable.
        hazardous = (i % round(1 / hazard_fraction)) == 0
        record = build_record(i, seed + i, template, hazardous)
        records.append(record)

        (out_dir / f"{record['record_id']}.txt").write_text(
            record["transcript"], encoding="utf-8"
        )
        (out_dir / f"{record['record_id']}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    manifest = {
        "generated_by": "gen/synth.py",
        "seed": seed,
        "count": len(records),
        "hazardous_count": sum(1 for r in records if r["asr_hazard"]),
        "templates": {t: sum(1 for r in records if r["template"] == t) for t in templates},
        "total_spans": sum(len(r["spans"]) for r in records),
        "entity_totals": _entity_totals(records),
        "records": [
            {
                "record_id": r["record_id"],
                "template": r["template"],
                "asr_hazard": r["asr_hazard"],
                "span_count": len(r["spans"]),
            }
            for r in records
        ],
        "note": (
            "Every value in this corpus is invented. Nothing here is derived "
            "from the real encounter artefacts, and the two must not be mixed."
        ),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def _entity_totals(records: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for record in records:
        for entity, n in record["entity_counts"].items():
            out[entity] = out.get(entity, 0) + n
    return dict(sorted(out.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=60)
    data = os.environ.get("MUNITAS_DATA", "").rstrip("/\\")
    parser.add_argument("--out", type=Path, required=not data,
                        default=Path(f"{data}/synthetic") if data else None)
    parser.add_argument("--seed", type=int, default=20260802)
    args = parser.parse_args()

    manifest = generate(args.count, args.out, args.seed)

    print(f"wrote {manifest['count']} records to {args.out}")
    print(f"  hazardous subset: {manifest['hazardous_count']}")
    print(f"  ground-truth spans: {manifest['total_spans']}")
    for entity, n in manifest["entity_totals"].items():
        print(f"    {entity:<24} {n}")


if __name__ == "__main__":
    main()
