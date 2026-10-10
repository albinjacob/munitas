"""The files Story A of the Health walkthroughs brings in through the console: a table of hospital admissions and a small lookup of diagnosis codes.

    .venv\\Scripts\\python.exe scripts\\seed\\make-story-a-files.py

Writes four files into web/walkthroughs/shots/_story-a-data/ (gitignored with the screenshots): `admissions.csv` and its contract, `diagnosis_codes.csv` and its
contract. Every row is invented. The admissions are the same 400 stays as the older derivation demo (scripts/seed/seed-derive-demo-data.py) with the columns
that name or place a person left out, because this table is brought in as an ordinary table, one that is not about people: nothing in it is locked and it
cannot be erased. The contracts say so, as the platform requires of such a table.
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "web" / "walkthroughs" / "shots" / "_story-a-data"

DIAGNOSES = [
    ("I10", "Essential hypertension", True), ("I20.9", "Angina pectoris, unspecified", True),
    ("I21.4", "Acute myocardial infarction", False), ("I25.1", "Coronary artery disease", True),
    ("I48.91", "Atrial fibrillation, unspecified", True), ("I50.9", "Heart failure, unspecified", True),
    ("I63.9", "Cerebral infarction, unspecified", False), ("I42.0", "Dilated cardiomyopathy", True),
    ("I47.1", "Supraventricular tachycardia", False), ("R07.9", "Chest pain, unspecified", False),
]


def admissions() -> list[dict]:
    rng = random.Random(20261002)
    rows = []
    for i in range(400):
        age = max(18, min(96, int(rng.gauss(64, 15))))
        code = rng.choice(DIAGNOSES)[0]
        stay = max(1, int(rng.gauss(4 + (age - 60) / 15, 2)))
        rows.append({
            "admission_id": f"ADM-{i + 1:04d}",
            "admitted_on": (date(2026, 1, 1) + timedelta(days=rng.randint(0, 270))).isoformat(),
            "age": age,
            "diagnosis_code": code,
            "length_of_stay_days": stay,
            "readmitted_30d": str(rng.random() < (0.08 + max(0, age - 60) / 250)).lower(),
        })
    return rows


def contract(name: str, reason: str, columns: list[tuple[str, str, bool]]) -> str:
    lines = [
        "apiVersion: v3.2.0", "kind: DataContract", f"id: {name}", f"name: {name}", "version: 1.0.0", "status: active",
        "customProperties:",
        "  - {property: erasable, vendor: munitas, value: false}",
        f'  - {{property: notErasableReason, vendor: munitas, value: "{reason}"}}',
        "schema:", f"  - id: {name}_obj", f"    name: {name}", "    logicalType: object", "    properties:",
    ]
    for column, kind, key in columns:
        lines += [f"      - id: {column}_prop", f"        name: {column}", f"        logicalType: {kind}"]
        if key:
            lines += ["        primaryKey: true", "        required: true"]
        lines += ["        classification: none", "        customProperties:",
                  "          - {property: treatment, vendor: munitas, value: readable}"]
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = admissions()
    with open(OUT / "admissions.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with open(OUT / "diagnosis_codes.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["code", "description", "chronic"])
        writer.writerows([(c, d, str(chronic).lower()) for c, d, chronic in DIAGNOSES])
    (OUT / "admissions.contract.yaml").write_text(contract(
        "admissions", "One row per hospital stay, with no name or identifier of a person in it.",
        [("admission_id", "string", True), ("admitted_on", "date", False), ("age", "integer", False), ("diagnosis_code", "string", False),
         ("length_of_stay_days", "integer", False), ("readmitted_30d", "boolean", False)]), encoding="utf-8")
    (OUT / "diagnosis_codes.contract.yaml").write_text(contract(
        "diagnosis_codes", "A public list of diagnosis codes and their meanings, about no person.",
        [("code", "string", True), ("description", "string", False), ("chronic", "boolean", False)]), encoding="utf-8")
    print(f"wrote {len(rows)} admissions and {len(DIAGNOSES)} diagnosis codes, with their contracts, to {OUT}")


if __name__ == "__main__":
    main()
