# Cardiology consult, synthetic audio sample

A fictional Indian doctor/patient conversation, synthesized end to end with
[`ai4bharat/indic-parler-tts`](https://huggingface.co/ai4bharat/indic-parler-tts)
(Apache-2.0), created as a richer audio test fixture than the silent WAV
`verify/common.py`'s `tiny_wav()` produces, one with real (synthetic) speech
content to exercise transcription/de-identification against.

**Files:**
- `transcript.txt`: the dialogue script. `DOCTOR:`/`PATIENT:` lines, one per
  turn.
- `synthesize.py`: regenerates the audio from the transcript. Doctor voice
  is "Mary", patient voice is "Thoma" (indic-parler-tts's own recommended
  English speakers), with description prompts and an audio post-processing
  chain (denoise, trim, fade, loudness normalize) tuned by ear across many
  rounds of comparison. See the `indic-parler-tts-synthesis-recipe` memory
  for the reasoning behind the specific filter settings.
- `cardiology-consult-synthetic.wav`: the finished audio, ~3m26s, 16kHz
  mono PCM. Can be regenerated from `transcript.txt` via `synthesize.py` if
  it's ever lost or needs to be redone.

**To regenerate:** needs a local GPU, a Python 3.12 venv with
`torch`/`torchaudio` (matched CUDA build) and `parler-tts` installed, and a
Hugging Face account that has been granted access to the gated
`ai4bharat/indic-parler-tts` repo (visit the model page and request access
once). Full setup steps are in the `indic-parler-tts-synthesis-recipe`
memory.

**Content note:** the conversation and all names in it are entirely
fictional, written for this test fixture. No real patient data.
