"""Recordings locked per person: an upload sent in parts that can each be sent again, a seal that refuses anything not locked, and the one way
a pipeline run is given a recording back.

A recording belongs to one person. It is stored locked under that person's key, in chunks (crypto.stream), so erasing the person destroys the
key and every recording of theirs is unreadable at once. The platform finds the person from an identifier the uploader supplies with the
file (and its kind: the chart number, say). The identifier is never stored: a blind index finds the person, as it does for a table.

An upload is three acts, each of which can be repeated:

  * open it: name the file, say how big it is and whose it is. The platform makes the person's key if this is the first time it has seen them;
  * send each part: the part is locked and kept, and sending it again replaces it, so a part that failed on a poor connection is simply sent
    again, and a person can ask which parts have arrived and carry on from there;
  * complete it: every part must be there, the stored size must be exactly what the locked file's size should be, and only then does it count
    as an upload of the dataset.

A recording that was not locked can never be sealed here, and a locked one can never be sealed as ordinary audio: each seal refuses the other's
files, because locked bytes opened as audio are noise and plain bytes sealed as locked are not locked.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import uuid
from datetime import datetime, timedelta, timezone

from crypto import DestroyedKeyError, KeyService, NotOpenable, lock_part, open_stream, read_range  # noqa: F401
from crypto import stream

from . import db, person_keys, storage, versions
from .person_errors import NoSuchVersion, NotPermitted, Refused

WAV = ".wav"
ANSWER_KEY = ".truth.json"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_BYTES = 512 * 1024 * 1024
UPLOAD_LIFE = timedelta(hours=24)
FORMAT = stream.FORMAT


class NoSuchUpload(LookupError):
    pass


class PersonErased(Exception):
    """The person's key was destroyed, so nothing more can be locked for them or opened for them."""


# Opening an upload ---------------------------------------------------------


def _problems_opening(file_name: str, size: int, kind: str, value: str) -> list[str]:
    out = []
    if not isinstance(file_name, str) or not NAME.match(file_name):
        out.append("the file name may use letters, numbers, dots, dashes and underscores only, and must start with a letter or number")
    elif not file_name.endswith((WAV, ANSWER_KEY)):
        out.append(f"{file_name} is neither a recording ({WAV}) nor an answer key ({ANSWER_KEY})")
    if not isinstance(size, int) or size < 1:
        out.append("the file is empty")
    elif size > MAX_BYTES:
        out.append(f"the file is {size} bytes, over the {MAX_BYTES} byte limit")
    elif isinstance(file_name, str) and file_name.endswith(ANSWER_KEY) and size > stream.PART:
        out.append(f"an answer key is sent whole, in one part of at most {stream.PART} bytes")
    if not str(kind or "").strip():
        out.append("say what kind of identifier names the person, such as a member number")
    if not str(value or "").strip():
        out.append("this recording names no person: a recording is locked to one person, and the person's identifier is empty")
    return out


def expire_stale(tenant_id: str) -> int:
    """Close the uploads nobody finished, and let storage drop the parts they sent. Runs whenever somebody opens an upload."""
    stale = db.all_rows("select u.id, u.storage_key, u.storage_upload, d.storage_backend from recording_upload u join dataset d on d.id = u.dataset_id "
                        "where u.tenant_id = %s and u.status = 'open' and u.expires_at < now()", (tenant_id,))
    for row in stale:
        _drop_storage_upload(tenant_id, row["storage_backend"] or "seaweedfs", row["storage_key"], row["storage_upload"])
        db.execute("update recording_upload set status = 'expired', finished_at = now() where id = %s and status = 'open' returning id", (row["id"],))
    return len(stale)


def _drop_storage_upload(tenant: str, backend: str, key: str, upload: str) -> None:
    try:
        storage.admin_client_for(backend, tenant).abort_multipart_upload(Bucket=storage.bucket_for(backend, tenant), Key=key, UploadId=upload)
    except Exception:  # noqa: BLE001 - the storage side may already be gone, and the row is what says the upload is over
        pass


