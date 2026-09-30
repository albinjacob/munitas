"""U66: an operator-registered DAG pipeline, from registration to a gate decision.

Runs on the host, matching v63's own reasoning: it needs a live worker on
this machine's Docker daemon for the sandboxed script steps.

    .venv\\Scripts\\python.exe verify\\v66_pipeline_dag.py

Needs PG_DSN, VERIFY_KRATOS and VERIFY_API pointing at the published localhost
ports, the same as v63.
"""

from __future__ import annotations

import io
import sys
import time
import uuid
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
import common  # noqa: E402
from common import check, heading, summary  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "platform" / "api"))
from app.pipeline_dag import (  # noqa: E402
    BUILTIN_BLOCKS, DagConfigError, parse_dag_config, validate_dag,
)

FIXTURE_YAML = """
name: fixture-dag
steps:
  - name: count
    kind: script
    script: steps/count.py
  - name: decide
    kind: gate
    depends_on: [count]
    to_class: AL
    inputs:
      recommendation: "${steps.count.recommendation}"
      recommendation_reason: "${steps.count.reason}"
"""

FIXTURE_SCRIPT = b"""
import json
print(json.dumps({"recommendation": "pass", "reason": "fixture"}))
"""


def _fixture_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("steps/count.py", FIXTURE_SCRIPT)
    return buf.getvalue()


def _department_id() -> str:
    with common.db() as conn:
        row = conn.execute(
            "select id from department where tenant_id = %s and name = %s",
            (common.CANARY, common.DEPARTMENT),
        ).fetchone()
        if not row:
            raise RuntimeError(
                f"no department named {common.DEPARTMENT!r} in tenant "
                f"{common.CANARY!r}; seed it first"
            )
        return str(row["id"])


def u66a_config_validation() -> None:
    heading("U66a: the DAG validator refuses what it should, and only that")

    valid = parse_dag_config("""
name: fixture
steps:
  - name: transcribe
    kind: builtin
    block: transcribe
  - name: score
    kind: script
    script: steps/score.py
    depends_on: [transcribe]
    inputs:
      records_key: "${steps.transcribe.records_key}"
  - name: decide
    kind: gate
    depends_on: [score]
    to_class: AL
    inputs:
      recommendation: "${steps.score.recommendation}"
      recommendation_reason: "${steps.score.reason}"
""")
    check("a well-formed DAG has no problems",
          validate_dag(valid, {"steps/score.py"}) == [])

    cyclic = parse_dag_config("""
name: fixture
steps:
  - name: a
    kind: script
    script: a.py
    depends_on: [b]
  - name: b
    kind: script
    script: b.py
    depends_on: [a]
""")
    problems = validate_dag(cyclic, {"a.py", "b.py"})
    check("a two-step cycle is refused",
          any("cycle" in p.lower() for p in problems), str(problems))

    dangling = parse_dag_config("""
name: fixture
steps:
  - name: a
    kind: script
    script: a.py
    depends_on: [nonexistent]
""")
    problems = validate_dag(dangling, {"a.py"})
    check("a depends_on naming a step that does not exist is refused",
          any("nonexistent" in p for p in problems), str(problems))

    missing_script = parse_dag_config("""
name: fixture
steps:
  - name: a
    kind: script
    script: not_in_zip.py
""")
    problems = validate_dag(missing_script, {"a.py"})
    check("a script step whose file is not in the zip is refused",
          any("not_in_zip.py" in p for p in problems), str(problems))

    no_gate = parse_dag_config("""
name: fixture
steps:
  - name: a
    kind: script
    script: a.py
""")
    problems = validate_dag(no_gate, {"a.py"})
    check("a DAG with no terminal gate step is refused",
          any("gate" in p.lower() for p in problems), str(problems))

    two_gates = parse_dag_config("""
name: fixture
steps:
  - name: a
    kind: gate
    to_class: AL
    inputs: {recommendation: "pass", recommendation_reason: "x"}
  - name: b
    kind: gate
    to_class: AL
    inputs: {recommendation: "pass", recommendation_reason: "x"}
""")
    problems = validate_dag(two_gates, set())
    check("a DAG with two terminal gate steps is refused",
          any("gate" in p.lower() for p in problems), str(problems))

    check("BUILTIN_BLOCKS names the platform's real steps",
          BUILTIN_BLOCKS == {"adopt_version", "transcribe", "detect", "handoff",
                             "redact", "verify"})

    try:
        parse_dag_config("not: [valid, yaml: at all")
        check("malformed YAML raises DagConfigError", False)
    except DagConfigError:
        check("malformed YAML raises DagConfigError", True)


def u66b_registration_and_upload() -> str | None:
    heading("U66b: registering a pipeline and sealing a version")
    headers = common.bearer_for("canary-engineer")
    department_id = _department_id()

    r = httpx.post(f"{common.API}/pipelines/register", json={
        "tenant_id": common.CANARY, "name": f"fixture-{uuid.uuid4().hex[:8]}",
        "department_id": department_id, "registered_by": "canary-engineer",
    }, headers=headers, timeout=15.0)
    check("registering a pipeline succeeds", r.status_code == 201, r.text[:300])
    if r.status_code != 201:
        return None
    pipeline_id = r.json()["id"]

    r = httpx.post(
        f"{common.API}/pipelines/{pipeline_id}/versions/upload",
        data={"registered_by": "canary-engineer"},
        files={
            "config_file": ("pipeline.yaml", FIXTURE_YAML.encode(), "application/x-yaml"),
            "scripts": ("scripts.zip", _fixture_zip(), "application/zip"),
        },
        headers=headers, timeout=15.0,
    )
    check("uploading a valid version succeeds", r.status_code == 201, r.text[:300])
    version_id = r.json()["id"] if r.status_code == 201 else None

    broken_yaml = FIXTURE_YAML.encode().replace(b"count.py", b"missing.py")
    r = httpx.post(
        f"{common.API}/pipelines/{pipeline_id}/versions/upload",
        data={"registered_by": "canary-engineer"},
        files={
            "config_file": ("pipeline.yaml", broken_yaml, "application/x-yaml"),
            "scripts": ("scripts.zip", _fixture_zip(), "application/zip"),
        },
        headers=headers, timeout=15.0,
    )
    check("a version naming a script not in the zip is refused",
          r.status_code == 400, r.text[:300])

    return version_id


