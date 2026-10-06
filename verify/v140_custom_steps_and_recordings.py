"""U141: a custom pipeline step is given the recordings only when a second person approved its version, and only through the platform's step routes.

A step of an operator's own pipeline can say `recordings: true`. It is then given the run's recordings, which means code the platform did not write
reads the sound. So the version that holds it is approved first, by the department's approver and never by whoever uploaded it, and a run of an
unapproved version is refused at the start.

The step's container holds no storage address, no storage key and no patient key. It holds a pass for this one step of this one run, and reaches the
platform through a gateway that forwards only the step routes. The platform asks of every request, not only the first: is the pass real and made for
a step, is the run still going, does the run's version declare this step, does the approval still stand, and was the patient erased. Opening a
recording is the one write to the key log, naming the step, and returns a handle that every read must carry.

This is the platform's half, checked here, one item at a time. U142 runs a real pipeline on the host and proves what the step itself can and cannot
do from inside its container.

    docker compose exec -T munitas-api python /verify/v140_custom_steps_and_recordings.py
"""

from __future__ import annotations

import io
import json
import random
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
from app import task_credential  # noqa: E402
from app.pipeline_dag import parse_dag_config, steps_asking_for_recordings, validate_dag  # noqa: E402

ELSEWHERE = "canary-elsewhere"
WORKLOAD = f"{CANARY}-pipeline"

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


def open_run(source: str, pipeline_version_id: str | None) -> dict:
    """A pipeline run's row and credential, as the workflow opens them, started from `source` and bound to a version of a pipeline as the start route does."""
    made = httpx.post(f"{API}/pipeline-runs", headers=WORKER_HEADERS, timeout=30.0, json={
        "tenant_id": CANARY, "dataset": "custom-audio", "workflow_id": f"u141-{uuid.uuid4()}", "trigger_kind": "manual",
        "triggered_by": ENGINEER, "input_versions": [source], "principal": WORKLOAD})
    made.raise_for_status()
    run = made.json()
    with db() as conn:
        conn.execute("update pipeline_run set pipeline_version_id = %s, source_version_id = %s where id = %s", (pipeline_version_id, source, run["pipeline_run_id"]))
    return {"id": run["pipeline_run_id"], "token": run["task_credential"]}


def make_pass(run: dict, step: str = "inspect", headers: dict | None = None) -> httpx.Response:
    return httpx.post(f"{API}/internal/dag-step/pass", headers=WORKER_HEADERS if headers is None else headers, timeout=30.0,
                      json={"principal": WORKLOAD, "tenant_id": CANARY, "task_credential": run["token"], "step": step})


def pass_for(run: dict, step: str = "inspect") -> str:
    made = make_pass(run, step)
    assert made.status_code == 200, (made.status_code, made.text[:200])
    return made.json()["pass"]


def step_call(method: str, path: str, token: str | None, **extra) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    headers.update(extra.pop("headers", {}))
    return httpx.request(method, f"{API}{path}", headers=headers, timeout=60.0, **extra)


def listing(token: str | None) -> httpx.Response:
    return step_call("GET", "/step/v1/recordings", token)


def opening(token: str | None, record: str) -> httpx.Response:
    return step_call("POST", f"/step/v1/recordings/{record}/open", token)


