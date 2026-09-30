"""Synthesize the full cardiology transcript with ai4bharat/indic-parler-tts.

Doctor  -> Mary  (recommended English speaker, model card)
Patient -> Thoma (recommended English speaker, model card; confirmed choice)

Each line gets its own generation call, then all lines are concatenated with
a pause whose length depends on how short/long the line is (matches the
approach used for the earlier edge-tts pass), and the whole thing is
re-encoded to 16kHz mono PCM WAV to match this project's audio-fixture
convention.
"""
import random
import subprocess
from pathlib import Path

import torch
from parler_tts import ParlerTTSForConditionalGeneration
from transformers import AutoTokenizer
import soundfile as sf

HERE = Path(__file__).parent
TRANSCRIPT = HERE / "cardiology_transcript.txt"
WORK = HERE / "shots" / "_indic_work"
WORK.mkdir(parents=True, exist_ok=True)
OUT = HERE / "cardiology-consult-synthetic.wav"

random.seed(42)

DESCRIPTIONS = {
    "DOCTOR": (
        "Mary speaks in a clear Indian English accent, with a low, "
        "steady pitch. Her tone is brisk and matter-of-fact, like a "
        "doctor who has already seen many patients today, professional "
        "without excess warmth or enthusiasm. The recording is very "
        "high quality, close and clear."
    ),
    "PATIENT": (
        "Thoma speaks in a clear Indian English accent, sounding tired and "
        "a little unwell, subdued and low-energy, speaking softly and "
        "slowly with no excitement in the voice. The recording is very "
        "high quality, close and clear."
    ),
}


def parse_lines():
    lines = []
    for raw in TRANSCRIPT.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        speaker, _, text = raw.partition(":")
        lines.append((speaker.strip(), text.strip()))
    return lines


def pause_for(text: str) -> int:
    """Confirmed by ear: 550ms between a short doctor line and a short
    patient reply reads as a natural conversational gap, not a splice."""
    words = len(text.split())
    if words <= 6:
        base = 550
    elif words <= 16:
        base = 650
    else:
        base = 800
    return base + random.randint(-40, 60)


# The greeting line is generated as two SEPARATE TTS calls with a real
# spliced silence between them (see test_greeting_two_calls.py), instead
# of the normal one-call-per-line approach: a single call with a period
# in the middle does not reliably produce an audible pause there, and an
# earlier attempt to find+split on the sentence's own internal silence
# actually split on its trailing silence instead (root-caused and fixed
# mid-session). Keyed by exact text so it only fires for this one line.
SPLIT_SENTENCES = {
    "Morning, Arjun. Come on in, take a seat.": [
        "Morning, Arjun.",
        "Come on in, take a seat.",
    ],
}
SPLIT_PAUSE_MS = 1000  # at least 1s, confirmed by the user


def build_silence(path: Path, ms: int):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"anullsrc=r=24000:cl=mono:d={ms / 1000}",
         "-c:a", "pcm_s16le", str(path)],
        check=True,
    )


