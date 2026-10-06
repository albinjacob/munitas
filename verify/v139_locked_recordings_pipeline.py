"""U140: a real de-identification run over recordings that are locked under their patients' keys, and again after one patient is erased.

U137 and U138 prove the opening of a locked recording at the platform and at the worker's own client. This proves the thing they exist for:
the whole pipeline (transcribe, find, redact, score) reading locked recordings and locked answer keys through the platform, with real speech,
and a patient who was erased left out of the next run.

It runs on the host, because the speech model and the sample recording live on this machine.

    $env:PG_DSN = "postgresql://munitas:munitas@localhost:5432/platform"
    $env:MUNITAS_VERIFY_KRATOS = "http://localhost:4433"
    $env:S3_ENDPOINT = "http://localhost:8333"
    .venv\\Scripts\\python.exe verify\\v139_locked_recordings_pipeline.py

It needs `python -m worker.main` running on the machine with the graphics card. Expect it to take several minutes, twice.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import CANARY, ENGINEER, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402
from v128_person_keys_erasure import count, register_dataset, reset_keys  # noqa: E402
from v137_locked_recordings import upload_whole  # noqa: E402

SAMPLE = Path(__file__).resolve().parents[1] / "docs" / "audio-samples" / "cardiology-consult"
CEILING_SECONDS = 25 * 60
POLL_SECONDS = 15


def wait_for(run_id: str, headers: dict) -> dict:
    began = time.monotonic()
    run: dict = {}
    seen: set[str] = set()
    while time.monotonic() - began < CEILING_SECONDS:
        run = api("GET", f"/pipeline-runs/{run_id}", headers=headers).json()
        for step in run["steps"]:
            if step["action"] not in seen:
                seen.add(step["action"])
                print(f"    {step['action']} started")
        if run["status"] != "running":
            break
        time.sleep(POLL_SECONDS)
    run["minutes"] = (time.monotonic() - began) / 60
    return run


def opened_by_run(run_id: str) -> list[dict]:
    """Key-log entries for every step of one pipeline run: the steps that open action runs, and the scoring step, which acts as the pipeline run."""
    with db() as conn:
        return list(conn.execute(
            """select k.person_code, k.purpose from key_use_log k
                where k.tenant_id = %s and k.action = 'unwrap'
                  and (k.actor = 'pipeline_run ' || %s::text
                       or k.actor in (select 'action_run ' || id::text from action_run where pipeline_run_id = %s))""", (CANARY, run_id, run_id)).fetchall())


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    engineer = bearer_for(ENGINEER)
    wav = (SAMPLE / "cardiology-consult-synthetic.wav").read_bytes()
    truth = (SAMPLE / "truth.json").read_bytes()
    tag = uuid.uuid4().hex[:8]
    first, second = f"A-{tag}-cardiac", f"B-{tag}-cardiac"

    heading("Two patients' consultations, locked, with their answer keys")
    dataset = register_dataset(ENGINEER)
    for stem, patient in (("c-0001", first), ("c-0002", second)):
        upload_whole(dataset, f"{stem}.wav", wav, patient)
        upload_whole(dataset, f"{stem}.truth.json", truth, patient)
    sealed = api("POST", f"/datasets/{dataset}/seal-recordings", headers=engineer)
    check("the locked recordings seal as a version the pipeline can read", sealed.status_code == 201, sealed.text[:200])
    if sealed.status_code != 201:
        return summary("v139")
    version = sealed.json()["id"]
    with db() as conn:
        stored = [m["key"].rsplit("/", 1)[-1] for m in conn.execute("select object_manifest from dataset_version where id = %s", (version,)).fetchone()["object_manifest"]]
    check("both recordings and both answer keys are stored", sorted(n for n in stored if n.endswith((".wav", ".truth.json"))) == ["c-0001.truth.json", "c-0001.wav", "c-0002.truth.json", "c-0002.wav"], str(stored))

    heading("The whole pipeline runs over them")
    started = api("POST", f"/datasets/{dataset}/versions/{version}/deidentify", headers=engineer)
    check("the run starts", started.status_code == 202, f"{started.status_code} {started.text[:240]}")
    if started.status_code != 202:
        return summary("v139")
    run_id = started.json()["pipeline_run_id"]
    print(f"    run {run_id}")
    run = wait_for(run_id, engineer)
    check("the run completes", run.get("status") == "succeeded", f"{run.get('status')} {run.get('error') or ''} after {run['minutes']:.1f} minutes")
    steps = {s["action"]: s["status"] for s in run.get("steps", [])}
    check("every step succeeded", bool(steps) and all(v == "succeeded" for v in steps.values()), str(steps))
    gate = run.get("gate_decision") or {}
    metrics = gate.get("metrics") or {}
    check("it was scored against the answer keys, which were locked", "recall_effective" in metrics and metrics.get("ground_truth_spans", 1) != 0, str(sorted(metrics))[:200])
    print(f"    recall {metrics.get('recall_effective')}, spans {metrics.get('ground_truth_spans')}")

    heading("Every opening is in the key log, with the run's own step named")
    log = opened_by_run(run_id)
    purposes = {}
    for row in log:
        purposes.setdefault(row["purpose"].split(":")[-1].strip()[:40], 0)
        purposes[row["purpose"].split(":")[-1].strip()[:40]] += 1
    print(f"    {len(log)} openings: {purposes}")
    verify_openings = [r for r in log if "answer key" in r["purpose"]]
    check("both answer keys were opened by the scoring step", len(verify_openings) == 2, str(len(verify_openings)))
    audio_openings = [r for r in log if "open a recording" in r["purpose"] and "answer key" not in r["purpose"]]
    check("each recording was opened at least once for the speech and redaction steps", len(audio_openings) >= 2, str(len(audio_openings)))
    check("the log names both patients, by code only", len({r["person_code"] for r in log}) == 2)

    heading("A patient who was erased is left out of the next run")
    with db() as conn:
        code = conn.execute("select locked_person_code from dataset_source where dataset_id = %s and locator = 'c-0002.wav'", (dataset,)).fetchone()["locked_person_code"]
        # The row change the key service's own destroy makes: the wrapped key goes and the date is written, together.
        conn.execute("update subject_key set wrapped_key = null, destroyed_at = now() where tenant_id = %s and person_code = %s", (CANARY, code))
    again = api("POST", f"/datasets/{dataset}/versions/{version}/deidentify", headers=engineer)
    check("a run still starts", again.status_code == 202, f"{again.status_code} {again.text[:240]}")
    if again.status_code != 202:
        return summary("v139")
    second_id = again.json()["pipeline_run_id"]
    print(f"    run {second_id}")
    rerun = wait_for(second_id, engineer)
    check("it completes", rerun.get("status") == "succeeded", f"{rerun.get('status')} {rerun.get('error') or ''} after {rerun['minutes']:.1f} minutes")
    after = opened_by_run(second_id)
    check("nothing of the erased patient was opened", all(r["person_code"] != code for r in after) and len(after) > 0, f"{len(after)} openings")
    check("the other patient's recording still went through", any(r["person_code"] != code for r in after))
    check("and only that patient's answer key was opened for scoring", len([r for r in after if "answer key" in r["purpose"]]) == 1,
          str(len([r for r in after if "answer key" in r["purpose"]])))
    reset_keys()
    return summary("v139")


if __name__ == "__main__":
    sys.exit(main())
