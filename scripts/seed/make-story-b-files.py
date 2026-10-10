"""The files Story B of the Health walkthroughs brings in through the console: three consultation recordings and a table of clinic appointments about the same patients.

    .venv\\Scripts\\python.exe scripts\\seed\\make-story-b-files.py

Writes into web/walkthroughs/shots/_story-b-data/ (gitignored with the screenshots):

    recordings/visit-1.wav, visit-2.wav, visit-3.wav     the three sample consultations in docs/audio-samples (made with a text-to-speech voice, so no real person is in them)
    recordings/visit-1.truth.json ...                    each recording's answer key: what was really said and which words identify the person
    recordings/manifest.csv                              which patient each recording is about, by patient number
    appointments/contract.yaml, appointments.csv         the table of appointments, in which the same three patients appear

The patient numbers in the manifest and in the table are the same, so the platform's lookup finds one person, with one key, in both. Every name and number is invented.
"""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "docs" / "audio-samples"
FIXTURE = ROOT / "web" / "walkthroughs" / "fixtures" / "locked-appointments"
OUT = ROOT / "web" / "walkthroughs" / "shots" / "_story-b-data"

# The three recorded consultations, who each is about (named in the recording and its answer key), and how they appear in the appointments table.
PATIENTS = [
    {"folder": "cardiology-consult", "visit": "visit-1", "number": "HC-48213", "name": "Arjun Reddy", "reason": "chest pain on exertion",
     "age_band": "50-59", "postcode": "H93", "clinic": "Cardiology outpatients", "seen_on": "2026-09-11", "row": "A-1019"},
    {"folder": "oncology-consult", "visit": "visit-2", "number": "HC-77044", "name": "Priya Nair", "reason": "follow up after treatment",
     "age_band": "40-49", "postcode": "H92", "clinic": "Cardiology outpatients", "seen_on": "2026-09-25", "row": "A-1012"},
    {"folder": "orthopaedics-consult", "visit": "visit-3", "number": "HC-33912", "name": "Rahul Verma", "reason": "knee pain",
     "age_band": "30-39", "postcode": "H91", "clinic": "Cardiology outpatients", "seen_on": "2026-09-03", "row": "A-1001"},
]


def main() -> None:
    (OUT / "recordings").mkdir(parents=True, exist_ok=True)
    (OUT / "appointments").mkdir(parents=True, exist_ok=True)
    manifest = [["file", "person"]]
    for p in PATIENTS:
        source = SAMPLES / p["folder"]
        shutil.copyfile(next(source.glob("*.wav")), OUT / "recordings" / f"{p['visit']}.wav")
        shutil.copyfile(source / "truth.json", OUT / "recordings" / f"{p['visit']}.truth.json")
        manifest.append([f"{p['visit']}.wav", p["number"]])
    with open(OUT / "recordings" / "manifest.csv", "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(manifest)

    shutil.copyfile(FIXTURE / "contract.yaml", OUT / "appointments" / "contract.yaml")
    rows = list(csv.DictReader(open(FIXTURE / "rows.csv", encoding="utf-8", newline="")))
    by_row = {p["row"]: p for p in PATIENTS}
    for row in rows:
        p = by_row.get(row["appointment_id"])
        if p:
            row.update(patient_number=p["number"], patient_name=p["name"], reason_for_visit=p["reason"], age_band=p["age_band"],
                       postcode=p["postcode"], clinic=p["clinic"], seen_on=p["seen_on"])
    with open(OUT / "appointments" / "appointments.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(PATIENTS)} recordings with answer keys and a manifest, and {len(rows)} appointments, to {OUT}")


if __name__ == "__main__":
    main()
