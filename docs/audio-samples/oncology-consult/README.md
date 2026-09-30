# Oncology consult, synthetic audio sample

A fictional Indian doctor/patient conversation (breast-cancer follow-up:
chemotherapy tolerance, scan results, treatment plan), synthesized with
[`ai4bharat/indic-parler-tts`](https://huggingface.co/ai4bharat/indic-parler-tts)
(Apache-2.0), the same recipe as `docs/audio-samples/cardiology-consult/`.
Built as a second, topically distinct test fixture. This one is meant to
be uploaded to Munitas **without** its `truth.json`, to exercise the
"real production data, no ground truth" path the built-in `deidentify`
pipeline currently refuses to start on.

**Files:**
- `transcript.txt`: the dialogue script. `DOCTOR:`/`PATIENT:` lines, one
  per turn.
- `build_truth.py`: pure text processing, no GPU: derives `truth.json`
  (entity spans + normalized reference transcript) from `transcript.txt`.
  Already run; `truth.json` is checked in.
- `synthesize.py`: regenerates the audio from the transcript. Doctor voice
  is "Mary", patient voice is "Thoma", with description prompts and the
  same denoise/trim/fade/loudness filter chain as the cardiology sample.
  See the `indic-parler-tts-synthesis-recipe` memory for the reasoning
  behind the specific filter settings.
- `oncology-consult-synthetic.wav`: the finished audio. Generated from
  `transcript.txt` via `synthesize.py`; needs a local GPU to (re)build.

**To regenerate the audio:** needs a local GPU, a Python 3.12 venv with
`torch`/`torchaudio` (matched CUDA build) and `parler-tts` installed, and a
Hugging Face account already granted access to the gated
`ai4bharat/indic-parler-tts` repo. Full setup steps are in the
`indic-parler-tts-synthesis-recipe` memory.

**Used to prove the no-ground-truth path, 2026-08-20.** Uploaded only
`oncology-consult-synthetic.wav` (withholding `truth.json`), confirmed the
built-in `deidentify` pipeline refuses it live, then ran it through
`web/public/pipeline-templates/redact-without-scoring/` end to end: all six
steps succeeded, gate decision reached with an honest `fail`
recommendation (nothing was scored, so nothing claimed to pass). See that
template's own README for what the run found and fixed along the way.

**Content note:** the conversation and all names in it are entirely
fictional, written for this test fixture. No real patient data.