def open_upload(service: KeyService, *, dataset_id: str, identity: dict, file_name: str, size_bytes: int,
                person_kind: str, person_value: str) -> dict:
    dataset = person_keys.intake_dataset(dataset_id, identity)
    problems = _problems_opening(file_name, size_bytes, person_kind, person_value)
    if problems:
        raise Refused(problems)
    tenant, backend = dataset["tenant_id"], dataset["storage_backend"] or "seaweedfs"
    expire_stale(tenant)

    person = person_keys.resolve_person(service, tenant, person_kind, person_value, identity["id"])
    prefix = versions.next_version(tenant, dataset_id)["storage_prefix"]
    key = f"{prefix}/{file_name}"

    # A second open of the same file replaces the first, whose parts are dropped, so a person who starts again does not leave a heap behind.
    for old in db.all_rows("select id, storage_key, storage_upload from recording_upload where dataset_id = %s and file_name = %s and status = 'open'",
                           (dataset_id, file_name)):
        _drop_storage_upload(tenant, backend, old["storage_key"], old["storage_upload"])
        db.execute("update recording_upload set status = 'aborted', finished_at = now() where id = %s returning id", (old["id"],))

    client = storage.admin_client_for(backend, tenant)
    storage_upload = client.create_multipart_upload(Bucket=storage.bucket_for(backend, tenant), Key=key)["UploadId"]
    upload_id = str(uuid.uuid4())
    expires = datetime.now(timezone.utc) + UPLOAD_LIFE
    db.execute("insert into recording_upload (id, tenant_id, dataset_id, file_name, size_bytes, person_code, storage_key, storage_upload, created_by, expires_at) "
               "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
               (upload_id, tenant, dataset_id, file_name, size_bytes, person, key, storage_upload, identity["id"], expires))
    return _describe(upload_id)


def _describe(upload_id: str) -> dict:
    up = db.one("select * from recording_upload where id = %s", (upload_id,))
    got = [r["part_number"] for r in db.all_rows("select part_number from recording_part where upload_id = %s order by part_number", (upload_id,))]
    total, parts = up["size_bytes"], stream.part_count(up["size_bytes"])
    return {"id": str(up["id"]), "file_name": up["file_name"], "size_bytes": total, "status": up["status"],
            "part_bytes": stream.PART, "parts": parts, "received": got, "missing": [n for n in range(1, parts + 1) if n not in got],
            "expires_at": up["expires_at"].isoformat(), "facts": up["facts"]}


def _own_upload(upload_id: str, identity: dict, *, need_open: bool = True) -> dict:
    up = db.one("select * from recording_upload where id = %s", (upload_id,))
    if not up or up["tenant_id"] != identity["tenant_id"] or up["created_by"] != identity["id"]:
        raise NoSuchUpload(upload_id)
    if need_open:
        if up["status"] != "open":
            raise Refused([f"this upload is {up['status']}, so no more can be sent to it. Open a new upload for the file"])
        if up["expires_at"] < datetime.now(timezone.utc):
            raise Refused(["this upload ran out of time, as it was left for a day. Open a new upload for the file"])
    return up


def status(upload_id: str, identity: dict) -> dict:
    _own_upload(upload_id, identity, need_open=False)
    return _describe(upload_id)


# What the first part says about the file ------------------------------------