def _sealed_dataset_version(headers: dict, department_id: str) -> tuple[str, str]:
    """Register a dataset, upload one file, seal it. Returns
    (dataset_id, dataset_version_id)."""
    r = httpx.post(f"{common.API}/datasets/register", json={
        "tenant_id": common.CANARY, "name": f"fixture-v66-{uuid.uuid4().hex[:8]}",
        "department_id": department_id, "registered_by": "canary-engineer",
        "provenance": "external_public",
    }, headers=headers, timeout=15.0)
    r.raise_for_status()
    dataset_id = r.json()["id"]

    r = httpx.post(
        f"{common.API}/datasets/{dataset_id}/files",
        files={"file": ("note.txt", b"hello", "text/plain")},
        headers=headers, timeout=15.0,
    )
    r.raise_for_status()

    r = httpx.post(f"{common.API}/datasets/{dataset_id}/seal",
                   headers=headers, timeout=15.0)
    r.raise_for_status()
    return dataset_id, r.json()["id"]


def u66c_end_to_end_run(pipeline_version_id: str | None) -> None:
    heading("U66c: a registered DAG runs end to end and reaches a gate decision")
    if not pipeline_version_id:
        check("a pipeline version was available to run", False,
              "U66b did not produce one; skipping U66c")
        return

    headers = common.bearer_for("canary-engineer")
    department_id = _department_id()
    dataset_id, source_version_id = _sealed_dataset_version(headers, department_id)

    r = httpx.post(
        f"{common.API}/datasets/{dataset_id}/versions/{source_version_id}/deidentify",
        json={"pipeline_kind": "dag", "pipeline_version_id": pipeline_version_id},
        headers=headers, timeout=15.0,
    )
    check("starting a dag run succeeds", r.status_code == 202, r.text[:300])
    if r.status_code != 202:
        return
    pipeline_run_id = r.json()["pipeline_run_id"]

    data = None
    for _ in range(30):
        r = httpx.get(f"{common.API}/pipeline-runs/{pipeline_run_id}",
                      headers=headers, timeout=15.0)
        data = r.json()
        if data.get("gate_decision"):
            break
        time.sleep(2)
    else:
        check("the run reached a gate decision within 60s", False, str(data))
        return

    check("the run reached a gate decision within 60s", True)
    check("the gate decision recommends what the fixture script decided",
          data["gate_decision"]["recommendation"] == "pass", str(data["gate_decision"]))
    check("a pipeline_step_run row exists for each step",
          len(data["steps"]) == 2, str(data["steps"]))
    check("the run reports the pipeline's own name",
          data.get("pipeline_name") is not None, str(data.get("pipeline_name")))


def u66d_wait_for_human_refused() -> None:
    heading("U66d: a DAG with a wait_for_human step is refused at run start")
    headers = common.bearer_for("canary-engineer")
    department_id = _department_id()

    yaml_with_wait = """
name: fixture-wait
steps:
  - name: pause
    kind: wait_for_human
    target: label_studio
  - name: decide
    kind: gate
    depends_on: [pause]
    to_class: AL
    inputs:
      recommendation: "${steps.pause.recommendation}"
      recommendation_reason: "${steps.pause.reason}"
"""
    r = httpx.post(f"{common.API}/pipelines/register", json={
        "tenant_id": common.CANARY, "name": f"fixture-wait-{uuid.uuid4().hex[:8]}",
        "department_id": department_id, "registered_by": "canary-engineer",
    }, headers=headers, timeout=15.0)
    check("registering a pipeline with a wait_for_human step still succeeds",
          r.status_code == 201, r.text[:300])
    if r.status_code != 201:
        return
    pipeline_id = r.json()["id"]

    r = httpx.post(
        f"{common.API}/pipelines/{pipeline_id}/versions/upload",
        data={"registered_by": "canary-engineer"},
        files={
            "config_file": ("pipeline.yaml", yaml_with_wait.encode(), "application/x-yaml"),
            "scripts": ("scripts.zip", _fixture_zip(), "application/zip"),
        },
        headers=headers, timeout=15.0,
    )
    check("a version with a wait_for_human step is still sealed (schema accepts it)",
          r.status_code == 201, r.text[:300])
    if r.status_code != 201:
        return
    version_id = r.json()["id"]

    department_id = _department_id()
    dataset_id, source_version_id = _sealed_dataset_version(headers, department_id)
    r = httpx.post(
        f"{common.API}/datasets/{dataset_id}/versions/{source_version_id}/deidentify",
        json={"pipeline_kind": "dag", "pipeline_version_id": version_id},
        headers=headers, timeout=15.0,
    )
    check("starting it is refused rather than hanging or silently skipping the step",
          r.status_code >= 400, r.text[:300])


def main() -> int:
    u66a_config_validation()
    version_id = u66b_registration_and_upload()
    u66c_end_to_end_run(version_id)
    u66d_wait_for_human_refused()
    return summary("U66")


if __name__ == "__main__":
    raise SystemExit(main())
