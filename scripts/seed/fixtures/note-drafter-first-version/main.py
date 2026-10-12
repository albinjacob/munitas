"""A note drafter: reads consultation transcripts and drafts a visit note from each, for a clinician to read, correct and sign.

It runs in Munitas's sandbox. What it is given and what it hands back:

  in    /data/manifest.json   the files of the dataset version this run was started on, and /data/<relative path> for each (the platform copied them in before this started)
        MUNITAS_DATASET_VERSION_ID, MUNITAS_RUN_ID, MUNITAS_PURPOSE   environment variables
  out   one JSON object printed as the last line of standard output

It uses no tools, calls no model and reaches no network. It drafts with plain rules, on purpose: every sentence of a draft is a line the transcript already contains, filed under a heading, and
the draft says which line each came from. A reader can check every claim in a draft against the transcript, and a rule cannot make a fact up. What it cannot do is understand: a line that
fits no heading is counted and left out, and a clinician has to read the transcript for anything it missed.

A transcript is a plain text file of lines that start with "Doctor:" or "Patient:".
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

DATA = Path(os.environ.get("NOTE_DRAFTER_DATA", "/data"))

# What each heading looks for. A line is filed under the first heading it fits, in this order, so the same line is never written twice. A cue is found anywhere in a line.
HEADINGS = [
    ("Examination and measurements", "Doctor", [
        r"\b\d{2,3}\s*(?:over|/)\s*\d{2,3}\b", r"blood pressure", r"heart rate", r"\bpulse\b", r"temperature", r"examin", r"listen", r"i can hear", r"oxygen",
    ]),
    ("Plan", "Doctor", [
        r"\border\b", r"\bstart\b", r"\bprescri(?:be|bed|bing|ption)\b", r"\brefer", r"follow[- ]up", r"come back", r"\badvise", r"schedule", r"\bincrease\b", r"\bstop\b", r"\bavoid\b", r"\bbook\b",
    ]),
    ("Assessment", "Doctor", [
        r"\bsuspect", r"\blikely\b", r"consistent with", r"\bi think\b", r"\bconcern", r"diagnos", r"\bappears\b", r"\bsounds like\b",
    ]),
    ("History", "Patient", [
        r"for about", r"\bsince\b", r"\bweeks?\b", r"\bdays?\b", r"\bmonths?\b", r"\bstarted\b", r"\bworse\b", r"\bbetter\b", r"\btak(?:e|es|ing)\b", r"allerg", r"smok",
        r"family", r"\bmedication", r"\btablet", r"\bhistory\b",
    ]),
]

TRAILER = "Drafted by rules from the transcript. Each line above is a line of the transcript, with its number. A clinician has to read it, correct it and sign it."

LINE = re.compile(r"^\s*(Doctor|Patient)\s*:\s*(.+?)\s*$", re.IGNORECASE)


def parse(text: str) -> list[dict]:
    """The spoken lines of a transcript: who spoke, what was said, and the line number in the file."""
    out = []
    for number, raw in enumerate(text.splitlines(), start=1):
        found = LINE.match(raw)
        if found:
            out.append({"line": number, "who": found.group(1).capitalize(), "said": found.group(2)})
    return out


def draft(name: str, text: str) -> dict:
    """A draft note for one transcript, and which transcript lines it used."""
    lines = parse(text)
    used: set[int] = set()
    sections: dict[str, list[dict]] = {}

    # The reason for the visit is the first thing the patient said.
    first = next((entry for entry in lines if entry["who"] == "Patient"), None)
    if first:
        sections["Reason for the visit"] = [first]
        used.add(first["line"])

    for heading, speaker, cues in HEADINGS:
        for entry in lines:
            if entry["line"] in used or entry["who"] != speaker:
                continue
            if any(re.search(cue, entry["said"], re.IGNORECASE) for cue in cues):
                sections.setdefault(heading, []).append(entry)
                used.add(entry["line"])

    order = ["Reason for the visit", "History", "Examination and measurements", "Assessment", "Plan"]
    body = [f"DRAFT NOTE for {name}"]
    for heading in order:
        entries = sections.get(heading)
        if not entries:
            body.append(f"{heading}: nothing in the transcript fit this heading.")
            continue
        body.append(f"{heading}:")
        body.extend(f"  - {entry['said']} (line {entry['line']})" for entry in sorted(entries, key=lambda e: e["line"]))
    left_out = [entry for entry in lines if entry["line"] not in used]
    body.append(f"Not placed: {len(left_out)} of {len(lines)} spoken lines fit no heading and are not in this draft.")
    body.append(TRAILER)
    return {
        "document": name,
        "note": "\n".join(body),
        "headings_found": [h for h in order if sections.get(h)],
        "lines_used": sorted(used),
        "lines_not_placed": [entry["line"] for entry in left_out],
    }


def main() -> int:
    manifest_path = DATA / "manifest.json"
    if not manifest_path.exists():
        print(json.dumps({"agent": "note-drafter", "error": "no data was given to this run: the version was not registered with a copy of its data, or the run was started with no dataset"}))
        return 0
    entries = json.loads(manifest_path.read_text()).get("objects", [])
    drafts, skipped = [], []
    for entry in sorted(entries, key=lambda e: e["relative_path"]):
        path = DATA / entry["relative_path"]
        if not entry["relative_path"].lower().endswith(".txt"):
            skipped.append(entry["relative_path"])
            continue
        drafts.append(draft(entry["relative_path"], path.read_text(encoding="utf-8", errors="replace")))
    print(json.dumps({
        "agent": "note-drafter",
        "dataset_version_id": os.environ.get("MUNITAS_DATASET_VERSION_ID"),
        "documents_read": len(drafts),
        "files_skipped": skipped,
        "drafts": drafts,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
