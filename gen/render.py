"""Render the synthetic corpus to audio with Piper.

One voice per speaker role, each turn synthesised separately and concatenated
with a natural gap, so the result has turn structure a diariser can find. Turn
time boundaries are written alongside, which is what lets a span be traced from
character offset to audio range.

On the accuracy of span timings, stated plainly because it would otherwise be
easy to over-trust: the per-turn boundaries here are exact, since each turn is a
separately rendered file of known length. The per-span times are interpolated
linearly across the turn by character position, which is an approximation and
nothing more. Real span timings arrive in slice 3 from faster-whisper's
word-level output. The interpolated values are useful for sanity checks and must
not be used to score anything.

Voices are downloaded on first use into the Hugging Face cache. Set
HF_HOME or PIPER_VOICE_DIR to keep them off the system drive.

Usage:
    python -m gen.render                  (reads MUNITAS_DATA)
    python -m gen.render --corpus D:/munitas-data/synthetic --limit 5
"""

from __future__ import annotations

import os
import argparse
import json
import sys
import wave
from pathlib import Path

# One voice per role. Different enough that diarisation has something to
# separate, which a single voice reading both parts would not give.
VOICE_BY_SPEAKER = {
    "patient": "en_GB-jenny_dioco-medium",
    "clinician": "en_GB-alan-medium",
    "receptionist": "en_US-amy-medium",
    "nurse": "en_US-lessac-medium",
}
FALLBACK_VOICE = "en_US-lessac-medium"

GAP_SECONDS = 0.45  # between turns, roughly conversational


def load_voices(names: set[str], voice_dir: Path | None) -> dict:
    from piper import PiperVoice
    from piper.download_voices import download_voice

    voices = {}
    target = voice_dir or Path.home() / ".cache" / "piper"
    target.mkdir(parents=True, exist_ok=True)

    for name in sorted(names):
        onnx = target / f"{name}.onnx"
        if not onnx.exists():
            print(f"  downloading voice {name}")
            download_voice(name, target)
        voices[name] = PiperVoice.load(onnx)
    return voices


def render_record(record: dict, voices: dict, out_wav: Path) -> dict:
    """Synthesise one record. Returns the turn timing map."""
    import numpy as np

    sample_rate = None
    chunks: list["np.ndarray"] = []
    timings: list[dict] = []
    cursor = 0.0

    for turn in record["turns"]:
        voice_name = VOICE_BY_SPEAKER.get(turn["speaker"], FALLBACK_VOICE)
        voice = voices[voice_name]

        audio = np.concatenate([
            np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
            for chunk in voice.synthesize(turn["text"])
        ])
        rate = voice.config.sample_rate
        sample_rate = sample_rate or rate
        if rate != sample_rate:
            raise RuntimeError(
                f"voice {voice_name} is {rate} Hz but the record started at "
                f"{sample_rate} Hz; mixing rates would silently shift every timing"
            )

        duration = len(audio) / rate
        timings.append({
            "turn_index": turn["turn_index"],
            "speaker": turn["speaker"],
            "voice": voice_name,
            "audio_start": round(cursor, 4),
            "audio_end": round(cursor + duration, 4),
            "char_start": turn["char_start"],
            "char_end": turn["char_end"],
        })

        chunks.append(audio)
        chunks.append(np.zeros(int(GAP_SECONDS * rate), dtype=np.int16))
        cursor += duration + GAP_SECONDS

    combined = np.concatenate(chunks)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(combined.tobytes())

    return {
        "record_id": record["record_id"],
        "sample_rate": sample_rate,
        "duration_seconds": round(len(combined) / sample_rate, 3),
        "gap_seconds": GAP_SECONDS,
        "turns": timings,
        "spans": [_interpolate(span, timings) for span in record["spans"]],
        "span_timing_caveat": (
            "Span times are interpolated linearly across the turn by character "
            "position. They are approximate and must not be used for scoring. "
            "Word-level timings come from faster-whisper in slice 3."
        ),
    }


def _interpolate(span: dict, timings: list[dict]) -> dict:
    """Estimate a span's audio range from its position within its turn."""
    turn = next((t for t in timings if t["turn_index"] == span["turn_index"]), None)
    if turn is None:
        return {**span, "audio_start": None, "audio_end": None}

    span_chars = max(turn["char_end"] - turn["char_start"], 1)
    turn_seconds = turn["audio_end"] - turn["audio_start"]
    start_ratio = (span["start"] - turn["char_start"]) / span_chars
    end_ratio = (span["end"] - turn["char_start"]) / span_chars

    return {
        **span,
        "audio_start": round(turn["audio_start"] + start_ratio * turn_seconds, 4),
        "audio_end": round(turn["audio_start"] + end_ratio * turn_seconds, 4),
        "timing_is_interpolated": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    data = os.environ.get("MUNITAS_DATA", "").rstrip("/\\")
    parser.add_argument("--corpus", type=Path, required=not data,
                        default=Path(f"{data}/synthetic") if data else None)
    parser.add_argument("--limit", type=int, default=0, help="render only the first N records")
    parser.add_argument("--voice-dir", type=Path, required=not data,
                        default=Path(f"{data}/piper-voices") if data else None)
    args = parser.parse_args()

    try:
        import numpy  # noqa: F401
        import piper  # noqa: F401
    except ImportError as exc:
        print(f"Piper is not available: {exc}")
        print("Install with: python -m pip install piper-tts")
        print("Text and ground truth are unaffected; only audio rendering needs this.")
        return 2

    records = sorted(args.corpus.glob("synth-*.json"))
    if args.limit:
        records = records[:args.limit]
    if not records:
        print(f"no records found in {args.corpus}; run: python -m gen.synth")
        return 2

    needed = {FALLBACK_VOICE} | set(VOICE_BY_SPEAKER.values())
    print(f"loading {len(needed)} voices into {args.voice_dir}")
    voices = load_voices(needed, args.voice_dir)

    audio_dir = args.corpus / "audio"
    total_seconds = 0.0
    for path in records:
        record = json.loads(path.read_text(encoding="utf-8"))
        timing = render_record(record, voices, audio_dir / f"{record['record_id']}.wav")
        (audio_dir / f"{record['record_id']}.timing.json").write_text(
            json.dumps(timing, indent=2), encoding="utf-8"
        )
        total_seconds += timing["duration_seconds"]
        print(f"  {record['record_id']}  {timing['duration_seconds']:>6.1f}s  "
              f"{len(timing['turns'])} turns")

    print(f"\nrendered {len(records)} records, {total_seconds / 60:.1f} minutes of audio")
    print(f"  audio and timings in {audio_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
