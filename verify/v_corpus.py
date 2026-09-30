"""Verify the synthetic corpus answer key, from disk.

This does not import the generator. It re-reads the .txt files and checks each
span's offsets against the text as actually written, because a generator that
verifies itself only proves it is self-consistent. If the writing step
normalised a character or the encoding round-tripped badly, this is what would
catch it.

Every span in every record is checked and counted. A number of spans checked
that does not match the manifest total is itself a failure, since a verifier
that quietly skips cases is worse than no verifier.

Runs on the host, needs no services:
    python verify/v_corpus.py
"""

from __future__ import annotations

import os
import json
import sys
from collections import Counter
from pathlib import Path

if len(sys.argv) > 1:
    CORPUS = Path(sys.argv[1])
elif os.environ.get("MUNITAS_DATA"):
    CORPUS = Path(os.environ["MUNITAS_DATA"].rstrip("/\\") + "/synthetic")
else:
    sys.exit("Name the corpus folder, or set MUNITAS_DATA.")

_results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    _results.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def main() -> int:
    if not CORPUS.exists():
        print(f"corpus not found at {CORPUS}; run: python -m gen.synth")
        return 2

    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    record_files = sorted(CORPUS.glob("synth-*.json"))

    print("\nSynthetic corpus: answer key integrity")
    print("-" * 38)

    check("every record in the manifest has a file on disk",
          len(record_files) == manifest["count"],
          f"manifest={manifest['count']} on disk={len(record_files)}")

    spans_checked = 0
    offset_failures: list[str] = []
    empty_spans: list[str] = []
    overlaps: list[str] = []
    entity_tally: Counter[str] = Counter()
    hazard_records = 0
    hazard_without_reason: list[str] = []
    transcript_mismatch: list[str] = []

    for path in record_files:
        record = json.loads(path.read_text(encoding="utf-8"))
        text = (CORPUS / f"{record['record_id']}.txt").read_text(encoding="utf-8")

        if text != record["transcript"]:
            transcript_mismatch.append(record["record_id"])

        if record["asr_hazard"]:
            hazard_records += 1
            if not record["hazards"]:
                hazard_without_reason.append(record["record_id"])

        seen: list[tuple[int, int]] = []
        for span in record["spans"]:
            spans_checked += 1
            entity_tally[span["entity"]] += 1

            if text[span["start"]:span["end"]] != span["text"]:
                offset_failures.append(
                    f"{record['record_id']} {span['entity']} "
                    f"expected {span['text']!r} got {text[span['start']:span['end']]!r}"
                )
            if span["end"] <= span["start"]:
                empty_spans.append(f"{record['record_id']} {span['entity']}")

            for start, end in seen:
                if span["start"] < end and start < span["end"]:
                    overlaps.append(f"{record['record_id']} {span['entity']}")
            seen.append((span["start"], span["end"]))

    check("the .txt file matches the transcript in the .json",
          not transcript_mismatch, ", ".join(transcript_mismatch[:3]) or "all match")

    check("every span's offsets point at its own text",
          not offset_failures,
          f"{len(offset_failures)} drifted: {offset_failures[0]}" if offset_failures
          else f"{spans_checked} spans checked")

    check("the number of spans checked matches the manifest",
          spans_checked == manifest["total_spans"],
          f"checked={spans_checked} manifest={manifest['total_spans']}")

    check("no span is empty", not empty_spans, ", ".join(empty_spans[:3]) or "none")
    check("no two spans in a record overlap", not overlaps, ", ".join(overlaps[:3]) or "none")

    check("entity totals match the manifest",
          dict(sorted(entity_tally.items())) == manifest["entity_totals"],
          f"{len(entity_tally)} entity types")

    check("clinical content is not in the answer key",
          "NONE" not in entity_tally,
          "a medication flagged as PHI would score as a false positive, as it should")

    print("\nHazard subset")
    print("-" * 13)

    check("the hazardous subset is non-empty",
          hazard_records > 0, f"{hazard_records} of {len(record_files)} records")
    check("the hazard count matches the manifest",
          hazard_records == manifest["hazardous_count"],
          f"{hazard_records} vs {manifest['hazardous_count']}")
    check("every hazardous record says why it is hazardous",
          not hazard_without_reason, ", ".join(hazard_without_reason[:3]) or "all have reasons")

    # The hazardous subset must actually differ from the rest, or V14 will find
    # nothing and the flag is decorative.
    hazardous_names = set()
    ordinary_names = set()
    for path in record_files:
        record = json.loads(path.read_text(encoding="utf-8"))
        target = hazardous_names if record["asr_hazard"] else ordinary_names
        target.add(record["values"]["patient_name"].split()[-1])

    homographs = {"Bell", "Church", "Rivers", "Sparrow", "Frost", "Song", "Read"}
    check("hazardous records use surnames that are ordinary words",
          hazardous_names <= homographs and bool(hazardous_names),
          f"{sorted(hazardous_names)}")

    print("\nCorpus shape")
    print("-" * 12)
    check("more than one template is represented",
          len(manifest["templates"]) > 1, str(manifest["templates"]))
    check("every template produced records",
          all(n > 0 for n in manifest["templates"].values()))

    _check_audio(CORPUS, record_files)

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"\nCorpus: {passed} passed, {failed} failed")
    print(f"  {spans_checked} spans verified across {len(record_files)} records")
    return 1 if failed else 0


