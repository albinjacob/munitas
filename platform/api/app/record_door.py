"""The door through which a person opens a file of a version: a recording, or a file a pipeline step made.

Nothing is handed out. A person asks the platform, which decides from what it holds why this person may open this version's files (never from what the
person says), unlocks a locked file with its patient's key inside the platform, and sends the bytes. The key and the storage address never leave. Every
opening is written to a log, naming the person, the version, the file and why, and a locked file's opening is written to the key log as well.

Why a person may open a version's files (`basis_of`):

  own_sample              the version is a development sample drawn for this person, and it is in force;
  synthetic               the version was declared synthetic, so no real person is in it;
  own_development_output  the version was made by a run this person started, on development data that is still in force for them;
  none                    anything else: real recordings, and what was made from them. They are opened under a lease, which is asked for on the page of the
                          recordings.

What is made from a recording is no more open than the recording. The policy (`access.rego`, `open_record_decision`) turns the basis into an answer.

Opening gives a short-lived handle to that one file. The handle is the only thing the stream route accepts, and the stream route asks everything
again on every request: is the handle real and unexpired, is the person still in post and their organisation open, is the basis still true, was the
patient erased. So a sample that ends, or a patient who is erased, stops what is playing at its next request.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Iterator

from crypto import DestroyedKeyError, NotOpenable, read_range

from . import auth, db, development_outputs, development_samples, opa, recordings, task_credential
from .person_errors import NoSuchVersion, NotPermitted

HANDLE_SECONDS = 15 * 60
LIST_LIMIT = 500
WINDOW = 4 * 1024 * 1024

CONTENT_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac", ".ogg": "audio/ogg", ".m4a": "audio/mp4",
                 ".txt": "text/plain; charset=utf-8", ".csv": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8", ".json": "application/json"}


class NoSuchRecord(LookupError):
    pass


class HandleRefused(Exception):
    """The handle is not good, or what it was for is no longer open to this person."""

    def __init__(self, status: int, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.status, self.reasons = status, reasons


def _content_type(name: str) -> str:
    dot = name.rfind(".")
    return CONTENT_TYPES.get(name[dot:].lower() if dot >= 0 else "", "application/octet-stream")


def _version(version_id: str, tenant_id: str) -> dict:
    row = db.one("select id, tenant_id, storage_backend, storage_prefix, object_manifest, produced_by_run from dataset_version where id = %s", (version_id,))
    if not row or row["tenant_id"] != tenant_id:
        raise NoSuchVersion(version_id)
    return row


def basis_of(person_id: str, tenant_id: str, version: dict) -> str:
    """Why this person may open this version's files, from facts the platform holds. See the module note."""
    version_id = str(version["id"])
    if development_outputs.has_ended(version_id):
        return "expired"
    if development_samples.current_sample(tenant_id, person_id, version_id):
        return "own_sample"
    if development_samples.synthetic_of(version_id):
        return "synthetic"
    if version["produced_by_run"]:
        run = db.one("select pr.source_version_id, pr.triggered_by from action_run ar join pipeline_run pr on pr.id = ar.pipeline_run_id "
                     "where ar.id = %s and ar.output_version = %s", (version["produced_by_run"], version_id))
        if run and run["triggered_by"] == person_id and development_samples.is_development_data(tenant_id, person_id, str(run["source_version_id"])):
            return "own_development_output"
    return "none"


def decision(person: dict, tenant_id: str, version: dict) -> tuple[bool, list[str], str]:
    basis = basis_of(person["id"], tenant_id, version)
    allowed, reasons = opa.may_open_record({"person": {"id": person["id"], "roles": person["roles"]}, "read": {"basis": basis}})
    return bool(allowed), reasons, basis


