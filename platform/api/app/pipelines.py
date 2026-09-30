"""Which Temporal workflow a pipeline_run's pipeline_kind actually starts.

The only new thing this module does is read a pipeline_kind value and hand
back the workflow type name that runs it. Mirrors storage.py's own reasoning:
every real decision (can this run start, what contract does it need, what
params does it get) stays with the caller; this just stands between a name
and the literal string Temporal wants, so a caller no longer has to hardcode
which workflow class backs which kind.

Keep this dict's keys equal to platform/schema.sql's pipeline_run_kind_valid
constraint, and to worker/main.py's registered workflows=[...] list. U6x
asserts the last of those three, because two lists in two processes can
drift; the schema constraint is checked the same way postgres checks any
other row, by refusing to insert a kind it does not recognise.
"""

from __future__ import annotations

WORKFLOW_TYPE: dict[str, str] = {
    "deidentify": "DeidentificationPipeline",
    "count_records": "CountRecordsPipeline",
    # One generic workflow class for every operator-registered DAG, not one
    # class per pipeline: which steps run comes from pipeline_run.
    # pipeline_version_id at start time, read by PipelineDagWorkflow itself.
    "dag": "PipelineDagWorkflow",
}


def workflow_type_for(kind: str) -> str:
    if kind not in WORKFLOW_TYPE:
        raise ValueError(
            f"unknown pipeline_kind {kind!r}; known kinds are "
            f"{', '.join(sorted(WORKFLOW_TYPE))}"
        )
    return WORKFLOW_TYPE[kind]
