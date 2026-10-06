"""U138: a pipeline run is given a locked recording back through the platform, and only a run that was launched for it.

The transcribe and redact steps read each recording from storage as audio. A recording locked under its patient's key is ciphertext there, so they
ask the platform to open it: the platform checks the task, unwraps the key (the opening goes in the key log with the task named) and streams the audio.
This calls the function those steps call, `open_recording`, against the real platform and real storage, and checks one item at a time:

  * a version of plain recordings is read from storage as before, and a version of locked recordings is asked for;
  * a locked recording comes back byte for byte, and the key log names the task that opened it;
  * the platform refuses, and gives nothing, to a caller without the worker's token, to a task whose inputs do not include the version, to a
    task credential minted for another workload, to a task that is no longer running, and for a recording the version does not hold;
  * a patient who was erased raises "erased", writes no file, and leaves the other patient's recording readable;
  * a recording changed in storage is not handed on as audio: the read fails, and nothing is handed on;
  * a recording is read in pieces and never written anywhere: random reads match the original, the key log records the opening once however many
    reads there were, and a recording of 512 MiB is read while holding a few blocks, which is measured;
  * a patient erased while their recording is being read stops the next block that has to be fetched.

It does not run transcription or redaction themselves, which need the speech models: the steps' own change is two calls to this function and a
skip for an erased patient, and what they hand to the function is what this passes.

Runs on the host, because it imports the worker's own client:

    $env:PG_DSN = "postgresql://munitas:munitas@localhost:5432/platform"
    $env:MUNITAS_VERIFY_KRATOS = "http://localhost:4433"
    $env:S3_ENDPOINT = "http://localhost:8333"
    .venv\\Scripts\\python.exe verify\\v138_worker_opens_locked_recordings.py
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import tracemalloc
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from common import ADMIN, API, CANARY, WORKER_HEADERS, api, bearer_for, bucket_for, check, db, fixture_tenant, heading, require_api, s3_client, summary, tiny_wav  # noqa: E402
from v128_person_keys_erasure import ENGINEER, count, register_dataset, reset_keys  # noqa: E402
from v137_locked_recordings import make_wav, upload_whole  # noqa: E402
from worker import platform_client as cp  # noqa: E402

WORKLOAD = f"{CANARY}-pipeline"
KIND = "chart number"


def new_run(action: str, inputs: list[str], operator: str = WORKLOAD) -> dict:
    made = api("POST", "/action-runs", json={
        "tenant_id": CANARY, "action_id": action, "code_hash": uuid.uuid4().hex, "image_digest": "sha256:verify",
        "operator": operator, "idempotency_key": f"u137-{uuid.uuid4()}", "input_versions": inputs,
        "params": {}, "trigger_kind": "manual", "triggered_by": ENGINEER})
    made.raise_for_status()
    return {"id": made.json()["id"], "token": made.json()["task_credential"]}


def opened(run: dict, version: str, record: str, reader=None, bucket: str = "x", key: str | None = None, purpose: str = "verification"):
    """A recording as a step opens it: a file read a range at a time, never written anywhere."""
    return cp.open_recording(reader, bucket, key or f"{CANARY}/none/{record}.wav", version_id=version, record_id=record, tenant_id=CANARY,
                             principal=WORKLOAD, task_credential=run["token"], purpose=purpose)


def fetch(run: dict, version: str, record: str, where: Path, reader=None) -> str:
    """What happened when the step asked: 'ok' (and the bytes it read are in `where`, for the check to compare), 'erased', or the platform's refusal."""
    try:
        with opened(run, version, record, reader) as source:
            data = source.read()
        where.write_bytes(data)
        return "ok"
    except cp.RecordingErased:
        return "erased"
    except cp.ControlPlaneError as exc:
        return str(exc)


