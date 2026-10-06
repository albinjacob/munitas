"""U141: a custom pipeline step is given the recordings only when a second person approved its version, and the platform opens them for it.

A step of an operator's own pipeline can say `recordings: true`. It is then given the run's recordings as files, which means code the platform
did not write holds the sound. So the version that holds it is approved first, by the department's approver and never by whoever uploaded it,
a run of an unapproved version is refused at the start, and the platform refuses the step the recordings again at the moment of opening, in case
a run somehow began. Every opening is in the key log with the step and the pipeline version named, and a patient who was erased is left out.

This is the platform's half, checked here: the file rules, the approval, the refusal at the start, and every case of the two internal routes a
step's worker calls. U142 runs a real pipeline on the host and proves the audio arrives in the sandbox.

    docker compose exec -T munitas-api python /verify/v140_custom_steps_and_recordings.py
"""

from __future__ import annotations

import io
import json
import sys
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

import httpx  # noqa: E402

from common import ADMIN, API, CANARY, WORKER_HEADERS, api, bearer_for, bucket_for, check, db, fixture_tenant, heading, require_api, s3_client, summary, tiny_wav  # noqa: E402
from v128_person_keys_erasure import CUSTODIAN, ENGINEER, RESEARCHER, count, register_dataset, reset_keys  # noqa: E402
from v137_locked_recordings import make_wav, upload_whole  # noqa: E402
from app.pipeline_dag import parse_dag_config, steps_asking_for_recordings, validate_dag  # noqa: E402

ELSEWHERE = "canary-elsewhere"
WORKLOAD = f"{CANARY}-pipeline"
KIND = "chart number"

YAML = """
name: custom-audio
steps:
  - name: inspect
    kind: script
    script: steps/inspect.py
    recordings: true
  - name: decide
    kind: gate
    depends_on: [inspect]
    to_class: OPEN_FOR_ANNOTATION
    inputs:
      recommendation: "${steps.inspect.recommendation}"
      recommendation_reason: "${steps.inspect.reason}"
"""

YAML_WITHOUT = YAML.replace("    recordings: true\n", "")


def scripts_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("steps/inspect.py", "import json\nprint(json.dumps({'recommendation': 'not_measured', 'reason': 'fixture'}))\n")
    return buffer.getvalue()


def reasons_of(response) -> str:
    try:
        detail = response.json().get("detail")
    except Exception:  # noqa: BLE001
        return response.text[:200]
    return json.dumps(detail)


def department() -> str:
    with db() as conn:
        return str(conn.execute("select id from department where tenant_id = %s and name = 'Verification'", (CANARY,)).fetchone()["id"])


def upload_version(pipeline_id: str, yaml_text: str, who: str = ENGINEER) -> httpx.Response:
    return httpx.post(f"{API}/pipelines/{pipeline_id}/versions/upload", data={"registered_by": who},
                      files={"config_file": ("pipeline.yaml", yaml_text.encode(), "application/x-yaml"), "scripts": ("scripts.zip", scripts_zip(), "application/zip")},
                      headers=bearer_for(who), timeout=30.0)


def open_run(inputs: list[str], pipeline_version_id: str | None) -> dict:
    """A pipeline run's row and credential, as the workflow opens them, bound to a version of a pipeline as the start route binds it."""
    made = httpx.post(f"{API}/pipeline-runs", headers=WORKER_HEADERS, timeout=30.0, json={
        "tenant_id": CANARY, "dataset": "custom-audio", "workflow_id": f"u141-{uuid.uuid4()}", "trigger_kind": "manual",
        "triggered_by": ENGINEER, "input_versions": inputs, "principal": WORKLOAD})
    made.raise_for_status()
    run = made.json()
    if pipeline_version_id:
        with db() as conn:
            conn.execute("update pipeline_run set pipeline_version_id = %s where id = %s", (pipeline_version_id, run["pipeline_run_id"]))
    return {"id": run["pipeline_run_id"], "token": run["task_credential"]}


