# Orthopaedics consult, synthetic audio sample

A fictional Indian doctor/patient conversation (an acute wrist-fracture
visit, not a follow-up), synthesized with
[`ai4bharat/indic-parler-tts`](https://huggingface.co/ai4bharat/indic-parler-tts)
(Apache-2.0), the same recipe as `docs/audio-samples/cardiology-consult/`
and `docs/audio-samples/oncology-consult/`. A third, deliberately different
department and a deliberately different script, not the same
identity-check-then-history shape reskinned with new names.

**What's different here, on purpose:** the conversation opens on the
injury itself (a fall, an X-ray already taken, a treatment decision), not
an identity check; that happens mid-conversation instead, woven into
"before I finalize your file." The clinician's name is said only once, not
twice. The content is injury-specific throughout: fracture type, cast vs.
surgery, cast-care red flags, a physiotherapy referral. Nothing here
reuses the chronic-disease-follow-up shape the other two samples share.

**Files:**
- `transcript.txt`: the dialogue script. `DOCTOR:`/`PATIENT:` lines, one
  per turn.
- `build_truth.py`: pure text processing, no GPU: derives `truth.json`
  (entity spans + normalized reference transcript) from `transcript.txt`.
  Already run; `truth.json` is checked in, all 8 spans verified
  individually against the printed reference text.
- `synthesize.py`: regenerates the audio from the transcript. Doctor voice
  is "Meera", patient voice is "Kabir" (deliberately not Mary/Thoma, which
  both other samples already use), with description prompts tuned for this
  scene's own tone (real pain early on, easing into relief once surgery is
  ruled out) and the same denoise/trim/fade/loudness filter chain as the
  other two samples. See the `indic-parler-tts-synthesis-recipe` memory for
  the reasoning behind the specific filter settings and the full voice list.
- `orthopaedics-consult-synthetic.wav`: the finished audio. Generated from
  `transcript.txt` via `synthesize.py`; needs a local GPU to (re)build.

**To regenerate the audio:** needs a local GPU, a Python 3.12 venv with
`torch`/`torchaudio` (matched CUDA build) and `parler-tts` installed, and a
Hugging Face account already granted access to the gated
`ai4bharat/indic-parler-tts` repo. Full setup steps are in the
`indic-parler-tts-synthesis-recipe` memory. The `.venv-tts` venv created for
the oncology sample already has everything installed.

**Content note:** the conversation and all names in it are entirely
fictional, written for this test fixture. No real patient data.
