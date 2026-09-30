# Redact without scoring

A pipeline template for real production recordings: audio with no answer
key, unlike every test/synthetic fixture in this repo. The built-in
"Deidentify" pipeline refuses to start against a version like that (see
`platform/api/app/pipeline.py`'s `MAX_NAMED` check): it needs a reference to
score recall/leaks against, and there is none for real data. This DAG runs
the identical redaction steps and skips only the scoring half.

**Proven working end to end** against a real synthesized recording
(`docs/audio-samples/oncology-consult/`), 2026-08-20: registered, sealed
without a `truth.json`, confirmed the built-in pipeline refuses it, ran this
DAG instead, reached a gate decision. Two real bugs were found and fixed
along the way: `adopt_version` was missing from the DAG framework's
`BUILTIN_BLOCKS` (now fixed in `worker/dag_workflow.py` and
`platform/api/app/pipeline_dag.py`), and an earlier draft of this template
used a `recommendation` value the database schema doesn't allow (`review`
instead of `pass`/`fail`). Both are fixed in the file here.

## What it does

Chains the platform's own real activities, not reimplementations:
`adopt_version` (reads the sealed recording) → `transcribe` (faster-whisper)
→ `detect` (the Presidio/spaCy/GLiNER ensemble) → `handoff` (Label Studio
review tasks) and `redact` (surrogate text + masked audio) in parallel →
a `gate` step that writes a `fail` recommendation directly, with a reason
explaining why: nothing here measured whether the redaction worked, so
nothing here should claim it did.

`state` still starts `pending` regardless of that recommendation; a human
always decides, exactly like the scored pipeline. The only difference is
that reviewer sees an honest "not scored" flag instead of a fabricated
number.

## Using it

1. **Register a pipeline** in the console (Pipelines → Register a pipeline).
2. **Upload a version**: this file (`redact_without_scoring.yaml`) as the
   config, and any zip as the scripts bundle. Every step here is a
   `builtin` block, so nothing needs a custom script. An empty zip works.
3. Upload a real recording (just the `.wav`, no `.truth.json`) through the
   normal upload path, seal it with **Seal as recordings**.
4. From that version's page, choose this registered pipeline instead of
   "Deidentify" and start a run.

Nothing here names a tenant, a dataset, or a specific recording. Register
it once per organisation and reuse it for every real recording that has no
ground truth.

## Before you register this as-is

Two lines in the YAML are opinions, not facts:

- **`to_class`** matches the built-in pipeline's own default
  (`OPEN_FOR_ANNOTATION`). Change it if your organisation routes
  redacted-but-unscored output somewhere else.
- **`recommendation: fail`** is deliberate, not a placeholder. The database
  only allows `pass` or `fail` (`platform/schema.sql`'s
  `gate_decision_recommendation_check`); there is no `review` state at
  that level. `pass` would claim a measurement that never happened, so this
  defaults to the conservative answer. Do not change it to `pass` without
  adding real scoring first.
