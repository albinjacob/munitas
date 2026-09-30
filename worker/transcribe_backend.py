"""Pluggable transcription backends behind one interface.

Everything downstream of transcription -- checkpointing, contract
validation, detection, redaction -- only depends on this module's plain-
dict output shape, never on any transcription library's own object types.
That is what is meant to let a second backend be added later without
touching worker/activities.py at all: one more class here, one more entry
in BACKENDS, one more accepted value for WHISPER_DEVICE in
worker/config.py.

Why there is only one backend today, and why that is a real ceiling and
not just "nobody got to it yet": transcription runs on faster-whisper,
which is built on CTranslate2, not PyTorch. CTranslate2 supports exactly
two devices, "cpu" and "cuda" -- passing "mps" raises "ValueError:
unsupported device mps" (github.com/SYSTRAN/faster-whisper/issues/911),
and that has been an open request against the library since at least 2024
(github.com/SYSTRAN/faster-whisper/issues/515) with no Metal support
landed. A Mac wanting real GPU transcription needs a different engine
entirely, not a different device string.

The obvious candidate is mlx-whisper: built on Apple's own MLX framework,
uses the GPU via Metal, and reported at 3-5x faster-whisper's CPU speed on
Apple Silicon. Adding it means:

  1. A new class here, e.g. MlxWhisperBackend, implementing this module's
     TranscribeBackend shape -- wrap mlx_whisper.transcribe() and
     normalise its segments into {"text", "words": [{"word", "start",
     "end", "probability"}]}. mlx-whisper's own output shape does not
     match faster-whisper's exactly (check its current docs rather than
     assuming), so this normalisation step is the real work.
  2. One more entry in BACKENDS, e.g. "mps": MlxWhisperBackend.
  3. Extend WHISPER_DEVICE's accepted values in worker/config.py's own
     comment.
  4. worker/activities.py's _free_vram() only clears a CUDA cache today;
     an MLX backend would want its own equivalent
     (mlx.core.metal.clear_cache() as of this writing) added there too,
     guarded the same way the existing torch.cuda branch is.

Nothing else in the pipeline needs to change: load_backend() is the only
thing worker/activities.py calls, and it already only sees plain dicts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class TranscribeBackend(Protocol):
    def transcribe(self, audio_path: Path) -> list[dict]:
        """One entry per speech segment, in order:

            {"text": str, "words": [{"word": str, "start": float,
             "end": float, "probability": float}, ...]}

        Timings are load-bearing, not decorative -- worker/activities.py's
        transcribe() docstring explains why (masking identifiers in the
        waveform, realigning annotator corrections). A backend with no
        word-level timing support is not a drop-in replacement here.
        """
        ...


class FasterWhisperBackend:
    """The only backend today. CPU or CUDA, never Metal -- see this
    module's own docstring for why that is a library limitation, not a
    configuration one."""

    def __init__(self, model_name: str, device: str, compute_type: str) -> None:
        from faster_whisper import WhisperModel
        self._model = WhisperModel(model_name, device=device, compute_type=compute_type)

    def transcribe(self, audio_path: Path) -> list[dict]:
        # vad_filter runs Silero VAD ahead of Whisper and skips segments
        # with no detected speech; condition_on_previous_text=False stops
        # each segment being conditioned on the text just before it.
        # Together these are the two standard mitigations for Whisper's
        # documented tendency to hallucinate a repeat of recent text
        # across a silence gap in long-form audio (near-zero audio
        # embeddings during silence give the model nothing to anchor on,
        # so it falls back to echoing whatever it decoded last). Confirmed
        # on this project's own audio: a ~4 minute recording produced a
        # 5-9 word phrase, spoken once in the real audio, transcribed a
        # second time at an unrelated point purely as a transcription
        # artifact -- every individual sub-clip of that same recording
        # transcribed clean in isolation, so the audio was never at
        # fault. Without vad_filter, faster-whisper falls back to
        # trusting its own decoded timestamp tokens for segment
        # boundaries, which is the same mechanism that lets the
        # hallucination happen in the first place.
        segments, _ = self._model.transcribe(
            str(audio_path),
            word_timestamps=True,
            language="en",
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return [
            {
                "text": segment.text,
                "words": [
                    {
                        "word": word.word,
                        "start": round(word.start, 4),
                        "end": round(word.end, 4),
                        "probability": round(word.probability, 4),
                    }
                    for word in (segment.words or [])
                ],
            }
            for segment in segments
        ]


# One entry per supported device. A device with no entry fails loudly and
# specifically here, rather than reaching faster-whisper's own generic
# "unsupported device" error with no pointer back to this module.
BACKENDS: dict[str, type] = {
    "cuda": FasterWhisperBackend,
    "cpu": FasterWhisperBackend,
}


def load_backend(model_name: str, device: str, compute_type: str) -> TranscribeBackend:
    backend_cls = BACKENDS.get(device)
    if backend_cls is None:
        raise ValueError(
            f"no transcription backend registered for device={device!r}. "
            f"Known devices: {sorted(BACKENDS)}. See this module's own "
            "docstring for how to add one, e.g. mlx-whisper for Apple "
            "Silicon Metal."
        )
    return backend_cls(model_name, device, compute_type)