def polish_clip(src: Path, dst: Path):
    """Winning chain from the accent/tone/noise tuning pass, confirmed
    best by ear: moderate denoise, trim leading/trailing near-silence
    (the trailing trim is deliberately more aggressive: Mary's residual
    noise floor sits higher than a plain -45dB threshold catches, so the
    "silent" lead-out was actually audible until this was loosened),
    fade in/out to match, loudness normalized to a consistent level."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-af",
         "afftdn=nr=40:nf=-28:tn=1,"
         "silenceremove=start_periods=1:start_duration=0:start_threshold=-32dB:"
         "detection=peak,"
         "areverse,"
         "silenceremove=start_periods=1:start_duration=0:start_threshold=-22dB:"
         "detection=peak,"
         "areverse,"
         "afade=t=in:d=0.025,"
         "areverse,afade=t=in:d=0.28,areverse,"
         "loudnorm=I=-18:TP=-2:LRA=7",
         "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
         str(dst)],
        check=True,
    )


def synth_one(model, tokenizer, description_tokenizer, device, desc, text, raw_path):
    desc_ids = description_tokenizer(desc, return_tensors="pt").to(device)
    prompt_ids = tokenizer(text, return_tensors="pt").to(device)
    generation = model.generate(
        input_ids=desc_ids.input_ids,
        attention_mask=desc_ids.attention_mask,
        prompt_input_ids=prompt_ids.input_ids,
        prompt_attention_mask=prompt_ids.attention_mask,
    )
    audio = generation.cpu().numpy().squeeze()
    sf.write(raw_path, audio, model.config.sampling_rate)


def main():
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print("loading model...")
    model = ParlerTTSForConditionalGeneration.from_pretrained(
        "ai4bharat/indic-parler-tts"
    ).to(device)
    tokenizer = AutoTokenizer.from_pretrained("ai4bharat/indic-parler-tts")
    description_tokenizer = AutoTokenizer.from_pretrained(
        model.config.text_encoder._name_or_path
    )
    print("model loaded")

    lines = parse_lines()
    print(f"{len(lines)} lines to synthesize")

    polished_dir = HERE / "shots" / "_indic_polished"
    polished_dir.mkdir(parents=True, exist_ok=True)

    clip_paths = []
    for i, (speaker, text) in enumerate(lines):
        desc = DESCRIPTIONS[speaker]

        if text in SPLIT_SENTENCES:
            # Two separate calls + a verified spliced silence, per the
            # confirmed fix for the "Good morning." pause bug.
            sub_polished = []
            for j, sub_text in enumerate(SPLIT_SENTENCES[text]):
                raw_path = WORK / f"{i:03d}_{speaker.lower()}_part{j}.wav"
                synth_one(model, tokenizer, description_tokenizer, device,
                          desc, sub_text, raw_path)
                polished_path = polished_dir / f"{i:03d}_{speaker.lower()}_part{j}.wav"
                polish_clip(raw_path, polished_path)
                sub_polished.append(polished_path)

            gap_silence = WORK / f"{i:03d}_splitgap.wav"
            build_silence(gap_silence, SPLIT_PAUSE_MS)

            spliced_path = polished_dir / f"{i:03d}_{speaker.lower()}.wav"
            sub_concat = WORK / f"{i:03d}_splitconcat.txt"
            with sub_concat.open("w", encoding="utf-8") as f:
                f.write(f"file '{sub_polished[0].resolve().as_posix()}'\n")
                f.write(f"file '{gap_silence.resolve().as_posix()}'\n")
                f.write(f"file '{sub_polished[1].resolve().as_posix()}'\n")
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                 "-i", str(sub_concat), "-c", "copy", str(spliced_path)],
                check=True,
            )
            clip_paths.append(spliced_path)
            print(f"  synthesized {i:03d} {speaker} (split, {SPLIT_PAUSE_MS}ms pause): {text[:50]}...")
            continue

        raw_path = WORK / f"{i:03d}_{speaker.lower()}.wav"
        synth_one(model, tokenizer, description_tokenizer, device,
                  desc, text, raw_path)
        polished_path = polished_dir / f"{i:03d}_{speaker.lower()}.wav"
        polish_clip(raw_path, polished_path)
        clip_paths.append(polished_path)
        print(f"  synthesized {i:03d} {speaker}: {text[:50]}...")

    concat_list = WORK / "concat.txt"
    with concat_list.open("w", encoding="utf-8") as f:
        for i, (clip, (_, text)) in enumerate(zip(clip_paths, lines)):
            f.write(f"file '{clip.resolve().as_posix()}'\n")
            if i != len(clip_paths) - 1:
                ms = pause_for(text)
                sil_path = WORK / f"silence_{i:03d}.wav"
                build_silence(sil_path, ms)
                f.write(f"file '{sil_path.resolve().as_posix()}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(concat_list),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
         str(OUT)],
        check=True,
    )
    print("wrote", OUT)


if __name__ == "__main__":
    main()
