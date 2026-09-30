"""U68: a real recording with no answer key, through redact-without-scoring.

Real recordings never come with an answer key, so checking a
de-identification without one has to be possible. This proves that case
is handled honestly rather than silently: the built-in 'deidentify' pipeline
refuses such a version outright, and the operator-registered
`redact-without-scoring` DAG template (web/public/pipeline-templates/) runs
the identical redaction activities and reaches a conservative gate decision
instead of a fabricated one.

Runs on the host, matching v66's own reasoning: it needs a live worker on
this machine for the real transcribe/detect/handoff/redact activities (not
sandboxed script steps this time, but the same host requirement -- GPU
models only worker.main loads). Several minutes; real ASR and detection
run against a ~2m16s recording.

    .venv\\Scripts\\python.exe verify\\v68_production_case_no_ground_truth.py

Needs PG_DSN, VERIFY_KRATOS and VERIFY_API pointing at the published
localhost ports, the same as v66, plus `python -m worker.main` running.
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

REPO = Path(__file__).parent.parent
WAV_PATH = REPO / "docs" / "audio-samples" / "oncology-consult" / "oncology-consult-synthetic.wav"
DAG_YAML = (REPO / "web" / "public" / "pipeline-templates" / "redact-without-scoring"
            / "redact_without_scoring.yaml")


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


def u68a_register_template(headers: dict, dept_id: str) -> str | None:
    heading("U68a: the committed redact-without-scoring template registers and seals")

    r = httpx.post(f"{common.API}/pipelines/register", json={
        "tenant_id": common.CANARY,
        "name": f"redact-without-scoring-{uuid.uuid4().hex[:8]}",
        "department_id": dept_id, "registered_by": "canary-engineer",
    }, headers=headers, timeout=15.0)
    check("registering the pipeline succeeds", r.status_code == 201, r.text[:400])
    if r.status_code != 201:
        return None
    pipeline_id = r.json()["id"]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # Every step in this DAG is a builtin block, so the zip only needs
        # to exist -- there is no script for it to be missing.
        zf.writestr(".keep", b"")

    r = httpx.post(
        f"{common.API}/pipelines/{pipeline_id}/versions/upload",
        data={"registered_by": "canary-engineer"},
        files={
            "config_file": ("redact_without_scoring.yaml",
                            DAG_YAML.read_bytes(), "application/x-yaml"),
            "scripts": ("scripts.zip", buf.getvalue(), "application/zip"),
        },
        headers=headers, timeout=15.0,
    )
    check("uploading the committed template as a version succeeds",
          r.status_code == 201, r.text[:600])
    check("the sealed version has all 6 declared steps",
          r.json().get("step_count") == 6, r.text[:400])
    return r.json()["id"] if r.status_code == 201 else None


def u68b_real_recording_no_truth(headers: dict, dept_id: str) -> tuple[str, str]:
    heading("U68b: a real recording seals with no answer key")

    r = httpx.post(f"{common.API}/datasets/register", json={
        "tenant_id": common.CANARY,
        "name": f"oncology-production-case-{uuid.uuid4().hex[:8]}",
        "department_id": dept_id, "registered_by": "canary-engineer",
        "provenance": "external_public",
    }, headers=headers, timeout=15.0)
    check("registering the dataset succeeds", r.status_code == 201, r.text[:400])
    r.raise_for_status()
    dataset_id = r.json()["id"]

    r = httpx.post(
        f"{common.API}/datasets/{dataset_id}/files",
        files={"file": ("oncology-consult.wav", WAV_PATH.read_bytes(), "audio/wav")},
        headers=headers, timeout=60.0,
    )
    check("uploading the recording succeeds", r.status_code == 201, r.text[:400])
    r.raise_for_status()
    check("no truth.json was uploaded for this recording -- the whole point", True)

    r = httpx.post(f"{common.API}/datasets/{dataset_id}/seal-audio",
                   headers=headers, timeout=30.0)
    check("sealing as a recordings version succeeds", r.status_code == 201, r.text[:600])
    r.raise_for_status()
    return dataset_id, r.json()["id"]


def u68c_deidentify_refuses(headers: dict, dataset_id: str, version_id: str) -> None:
    heading("U68c: the built-in 'deidentify' pipeline refuses this exact version")

    r = httpx.post(
        f"{common.API}/datasets/{dataset_id}/versions/{version_id}/deidentify",
        json={"pipeline_kind": "deidentify"},
        headers=headers, timeout=15.0,
    )
    check(
        "refused with 400 and names the missing answer key, not a silent "
        "acceptance or a crash deep inside verify",
        r.status_code == 400 and "answer key" in r.text,
        f"status={r.status_code} body={r.text[:400]}",
    )


def u68d_dag_reaches_honest_gate(headers: dict, dataset_id: str, version_id: str,
                                 pipeline_version_id: str | None) -> None:
    heading("U68d: the DAG runs the real activities and reaches an honest gate decision")
    if not pipeline_version_id:
        check("a pipeline version was available to run", False,
              "U68a did not produce one; skipping U68d")
        return

    r = httpx.post(
        f"{common.API}/datasets/{dataset_id}/versions/{version_id}/deidentify",
        json={"pipeline_kind": "dag", "pipeline_version_id": pipeline_version_id},
        headers=headers, timeout=15.0,
    )
    check("starting the dag run succeeds", r.status_code == 202, r.text[:400])
    if r.status_code != 202:
        return
    pipeline_run_id = r.json()["pipeline_run_id"]

    data = None
    for _ in range(90):  # up to 6 minutes -- real transcribe/detect/redact
        r = httpx.get(f"{common.API}/pipeline-runs/{pipeline_run_id}",
                      headers=headers, timeout=15.0)
        data = r.json()
        if data.get("gate_decision"):
            break
        time.sleep(4)
    else:
        check("the run reached a gate decision within 6 minutes", False, str(data))
        return

    check("the run reached a gate decision within 6 minutes", True)
    gate = data["gate_decision"]

    check(
        "gate recommendation is 'fail', the conservative default -- 'pass' "
        "would claim a measurement that never happened",
        gate.get("recommendation") == "fail", str(gate),
    )
    check("gate reason names the missing ground truth",
          "ground truth" in (gate.get("recommendation_reason") or ""), str(gate))
    check("gate state is still 'pending' -- a human decides regardless of "
          "the recommendation, same as the scored pipeline",
          gate.get("state") == "pending", str(gate))

    step_names = sorted(s.get("action") for s in data.get("steps", []))
    check(
        "exactly the 6 expected steps ran: adopt, transcribe, detect, "
        "handoff, redact, gate -- verify is NOT among them, because there "
        "was nothing to score against",
        step_names == ["adopt", "detect", "gate", "handoff", "redact", "transcribe"],
        str(step_names),
    )


def main() -> int:
    if not WAV_PATH.exists():
        heading("U68: production case, no ground truth")
        check(f"{WAV_PATH.name} exists", False,
              f"missing at {WAV_PATH} -- see docs/audio-samples/oncology-consult/README.md "
              f"to regenerate it (needs a local GPU)")
        return summary("U68")

    headers = common.bearer_for("canary-engineer")
    dept_id = _department_id()

    pipeline_version_id = u68a_register_template(headers, dept_id)
    dataset_id, version_id = u68b_real_recording_no_truth(headers, dept_id)
    u68c_deidentify_refuses(headers, dataset_id, version_id)
    u68d_dag_reaches_honest_gate(headers, dataset_id, version_id, pipeline_version_id)

    return summary("U68")


if __name__ == "__main__":
    raise SystemExit(main())
