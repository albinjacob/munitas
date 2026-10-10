"""Bringing in a table in parts: a person sends a large file through the platform, and when they finish a worker writes the table.

WHY THE API TAKES THE PARTS

Storage is on the platform's own network and nothing outside it can reach it, and a key made for each upload would mean printing the storage
permissions every time. So the file comes through the API in parts of 8 MB, as a locked recording does (locked_files.py): each part is held in memory
while it is stored, and none is ever written to the API's own disk. A part can be sent again, and an upload cut off carries on from where it stopped.

THE UPLOAD IS A TABLE JOB THAT IS WAITING FOR ITS FILE

Opening an upload makes the job that will write the table (table_jobs.py), reserves the next version number and its folder, and starts the storage's own
multipart upload into `<folder>/records.<ending>`. The job is `ready = false`: no worker is given it and no storage key is made for it. Finishing completes
the storage upload, checks the size, and sets `ready = true`, and from there it is any other table job: the dispatcher hands it to a worker, which reads
the file where it lies and writes the table beside it, and the platform finishes the seal. Reserving the number when the upload opens means a long
upload is never refused at the end because somebody else sealed a version meanwhile; an upload nobody finishes gives its number back after a day.

WHAT KEEPS A FEW PEOPLE FROM SLOWING EVERYONE ELSE DOWN

Past a limit a request is turned away at once with the seconds to wait (429), never queued, because a queue of waiting uploads holds their memory too.
Per person: uploads open at once, uploads started in an hour, tables refused or failed in an hour. Per upload: parts being stored at once. For the whole
platform: parts being stored at once (gate.py), which is what holds the API's memory. Per organisation (access rules): the largest file, tables written at
once, tables allowed to wait, bytes kept while they wait, bytes brought in a day. The counts come from the register, so they are exact and hold with
several API replicas; only the count of parts in flight is per process, and it is the memory of this process that it protects.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone

import os

import tablewriter.binaryrows as binaryrows
import tablewriter.textrows as textrows

from . import access_rules, config, db, gate, person_keys, privacy_contract, storage, table_files, table_jobs, versions
from .person_errors import Refused

PART_BYTES = 8 * 1024 * 1024
MAX_PARTS = 10_000          # what the storage's multipart upload allows
GB = 1024 ** 3
ENDINGS = (".csv, .tsv, .json (one list of rows) or .jsonl (one row per line), each with .gz or .zst after it for a compressed file, or a binary table file: "
           ".parquet, .avro, .orc, .arrow, .feather or .xlsx")

_gate = gate.Gate(config.TABLE_UPLOAD_REQUESTS_IN_FLIGHT, config.TABLE_UPLOAD_PARTS_IN_FLIGHT)


class NoSuchUpload(Exception):
    pass


class TooBusy(Exception):
    """A limit was reached. `reasons` say which, in words, and `retry_after` is how many seconds to wait."""

    def __init__(self, reasons: list[str], retry_after: int):
        super().__init__("; ".join(reasons))
        self.reasons, self.retry_after = reasons, retry_after


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _amount(n: int) -> str:
    """A size for a sentence: gigabytes when it is a gigabyte or near, and megabytes or less when the limit has been set small."""
    if n >= GB // 10:
        return f"{round(n / GB, 1):g} GB"
    if n >= 1024 * 1024:
        return f"{round(n / (1024 * 1024), 1):g} MB"
    return f"{max(n, 0) // 1024} KB"


def limits(tenant_id: str) -> dict:
    """This organisation's own limits, as numbers in the units they are used in (bytes, tables)."""
    rules = access_rules.in_force(tenant_id)
    value = lambda key: rules[f"table_uploads.{key}"]["value"]  # noqa: E731
    return {"largest_file": value("largest_file_gb") * GB, "jobs_at_once": value("jobs_at_once"), "jobs_waiting": value("jobs_waiting"),
            "staged": value("staged_gb") * GB, "per_day": value("per_day_gb") * GB}


def _count(sql: str, args: tuple) -> int:
    return int(db.one(sql, args)["n"] or 0)