def _files(version: dict) -> list[dict]:
    """What the version holds that a person can open: each file's name, size, whether it is locked, whose it is, and where it is stored."""
    manifest = {m["key"].rsplit("/", 1)[-1]: m for m in version["object_manifest"]}
    entry = next((m for m in version["object_manifest"] if m["key"].endswith("/records.json")), None)
    if entry is None:
        return []
    client, bucket = recordings._storage({"backend": version["storage_backend"], "tenant": version["tenant_id"]})
    index = json.loads(client.get_object(Bucket=bucket, Key=entry["key"])["Body"].read())
    locked = {r["record_id"]: r for r in db.all_rows(
        "select r.record_id, r.person_code, r.plain_bytes, p.destroyed_at is not null as erased from recording_person r "
        "left join subject_key p on p.tenant_id = r.tenant_id and p.person_code = r.person_code where r.dataset_version_id = %s", (str(version["id"]),))}
    files = []
    for row in index:
        if "record_id" in row and "audio_key" in row:        # a recording of a version of recordings: stored as <record>.wav, locked as that file name
            record, stored_name, kind = row["record_id"], f"{row['record_id']}.wav", "recording"
        elif {"name", "bytes", "sha256"} <= set(row):      # a file a step wrote
            record, stored_name, kind = row["name"], row["name"], "file"
        else:                                              # the rows of a table: a version of rows holds no files to open
            continue
        found = manifest.get(stored_name)
        if not found:
            continue
        own = locked.get(record)
        files.append({"record_id": record, "kind": kind, "content_type": _content_type(stored_name), "locked": own is not None,
                      "erased": bool(own["erased"]) if own else False, "bytes": int(own["plain_bytes"]) if own else int(row.get("bytes") or found.get("bytes") or 0),
                      "key": found["key"], "name": stored_name, "person": own["person_code"] if own else None})
    return files


def _public(f: dict) -> dict:
    return {k: f[k] for k in ("record_id", "kind", "content_type", "locked", "erased", "bytes")}


def list_records(identity: dict, version_id: str) -> dict:
    """The files of a version, whether this person may open them and why not, without opening anything. Nothing is written: listing is not reading."""
    version = _version(version_id, identity["tenant_id"])
    allowed, reasons, basis = decision(identity, identity["tenant_id"], version)
    life = development_outputs.lifecycle_of(version_id)
    files = [] if basis == "expired" else _files(version)
    return {"version_id": version_id, "basis": basis, "may_open": allowed, "reasons": [] if allowed else reasons, "total": len(files),
            "records": [_public(f) for f in files[:LIST_LIMIT]], "development": life}