def _check_audio(corpus: Path, record_files: list[Path]) -> None:
    """Check the rendered audio, if any exists.

    Audio is optional: the text corpus and its answer key stand alone, and the
    text-scored half of V14 needs nothing else. So a missing audio directory is
    reported as absent rather than failed.
    """
    import wave

    audio_dir = corpus / "audio"
    wavs = sorted(audio_dir.glob("synth-*.wav")) if audio_dir.exists() else []

    print("\nRendered audio")
    print("-" * 14)
    if not wavs:
        print("  [ABSENT] no audio rendered yet; run: python -m gen.render")
        return

    print(f"  {len(wavs)} of {len(record_files)} records rendered")

    silent: list[str] = []
    timing_gaps: list[str] = []
    duration_mismatch: list[str] = []
    total = 0.0

    for wav_path in wavs:
        record_id = wav_path.stem
        timing_path = audio_dir / f"{record_id}.timing.json"
        if not timing_path.exists():
            timing_gaps.append(record_id)
            continue

        timing = json.loads(timing_path.read_text(encoding="utf-8"))
        with wave.open(str(wav_path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
        actual = frames / rate
        total += actual

        if actual < 1.0:
            silent.append(record_id)
        # The timing map claims a duration. If the file disagrees, every span
        # time derived from that map is wrong by the same amount.
        if abs(actual - timing["duration_seconds"]) > 0.05:
            duration_mismatch.append(
                f"{record_id} file={actual:.2f}s map={timing['duration_seconds']:.2f}s"
            )

    check("every rendered record has a timing map",
          not timing_gaps, ", ".join(timing_gaps[:3]) or "all present")
    check("no rendered record is empty or near silent",
          not silent, ", ".join(silent[:3]) or f"{total / 60:.1f} minutes total")
    check("the timing map's duration matches the wav file",
          not duration_mismatch, duration_mismatch[0] if duration_mismatch else "all within 50ms")

    # Turn boundaries must be ordered and non-overlapping, or diarisation has
    # nothing coherent to recover.
    disordered: list[str] = []
    for wav_path in wavs:
        timing_path = audio_dir / f"{wav_path.stem}.timing.json"
        if not timing_path.exists():
            continue
        turns = json.loads(timing_path.read_text(encoding="utf-8"))["turns"]
        for earlier, later in zip(turns, turns[1:]):
            if later["audio_start"] < earlier["audio_end"]:
                disordered.append(wav_path.stem)
                break
    check("turn boundaries are ordered and do not overlap",
          not disordered, ", ".join(disordered[:3]) or "all ordered")

    sample = json.loads(
        (audio_dir / f"{wavs[0].stem}.timing.json").read_text(encoding="utf-8")
    )
    check("span timings are labelled as interpolated",
          all(s.get("timing_is_interpolated") for s in sample["spans"]),
          "they are approximations and must not be used for scoring")


if __name__ == "__main__":
    sys.exit(main())
