# Adding a pipeline kind

`starter_pipeline.py`, next to this file, is a working pipeline kind
(`count_records`) already registered in Munitas. It runs on a laptop, no
model and no GPU required. The fastest way to understand this mechanism is to
run that one first, then come back here to adapt it into your own kind.

## Running the starter as it is

1. From the console, seal any dataset version (an ordinary seal or a
   recordings seal both work).
2. Choose **Count records (starter pipeline)** from the pipeline picker and
   start it.
3. Open the run's page. It finishes in seconds and produces a gate decision
   recommending the version for review, the same as a real de-identification
   run would, just without the redaction in between.

## Adapting it into a new kind

Five edits, in this order, each one a numbered comment in
`starter_pipeline.py`:

1. **Name your kind.** Add its value to `platform/schema.sql`'s
   `pipeline_run_kind_valid` CHECK constraint, next to `'deidentify'` and
   `'count_records'`.
2. **Register the dispatch.** Add a `"your_kind": "YourWorkflowClassName"`
   entry to `platform/api/app/pipelines.py`'s `WORKFLOW_TYPE` dict.
3. **Write the activity or activities.** Drop them into
   `worker/activities.py`, following `count_records`'s shape: plain dict in,
   plain dict out. If your kind needs real data, read it the way `transcribe`
   and `detect` do (both in the same file) rather than the way
   `count_records` does, since `count_records` deliberately skips storage.
4. **Write the workflow.** Drop a class into `worker/workflows.py`, following
   `CountRecordsPipeline`'s shape: call your activities, then end with either
   `record_gate_decision` (if your kind scores against ground truth, the way
   the real pipeline does) or `record_simple_gate_decision` (if it does not,
   the way this starter does).
5. **Register both processes.** Add your workflow class and any new activity
   functions to `worker/main.py`'s `workflows=[...]` and `activities=[...]`
   lists, and add your kind's name to the `PIPELINE_KINDS` tuple in that same
   file. A verify script (`verify/v63_pipeline_tenant.py`, section "U63g")
   checks that list against `pipelines.WORKFLOW_TYPE`, so a kind registered
   in only one of the two processes fails loudly rather than hanging the
   first time somebody starts it.

Console changes are optional for a kind you are only testing from the
command line. To offer it from the console, add an option to the pipeline
picker in `web/src/features/ingest/RegisterDataset.tsx` and, if it needs its
own step list, an entry in `web/src/features/pipeline/PipelineRun.tsx`'s
`STEPS_BY_KIND`.

## Other kinds worth building, and what each one reuses

Every one of these is a different sequence of activities that already exist
in `worker/activities.py`; none needs new step logic; each is a matter of
editing `starter_pipeline.py`'s workflow to call a different set of them.

| Kind | Steps it chains | Skips | Who reaches for it |
|---|---|---|---|
| **Bare Bones Starter** (`count_records`, ships as-is) | `count_records` | everything | learning the mechanism, no GPU, no audio |
| **Transcribe Only** | `ingest`/`adopt_version` then `transcribe` | `detect`, `handoff`, `redact` | wants a transcript, not a de-identified release |
| **Audit Pass** | `transcribe` then `detect` then `record_gate_decision` (recommends hold back) | `handoff`, `redact` | scoping how exposed a new dataset is, before committing to the full pipeline |
| **Re-score Only** | `verify` against an already-redacted version | `transcribe`, `detect`, `redact`: the expensive GPU steps | leak-detection rules changed, old outputs need re-checking |
| **Import and Seal Only** | `adopt_version`/`ingest` plus a contract check, then seal | every audio-specific step | data already arrives as text or pre-transcribed |

For any of these, start from `starter_pipeline.py`'s workflow shape and swap
which activities it calls and in what order; the five edits above stay the
same regardless of which one you are building.