def _check_person(identity: dict) -> None:
    person = identity["id"]
    if _count("select count(*) as n from table_job where created_by = %s and upload is not null and not ready and status = 'pending'", (person,)) \
            >= config.TABLE_UPLOAD_OPEN_PER_PERSON:
        raise TooBusy([f"you already have {config.TABLE_UPLOAD_OPEN_PER_PERSON} uploads open, which is the most one person may have at once. "
                       "Finish or cancel one, then start this one"], 30)
    if _count("select count(*) as n from table_job where created_by = %s and upload is not null and created_at > now() - interval '1 hour'", (person,)) \
            >= config.TABLE_UPLOAD_PER_HOUR:
        raise TooBusy([f"you have started {config.TABLE_UPLOAD_PER_HOUR} uploads in the last hour, which is the most one person may. Try again in a while"], 600)
    if _count("select count(*) as n from table_job where created_by = %s and status in ('refused', 'expired') and outcome is distinct from 'cancelled' "
              "and finished_at > now() - interval '1 hour'", (person,)) >= config.TABLE_UPLOAD_REFUSED_PER_HOUR:
        raise TooBusy([f"{config.TABLE_UPLOAD_REFUSED_PER_HOUR} of your tables were refused or failed in the last hour. Look at what was said about them, "
                       "correct the files, then try again"], 600)


def _check_organisation(tenant_id: str, size: int, *, opening: bool) -> None:
    own = limits(tenant_id)
    if opening:
        if size > own["largest_file"]:
            raise Refused([f"the file is {_amount(size)}, and the most one upload may hold here is {_amount(own['largest_file'])}"])
        staged = _count("select coalesce(sum((upload->>'size_bytes')::bigint), 0) as n from table_job where tenant_id = %s and upload is not null "
                        "and status in ('pending', 'running')", (tenant_id,))
        if staged + size > own["staged"]:
            raise TooBusy([f"this organisation already has {_amount(staged)} waiting to be written, and may keep {_amount(own['staged'])} waiting. "
                           "Try again when some of it has been written"], 300)
        today = _count("select coalesce(sum((upload->>'size_bytes')::bigint), 0) as n from table_job where tenant_id = %s and upload is not null "
                       "and created_at > now() - interval '24 hours' and outcome is distinct from 'cancelled'", (tenant_id,))
        if today + size > own["per_day"]:
            raise TooBusy([f"this organisation has brought in {_amount(today)} in the last day, and may bring in {_amount(own['per_day'])} a day. "
                           "Try again later"], 3600)
    else:
        waiting = _count("select count(*) as n from table_job where tenant_id = %s and status = 'pending' and ready", (tenant_id,))
        if waiting >= own["jobs_waiting"]:
            raise TooBusy([f"{waiting} tables of this organisation are already waiting to be written, which is the most allowed. Try again when one is done"], 120)


def _describe(job: dict) -> dict:
    upload = job["upload"] if isinstance(job["upload"], dict) else json.loads(job["upload"])
    got = [r["part_number"] for r in db.all_rows("select part_number from table_job_part where job_id = %s order by part_number", (str(job["id"]),))]
    parts = math.ceil(upload["size_bytes"] / PART_BYTES)
    shown = table_jobs._shown(job)
    request = job["request"] if isinstance(job["request"], dict) else json.loads(job["request"])
    about_people = bool(request.get("lock"))
    report = db.one("select people, people_in_small_groups, threshold from group_size_report where dataset_version_id = %s",
                    (str(job["version_id"]),)) if about_people and job["status"] == "sealed" and job["version_id"] else None
    shown = {**shown, "about_people": about_people, "people": report["people"] if report else None,
             "people_in_small_groups": report["people_in_small_groups"] if report else None, "group_threshold": report["threshold"] if report else None}
    return {**shown, "file_name": upload["file_name"], "size_bytes": upload["size_bytes"], "part_bytes": PART_BYTES, "parts": parts, "received": got,
            "missing": [n for n in range(1, parts + 1) if n not in set(got)], "open": not job["ready"] and job["status"] == "pending",
            "upload_expires_at": job["expires_at"].isoformat() if not job["ready"] else None}


def _own(identity: dict, job_id: str) -> dict:
    """The upload, only for the person who started it, in their own organisation. Anybody else is told there is no such upload."""
    try:
        uuid.UUID(job_id)
    except ValueError as exc:
        raise NoSuchUpload(job_id) from exc
    job = db.one("select * from table_job where id = %s and upload is not null", (job_id,))
    if not job or str(job["tenant_id"]) != identity["tenant_id"] or job["created_by"] != identity["id"]:
        raise NoSuchUpload(job_id)
    return job