def open_record(service, identity: dict, version_id: str, record_id: str) -> dict:
    """Open one file: decide, unlock its key (one line in the key log for a locked file), write the opening to the log, and hand back a handle."""
    version = _version(version_id, identity["tenant_id"])
    allowed, reasons, basis = decision(identity, identity["tenant_id"], version)
    if not allowed:
        db.execute("""insert into access_decision (principal, principal_kind, principal_roles, tenant_id, purpose, allowed, reasons, phase, dataset_version_id)
                      values (%s, %s, %s, %s, 'open a record', false, %s, 'policy', %s)""",
                   (identity["id"], identity.get("kind", "human"), identity["roles"], identity["tenant_id"], reasons, version_id))
        raise NotPermitted(reasons)
    found = next((f for f in _files(version) if f["record_id"] == record_id), None)
    if found is None:
        raise NoSuchRecord(record_id)
    if found["locked"]:
        if found["erased"]:
            raise recordings.PatientErased("the patient this file is about was erased, so it cannot be opened")
        try:
            service.unwrap(version["tenant_id"], found["person"], f"person {identity['id']}", f"open record {record_id} of version {version_id} ({basis})", audit=True)
        except DestroyedKeyError as exc:
            raise recordings.PatientErased("the patient this file is about was erased, so it cannot be opened") from exc
    opening_id = str(uuid.uuid4())
    db.execute("""insert into record_read_log (id, tenant_id, principal, version_id, record_id, basis, bytes_total, locked)
                  values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
               (opening_id, identity["tenant_id"], identity["id"], version_id, record_id, basis, found["bytes"], found["locked"]))
    handle = task_credential.mint(principal=identity["id"], task_kind="person_read", task_id=opening_id, tenant_id=identity["tenant_id"],
                                  ttl_seconds=HANDLE_SECONDS, scope=f"{version_id}|{record_id}")
    return {"record_id": record_id, "bytes": found["bytes"], "content_type": found["content_type"], "locked": found["locked"], "handle": handle, "expires_in": HANDLE_SECONDS}


def check_handle(token: str) -> task_credential.TaskClaim:
    try:
        claim = task_credential.verify(token)
    except task_credential.InvalidTaskCredential as exc:
        raise HandleRefused(401, [f"this handle is not good: {exc}. Open the file again"]) from exc
    if claim.task_kind != "person_read" or not claim.scope or "|" not in claim.scope:
        raise HandleRefused(401, ["this is not the handle to an opened file"])
    return claim


_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _span(header: str | None, total: int) -> tuple[int, int, bool]:
    """(first byte, last byte, whether it was asked for as a range). Raises HandleRefused(416) for a range the file cannot satisfy."""
    if not header or "," in header:
        return 0, total - 1, False
    m = _RANGE.match(header.strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return 0, total - 1, False
    if m.group(1) == "":                              # the last n bytes
        n = int(m.group(2))
        if n == 0 or total == 0:
            raise HandleRefused(416, [f"the file is {total} bytes"])
        return max(0, total - n), total - 1, True
    first = int(m.group(1))
    last = int(m.group(2)) if m.group(2) else total - 1
    if first >= total or last < first:
        raise HandleRefused(416, [f"the file is {total} bytes"])
    return first, min(last, total - 1), True


def serve(service, token: str, range_header: str | None) -> tuple[int, dict, Iterator[bytes]]:
    """What a stream request gets: a status, headers and the bytes. Everything is asked again, because the handle only says who asked and for what."""
    claim = check_handle(token)
    version_id, record_id = claim.scope.split("|", 1)
    person = auth.identity_for(claim.principal)
    if not person or person.get("ended_at"):
        raise HandleRefused(403, ["the person this handle was made for is no longer in post"])
    if auth.closed_refusal(person.get("phase"), person["roles"]):
        raise HandleRefused(403, [auth.closed_refusal(person.get("phase"), person["roles"])])
    if person["tenant_id"] != claim.tenant_id:
        raise HandleRefused(403, ["this handle is for another organisation"])
    version = _version(version_id, claim.tenant_id)
    allowed, reasons, _basis = decision(person, claim.tenant_id, version)
    if not allowed:
        raise HandleRefused(403, reasons)
    found = next((f for f in _files(version) if f["record_id"] == record_id), None)
    if found is None:
        raise HandleRefused(404, ["no such file in this version"])
    key = None
    if found["locked"]:
        try:
            key = service.unwrap(version["tenant_id"], found["person"], f"person {person['id']}", f"read a range of record {record_id}", audit=False)
        except DestroyedKeyError as exc:
            raise HandleRefused(410, ["the patient this file is about was erased, so it cannot be read"]) from exc
    total = found["bytes"]
    first, last, ranged = _span(range_header, total) if total else (0, -1, False)
    client, bucket = recordings._storage({"backend": version["storage_backend"], "tenant": version["tenant_id"]})

    def fetch(at: int, size: int) -> bytes:
        return client.get_object(Bucket=bucket, Key=found["key"], Range=f"bytes={at}-{at + size - 1}")["Body"].read()

    def body() -> Iterator[bytes]:
        sent, at = 0, first
        try:
            while at <= last:
                size = min(WINDOW, last - at + 1)
                piece = (read_range(key, version["tenant_id"], found["person"], found["name"], total, fetch, at, size) if key else fetch(at, size))
                sent += len(piece)
                at += size
                yield piece
        except NotOpenable:
            return
        finally:
            if sent:
                db.execute("update record_read_log set bytes_served = bytes_served + %s where id = %s returning id", (sent, claim.task_id))

    headers = {"Content-Type": found["content_type"], "Accept-Ranges": "bytes", "Cache-Control": "no-store", "Content-Length": str(max(0, last - first + 1)),
               "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"}
    if ranged:
        headers["Content-Range"] = f"bytes {first}-{last}/{total}"
    return (206 if ranged else 200), headers, body()
