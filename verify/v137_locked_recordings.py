"""U137: a recording is uploaded in parts that can each be sent again, locked under its patient's key, sealed, and unreadable once the patient is erased.

A recording belongs to one patient. It is sent in parts, each locked on its own, and the platform keeps only what it can prove: every part there, the stored
size exactly what a locked file of that size is. This checks, one item at a time, on real storage and a real database, with recordings the check makes
up for itself (noise, with a real wav header, and invented patients):

  * an upload is refused, with every reason listed, when the name, the size or the patient is wrong, when a researcher asks, and when it is
    another organisation's dataset; nobody is made for a refusal;
  * a file of three parts is sent out of order, one part is sent twice, one is the wrong length, one is cut short, one is past the end, and the upload
    is refused as complete until every part has arrived; asking says which are missing, so a cut-off upload carries on from where it stopped;
  * the stored file holds none of the recording, is exactly the size a locked file should be, and its recorded fingerprint is of what is stored;
  * the length and sample rate recorded are the ones the standard `wave` reader finds in the whole file;
  * two recordings of one patient have one person code, a different kind of identifier with the same text is a different person, and the identifier is
    found nowhere in what the platform keeps;
  * an answer key is sent whole and checked as the ordinary upload checks it, and is locked too;
  * an upload nobody finishes is closed after its day and its parts are dropped; one that is given up leaves nothing;
  * the two older ways of sealing refuse a locked recording and say why, and sealing locked recordings refuses an ordinary file, an answer key for a recording
    that is not there, and an answer key locked to a different patient from its recording;
  * the sealed version lists the stored files by their fingerprints, a plain index that names no patient, and one row for each recording saying whose it
    is by person code only;
  * the recording opens, byte for byte, for a pipeline run and the opening is in the key log with the run named; a file changed in storage does not open;
  * once the patient is erased the recording cannot be opened, nothing more can be locked for the patient, the other patient's recording still opens,
    and the stored bytes did not change.

    docker compose exec -T munitas-api python /verify/v137_locked_recordings.py
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import uuid
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

import httpx  # noqa: E402

from common import ADMIN, API, CANARY, api, bearer_for, bucket_for, check, db, fixture_contract, fixture_tenant, heading, require_api, s3_client, summary  # noqa: E402
from lifecycle_fixture import drop_org, make_org  # noqa: E402
from v128_person_keys_erasure import CUSTODIAN, ENGINEER, RESEARCHER, count, identity, reasons_of, register_dataset, reset_keys  # noqa: E402

PART = 8 * 1024 * 1024
KIND = "chart number"


def make_wav(data_bytes: int, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(os.urandom(data_bytes))
    return buffer.getvalue()


def open_up(dataset: str, who: str, name: str, size: int, kind: str = KIND, value: str = "x"):
    return httpx.post(f"{API}/datasets/{dataset}/recording-uploads", headers=bearer_for(who), timeout=60.0,
                      json={"file_name": name, "size_bytes": size, "patient_kind": kind, "patient_value": value})


def put(upload: str, n: int, data: bytes, who: str = ENGINEER):
    return httpx.put(f"{API}/recording-uploads/{upload}/parts/{n}", headers={**bearer_for(who), "content-type": "application/octet-stream"},
                     content=data, timeout=180.0)


def get_status(upload: str, who: str = ENGINEER):
    return httpx.get(f"{API}/recording-uploads/{upload}", headers=bearer_for(who), timeout=30.0)


def complete(upload: str, who: str = ENGINEER):
    return httpx.post(f"{API}/recording-uploads/{upload}/complete", headers=bearer_for(who), timeout=300.0)


def slice_of(data: bytes, n: int) -> bytes:
    return data[(n - 1) * PART:n * PART]


def upload_whole(dataset: str, name: str, data: bytes, value: str, kind: str = KIND) -> dict:
    r = open_up(dataset, ENGINEER, name, len(data), kind, value)
    assert r.status_code == 201, (r.status_code, r.text[:200])
    up = r.json()
    for n in range(1, up["parts"] + 1):
        p = put(up["id"], n, slice_of(data, n))
        assert p.status_code == 200, (n, p.status_code, p.text[:200])
    c = complete(up["id"])
    assert c.status_code == 200, (c.status_code, c.text[:300])
    return {"upload": up["id"], **c.json()}


def fixture_version_id() -> str:
    """A sealed version nothing to do with the recordings."""
    from common import fixture_version
    return fixture_version(CANARY, fixture_contract(CANARY))["id"]


def stored_object(key: str) -> bytes:
    return s3_client(*ADMIN).get_object(Bucket=bucket_for(CANARY), Key=key)["Body"].read()


def in_store(needle: str) -> int:
    """How many rows, across everything the platform keeps about recordings and keys, mention this text."""
    total = 0
    with db() as conn:
        for table in ("recording_upload", "recording_part", "recording_person", "dataset_source", "key_use_log", "subject_lookup", "subject_key", "tombstone"):
            total += conn.execute(f"select count(*) as n from {table} where {table}::text like %s", (f"%{needle}%",)).fetchone()["n"]
    return total


def main() -> int:
    from crypto import stream
    from app import db as app_db, person_keys, recordings
    from crypto import NotOpenable
    if app_db.pool.closed:
        app_db.pool.open()
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    service = person_keys.key_service()
    tag = uuid.uuid4().hex[:8]

    heading("An upload is refused, with every reason listed")
    dataset = register_dataset(ENGINEER)
    people_before = count("select count(*) as n from subject_lookup where tenant_id = %s", (CANARY,))
    r = open_up(dataset, ENGINEER, "../visit 1.mp3", 0, kind=" ", value="")
    text = reasons_of(r)
    check("a bad name, an empty file and no patient are all listed in one answer",
          r.status_code == 400 and "letters, numbers" in text and "empty" in text and "kind of identifier" in text and "no patient" in text, text[:300])
    r = open_up(dataset, ENGINEER, "visit-1.mp3", 100)
    check("a file that is neither a recording nor an answer key is refused", r.status_code == 400 and "neither a recording" in reasons_of(r), reasons_of(r)[:140])
    r = open_up(dataset, ENGINEER, "visit-1.wav", 513 * 1024 * 1024)
    check("a file over 512 MB is refused with the limit", r.status_code == 400 and "limit" in reasons_of(r), reasons_of(r)[:140])
    r = open_up(dataset, ENGINEER, "visit-1.truth.json", PART + 1)
    check("an answer key bigger than one part is refused", r.status_code == 400 and "one part" in reasons_of(r), reasons_of(r)[:140])
    check("a researcher may not open an upload", open_up(dataset, RESEARCHER, "visit-1.wav", 100, value="p").status_code == 403)
    org = make_org()
    try:
        r = httpx.post(f"{API}/datasets/{dataset}/recording-uploads", headers=org.bearer("custodian"), timeout=30.0,
                       json={"file_name": "visit-1.wav", "size_bytes": 100, "patient_kind": KIND, "patient_value": "p"})
        check("another organisation's custodian finds no such dataset", r.status_code == 404, str(r.status_code))
    finally:
        drop_org(org)
    check("nobody was made for any of those refusals", count("select count(*) as n from subject_lookup where tenant_id = %s", (CANARY,)) == people_before)

    heading("A recording of three parts, sent as a poor connection would send it")
    wav = make_wav(17_000_000)
    total = len(wav)
    patient_a, patient_b = f"A-{tag}-chart", f"B-{tag}-chart"
    r = open_up(dataset, ENGINEER, "visit-0001.wav", total, value=patient_a)
    up = r.json()
    check("it opens, and says how it is sent: three parts of at most 8 MB, none arrived",
          r.status_code == 201 and up["parts"] == 3 and up["part_bytes"] == PART and up["missing"] == [1, 2, 3] and up["received"] == [], str(up)[:200])
    check("part 2 sent first is kept", put(up["id"], 2, slice_of(wav, 2)).status_code == 200)
    first = put(up["id"], 1, slice_of(wav, 1))
    check("part 1 sent after it is kept", first.status_code == 200 and first.json()["missing"] == [3], first.text[:160])
    check("a part one byte short is refused and says what it should be", put(up["id"], 3, slice_of(wav, 3)[:-1]).status_code == 400
          and str(len(slice_of(wav, 3))) in reasons_of(put(up["id"], 3, slice_of(wav, 3)[:-1])))
    check("a part past the end of the file is refused", put(up["id"], 4, b"x").status_code == 400)
    check("part 0 is refused", put(up["id"], 0, b"x").status_code == 400)
    s = get_status(up["id"]).json()
    check("asking says which parts arrived and which are missing, so the upload carries on from there", s["received"] == [1, 2] and s["missing"] == [3], str(s)[:160])
    c = complete(up["id"])
    check("completing with a part missing is refused and names it", c.status_code == 400 and "part 3" in reasons_of(c), reasons_of(c)[:160])
    check("part 2 sent a second time (a retry) is kept in place of the first", put(up["id"], 2, slice_of(wav, 2)).status_code == 200)
    check("part 3 arrives", put(up["id"], 3, slice_of(wav, 3)).status_code == 200)
    c = complete(up["id"])
    done = c.json()
    check("then it completes", c.status_code == 200 and done["stored_bytes"] == stream.stored_size(total), str(done)[:200])
    check("nobody else's upload can be read: the researcher is told there is none", get_status(up["id"], RESEARCHER).status_code == 404)
    check("a finished upload takes no more parts", put(up["id"], 1, slice_of(wav, 1)).status_code == 400)

    heading("What was stored")
    with db() as conn:
        src = conn.execute("select * from dataset_source where id = %s", (done["source_id"],)).fetchone()
        upl = conn.execute("select * from recording_upload where id = %s", (up["id"],)).fetchone()
    body = stored_object(upl["storage_key"])
    check("the stored file is exactly the size a locked file of this size is", len(body) == stream.stored_size(total), f"{len(body)} vs {stream.stored_size(total)}")
    check("the recorded fingerprint is of what is stored", hashlib.sha256(body).hexdigest() == src["checksum"] and src["bytes"] == len(body))
    check("no stretch of the recording is in the stored file", all(wav[at:at + 64] not in body for at in range(44, total - 64, 1_000_003)))
    with wave.open(io.BytesIO(wav), "rb") as handle:
        frames, rate = handle.getnframes(), handle.getframerate()
    check("the length and sample rate recorded are those the standard reader finds in the whole file",
          abs(src["audio_duration_seconds"] - frames / rate) < 1e-6 and src["audio_sample_rate"] == rate, f"{src['audio_duration_seconds']} vs {frames / rate}")
    check("the file is marked as locked to a person, with the size of the plain file", src["locked_person_code"] and src["plain_bytes"] == total and src["locked_format"] == stream.FORMAT)

    heading("The patient")
    wav2 = make_wav(300_000)
    second = upload_whole(dataset, "visit-0002.wav", wav2, patient_a)
    third = upload_whole(dataset, "visit-0003.wav", make_wav(200_000), patient_b)
    other_kind = upload_whole(dataset, "visit-0004.wav", make_wav(100_000), patient_a, kind="national number")
    with db() as conn:
        codes = {r["locator"]: r["locked_person_code"] for r in conn.execute("select locator, locked_person_code from dataset_source where dataset_id = %s", (dataset,)).fetchall()}
    check("two recordings of one patient have one person code", codes["visit-0001.wav"] == codes["visit-0002.wav"], str(codes))
    check("another patient has another", codes["visit-0003.wav"] not in (codes["visit-0001.wav"], codes["visit-0002.wav"]))
    check("the same text as another kind of identifier is another person", codes["visit-0004.wav"] != codes["visit-0001.wav"])
    check("the identifier is found nowhere the platform keeps recordings or keys", in_store(patient_a) == 0 and in_store(patient_b) == 0, f"{in_store(patient_a)} {in_store(patient_b)}")
    n_keys = count("select count(*) as n from key_use_log where tenant_id = %s and action = 'unwrap' and purpose like %s", (CANARY, "lock part%"))
    check("every part locked is in the key log, with the part and the file named", n_keys >= 3 + 1 + 1 + 1 + 1, str(n_keys))

    heading("An answer key")
    truth = json.dumps({"reference_transcript": "the patient was seen on Tuesday", "spans": [], "hazard": "none"}).encode()
    key_upload = upload_whole(dataset, "visit-0001.truth.json", truth, patient_a)
    check("an answer key is sent whole, as one part, and kept", key_upload["stored_bytes"] == stream.stored_size(len(truth)))
    with db() as conn:
        k = conn.execute("select * from recording_upload where id = %s", (key_upload["upload"],)).fetchone()
    check("it is locked: the transcript is not in the stored file", b"Tuesday" not in stored_object(k["storage_key"]))
    no_text = b'{"spans": []}'
    r = open_up(dataset, ENGINEER, "visit-0009.truth.json", len(no_text), value=patient_a)
    bad = put(r.json()["id"], 1, no_text)
    check("an answer key missing what scoring reads is refused when its part is sent, as the ordinary upload refuses it",
          bad.status_code == 400 and "reference_transcript" in reasons_of(bad), reasons_of(bad)[:160])
    not_audio = b"this is not a wav file at all, not even close to one, no header"
    r = open_up(dataset, ENGINEER, "visit-0010.wav", len(not_audio), value=patient_a)
    bad = put(r.json()["id"], 1, not_audio)
    check("a file named as audio that is not a wav file is refused when its first part is sent, for being no wav file and not for its length",
          bad.status_code == 400 and "not a wav file" in reasons_of(bad) and "must be" not in reasons_of(bad), reasons_of(bad)[:160])

    heading("An upload nobody finishes, and one that is given up")
    r = open_up(dataset, ENGINEER, "visit-0020.wav", 500_000, value=patient_b)
    stale = r.json()["id"]
    check("a part is kept for it", put(stale, 1, make_wav(499_956)).status_code == 200)
    with db() as conn:
        conn.execute("update recording_upload set expires_at = now() - interval '1 minute' where id = %s", (stale,))
    check("an upload left past its day takes no more parts", put(stale, 1, make_wav(499_956)).status_code == 400)
    r = open_up(dataset, ENGINEER, "visit-0021.wav", 500_000, value=patient_b)
    with db() as conn:
        s = conn.execute("select status from recording_upload where id = %s", (stale,)).fetchone()["status"]
    check("opening another upload closes the one left behind", s == "expired", s)
    gone = r.json()["id"]
    d = httpx.delete(f"{API}/recording-uploads/{gone}", headers=bearer_for(ENGINEER), timeout=30.0)
    check("an upload that is given up says so, and takes nothing more", d.status_code == 200 and d.json()["status"] == "aborted" and put(gone, 1, make_wav(499_956)).status_code == 400)
    first_open = open_up(dataset, ENGINEER, "visit-0022.wav", 500_000, value=patient_b).json()["id"]
    second_open = open_up(dataset, ENGINEER, "visit-0022.wav", 500_000, value=patient_b).json()["id"]
    with db() as conn:
        s1 = conn.execute("select status from recording_upload where id = %s", (first_open,)).fetchone()["status"]
    check("opening the same file again replaces the first open one", s1 == "aborted" and first_open != second_open, s1)
    httpx.delete(f"{API}/recording-uploads/{second_open}", headers=bearer_for(ENGINEER), timeout=30.0)

    heading("The two older seals refuse a locked recording")
    plain = httpx.post(f"{API}/datasets/{dataset}/files", headers=bearer_for(ENGINEER), timeout=60.0,
                       files={"file": ("notes.txt", b"ordinary notes", "text/plain")})
    check("an ordinary file is uploaded alongside", plain.status_code == 201, plain.text[:100])
    r = api("POST", f"/datasets/{dataset}/seal-audio", headers=bearer_for(ENGINEER))
    check("sealing as ordinary audio refuses the locked recording and names it", r.status_code == 400 and "visit-0001.wav" in reasons_of(r) and "noise" in reasons_of(r), reasons_of(r)[:200])
    r = api("POST", f"/datasets/{dataset}/seal", headers=bearer_for(ENGINEER))
    check("the ordinary seal refuses it too, and names it", r.status_code == 400 and "visit-0001.wav" in reasons_of(r), reasons_of(r)[:200])
    r = api("POST", f"/datasets/{dataset}/seal-recordings", headers=bearer_for(ENGINEER))
    text = reasons_of(r)
    check("sealing locked recordings refuses the ordinary file, and names it", r.status_code == 400 and "notes.txt" in text and "ordinary file" in text, text[:200])
    with db() as conn:
        n_versions = conn.execute("select count(*) as n from dataset_version where dataset_id = %s", (dataset,)).fetchone()["n"]
    check("none of the three refusals sealed anything", n_versions == 0, str(n_versions))

    heading("Sealing the locked recordings")
    source_id = plain.json()["source_id"]
    w = api("POST", f"/datasets/{dataset}/uploads/{source_id}/withdraw", headers=bearer_for(ENGINEER))
    check("the ordinary file is withdrawn", w.status_code == 200, w.text[:100])
    with db() as conn:
        conn.execute("update dataset_source set withdrawn_at = now() where dataset_id = %s and locator = 'visit-0004.wav'", (dataset,))
    mismatch = upload_whole(dataset, "visit-0003.truth.json", truth, patient_a)
    r = api("POST", f"/datasets/{dataset}/seal-recordings", headers=bearer_for(ENGINEER))
    check("an answer key locked to a different patient from its recording is refused", r.status_code == 400 and "different patient" in reasons_of(r) and "visit-0003" in reasons_of(r), reasons_of(r)[:200])
    upload_whole(dataset, "visit-0003.truth.json", truth, patient_b)
    upload_whole(dataset, "visit-0099.truth.json", truth, patient_b)
    r = api("POST", f"/datasets/{dataset}/seal-recordings", headers=bearer_for(ENGINEER))
    check("an answer key for a recording that was not uploaded is refused", r.status_code == 400 and "visit-0099" in reasons_of(r), reasons_of(r)[:200])
    with db() as conn:
        conn.execute("update dataset_source set withdrawn_at = now() where dataset_id = %s and locator = 'visit-0099.truth.json'", (dataset,))
    check("a researcher may not seal", api("POST", f"/datasets/{dataset}/seal-recordings", headers=bearer_for(RESEARCHER)).status_code == 403)
    r = api("POST", f"/datasets/{dataset}/seal-recordings", headers=bearer_for(ENGINEER))
    sealed = r.json()
    check("it seals: three recordings, two patients", r.status_code == 201 and sealed["records"] == 3 and sealed["people"] == 2, f"{r.status_code} {str(sealed)[:200]}")
    version = sealed["id"]
    with db() as conn:
        v = conn.execute("select object_manifest, storage_prefix, schema_id from dataset_version where id = %s", (version,)).fetchone()
        rows = conn.execute("select * from recording_person where dataset_version_id = %s order by record_id", (version,)).fetchall()
        contract = conn.execute("select name from schema_contract where id = %s", (v["schema_id"],)).fetchone()["name"]
    names = sorted(m["key"].rsplit("/", 1)[1] for m in v["object_manifest"])
    wanted = {"records.json", "visit-0001.wav", "visit-0002.wav", "visit-0003.wav", "visit-0001.truth.json", "visit-0003.truth.json"}
    check("the version lists every stored file and the plain index (sealing adds the Iceberg table's own files beside them)", wanted <= set(names), str(sorted(wanted - set(names))))
    check("and lists no recording that was withdrawn or left unfinished", not any(n in names for n in ("visit-0004.wav", "visit-0099.truth.json", "notes.txt", "visit-0020.wav")))
    check("each listed fingerprint is of what is stored", all(hashlib.sha256(stored_object(m["key"])).hexdigest() == m["sha256"] for m in v["object_manifest"]))
    index = json.loads(stored_object(f"{v['storage_prefix']}/records.json"))
    check("the plain index is the recordings' own contract, and names no patient and no person code",
          contract == "encounter_raw" and [r["record_id"] for r in index] == ["visit-0001", "visit-0002", "visit-0003"]
          and all(set(r) == {"record_id", "audio_key", "duration_seconds", "sample_rate"} for r in index)
          and patient_a not in json.dumps(index) and codes["visit-0001.wav"] not in json.dumps(index))
    check("a row says whose each recording is, by person code, and its plain size",
          [r["person_code"] for r in rows] == [codes["visit-0001.wav"], codes["visit-0002.wav"], codes["visit-0003.wav"]] and rows[0]["plain_bytes"] == total)

    heading("A pipeline run is given the recording")
    stored_before = {m["key"]: stored_object(m["key"]) for m in v["object_manifest"]}
    run = f"pipeline_run verify-{tag}"
    size, pieces = recordings.open_recording(service, version_id=version, record_id="visit-0001", run=run, purpose="check")
    opened = b"".join(pieces)
    check("the recording opens byte for byte, and its size is what was said", opened == wav and size == total, f"{len(opened)} vs {total}")
    log = count("select count(*) as n from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s", (CANARY, run))
    check("the opening is in the key log, with the run named", log == 1, str(log))
    check("a version that holds no such recording is refused", _raises(lambda: recordings.open_recording(service, version_id=version, record_id="visit-5000", run="r", purpose="p"), person_keys.NoSuchVersion))
    key = f"{v['storage_prefix']}/visit-0002.wav"
    altered = bytearray(stored_before[key])
    altered[len(altered) // 2] ^= 1
    s3_client(*ADMIN).put_object(Bucket=bucket_for(CANARY), Key=key, Body=bytes(altered))
    check("a file changed in storage does not open", _raises(lambda: b"".join(recordings.open_recording(service, version_id=version, record_id="visit-0002", run="r", purpose="p")[1]), NotOpenable))
    s3_client(*ADMIN).put_object(Bucket=bucket_for(CANARY), Key=key, Body=stored_before[key])
    check("and opens again, to the same bytes, when put back", b"".join(recordings.open_recording(service, version_id=version, record_id="visit-0002", run="r", purpose="p")[1]) == wav2)
    patient_c = f"C-{tag}-chart"
    c_open = open_up(dataset, ENGINEER, "visit-0060.wav", 500_000, value=patient_c).json()["id"]
    check("a part is kept for a patient whose upload is in progress", put(c_open, 1, make_wav(499_956)).status_code == 200)

    heading("What is made from a patient's recordings")
    workload = f"{CANARY}-pipeline"
    with db() as conn:
        conn.execute("insert into directory (id, tenant_id, label, kind, roles) values (%s, %s, 'Pipeline', 'workload', '{pipeline_action}') on conflict (id) do nothing", (workload, CANARY))
        action = str(uuid.uuid4())
        conn.execute("insert into dataset_action (id, tenant_id, name) values (%s, %s, 'u136 derive')", (action, CANARY))
    schema = fixture_contract(CANARY)

    def derive(from_version: str, name: str) -> dict:
        run = api("POST", "/action-runs", json={"tenant_id": CANARY, "action_id": action, "code_hash": uuid.uuid4().hex, "image_digest": "sha256:verify",
                                                "operator": workload, "idempotency_key": f"u136-{uuid.uuid4()}", "input_versions": [from_version],
                                                "params": {}, "trigger_kind": "manual", "triggered_by": ENGINEER}).json()
        ds = api("POST", "/datasets", json={"tenant_id": CANARY, "name": f"{name}-{tag}"}).json()["id"]
        made = api("POST", "/dataset-versions", json={"tenant_id": CANARY, "dataset_id": ds, "schema_id": schema, "visibility_class": "RAW",
                                                      "object_manifest": [{"key": "part-0.json", "bytes": 1}], "record_count": 1, "produced_by_run": run["id"]})
        made.raise_for_status()
        return {"id": made.json()["id"], "name": f"{name}-{tag}", "run": run["id"]}

    transcripts = derive(version, "transcripts")
    redacted = derive(transcripts["id"], "redacted")
    unrelated = derive(fixture_version_id(), "unrelated")
    left = person_keys.still_made_from(CANARY, codes["visit-0001.wav"])
    got = {d["version_id"]: d for d in left["derived_versions"]}
    check("the version that holds the patient's recordings is named", left["recording_versions"] == [version], str(left["recording_versions"]))
    check("what a run made from it is listed, with the run that made it", transcripts["id"] in got and got[transcripts["id"]]["run_id"] == transcripts["run"], str(got)[:200])
    check("and what was made from that, however far down", redacted["id"] in got and got[redacted["id"]]["dataset_name"] == redacted["name"])
    check("something made from an unrelated version is not listed", unrelated["id"] not in got)
    check("the version of the recordings is not listed as derived from itself", version not in got)
    check("a person with no recordings has nothing listed", person_keys.still_made_from(CANARY, "T-0000000000000000") == {"recording_versions": [], "derived_versions": []})

    heading("Erasing the patient")
    out = person_keys.erase_person(service, CANARY, KIND, patient_a, identity(CUSTODIAN, ["data_custodian"]), "asked to be forgotten")
    check("the patient is erased: their key is destroyed", out["found"] and out["key_destroyed"], str(out))
    check("and the answer says what was made from their recordings and was not changed",
          {transcripts["id"], redacted["id"]} <= {d["version_id"] for d in out["derived_versions"]} and out["recording_versions"] == [version], str(out)[:200])
    check("their first recording cannot be opened", _raises(lambda: recordings.open_recording(service, version_id=version, record_id="visit-0001", run="r", purpose="p"), recordings.PatientErased))
    check("their second cannot be opened either", _raises(lambda: recordings.open_recording(service, version_id=version, record_id="visit-0002", run="r", purpose="p"), recordings.PatientErased))
    check("the other patient's recording still opens", len(b"".join(recordings.open_recording(service, version_id=version, record_id="visit-0003", run="r", purpose="p")[1])) > 0)
    check("nothing stored changed", all(stored_object(k) == b for k, b in stored_before.items()))
    out = person_keys.erase_person(service, CANARY, KIND, patient_c, identity(CUSTODIAN, ["data_custodian"]), "asked to be forgotten")
    sent = put(c_open, 1, make_wav(499_956))
    check("a patient erased while their upload was open: the next part is refused, and says why", sent.status_code == 409 and "destroyed" in reasons_of(sent), f"{sent.status_code} {reasons_of(sent)[:140]}")
    again = open_up(dataset, ENGINEER, "visit-0061.wav", 1000, value=patient_a)
    with db() as conn:
        fresh = conn.execute("select person_code from recording_upload where id = %s", (again.json()["id"],)).fetchone()["person_code"]
    check("the same identifier after erasure is a new person, with a new key, and not the one erased", again.status_code == 201 and fresh != codes["visit-0001.wav"], fresh)
    reset_keys()
    return summary("v137")


def _raises(fn, exc) -> bool:
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


if __name__ == "__main__":
    sys.exit(main())