def raw(body: dict, headers: dict) -> httpx.Response:
    return httpx.post(f"{API}/internal/recordings/open", json=body, headers=headers, timeout=60.0)


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    tag = uuid.uuid4().hex[:8]
    a, b = f"A-{tag}-chart", f"B-{tag}-chart"
    work = Path(tempfile.mkdtemp())

    with db() as conn:
        conn.execute("insert into directory (id, tenant_id, label, kind, roles) values (%s, %s, 'Pipeline', 'workload', '{pipeline_action}') on conflict (id) do nothing", (WORKLOAD, CANARY))
        action = str(uuid.uuid4())
        conn.execute("insert into dataset_action (id, tenant_id, name) values (%s, %s, %s)", (action, CANARY, f"u138 read {action}"))

    heading("Two versions of recordings: one plain, one locked")
    plain_dataset = register_dataset(ENGINEER)
    plain_wav = tiny_wav(0.5)
    with httpx.Client(timeout=60.0) as client:
        client.post(f"{API}/datasets/{plain_dataset}/files", headers=bearer_for(ENGINEER), files={"file": ("p-0001.wav", plain_wav, "audio/wav")}).raise_for_status()
    plain_version = api("POST", f"/datasets/{plain_dataset}/seal-audio", headers=bearer_for(ENGINEER)).json()["id"]
    locked_dataset = register_dataset(ENGINEER)
    wav_a, wav_b = make_wav(900_000), make_wav(700_000)
    upload_whole(locked_dataset, "l-0001.wav", wav_a, a)
    upload_whole(locked_dataset, "l-0002.wav", wav_b, b)
    wav_c, c = make_wav(17_000_000), f"C-{tag}-chart"
    upload_whole(locked_dataset, "l-0003.wav", wav_c, c)
    truth_a = json.dumps({"reference_transcript": "Chart 4471 for the patient.", "spans": [{"start": 6, "end": 10, "label": "id"}]}).encode()
    upload_whole(locked_dataset, "l-0001.truth.json", truth_a, a)
    sealed = api("POST", f"/datasets/{locked_dataset}/seal-recordings", headers=bearer_for(ENGINEER))
    check("the locked version seals", sealed.status_code == 201, sealed.text[:160])
    version = sealed.json()["id"]
    check("the platform says the plain version is not locked and the locked one is", cp.recordings_are_locked(plain_version) is False and cp.recordings_are_locked(version) is True)
    check("only the worker may ask whether a version is locked",
          httpx.get(f"{API}/internal/dataset-versions/{version}/locked-recordings", headers=bearer_for(ENGINEER), timeout=30.0).status_code == 403)

    heading("A plain recording is read from storage, as before")
    run_plain = new_run(action, [plain_version])
    target = work / "plain.wav"
    reader = s3_client(*ADMIN)
    with db() as conn:
        key = next(m["key"] for m in conn.execute("select object_manifest from dataset_version where id = %s", (plain_version,)).fetchone()["object_manifest"] if m["key"].endswith("p-0001.wav"))
    try:
        with opened(run_plain, plain_version, "p-0001", reader, bucket_for(CANARY), key) as source:
            got = source.read()
    except Exception as exc:  # noqa: BLE001
        got = str(exc).encode()
    check("it is the bytes that were uploaded, and the platform was not asked to open anything", got == plain_wav)

    heading("A locked recording comes back through the platform")
    run = new_run(action, [version])
    target = work / "a.wav"
    result = fetch(run, version, "l-0001", target)
    check("the recording comes back byte for byte", result == "ok" and target.read_bytes() == wav_a, result[:160])
    check("the other patient's too", fetch(run, version, "l-0002", work / "b.wav") == "ok" and (work / "b.wav").read_bytes() == wav_b)
    log = count("select count(*) as n from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s", (CANARY, f"action_run {run['id']}"))
    check("each opening is in the key log, with the task named", log == 2, str(log))

    heading("A locked answer key comes back parsed, and a recording without one is not found")
    run_key = new_run(action, [version])
    expected_before = count("select count(*) as n from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s", (CANARY, f"action_run {run_key['id']}"))
    try:
        got = cp.read_answer_key(None, "x", f"{CANARY}/none/l-0001.truth.json", version_id=version, record_id="l-0001", tenant_id=CANARY,
                                 principal=WORKLOAD, task_credential=run_key["token"], purpose="verification")
    except Exception as exc:  # noqa: BLE001
        got = str(exc)
    check("the answer key comes back as what was uploaded", got == json.loads(truth_a), str(got)[:160])
    logged = count("select count(*) as n from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s", (CANARY, f"action_run {run_key['id']}"))
    check("opening it is in the key log, with the task named", expected_before == 0 and logged == 1, f"{expected_before} then {logged}")
    key_body = {"principal": WORKLOAD, "tenant_id": CANARY, "task_credential": run_key["token"], "dataset_version_id": version, "record_id": "l-0002",
                "purpose": "x", "kind": "answer_key"}
    r = raw(key_body, WORKER_HEADERS)
    check("a recording that has no answer key is not found, and says so", r.status_code == 404 and "no answer key" in r.text, f"{r.status_code} {r.text[:120]}")
    r = raw({**key_body, "record_id": "l-0001", "kind": "bogus"}, WORKER_HEADERS)
    check("a kind that is neither audio nor answer key is refused", r.status_code == 404, f"{r.status_code} {r.text[:120]}")
    r = raw({**key_body, "record_id": "l-0001", "task_credential": new_run(action, [plain_version])["token"]}, WORKER_HEADERS)
    check("an answer key is held to the same inputs rule as the recording", r.status_code == 403 and "outside the inputs" in r.text, f"{r.status_code} {r.text[:120]}")

    heading("A locked recording is read in pieces, and is never a file")
    run_r = new_run(action, [version])
    with opened(run_r, version, "l-0001") as f:
        check("its size is the recording's own size, without reading it", f.seek(0, 2) == len(wav_a) and f.tell() == len(wav_a))
        rnd, wrong = random.Random(5), 0
        for _ in range(40):
            at, n = rnd.randrange(0, len(wav_a)), rnd.randrange(1, 300_000)
            f.seek(at)
            if f.read(n) != wav_a[at:at + n]:
                wrong += 1
        check("forty reads at random places match the original", wrong == 0, f"{wrong} did not")
        f.seek(0)
        check("reading it from the start to the end matches the original", f.read() == wav_a)
        check("a read past the end gives nothing", f.read(10) == b"")
    logged = count("select count(*) as n from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s", (CANARY, f"action_run {run_r['id']}"))
    check("however many reads there were, the opening is in the key log once, naming the task", logged == 1, str(logged))
    answer = cp.read_answer_key(None, "x", "k", version_id=version, record_id="l-0001", tenant_id=CANARY, principal=WORKLOAD,
                                task_credential=new_run(action, [version])["token"], purpose="verification")
    check("an answer key is read the same way and parses", answer["reference_transcript"].startswith("Chart 4471"), str(answer)[:80])

    heading("However long the recording is, memory stays small")
    blocks, big = [], 512 * 1024 * 1024

    def fake(at: int, n: int) -> bytes:
        blocks.append(n)
        return bytes(n)

    tracemalloc.start()
    sink = cp.RangeFile(big, fake)
    total_read = 0
    while True:
        piece = sink.read(64 * 1024)
        if not piece:
            break
        total_read += len(piece)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    check("a recording of 512 MiB is read to its end", total_read == big and sum(blocks) == big, f"{total_read} bytes in {len(blocks)} requests")
    check("and the most it held at once was a few blocks, not the recording", peak < 40 * 1024 * 1024, f"{peak / 1024 / 1024:.1f} MiB at most")
    check("each request was one block, and no larger", max(blocks) <= 8 * 1024 * 1024, str(max(blocks)))

    heading("What the platform refuses")
    body = {"principal": WORKLOAD, "tenant_id": CANARY, "task_credential": run["token"], "dataset_version_id": version, "record_id": "l-0001", "purpose": "x"}
    check("a caller without the worker's token gets nothing", raw(body, bearer_for(ENGINEER)).status_code == 403 and raw(body, {}).status_code == 403)
    other_run = new_run(action, [plain_version])
    r = raw({**body, "task_credential": other_run["token"]}, WORKER_HEADERS)
    check("a task whose inputs do not include this version gets nothing, and is told why", r.status_code == 403 and "outside the inputs" in r.text, f"{r.status_code} {r.text[:140]}")
    r = raw({**body, "task_credential": "not-a-token"}, WORKER_HEADERS)
    check("a task credential that is not one gets nothing", r.status_code == 403, f"{r.status_code} {r.text[:100]}")
    r = raw({**body, "principal": "canary-engineer"}, WORKER_HEADERS)
    check("a task credential presented by another principal gets nothing", r.status_code == 403, f"{r.status_code} {r.text[:100]}")
    r = raw({**body, "record_id": "l-9999"}, WORKER_HEADERS)
    check("a recording the version does not hold is not found", r.status_code == 404, f"{r.status_code} {r.text[:100]}")
    ended = new_run(action, [version])
    with db() as conn:
        conn.execute("update action_run set status = 'failed' where id = %s", (ended["id"],))
    r = raw({**body, "task_credential": ended["token"]}, WORKER_HEADERS)
    check("a task that is no longer running gets nothing", r.status_code == 403 and "no longer running" in r.text, f"{r.status_code} {r.text[:140]}")

    heading("A recording changed in storage is not handed on as audio")
    with db() as conn:
        v = conn.execute("select object_manifest from dataset_version where id = %s", (version,)).fetchone()
    stored_key = next(m["key"] for m in v["object_manifest"] if m["key"].endswith("l-0002.wav"))
    admin = s3_client(*ADMIN)
    original = admin.get_object(Bucket=bucket_for(CANARY), Key=stored_key)["Body"].read()
    altered = bytearray(original)
    altered[len(altered) // 2] ^= 1
    admin.put_object(Bucket=bucket_for(CANARY), Key=stored_key, Body=bytes(altered))
    target = work / "tampered.wav"
    result = fetch(run, version, "l-0002", target)
    check("the read fails and says the stored recording does not open", result not in ("ok", "erased") and "does not open" in result, result[:200])
    check("and nothing was handed on", not target.exists())
    admin.put_object(Bucket=bucket_for(CANARY), Key=stored_key, Body=original)
    check("put back, it comes back byte for byte", fetch(run, version, "l-0002", work / "b2.wav") == "ok" and (work / "b2.wav").read_bytes() == wav_b)

    heading("A patient who was erased")
    with db() as conn:
        code = conn.execute("select locked_person_code from dataset_source where dataset_id = %s and locator = 'l-0001.wav'", (locked_dataset,)).fetchone()["locked_person_code"]
        # The row change the key service's own destroy makes: the wrapped key goes and the date is written, together.
        conn.execute("update subject_key set wrapped_key = null, destroyed_at = now() where tenant_id = %s and person_code = %s", (CANARY, code))
    target = work / "erased.wav"
    check("their recording is reported as erased, and no file is written", fetch(run, version, "l-0001", target) == "erased" and not target.exists())
    check("the other patient's recording is still handed back", fetch(run, version, "l-0002", work / "b3.wav") == "ok" and (work / "b3.wav").read_bytes() == wav_b)
    r = raw(body, WORKER_HEADERS)
    check("the platform says gone (410) and names the reason", r.status_code == 410 and "erased" in r.text, f"{r.status_code} {r.text[:120]}")
    r = raw({**body, "kind": "answer_key"}, WORKER_HEADERS)
    check("their answer key is gone with them (410)", r.status_code == 410, f"{r.status_code} {r.text[:120]}")
    heading("A patient who is erased while their recording is being read")
    with db() as conn:
        code_c = conn.execute("select locked_person_code from dataset_source where dataset_id = %s and locator = 'l-0003.wav'", (locked_dataset,)).fetchone()["locked_person_code"]
    run_c = new_run(action, [version])
    with opened(run_c, version, "l-0003") as f:
        check("the first block is read", f.read(1000) == wav_c[:1000])
        with db() as conn:
            conn.execute("update subject_key set wrapped_key = null, destroyed_at = now() where tenant_id = %s and person_code = %s", (CANARY, code_c))
        f.seek(0)
        check("what was already read stays readable from memory (it was in this process already)", f.read(1000) == wav_c[:1000])
        f.seek(9_000_000)
        try:
            f.read(10)
            gone = False
        except cp.RecordingErased:
            gone = True
        check("the next block that has to be fetched is refused, because the patient was erased", gone)
    check("and a new opening is refused too", fetch(new_run(action, [version]), version, "l-0003", work / "c.wav") == "erased")
    reset_keys()
    return summary("v138")


if __name__ == "__main__":
    sys.exit(main())