def _open(job: dict) -> None:
    if job["ready"] or job["status"] != "pending":
        raise Refused([f"this upload is {'finished' if job['ready'] else job['status']}, so no more can be sent to it. Start a new upload for the file"])
    if job["expires_at"] < _now():
        raise Refused(["this upload ran out of time, as it was left open too long. Start a new upload for the file"])


def open_upload(*, dataset_id: str, identity: dict, file_name: str, size_bytes: int, contract_text: str, options: dict | None) -> dict:
    dataset = person_keys.intake_dataset(dataset_id, identity)
    doc = table_files.parse_contract((contract_text or "").encode("utf-8"))
    people = not privacy_contract.is_plain(doc)
    if people:
        problems = person_keys._contract_problems(doc) or person_keys.large_people_problems(doc)
        if problems:
            raise Refused(problems)
    found = textrows.format_of(file_name or "")
    binary = binaryrows.format_of(file_name or "") if found is None else None
    if found is None and binary is None:
        raise Refused([f"the file {file_name!r} ends in an ending this route does not read. Use {ENDINGS}"])
    kind, compression = found if found else (binary, None)
    try:
        declared = textrows.Options.of({**(options or {}), **({"compression": compression} if compression else {})}, kind)
    except ValueError as exc:
        raise Refused([str(exc)]) from exc
    if not isinstance(size_bytes, int) or size_bytes < 1:
        raise Refused(["the file is empty"])
    if math.ceil(size_bytes / PART_BYTES) > MAX_PARTS:
        raise Refused([f"the file would need more than {MAX_PARTS:,} parts"])
    tenant = dataset["tenant_id"]
    _check_organisation(tenant, size_bytes, opening=True)
    _check_person(identity)

    backend = dataset["storage_backend"] or "seaweedfs"
    reserved = versions.next_version(tenant, dataset_id)
    prefix = reserved["storage_prefix"]
    ending = os.path.splitext(file_name.lower())[1].lstrip(".") if binary else kind + {"gzip": ".gz", "zstd": ".zst", None: ""}[compression]
    key = f"{prefix}/records.{ending}"
    schema_id = person_keys.people_table_schema_id(tenant, doc) if people else person_keys.plain_schema_id(tenant, doc)
    client, bucket = storage.admin_client_for(backend, tenant), storage.bucket_for(backend, tenant)
    storage_upload = client.create_multipart_upload(Bucket=bucket, Key=key)["UploadId"]
    job_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
    request = {"tenant_id": tenant, "dataset_id": dataset_id, "schema_id": schema_id, "visibility_class": "RAW", "storage_backend": backend,
               "object_manifest": [], "record_count": 0, "records_key": key, "records_options": declared.as_dict(), "table_required": True,
               "table_mode": "background", "from_upload": True, "contract_name": doc["name"]}
    if people:
        # A table about people is locked as it is written: the plan says which columns, and how to find each row's person. No value is in it.
        request["lock"] = person_keys.lock_plan(doc)
    upload = {"file_name": file_name, "size_bytes": size_bytes, "storage_upload": storage_upload}
    try:
        db.execute(
            """insert into table_job (id, tenant_id, dataset_id, version, storage_prefix, storage_backend, request, queue, status, expires_at, version_id,
                                      ready, created_by, upload)
               values (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, false, %s, %s)""",
            (job_id, tenant, dataset_id, reserved["version"], prefix, backend, json.dumps(request), table_jobs.queue_for(tenant),
             _now() + timedelta(hours=config.TABLE_UPLOAD_LIFE_HOURS), version_id, identity["id"], json.dumps(upload)))
    except Exception as exc:  # noqa: BLE001 - the one that matters is the reserved number being taken
        client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=storage_upload)
        if "table_job_reserved" in str(exc):
            raise TooBusy(["another version of this dataset is being brought in at the same moment. Try again in a moment"], 5) from exc
        raise
    return _describe(db.one("select * from table_job where id = %s", (job_id,)))


def status(identity: dict, job_id: str) -> dict:
    return _describe(_own(identity, job_id))