def listing(run: dict, version: str, step: str = "inspect", headers: dict | None = None) -> httpx.Response:
    return httpx.post(f"{API}/internal/dag-step/recordings", headers=headers or WORKER_HEADERS, timeout=60.0,
                      json={"principal": WORKLOAD, "tenant_id": CANARY, "task_credential": run["token"], "dataset_version_id": version, "step": step})


def opening(run: dict, version: str, record: str, step: str | None = "inspect") -> httpx.Response:
    body = {"principal": WORKLOAD, "tenant_id": CANARY, "task_credential": run["token"], "dataset_version_id": version, "record_id": record, "purpose": "given to the step"}
    if step is not None:
        body["step"] = step
    return httpx.post(f"{API}/internal/recordings/open", headers=WORKER_HEADERS, timeout=60.0, json=body)


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    tag = uuid.uuid4().hex[:8]
    a, b = f"A-{tag}-chart", f"B-{tag}-chart"

    heading("What a version file may say")
    ok = parse_dag_config(YAML)
    check("a script step may say it wants the recordings, and is named so", validate_dag(ok, {"steps/inspect.py"}) == [] and steps_asking_for_recordings(ok) == ["inspect"])
    check("a version that says nothing asks for none", steps_asking_for_recordings(parse_dag_config(YAML_WITHOUT)) == [])
    builtin = parse_dag_config("name: x\nsteps:\n  - {name: t, kind: builtin, block: transcribe, recordings: true}\n  - {name: d, kind: gate, depends_on: [t], to_class: OPEN_FOR_ANNOTATION, inputs: {recommendation: pass, recommendation_reason: x}}\n")
    check("a built-in step may not say it, because it already reads what it needs", any("only a script step may" in p for p in validate_dag(builtin, set())), str(validate_dag(builtin, set())))
    odd = parse_dag_config(YAML.replace("recordings: true", "recordings: yes please"))
    check("a value that is not true or false is refused", any("true or false" in p for p in validate_dag(odd, {"steps/inspect.py"})))
    clash = parse_dag_config(YAML.replace("    recordings: true\n", "    recordings: true\n    inputs: {recordings: x}\n"))
    check("a step cannot also take an input named for what it is given", any("cannot also take inputs" in p for p in validate_dag(clash, {"steps/inspect.py"})))

    heading("A pipeline, a version that asks for the recordings, and one that does not")
    dept = department()
    pipeline = api("POST", "/pipelines/register", json={"tenant_id": CANARY, "name": f"custom-{tag}", "department_id": dept, "registered_by": ENGINEER}, headers=bearer_for(ENGINEER))
    check("the pipeline registers", pipeline.status_code == 201, pipeline.text[:160])
    pid = pipeline.json()["id"]
    first = upload_version(pid, YAML)
    check("the version uploads, and says it needs approval and for which step", first.status_code == 201 and first.json()["needs_approval"] is True
          and first.json()["steps_given_recordings"] == ["inspect"], f"{first.status_code} {first.text[:200]}")
    plain_version = upload_version(pid, YAML_WITHOUT)
    check("a version that asks for nothing says it needs no approval", plain_version.status_code == 201 and plain_version.json()["needs_approval"] is False)
    w1, w2 = first.json()["id"], plain_version.json()["id"]
    with db() as conn:
        uploader = conn.execute("select uploaded_by from pipeline_version where id = %s", (w1,)).fetchone()["uploaded_by"]
        approvers = conn.execute("select active_department_approvers(%s::uuid) as a", (dept,)).fetchone()["a"]
    check("who uploaded it is kept", uploader == ENGINEER)
    check("the department's approvers include the custodian, and not the uploader", CUSTODIAN in approvers and ENGINEER not in approvers, str(approvers))

    heading("Recordings to give: one locked version with two patients, and one plain version")
    locked_dataset = register_dataset(ENGINEER)
    wav_a, wav_b = make_wav(300_000), make_wav(200_000)
    upload_whole(locked_dataset, "r-0001.wav", wav_a, a)
    upload_whole(locked_dataset, "r-0002.wav", wav_b, b)
    sealed = api("POST", f"/datasets/{locked_dataset}/seal-recordings", headers=bearer_for(ENGINEER))
    check("the locked recordings seal", sealed.status_code == 201, sealed.text[:160])
    locked_version = sealed.json()["id"]
    plain_dataset = register_dataset(ENGINEER)
    plain_wav = tiny_wav(0.5)
    with httpx.Client(timeout=60.0) as client:
        client.post(f"{API}/datasets/{plain_dataset}/files", headers=bearer_for(ENGINEER), files={"file": ("p-0001.wav", plain_wav, "audio/wav")}).raise_for_status()
    plain_recordings = api("POST", f"/datasets/{plain_dataset}/seal-audio", headers=bearer_for(ENGINEER)).json()["id"]

    heading("Before anyone approves")
    run = open_run([locked_version], w1)
    r = listing(run, locked_version)
    check("the platform will not list the recordings for the step", r.status_code == 403 and "not been approved" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    r = opening(run, locked_version, "r-0001")
    check("nor open one for it", r.status_code == 403 and "not been approved" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    started = api("POST", f"/datasets/{locked_dataset}/versions/{locked_version}/deidentify", json={"pipeline_kind": "dag", "pipeline_version_id": w1}, headers=bearer_for(ENGINEER))
    check("a run of the version is refused at the start, and says why", started.status_code == 400 and "has not been approved" in reasons_of(started) and "inspect" in reasons_of(started), f"{started.status_code} {reasons_of(started)[:200]}")

    heading("Who may approve")
    asking = lambda who: api("POST", f"/pipelines/{pid}/versions/{w1}/approve", json={"note": "reviewed"}, headers=bearer_for(who))  # noqa: E731
    r = asking(ENGINEER)
    check("the person who uploaded it may not", r.status_code == 403, f"{r.status_code} {reasons_of(r)[:200]}")
    r = asking(RESEARCHER)
    check("a researcher may not", r.status_code == 403, f"{r.status_code} {reasons_of(r)[:120]}")
    r = asking(ELSEWHERE)
    check("another department's custodian may not, and is told why", r.status_code == 403 and "department that owns this pipeline" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:200]}")
    r = api("POST", f"/pipelines/{pid}/versions/{w2}/approve", json={"note": "x"}, headers=bearer_for(CUSTODIAN))
    check("a version that gives no step the recordings has nothing to approve", r.status_code == 400 and "nothing to approve" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    r = asking(CUSTODIAN)
    check("the owning department's approver approves it", r.status_code == 200 and r.json()["approved"] is True, f"{r.status_code} {r.text[:200]}")
    r = asking(CUSTODIAN)
    check("approving it again is refused", r.status_code == 409, f"{r.status_code}")
    shown = api("GET", f"/pipelines/{pid}", headers=bearer_for(ENGINEER)).json()["versions"]
    mine = next(v for v in shown if v["id"] == w1)
    check("the pipeline's page says it is approved, by whom, and who uploaded it", mine["approved"] and mine["approved_by"] == CUSTODIAN and mine["uploaded_by"] == ENGINEER and mine["needs_approval"], str({k: mine[k] for k in ("approved", "approved_by", "uploaded_by")}))

    heading("The platform gives the approved step the recordings")
    r = listing(run, locked_version)
    got = {x["record_id"]: x for x in r.json()["recordings"]} if r.status_code == 200 else {}
    check("it lists both, with their plain sizes, none erased", r.status_code == 200 and sorted(got) == ["r-0001", "r-0002"] and got["r-0001"]["bytes"] == len(wav_a)
          and got["r-0002"]["bytes"] == len(wav_b) and not any(x["erased"] for x in got.values()), f"{r.status_code} {r.text[:200]}")
    o = opening(run, locked_version, "r-0001")
    check("it opens one byte for byte", o.status_code == 200 and o.content == wav_a, f"{o.status_code} {len(o.content)}")
    with db() as conn:
        purposes = [r["purpose"] for r in conn.execute("select purpose from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s", (CANARY, f"pipeline_run {run['id']}")).fetchall()]
    check("the opening is in the key log, naming the step, the pipeline and its version", len(purposes) == 1 and f"custom step inspect of pipeline custom-{tag} version 1" in purposes[0], str(purposes))
    r = opening(run, locked_version, "r-0001", step="not-a-step")
    check("a step the version does not declare is refused", r.status_code == 403 and "does not declare" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    other_run = open_run([locked_version], w2)
    r = listing(other_run, locked_version)
    check("a run of the version that asks for nothing is refused the recordings, even named", r.status_code == 403 and "does not declare" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    stray = open_run([plain_recordings], w1)
    r = listing(stray, locked_version)
    check("a version outside the run's own inputs is refused", r.status_code == 403 and "outside the inputs" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    check("a caller without the worker's token gets nothing", listing(run, locked_version, headers=bearer_for(ENGINEER)).status_code == 403)
    heading("A plain recordings version is given the same way")
    plain_run = open_run([plain_recordings], w1)
    r = listing(plain_run, plain_recordings)
    check("it is listed, with its size", r.status_code == 200 and [x["record_id"] for x in r.json()["recordings"]] == ["p-0001"] and r.json()["recordings"][0]["bytes"] == len(plain_wav), f"{r.status_code} {r.text[:160]}")
    o = opening(plain_run, plain_recordings, "p-0001")
    check("and opened byte for byte", o.status_code == 200 and o.content == plain_wav, f"{o.status_code}")
    o = opening(plain_run, plain_recordings, "p-0001", step=None)
    check("without a step named, a plain recording is still not opened through here", o.status_code == 404, f"{o.status_code}")

    heading("A patient who was erased is left out")
    with db() as conn:
        code = conn.execute("select locked_person_code from dataset_source where dataset_id = %s and locator = 'r-0001.wav'", (locked_dataset,)).fetchone()["locked_person_code"]
        conn.execute("update subject_key set wrapped_key = null, destroyed_at = now() where tenant_id = %s and person_code = %s", (CANARY, code))
    r = listing(run, locked_version)
    flags = {x["record_id"]: x["erased"] for x in r.json()["recordings"]}
    check("the listing marks their recording erased and the other not", flags == {"r-0001": True, "r-0002": False}, str(flags))
    check("their recording is refused as gone (410)", opening(run, locked_version, "r-0001").status_code == 410)
    check("the other patient's still opens", opening(run, locked_version, "r-0002").content == wav_b)

    heading("Taking the approval back")
    r = api("POST", f"/pipelines/{pid}/versions/{w1}/withdraw-approval", json={"note": "x"}, headers=bearer_for(ENGINEER))
    check("the person who uploaded it may not take the approval back", r.status_code == 403, f"{r.status_code}")
    r = api("POST", f"/pipelines/{pid}/versions/{w1}/withdraw-approval", json={"note": "changed my mind"}, headers=bearer_for(CUSTODIAN))
    check("the department's approver may", r.status_code == 200 and r.json()["approved"] is False, f"{r.status_code} {r.text[:120]}")
    r = listing(run, locked_version)
    check("a run that was already going is refused from then on", r.status_code == 403 and "not been approved" in reasons_of(r), f"{r.status_code}")
    started = api("POST", f"/datasets/{locked_dataset}/versions/{locked_version}/deidentify", json={"pipeline_kind": "dag", "pipeline_version_id": w1}, headers=bearer_for(ENGINEER))
    check("and a new run is refused at the start again", started.status_code == 400 and "has not been approved" in reasons_of(started), f"{started.status_code}")
    check("taking it back twice is refused", api("POST", f"/pipelines/{pid}/versions/{w1}/withdraw-approval", json={"note": "x"}, headers=bearer_for(CUSTODIAN)).status_code == 409)
    with db() as conn:
        rows = conn.execute("select approved_by, withdrawn_by from pipeline_version_approval where pipeline_version_id = %s", (w1,)).fetchall()
    check("the record keeps who approved it and who took it back", len(rows) == 1 and rows[0]["approved_by"] == CUSTODIAN and rows[0]["withdrawn_by"] == CUSTODIAN, str(rows))

    reset_keys()
    return summary("v140")


if __name__ == "__main__":
    sys.exit(main())