def wav_facts(head: bytes, total: int) -> dict:
    """The length and sample rate of a recording, read from the start of its bytes, as `wave` would read them from the whole file.

    The first part holds the header of any ordinary recording. A header that is not in the first part is refused, because the platform does
    not hold the rest of the file while it reads this.
    """
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise Refused(["this is named as audio but is not a wav file: it does not start as one"])
    at, fmt, data = 12, None, None
    while at + 8 <= len(head):
        tag, size = head[at:at + 4], struct.unpack("<I", head[at + 4:at + 8])[0]
        body = at + 8
        if tag == b"fmt " and body + 16 <= len(head):
            _, channels, rate, _, align, bits = struct.unpack("<HHIIHH", head[body:body + 16])
            fmt = (channels, rate, align, bits)
        elif tag == b"data":
            data = (body, size)
            break
        at = body + size + (size & 1)
    if fmt is None or data is None:
        raise Refused(["this is named as audio but its header is not within its first 8 MB, so the platform cannot read its length"])
    channels, rate, align, _ = fmt
    if not rate or not align:
        raise Refused(["this recording reports a sample rate of zero, so no duration can be derived from it"])
    start, declared = data
    available = total - start
    size = declared if 0 < declared <= available else available
    return {"audio_duration_seconds": (size // align) / rate, "audio_sample_rate": rate}


def _facts(file_name: str, first_part: bytes, total: int) -> dict:
    if file_name.endswith(WAV):
        return wav_facts(first_part, total)
    from . import ingest  # the same answer-key rules as the ordinary upload
    from fastapi import HTTPException
    try:
        return ingest._answer_key_facts(file_name, first_part)
    except HTTPException as exc:
        raise Refused(list(exc.detail["reasons"])) from exc


# Sending a part ---------------------------------------------------------------


def receive_part(service: KeyService, *, upload_id: str, part_number: int, data: bytes, identity: dict) -> dict:
    up = _own_upload(upload_id, identity)
    total = up["size_bytes"]
    if not 1 <= part_number <= stream.part_count(total):
        raise Refused([f"this file has {stream.part_count(total)} part(s), so there is no part {part_number}"])
    expected = stream.part_plain_size(total, part_number)
    if len(data) != expected:
        raise Refused([f"part {part_number} of {up['file_name']} must be {expected} bytes, and {len(data)} arrived"])
    if part_number == 1:
        db.execute("update recording_upload set facts = %s where id = %s returning id", (json.dumps(_facts(up["file_name"], data, total)), upload_id))

    tenant, person = up["tenant_id"], up["person_code"]
    try:
        key = service.unwrap(tenant, person, identity["id"], f"lock part {part_number} of recording {up['file_name']}")
    except DestroyedKeyError as exc:
        raise PersonErased(f"the key for this person was destroyed, so nothing more can be locked for them") from exc
    stored = lock_part(key, tenant, person, up["file_name"], total, part_number, data)

    dataset = db.one("select storage_backend from dataset where id = %s", (up["dataset_id"],))
    backend = dataset["storage_backend"] or "seaweedfs"
    etag = storage.admin_client_for(backend, tenant).upload_part(
        Bucket=storage.bucket_for(backend, tenant), Key=up["storage_key"], UploadId=up["storage_upload"], PartNumber=part_number, Body=stored)["ETag"]
    db.execute("insert into recording_part (upload_id, part_number, tenant_id, stored_bytes, etag) values (%s, %s, %s, %s, %s) "
               "on conflict (upload_id, part_number) do update set stored_bytes = excluded.stored_bytes, etag = excluded.etag, received_at = now() "
               "returning part_number", (upload_id, part_number, tenant, len(stored), etag))
    return {"part_number": part_number, "stored_bytes": len(stored), **{k: v for k, v in _describe(upload_id).items() if k in ("received", "missing")}}


# Completing an upload -----------------------------------------------------------


def complete(upload_id: str, identity: dict) -> dict:
    up = _own_upload(upload_id, identity)
    tenant, total = up["tenant_id"], up["size_bytes"]
    parts = db.all_rows("select part_number, etag from recording_part where upload_id = %s order by part_number", (upload_id,))
    have = {p["part_number"] for p in parts}
    missing = [n for n in range(1, stream.part_count(total) + 1) if n not in have]
    if missing:
        raise Refused([f"part {n} of {up['file_name']} has not arrived" for n in missing])

    dataset = db.one("select storage_backend, registered_by from dataset where id = %s", (up["dataset_id"],))
    backend = dataset["storage_backend"] or "seaweedfs"
    client, bucket = storage.admin_client_for(backend, tenant), storage.bucket_for(backend, tenant)
    if up["storage_key"].rsplit("/", 1)[0] != versions.next_version(tenant, str(up["dataset_id"]))["storage_prefix"]:
        _drop_storage_upload(tenant, backend, up["storage_key"], up["storage_upload"])
        db.execute("update recording_upload set status = 'aborted', finished_at = now() where id = %s returning id", (upload_id,))
        raise Refused(["another version of this dataset was sealed while this was being sent, so its place has moved. Open a new upload for the file"])

    client.complete_multipart_upload(Bucket=bucket, Key=up["storage_key"], UploadId=up["storage_upload"],
                                     MultipartUpload={"Parts": [{"PartNumber": p["part_number"], "ETag": p["etag"]} for p in parts]})
    digest, size = hashlib.sha256(), 0
    for block in client.get_object(Bucket=bucket, Key=up["storage_key"])["Body"].iter_chunks(chunk_size=stream.PART):
        digest.update(block)
        size += len(block)
    if size != stream.stored_size(total):
        client.delete_object(Bucket=bucket, Key=up["storage_key"])
        db.execute("update recording_upload set status = 'aborted', finished_at = now() where id = %s returning id", (upload_id,))
        raise Refused([f"{up['file_name']} was stored as {size} bytes, and a locked file of {total} bytes is {stream.stored_size(total)}. It was not kept. Open a new upload for the file"])

    facts = up["facts"] or {}
    source_id = str(uuid.uuid4())
    db.execute("insert into dataset_source (id, dataset_id, kind, locator, checksum, bytes, fetched_by, audio_duration_seconds, audio_sample_rate, "
               "truth_has_hazard, locked_person_code, locked_format, plain_bytes) values (%s, %s, 'upload', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
               (source_id, up["dataset_id"], up["file_name"], digest.hexdigest(), size, identity["id"], facts.get("audio_duration_seconds"),
                facts.get("audio_sample_rate"), facts.get("truth_has_hazard"), up["person_code"], FORMAT, total))
    db.execute("update recording_upload set status = 'complete', finished_at = now(), source_id = %s where id = %s returning id", (source_id, upload_id))
    return {"id": upload_id, "source_id": source_id, "file_name": up["file_name"], "stored_bytes": size, "checksum": digest.hexdigest(), "facts": facts}


def abort(upload_id: str, identity: dict) -> dict:
    up = _own_upload(upload_id, identity)
    dataset = db.one("select storage_backend from dataset where id = %s", (up["dataset_id"],))
    _drop_storage_upload(up["tenant_id"], dataset["storage_backend"] or "seaweedfs", up["storage_key"], up["storage_upload"])
    db.execute("update recording_upload set status = 'aborted', finished_at = now() where id = %s returning id", (upload_id,))
    return _describe(upload_id)


# Sealing -----------------------------------------------------------------------------


def _pending(dataset_id: str) -> list[dict]:
    """The uploads that count, newest first per file name (the same rule as ordinary uploads), with what locking adds."""
    return db.all_rows(
        """select distinct on (locator) locator, checksum, bytes, audio_duration_seconds, audio_sample_rate, truth_has_hazard,
                  locked_person_code, locked_format, plain_bytes
             from dataset_source
            where dataset_id = %s and kind = 'upload' and checksum is not null and withdrawn_at is null
            order by locator, fetched_at desc""", (dataset_id,))


def seal_recordings(*, dataset_id: str, identity: dict) -> dict:
    """Seal the uploads as a version the de-identification pipeline can read, every recording locked under its person's key.

    Pressing this is the check, as it is for a table: a refusal seals nothing and every problem is listed together.
    """
    from . import ingest
    dataset = person_keys.intake_dataset(dataset_id, identity)
    if db.one("select id from huggingface_fetch_job where dataset_id = %s and status = 'running'", (dataset_id,)):
        raise Refused(["a HuggingFace fetch is still running for this dataset; sealing now could produce a version missing files still on the way"])
    uploads = _pending(dataset_id)
    audio = {u["locator"][:-len(WAV)]: u for u in uploads if u["locator"].endswith(WAV) and u["locked_person_code"]}
    keys = {u["locator"][:-len(ANSWER_KEY)]: u for u in uploads if u["locator"].endswith(ANSWER_KEY) and u["locked_person_code"]}

    reasons: list[str] = []
    if not uploads:
        reasons.append("nothing has been uploaded to this dataset yet")
    elif not audio:
        reasons.append("no recording has been uploaded locked to a person. Recordings are named <record>.wav")
    for u in sorted(uploads, key=lambda u: u["locator"]):
        if not u["locked_person_code"]:
            reasons.append(f"{u['locator']} was uploaded as an ordinary file, which locks nothing. Withdraw it and upload it again as a locked recording")
    for stem in sorted(set(keys) - set(audio)):
        reasons.append(f"{stem}{ANSWER_KEY} is an answer key for a recording that was not uploaded. Upload {stem}{WAV} or withdraw the key")
    for stem in sorted(set(keys) & set(audio)):
        if keys[stem]["locked_person_code"] != audio[stem]["locked_person_code"]:
            reasons.append(f"{stem}{ANSWER_KEY} is locked to a different person from {stem}{WAV}: a recording and its answer key are one person's")
    if reasons:
        raise Refused(reasons)

    tenant, backend = dataset["tenant_id"], dataset["storage_backend"] or "seaweedfs"
    prefix = versions.next_version(tenant, dataset_id)["storage_prefix"]
    records = [{"record_id": stem, "audio_key": f"{prefix}/{stem}{WAV}", "duration_seconds": float(u["audio_duration_seconds"]),
                "sample_rate": int(u["audio_sample_rate"])} for stem, u in sorted(audio.items())]
    payload = json.dumps(records).encode("utf-8")
    storage.admin_client_for(backend, tenant).put_object(Bucket=storage.bucket_for(backend, tenant), Key=f"{prefix}/records.json", Body=payload)
    contract = versions.register_contract(tenant, "encounter_raw", ingest.ENCOUNTER_RAW_FIELDS, ["record_id"])
    manifest = [{"key": f"{prefix}/{u['locator']}", "bytes": u["bytes"], "sha256": u["checksum"]} for u in uploads]
    manifest.append({"key": f"{prefix}/records.json", "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    version = versions.seal(tenant_id=tenant, dataset_id=dataset_id, schema_id=contract["id"], visibility_class="RAW", storage_backend=backend,
                            object_manifest=manifest, record_count=len(records), records_key=f"{prefix}/records.json")
    for stem, u in sorted(audio.items()):
        db.execute("insert into recording_person (dataset_version_id, record_id, tenant_id, person_code, plain_bytes, format, answer_key_bytes) "
                   "values (%s, %s, %s, %s, %s, %s, %s) returning record_id",
                   (version["id"], stem, tenant, u["locked_person_code"], u["plain_bytes"], u["locked_format"], keys[stem]["plain_bytes"] if stem in keys else None))
    return {**version, "records": len(records), "people": len({u["locked_person_code"] for u in audio.values()}), "sealed_by": identity["id"]}


# What a screen shows of a sealed version ----------------------------------------------------------


def summary(version_id: str, identity: dict) -> dict:
    """How many recordings a version holds, locked, and how many different people they are about. Counts only: no name, no person code.

    Asking records nothing, because it opens nothing. Raises NoSuchVersion for a version that is not this person's organisation's or that holds no
    locked recordings, so a screen shows the panel only where it is true.
    """
    row = db.one("select v.tenant_id, count(*) as recordings, count(distinct r.person_code) as people, count(*) filter (where p.destroyed_at is not null) as erased "
                 "from recording_person r join dataset_version v on v.id = r.dataset_version_id "
                 "left join subject_key p on p.tenant_id = r.tenant_id and p.person_code = r.person_code "
                 "where r.dataset_version_id = %s group by v.tenant_id", (version_id,))
    if not row or row["tenant_id"] != identity["tenant_id"]:
        raise NoSuchVersion(version_id)
    return {"version_id": version_id, "recordings": row["recordings"], "people": row["people"], "erased_recordings": row["erased"], "format": FORMAT}


# Giving a recording back to a pipeline run -----------------------------------------------------


def is_locked_version(version_id: str) -> bool:
    return db.one("select 1 as x from recording_person where dataset_version_id = %s limit 1", (version_id,)) is not None


def list_for_step(version_id: str) -> list[dict]:
    """Every recording a sealed version holds, as a custom pipeline step is told of them: its id, its size, and whether its person was erased.

    Reads the version's plain index (the recordings' own contract, which names no person). A recording of an erased person is listed as erased
    and is never opened.
    """
    row = db.one("select tenant_id, storage_backend, object_manifest from dataset_version where id = %s", (version_id,))
    if not row:
        raise NoSuchVersion(version_id)
    entry = next((m for m in row["object_manifest"] if m["key"].endswith("/records.json")), None)
    if entry is None:
        raise NoSuchVersion(f"{version_id} has no list of recordings")
    client = storage.admin_client_for(row["storage_backend"], row["tenant_id"])
    index = json.loads(client.get_object(Bucket=storage.bucket_for(row["storage_backend"], row["tenant_id"]), Key=entry["key"])["Body"].read())
    locked = {r["record_id"]: r for r in db.all_rows(
        "select r.record_id, r.plain_bytes, p.destroyed_at is not null as erased from recording_person r "
        "left join subject_key p on p.tenant_id = r.tenant_id and p.person_code = r.person_code where r.dataset_version_id = %s", (version_id,))}
    out = []
    for record in index:
        rid = record["record_id"]
        if rid in locked:
            out.append({"record_id": rid, "bytes": int(locked[rid]["plain_bytes"]), "erased": bool(locked[rid]["erased"])})
        else:
            stored = next((m for m in row["object_manifest"] if m["key"].endswith(f"/{rid}{WAV}")), None)
            if stored:
                out.append({"record_id": rid, "bytes": int(stored["bytes"]), "erased": False})
    return out


def _locate(version_id: str, record_id: str, kind: str, plain_ok: bool) -> dict:
    """Where a recording (or its answer key) is stored, how big it is once opened, and whose it is. `person` is None for one that is not locked.

    Raises NoSuchVersion when the version holds no such recording. A recording that is not locked is found only for a caller that is allowed to
    be given plain ones (an approved custom step), and only for audio.
    """
    if kind not in ("audio", "answer_key"):
        raise NoSuchVersion(f"unknown kind {kind}")
    row = db.one("select r.tenant_id, r.person_code, r.plain_bytes, r.answer_key_bytes, r.format, v.storage_backend, v.object_manifest from recording_person r "
                 "join dataset_version v on v.id = r.dataset_version_id where r.dataset_version_id = %s and r.record_id = %s", (version_id, record_id))
    if not row:
        if plain_ok and kind == "audio" and not is_locked_version(version_id):
            plain = db.one("select tenant_id, storage_backend, object_manifest from dataset_version where id = %s", (version_id,))
            entry = next((m for m in (plain["object_manifest"] if plain else []) if m["key"].endswith(f"/{record_id}{WAV}")), None)
            if entry is None:
                raise NoSuchVersion(f"{version_id} holds no recording {record_id}")
            return {"tenant": plain["tenant_id"], "person": None, "plain": int(entry["bytes"]), "key": entry["key"], "backend": plain["storage_backend"], "name": None}
        raise NoSuchVersion(f"{version_id} holds no locked recording {record_id}")
    suffix = WAV if kind == "audio" else ANSWER_KEY
    plain = row["plain_bytes"] if kind == "audio" else row["answer_key_bytes"]
    if plain is None:
        raise NoSuchVersion(f"{version_id} holds no answer key for {record_id}")
    entry = next((m for m in row["object_manifest"] if m["key"].endswith(f"/{record_id}{suffix}")), None)
    if entry is None:
        raise NoSuchVersion(f"{version_id} has no stored file for {record_id}")
    return {"tenant": row["tenant_id"], "person": row["person_code"], "plain": int(plain), "key": entry["key"], "backend": row["storage_backend"],
            "name": f"{record_id}{suffix}"}


def _open_key(service: KeyService, where: dict, run: str, purpose: str, audit: bool) -> bytes:
    try:
        return service.unwrap(where["tenant"], where["person"], run, purpose, audit)
    except DestroyedKeyError as exc:
        raise PersonErased("the person this recording is about was erased, so it cannot be opened") from exc


def _storage(where: dict):
    return storage.admin_client_for(where["backend"], where["tenant"]), storage.bucket_for(where["backend"], where["tenant"])


def open_recording(service: KeyService, *, version_id: str, record_id: str, run: str, purpose: str, kind: str = "audio", plain_ok: bool = False):
    """The recording's bytes (or, with kind "answer_key", its answer key's), opened a chunk at a time. Each opening is recorded in the key log with
    the run named.

    Raises NoSuchVersion when the version holds no such recording and PersonErased when the person's key was destroyed.
    """
    where = _locate(version_id, record_id, kind, plain_ok)
    client, bucket = _storage(where)
    if where["person"] is None:
        return where["plain"], client.get_object(Bucket=bucket, Key=where["key"])["Body"].iter_chunks(1 << 20)
    key = _open_key(service, where, run, purpose, audit=True)
    body = client.get_object(Bucket=bucket, Key=where["key"])["Body"]
    return where["plain"], open_stream(key, where["tenant"], where["person"], where["name"], where["plain"], body)


def begin_recording(service: KeyService, *, version_id: str, record_id: str, run: str, purpose: str, kind: str = "audio", plain_ok: bool = False) -> int:
    """Open a recording to be read a range at a time. This is the opening that is written to the key log, once, with the run named; it returns how
    big the recording is when opened. Raises PersonErased when the person's key was destroyed."""
    where = _locate(version_id, record_id, kind, plain_ok)
    if where["person"] is not None:
        _open_key(service, where, run, purpose, audit=True)
    return where["plain"]


def read_recording_range(service: KeyService, *, version_id: str, record_id: str, run: str, offset: int, length: int, kind: str = "audio",
                         plain_ok: bool = False) -> tuple[int, bytes]:
    """`length` bytes of a recording from `offset`, opened, touching only the chunks that hold them. The person's key is checked every time, so
    a recording that is being read stops being readable the moment the person is erased; the key log is not written again, because the opening
    it belongs to was logged by `begin_recording`. Returns the recording's size and the bytes."""
    where = _locate(version_id, record_id, kind, plain_ok)
    client, bucket = _storage(where)

    def fetch(at: int, size: int) -> bytes:
        return client.get_object(Bucket=bucket, Key=where["key"], Range=f"bytes={at}-{at + size - 1}")["Body"].read()

    if where["person"] is None:
        end = min(where["plain"], offset + length)
        return where["plain"], (fetch(offset, end - offset) if offset < end else b"")
    key = _open_key(service, where, run, "read a range of an opened recording", audit=False)
    return where["plain"], read_range(key, where["tenant"], where["person"], where["name"], where["plain"], fetch, offset, length)