def receive_part(*, identity: dict, job_id: str, part_number: int, data: bytes) -> dict:
    job = _own(identity, job_id)
    _open(job)
    upload = job["upload"] if isinstance(job["upload"], dict) else json.loads(job["upload"])
    total = upload["size_bytes"]
    parts = math.ceil(total / PART_BYTES)
    if not 1 <= part_number <= parts:
        raise Refused([f"this file has {parts} part(s), so there is no part {part_number}"])
    expected = PART_BYTES if part_number < parts else total - (parts - 1) * PART_BYTES
    if len(data) != expected:
        raise Refused([f"part {part_number} of {upload['file_name']} must be {expected} bytes, and {len(data)} arrived"])
    try:
        with _gate.enter(job_id, "the platform is taking as many parts as it can at the moment, from everybody. Send this part again in a few seconds",
                         f"{config.TABLE_UPLOAD_PARTS_IN_FLIGHT} parts of this upload are already being stored. Send this part again in a moment"):
            backend, tenant = job["storage_backend"], str(job["tenant_id"])
            request = job["request"] if isinstance(job["request"], dict) else json.loads(job["request"])
            etag = storage.admin_client_for(backend, tenant).upload_part(
                Bucket=storage.bucket_for(backend, tenant), Key=request["records_key"], UploadId=upload["storage_upload"], PartNumber=part_number,
                Body=data)["ETag"]
    except gate.Busy as busy:
        raise TooBusy([busy.what], busy.retry_after) from busy
    db.execute("insert into table_job_part (job_id, part_number, etag, bytes) values (%s, %s, %s, %s) "
               "on conflict (job_id, part_number) do update set etag = excluded.etag, bytes = excluded.bytes, received_at = now() returning part_number",
               (job_id, part_number, etag, len(data)))
    shown = _describe(db.one("select * from table_job where id = %s", (job_id,)))
    return {"part_number": part_number, "received": shown["received"], "missing": shown["missing"]}


def finish(*, identity: dict, job_id: str) -> dict:
    job = _own(identity, job_id)
    if job["ready"]:
        return _describe(job)            # finishing twice is the same as finishing once
    _open(job)
    shown = _describe(job)
    if shown["missing"]:
        raise Refused([f"part {n} of {shown['file_name']} has not arrived" for n in shown["missing"][:20]]
                      + ([f"and {len(shown['missing']) - 20} more parts have not arrived"] if len(shown["missing"]) > 20 else []))
    _check_organisation(str(job["tenant_id"]), shown["size_bytes"], opening=False)
    upload = job["upload"] if isinstance(job["upload"], dict) else json.loads(job["upload"])
    request = job["request"] if isinstance(job["request"], dict) else json.loads(job["request"])
    backend, tenant = job["storage_backend"], str(job["tenant_id"])
    client, bucket = storage.admin_client_for(backend, tenant), storage.bucket_for(backend, tenant)
    parts = db.all_rows("select part_number, etag from table_job_part where job_id = %s order by part_number", (job_id,))
    client.complete_multipart_upload(Bucket=bucket, Key=request["records_key"], UploadId=upload["storage_upload"],
                                     MultipartUpload={"Parts": [{"PartNumber": p["part_number"], "ETag": p["etag"]} for p in parts]})
    stored = int(client.head_object(Bucket=bucket, Key=request["records_key"])["ContentLength"])
    if stored != upload["size_bytes"]:
        client.delete_object(Bucket=bucket, Key=request["records_key"])
        table_jobs._end(job_id, "refused", "refused", f"{upload['file_name']} was stored as {stored} bytes and was said to be {upload['size_bytes']}. It was not kept.")
        raise Refused([f"{upload['file_name']} was stored as {stored} bytes, and it was said to be {upload['size_bytes']}. It was not kept. Start a new upload for the file"])
    db.execute("update table_job set ready = true, ready_at = now(), expires_at = now() + make_interval(secs => %s) where id = %s and not ready",
               (config.TABLE_JOB_TTL_SECONDS, job_id))
    return _describe(db.one("select * from table_job where id = %s", (job_id,)))


def cancel(*, identity: dict, job_id: str) -> dict:
    """Stop an upload, or a table that is waiting or being written. What was sent is thrown away and the version number is free again."""
    job = _own(identity, job_id)
    if job["status"] not in table_jobs.ACTIVE:
        return _describe(job)
    table_jobs.discard(job)
    table_jobs._end(job_id, "refused", "cancelled", "This was cancelled by the person who started it, so no version was made.")
    table_jobs._drop_key()
    return _describe(db.one("select * from table_job where id = %s", (job_id,)))