def ranged(token: str | None, record: str, handle: str | None, offset: int, length: int) -> httpx.Response:
    return step_call("GET", f"/step/v1/recordings/{record}/range?offset={offset}&length={length}", token, headers={"x-handle": handle} if handle else {})


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

    def limits(extra: str) -> list[str]:
        return validate_dag(parse_dag_config(YAML.replace("    recordings: true\n", "    recordings: true\n" + extra + "\n")), {"steps/inspect.py"})

    check("a step may ask for memory and scratch space within the ceilings", limits("    memory_mb: 2048\n    scratch_mb: 512") == [])
    check("memory above the ceiling is refused, and says the ceiling", any("from 16 to 4096" in p for p in limits("    memory_mb: 99999")), str(limits("    memory_mb: 99999")))
    check("scratch space above its ceiling is refused", any("from 16 to 2048" in p for p in limits("    memory_mb: 4096\n    scratch_mb: 5000")))
    check("scratch space larger than the memory is refused, because it is memory-backed", any("cannot be larger than the memory" in p for p in limits("    memory_mb: 256\n    scratch_mb: 512")))
    check("a value that is not a whole number is refused", any("whole number" in p for p in limits("    memory_mb: lots")))

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
    run = open_run(locked_version, w1)
    r = make_pass(run)
    check("the platform will not make the step a pass", r.status_code == 403 and "not been approved" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
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

    heading("The platform makes the step a pass, and only for a step it would serve")
    made = make_pass(run)
    claim = task_credential.verify(made.json()["pass"]) if made.status_code == 200 else None
    check("an approved step is given a pass", made.status_code == 200, f"{made.status_code} {reasons_of(made)[:160]}")
    check("the pass is made for a step, of this run, this step and this organisation, and lasts half an hour",
          claim and (claim.task_kind, claim.task_id, claim.step, claim.tenant_id) == ("step_run", run["id"], "inspect", CANARY) and 1700 < claim.expires_at - __import__("time").time() <= 1800,
          str(claim))
    r = make_pass(run, step="not-a-step")
    check("a step the version does not declare gets none", r.status_code == 403 and "does not declare" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    r = make_pass(open_run(locked_version, w2))
    check("a run of the version that asks for nothing gets none, even by name", r.status_code == 403 and "does not declare" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    check("nobody but the platform's worker may ask for one", make_pass(run, headers=bearer_for(ENGINEER)).status_code == 403 and make_pass(run, headers={}).status_code == 403)
    action = str(uuid.uuid4())
    with db() as conn:
        conn.execute("insert into dataset_action (id, tenant_id, name) values (%s, %s, %s)", (action, CANARY, f"u141 action {action}"))
    from_action = httpx.post(f"{API}/action-runs", headers=WORKER_HEADERS, timeout=30.0, json={
        "tenant_id": CANARY, "action_id": action, "code_hash": uuid.uuid4().hex, "image_digest": "sha256:x", "operator": WORKLOAD, "idempotency_key": f"u141-{uuid.uuid4()}",
        "input_versions": [locked_version], "params": {}, "trigger_kind": "manual", "triggered_by": ENGINEER})
    exchanged = make_pass({"token": from_action.json()["task_credential"]}) if from_action.status_code == 201 else None
    check("a credential that is not a pipeline run's (an action run's, which is real) cannot be exchanged for one",
          exchanged is not None and exchanged.status_code == 403 and "pipeline run" in reasons_of(exchanged), f"{from_action.status_code} {exchanged and reasons_of(exchanged)[:160]}")

    heading("The step reads its recordings through the pass")
    token = pass_for(run)
    r = listing(token)
    got = {x["record_id"]: x for x in r.json()["recordings"]} if r.status_code == 200 else {}
    check("it lists both, with their plain sizes, none erased", r.status_code == 200 and sorted(got) == ["r-0001", "r-0002"] and got["r-0001"]["bytes"] == len(wav_a)
          and got["r-0002"]["bytes"] == len(wav_b) and not any(x["erased"] for x in got.values()), f"{r.status_code} {r.text[:200]}")
    held = opening(token, "r-0001")
    handle = held.json().get("handle") if held.status_code == 200 else None
    check("it opens one, and is told its size and given a handle", held.status_code == 200 and held.json()["bytes"] == len(wav_a) and bool(handle), f"{held.status_code} {held.text[:160]}")
    rnd, wrong = random.Random(3), 0
    for _ in range(20):
        at, n = rnd.randrange(0, len(wav_a)), rnd.randrange(1, 80_000)
        piece = ranged(token, "r-0001", handle, at, n)
        if piece.status_code != 200 or piece.content != wav_a[at:at + n]:
            wrong += 1
    check("twenty ranges at random places match the original", wrong == 0, f"{wrong} did not")
    whole = ranged(token, "r-0001", handle, 0, len(wav_a))
    check("and the whole recording, in one range, matches byte for byte", whole.status_code == 200 and whole.content == wav_a and whole.headers["x-plain-bytes"] == str(len(wav_a)))
    with db() as conn:
        lines = [r["purpose"] for r in conn.execute("select purpose from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s", (CANARY, f"step_run {run['id']}")).fetchall()]
    check("however many ranges were read, the key log has one line, naming the step, the pipeline and its version",
          len(lines) == 1 and f"custom step inspect of pipeline custom-{tag} version 1" in lines[0], str(lines))

    heading("What a pass cannot do")
    check("a request with no pass is refused", listing(None).status_code == 401 and opening(None, "r-0001").status_code == 401)
    check("a pass that is not one is refused", listing("not-a-token").status_code == 401)
    r = listing(run["token"])
    check("a pipeline run's own credential is not a step's pass", r.status_code == 401 and "not a step's pass" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    r = httpx.get(f"{API}/datasets/{locked_dataset}", headers={"x-task-credential": token}, timeout=30.0)
    check("a step's pass is refused where a run's credential is accepted, even if it is presented there", r.status_code == 401 and "step routes" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    r = httpx.post(f"{API}/credentials", json={"principal": WORKLOAD, "principal_kind": "workload", "roles": ["pipeline_action"], "tenant_id": CANARY, "dataset_version_id": locked_version,
                                               "purpose": "x", "task_credential": token}, headers=WORKER_HEADERS, timeout=30.0)
    check("and it is not accepted for a storage key", r.status_code == 403, f"{r.status_code} {reasons_of(r)[:160]}")
    r = httpx.post(f"{API}/write-credentials", json={"principal": WORKLOAD, "principal_kind": "workload", "roles": ["pipeline_action"], "tenant_id": CANARY, "dataset_id": locked_dataset,
                                                     "purpose": "x", "task_credential": token}, headers=WORKER_HEADERS, timeout=30.0)
    check("nor for a write key", r.status_code == 403, f"{r.status_code} {reasons_of(r)[:160]}")
    r = ranged(token, "r-0001", None, 0, 10)
    check("a read with no handle is refused: a recording is opened before it is read", r.status_code == 403 and "Open" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    other = opening(token, "r-0002")
    r = ranged(token, "r-0001", other.json()["handle"], 0, 10)
    check("a handle for another recording is refused", r.status_code == 403, f"{r.status_code}")
    r = ranged(token, "r-0001", token, 0, 10)
    check("the pass cannot stand in for a handle", r.status_code == 403, f"{r.status_code}")
    second = open_run(locked_version, w1)
    token2 = pass_for(second)
    r = ranged(token2, "r-0001", handle, 0, 10)
    check("a handle from another run is refused", r.status_code == 403, f"{r.status_code}")
    r = step_call("POST", "/internal/recordings/begin", token, json={"principal": WORKLOAD, "tenant_id": CANARY, "task_credential": token, "dataset_version_id": locked_version,
                                                                      "record_id": "r-0001", "purpose": "x"})
    check("the platform's own worker routes are closed to a step", r.status_code == 403, f"{r.status_code}")
    check("a range longer than 16 MiB is refused", ranged(token, "r-0001", handle, 0, 17 * 1024 * 1024).status_code == 422)

    heading("A plain recordings version is given the same way")
    plain_run = open_run(plain_recordings, w1)
    ptoken = pass_for(plain_run)
    r = listing(ptoken)
    check("it is listed, with its size", r.status_code == 200 and [x["record_id"] for x in r.json()["recordings"]] == ["p-0001"] and r.json()["recordings"][0]["bytes"] == len(plain_wav), f"{r.status_code} {r.text[:160]}")
    o = opening(ptoken, "p-0001")
    got_plain = ranged(ptoken, "p-0001", o.json().get("handle"), 0, len(plain_wav)) if o.status_code == 200 else None
    check("and opened and read byte for byte", got_plain is not None and got_plain.status_code == 200 and got_plain.content == plain_wav, f"{o.status_code}")

    heading("A patient who was erased is left out, even while a recording is open")
    with db() as conn:
        code = conn.execute("select locked_person_code from dataset_source where dataset_id = %s and locator = 'r-0001.wav'", (locked_dataset,)).fetchone()["locked_person_code"]
        conn.execute("update subject_key set wrapped_key = null, destroyed_at = now() where tenant_id = %s and person_code = %s", (CANARY, code))
    r = listing(token)
    flags = {x["record_id"]: x["erased"] for x in r.json()["recordings"]}
    check("the listing marks their recording erased and the other not", flags == {"r-0001": True, "r-0002": False}, str(flags))
    check("opening their recording is refused as gone (410)", opening(token, "r-0001").status_code == 410)
    r = ranged(token, "r-0001", handle, 0, 10)
    check("a handle opened before the erasure stops working at the next read (410)", r.status_code == 410, f"{r.status_code}")
    check("the other patient's recording still reads", ranged(token, "r-0002", other.json()["handle"], 0, len(wav_b)).content == wav_b)

    heading("The platform stops a step that is running")
    done = open_run(locked_version, w1)
    dtoken = pass_for(done)
    check("a pass works while its run is going", listing(dtoken).status_code == 200)
    httpx.post(f"{API}/pipeline-runs/{done['id']}/end", headers=WORKER_HEADERS, json={"status": "succeeded", "error": None}, timeout=30.0).raise_for_status()
    r = listing(dtoken)
    check("and stops when the run has ended, though the pass has not expired", r.status_code == 403 and "has ended" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")

    heading("Taking the approval back")
    r = api("POST", f"/pipelines/{pid}/versions/{w1}/withdraw-approval", json={"note": "x"}, headers=bearer_for(ENGINEER))
    check("the person who uploaded it may not take the approval back", r.status_code == 403, f"{r.status_code}")
    r = api("POST", f"/pipelines/{pid}/versions/{w1}/withdraw-approval", json={"note": "changed my mind"}, headers=bearer_for(CUSTODIAN))
    check("the department's approver may", r.status_code == 200 and r.json()["approved"] is False, f"{r.status_code} {r.text[:120]}")
    r = listing(token)
    check("a pass already issued stops working at its next request", r.status_code == 403 and "not been approved" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    check("so does a handle already issued", ranged(token, "r-0002", other.json()["handle"], 0, 10).status_code == 403)
    check("no new pass is made", make_pass(run).status_code == 403)
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
