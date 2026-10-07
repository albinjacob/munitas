"""U148: what a step makes from a patient's recording is locked under that patient's key as it arrives, and never stored in the clear.

A redacted recording still carries the patient's voice, so it is the patient's data. A step that is given recordings and writes files says which
recording a file was made from and how big the file will be. The platform checks that the step opened that recording in this run, locks each part
under that patient's key as it arrives, and records the file as belonging to that patient. Erasing the patient then makes it unreadable, with the
recording itself. A file that is sound, from a step that was given recordings, and says nothing of where it came from, is refused and thrown away.

Checked here one item at a time, against the live platform: what a step may say, the sizes, what is stored, that it opens with the patient's key and
only with it, what is recorded of it, and the refusal of plain sound.

    docker compose exec -T munitas-api python /verify/v145_locked_outputs.py
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

from common import ADMIN, CANARY, api, bearer_for, bucket_for, check, db, fixture_tenant, heading, require_api, s3_client, summary  # noqa: E402
from v128_person_keys_erasure import CUSTODIAN, ENGINEER, identity, register_dataset, reset_keys  # noqa: E402
from v137_locked_recordings import KIND, make_wav, upload_whole  # noqa: E402
from v140_custom_steps_and_recordings import (YAML, YAML_WRITES, complete_out, department, finish, listing, open_run, opening, pass_for, put_out, reasons_of,  # noqa: E402
                                              step_call, upload_version)
from app import person_keys  # noqa: E402
from crypto import DestroyedKeyError, read_range, stream  # noqa: E402

YAML_BOTH = YAML.replace("    recordings: true\n", "    recordings: true\n    writes_files: true\n")


def open_locked(token: str, name: str, source: str | None, size: int | None):
    body: dict = {}
    if source is not None:
        body["source_record"] = source
    if size is not None:
        body["size"] = size
    return step_call("POST", f"/step/v1/outputs/{name}", token, json=body, headers={"Content-Type": "application/json"})


def stored(key: str) -> bytes | None:
    client = s3_client(*ADMIN)
    try:
        return client.get_object(Bucket=bucket_for(CANARY), Key=key)["Body"].read()
    except client.exceptions.NoSuchKey:
        return None


def send(token: str, name: str, plain: bytes) -> list:
    """A whole file in parts of 8 MiB, the way the library sends it. Returns what each part answered."""
    part = stream.PART
    pieces = [plain[i:i + part] for i in range(0, len(plain), part)]
    return [put_out(token, name, n, piece) for n, piece in enumerate(pieces, start=1)]


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    from app import db as app_db
    if app_db.pool.closed:
        app_db.pool.open()
    tag = uuid.uuid4().hex[:8]
    service = person_keys.key_service()

    heading("Three recordings of two patients, and a version whose step reads them and writes files")
    dataset = register_dataset(ENGINEER)
    patients = [f"O-{tag}-a", f"O-{tag}-b"]
    wavs = {"r-0001": make_wav(40_000), "r-0002": make_wav(55_000), "r-0003": make_wav(70_000)}
    for record, p in (("r-0001", 0), ("r-0002", 0), ("r-0003", 1)):
        upload_whole(dataset, f"{record}.wav", wavs[record], patients[p])
    sealed = api("POST", f"/datasets/{dataset}/seal-recordings", headers=bearer_for(ENGINEER))
    check("the recordings seal", sealed.status_code == 201, sealed.text[:160])
    src = sealed.json()["id"]
    with db() as conn:
        owners = {r["record_id"]: r["person_code"] for r in conn.execute("select record_id, person_code from recording_person where dataset_version_id = %s", (src,)).fetchall()}
    pipeline = api("POST", "/pipelines/register", json={"tenant_id": CANARY, "name": f"u148-{tag}", "department_id": department(), "registered_by": ENGINEER}, headers=bearer_for(ENGINEER))
    pid = pipeline.json()["id"]
    both = upload_version(pid, YAML_BOTH).json()["id"]
    writes_only = upload_version(pid, YAML_WRITES).json()["id"]
    for v in (both, writes_only):
        api("POST", f"/pipelines/{pid}/versions/{v}/promote", json={"note": "reviewed"}, headers=bearer_for(CUSTODIAN))
    run = open_run(src, both)
    token = pass_for(run)

    heading("What a step may say when it opens a file made from a recording")
    r = open_locked(token, "a.bin", "r-0001", 1000)
    check("a step that has not opened that recording cannot say a file was made from it", r.status_code == 400 and "has not opened recording r-0001" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    check("it opens the recording first", opening(token, "r-0001").status_code == 200 and opening(token, "r-0003").status_code == 200)
    with db() as conn:
        seen = {r["record_id"] for r in conn.execute("select record_id from step_recording_opened where pipeline_run_id = %s and step = 'inspect'", (run["id"],)).fetchall()}
    check("the platform recorded which recordings this step opened in this run", seen == {"r-0001", "r-0003"}, str(seen))
    r = open_locked(token, "a.bin", "r-0002", 1000)
    check("a recording it did not open still cannot be named", r.status_code == 400 and "has not opened recording r-0002" in reasons_of(r))
    r = open_locked(token, "a.bin", "r-0001", None)
    check("a file made from a recording must say its size", r.status_code == 400 and "size in bytes up front" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    check("a size of nothing is refused, since nothing is locked", open_locked(token, "a.bin", "r-0001", 0).status_code == 400)
    check("a size above the limit is refused", open_locked(token, "a.bin", "r-0001", 600 * 1024 * 1024).status_code == 400)
    r = step_call("POST", "/step/v1/outputs/a.bin", token, json={"size": 10}, headers={"Content-Type": "application/json"})
    check("a size with no recording is refused: it is for a file that is locked", r.status_code == 400 and "only for a file made from a recording" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    plain_open = step_call("POST", "/step/v1/outputs/plain.txt", token)
    check("a file that says nothing of a recording opens as before, and is not locked", plain_open.status_code == 201 and plain_open.json()["locked"] is False)

    heading("A file made from a recording is locked, part by part, under that patient's key")
    plain_a = os.urandom(300_000)
    opened = open_locked(token, "r-0001.silenced.bin", "r-0001", len(plain_a))
    check("it opens, and says it is locked", opened.status_code == 201 and opened.json()["locked"] is True, f"{opened.status_code} {opened.text[:160]}")
    r = put_out(token, "r-0001.silenced.bin", 1, plain_a[:1000])
    check("a part of the wrong size is refused, and says what size it must be", r.status_code == 400 and f"must be {len(plain_a)} bytes" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    r = put_out(token, "r-0001.silenced.bin", 2, plain_a)
    check("a part that the size does not give is refused", r.status_code == 400 and "there is no part 2" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    check("the part of the right size is taken", all(x.status_code == 200 for x in send(token, "r-0001.silenced.bin", plain_a)))
    done = complete_out(token, "r-0001.silenced.bin")
    check("the file completes, and says it is locked", done.status_code == 200 and done.json()["locked"] is True, f"{done.status_code} {done.text[:160]}")
    with db() as conn:
        row = conn.execute("select storage_key, bytes, sha256, person_code, plain_bytes, source_record from step_output where pipeline_run_id = %s and name = 'r-0001.silenced.bin' and status = 'complete'", (run["id"],)).fetchone()
    blob = stored(row["storage_key"])
    check("what is stored is the locked file, which is longer than what was sent by exactly the lock's overhead", blob is not None and len(blob) == stream.stored_size(len(plain_a)) == row["bytes"], f"{len(blob or b'')} {stream.stored_size(len(plain_a))}")
    check("none of what was sent is in what is stored", blob is not None and plain_a[:64] not in blob and plain_a[1000:1064] not in blob)
    check("the record says whose it is, which recording it was made from and how big it is", row["person_code"] == owners["r-0001"] and row["source_record"] == "r-0001" and row["plain_bytes"] == len(plain_a))
    import hashlib
    check("the fingerprint is of what is stored", hashlib.sha256(blob).hexdigest() == row["sha256"])
    key = service.unwrap(CANARY, owners["r-0001"], "verify", "check a locked output opens", False)
    opened_back = read_range(key, CANARY, owners["r-0001"], "r-0001.silenced.bin", len(plain_a), lambda off, n: blob[off:off + n], 0, len(plain_a))
    check("with that patient's key it opens to exactly what was sent", opened_back == plain_a)
    other = service.unwrap(CANARY, owners["r-0003"], "verify", "check another patient's key does not open it", False)
    try:
        read_range(other, CANARY, owners["r-0003"], "r-0001.silenced.bin", len(plain_a), lambda off, n: blob[off:off + n], 0, len(plain_a))
        refused_other = False
    except Exception:  # noqa: BLE001
        refused_other = True
    check("another patient's key does not open it", refused_other)
    with db() as conn:
        lines = [r["purpose"] for r in conn.execute("select purpose from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s and purpose like %s", (CANARY, f"step_run {run['id']}", "lock part%")).fetchall()]
    check("locking the part is in the key log, naming the part, the file and the step", len(lines) == 1 and "lock part 1 of output r-0001.silenced.bin for custom step inspect" in lines[0], str(lines))

    heading("A file of more than one part, and one made from another patient's recording")
    big = os.urandom(stream.PART + 123_456)
    check("it opens for a second recording", open_locked(token, "r-0003.big.bin", "r-0003", len(big)).status_code == 201)
    answers = send(token, "r-0003.big.bin", big)
    check("both parts are taken, and a part sent again is simply replaced", all(a.status_code == 200 for a in answers) and put_out(token, "r-0003.big.bin", 1, big[:stream.PART]).status_code == 200)
    r = complete_out(token, "r-0003.big.bin")
    check("it completes", r.status_code == 200 and r.json()["locked"] is True, f"{r.status_code} {r.text[:160]}")
    with db() as conn:
        big_row = conn.execute("select storage_key from step_output where pipeline_run_id = %s and name = 'r-0003.big.bin' and status = 'complete'", (run["id"],)).fetchone()
    big_blob = stored(big_row["storage_key"])
    got = read_range(service.unwrap(CANARY, owners["r-0003"], "verify", "check", False), CANARY, owners["r-0003"], "r-0003.big.bin", len(big), lambda off, n: big_blob[off:off + n], 0, len(big))
    check("it is stored locked, in the size that follows from its size, and opens whole under the other patient's key", len(big_blob) == stream.stored_size(len(big)) and got == big)
    open_locked(token, "short.bin", "r-0001", 5000)
    send(token, "short.bin", os.urandom(4000))
    r = complete_out(token, "short.bin")
    check("a file that is not the size it was opened as cannot be completed", r.status_code == 400, f"{r.status_code} {reasons_of(r)[:160]}")
    with db() as conn:
        conn.execute("update step_output set status = 'aborted' where pipeline_run_id = %s and name = 'short.bin'", (run["id"],))

    heading("The step ends, and what it made is sealed under review, belonging to its patients")
    write_whole_plain = step_call("PUT", "/step/v1/outputs/plain.txt/parts/1", token, content=b"notes", headers={"Content-Type": "application/octet-stream"})
    check("a plain file is sent as before", write_whole_plain.status_code == 200 and complete_out(token, "plain.txt").status_code == 200)
    sealed_out = finish(run)
    body = sealed_out.json()
    check("the platform seals the three files as one version", sealed_out.status_code == 200 and body.get("status") == "sealed" and body.get("files_written") == 3, sealed_out.text[:200])
    out_version = body["output_version_id"]
    with db() as conn:
        version = conn.execute("select visibility_class, object_manifest from dataset_version where id = %s", (out_version,)).fetchone()
        owned = {r["record_id"]: (r["person_code"], r["plain_bytes"], r["format"]) for r in conn.execute("select record_id, person_code, plain_bytes, format from recording_person where dataset_version_id = %s", (out_version,)).fetchall()}
    check("it is under review", version["visibility_class"] == "UNDER_REVIEW")
    check("the two locked files belong to their patients, with their plain sizes and the lock's format, and the plain file to nobody",
          owned == {"r-0001.silenced.bin": (owners["r-0001"], len(plain_a), "stream:v1"), "r-0003.big.bin": (owners["r-0003"], len(big), "stream:v1")}, str(owned))
    names = {m["key"].rsplit("/", 1)[1]: m for m in version["object_manifest"]}
    check("the manifest holds the sizes and fingerprints of what is stored, locked or not", names["r-0001.silenced.bin"]["bytes"] == stream.stored_size(len(plain_a)) and names["plain.txt"]["bytes"] == 5)
    listed = person_keys.still_made_from(CANARY, owners["r-0001"])
    check("erasing the first patient lists the version among those that hold their recordings", out_version in listed["recording_versions"], str(listed)[:200])

    heading("Erasing the patient makes what was made from their recording unreadable")
    run5 = open_run(src, both)
    token5 = pass_for(run5)
    check("another run opens that patient's other recording before the patient is erased", opening(token5, "r-0002").status_code == 200)
    erased = person_keys.erase_person(service, CANARY, KIND, patients[0], identity(CUSTODIAN, ["data_custodian"]), "asked to be forgotten")
    check("the patient is erased", erased["key_destroyed"] is True)
    try:
        service.unwrap(CANARY, owners["r-0001"], "verify", "after erasure", False)
        readable = True
    except DestroyedKeyError:
        readable = False
    check("their key is gone, so the locked file cannot be opened by anybody, though it is still in storage", readable is False and stored(row["storage_key"]) is not None)
    check("the other patient's file still opens", read_range(service.unwrap(CANARY, owners["r-0003"], "verify", "check", False), CANARY, owners["r-0003"], "r-0003.big.bin", len(big),
                                                             lambda off, n: big_blob[off:off + n], 0, 1000) == big[:1000])
    check("the recordings listing says the erased patient's recordings are erased", all(x["erased"] for x in listing(token5).json()["recordings"] if x["record_id"] in ("r-0001", "r-0002")))
    r = open_locked(token5, "late.bin", "r-0002", 100)
    check("a step that opened that recording earlier cannot make a new file from it: the patient was erased", r.status_code == 400 and "was erased" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")

    heading("Sound that does not say where it came from is refused")
    run3 = open_run(src, both)
    token3 = pass_for(run3)
    wav_plain = make_wav(30_000)
    open_out_r = step_call("POST", "/step/v1/outputs/leak.wav", token3)
    check("a step opens a file for sound without saying a recording", open_out_r.status_code == 201)
    send(token3, "leak.wav", wav_plain)
    refused = complete_out(token3, "leak.wav")
    check("it cannot finish it: sound from a step given recordings must say which recording, and the answer says how", refused.status_code == 400 and "must say which recording" in reasons_of(refused), f"{refused.status_code} {reasons_of(refused)[:200]}")
    with db() as conn:
        gone = conn.execute("select storage_key, status from step_output where pipeline_run_id = %s and name = 'leak.wav'", (run3["id"],)).fetchone()
    check("what was stored of it is thrown away at once, and the file is marked aborted", gone["status"] == "aborted" and stored(gone["storage_key"]) is None, str(gone))
    again = step_call("POST", "/step/v1/outputs/leak.wav", token3)
    check("the name can be opened again", again.status_code == 201)
    send(token3, "leak.wav", b"RIFX not sound at all, only looks a little like it")
    check("a file that does not look like sound is taken, whatever its name", complete_out(token3, "leak.wav").status_code == 200)

    run4 = open_run(src, writes_only)
    token4 = pass_for(run4, "inspect")
    step_call("POST", "/step/v1/outputs/music.wav", token4)
    send(token4, "music.wav", wav_plain)
    r = complete_out(token4, "music.wav")
    check("a step that was not given recordings may write sound: it cannot have made it from a patient's recording", r.status_code == 200 and r.json()["locked"] is False, f"{r.status_code} {reasons_of(r)[:160]}")

    reset_keys()
    return summary("v145")


if __name__ == "__main__":
    sys.exit(main())
