"""The files a custom pipeline step writes, through the platform and nowhere else.

A step that says `writes_files: true` in its pipeline file cannot reach storage and holds no key. It sends each file to the platform in parts, the
way a recording is uploaded, and the platform stores them under the folder the next version of the output dataset will own. Nothing is a version
yet. When the step ends, the platform's worker says how it ended:

  * if it succeeded, the platform seals every file the step finished, together, as one version of the dataset `<the run's dataset>-<step>`, marked
    "under review" so that nobody can use it until a person has looked, with an index of the files (name, size, fingerprint) and the run that made
    it recorded, so erasing a patient lists it among what was made from their recordings;
  * if it failed, or left a file unfinished, everything it wrote is thrown away, and no version exists.

The platform makes the version, so no key is handed to anything: not to the step, and not to the worker.

Limits, so one step cannot fill storage: at most 500 files, 512 MiB each and 1 GiB together, with names of letters, digits, dots, dashes and
underscores, and none named for the platform's own index.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from . import db, storage, versions

PART = 8 * 1024 * 1024
MAX_FILES = 500
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_PARTS = MAX_FILE_BYTES // PART
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RESERVED = {"records.json"}
UPLOAD_LIFE = timedelta(hours=24)
CONTRACT = "custom_step_files"
FIELDS = [{"name": "name", "type": "string", "sensitivity": "none", "added_by": "platform"},
          {"name": "bytes", "type": "int", "sensitivity": "none", "added_by": "platform"},
          {"name": "sha256", "type": "string", "sensitivity": "none", "added_by": "platform"}]


class Refused(Exception):
    """The platform will not take this, and says why."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _run(pipeline_run_id: str) -> dict:
    run = db.one("""select r.tenant_id, r.dataset, r.source_version_id, r.trigger_kind, r.triggered_by, r.schedule_id, r.pipeline_version_id, pv.config_hash
                      from pipeline_run r left join pipeline_version pv on pv.id = r.pipeline_version_id where r.id = %s""", (pipeline_run_id,))
    if not run:
        raise Refused(["no such pipeline run"])
    return run


def _target(run: dict, step: str) -> dict:
    """The output dataset for a step (made if it does not exist, in the department of the dataset the run reads), where its next version's folder
    is, and which storage it is on."""
    name = f"{run['dataset']}-{step}"
    try:
        dataset_id, _ = versions.ensure_dataset(run["tenant_id"], name, str(run["source_version_id"]))
    except LookupError as exc:
        raise Refused([str(exc)]) from exc
    dataset = db.one("select storage_backend from dataset where id = %s", (dataset_id,))
    backend = dataset["storage_backend"] or "seaweedfs"
    return {"name": name, "dataset_id": dataset_id, "backend": backend, "prefix": versions.next_version(run["tenant_id"], dataset_id)["storage_prefix"],
            "client": storage.admin_client_for(backend, run["tenant_id"]), "bucket": storage.bucket_for(backend, run["tenant_id"])}


def _drop(row: dict, backend_of: dict) -> None:
    """Throw away what an output holds in storage: an upload that is still open, or an object that was finished."""
    try:
        if row["status"] == "open" and row["storage_upload"]:
            backend_of["client"].abort_multipart_upload(Bucket=backend_of["bucket"], Key=row["storage_key"], UploadId=row["storage_upload"])
        elif row["status"] in ("complete",):
            backend_of["client"].delete_object(Bucket=backend_of["bucket"], Key=row["storage_key"])
    except Exception:  # noqa: BLE001 - what is not there cannot be dropped; the row says aborted either way
        pass
    db.execute("update step_output set status = 'aborted', storage_upload = null, finished_at = now() where id = %s returning id", (str(row["id"]),))


def _stale(tenant_id: str) -> None:
    """Uploads that were opened and never finished or finished off are closed when another is opened, so storage does not keep them for ever."""
    cutoff = datetime.now(timezone.utc) - UPLOAD_LIFE
    for row in db.all_rows("select o.*, r.dataset from step_output o join pipeline_run r on r.id = o.pipeline_run_id "
                           "where o.tenant_id = %s and o.status in ('open','complete') and o.created_at < %s", (tenant_id, cutoff)):
        run = _run(str(row["pipeline_run_id"]))
        _drop(row, _target(run, row["step"]))


def open_output(*, tenant_id: str, pipeline_run_id: str, step: str, name: str) -> dict:
    """Open one file to be sent in parts. Opening a name again replaces the earlier file of that name."""
    if not NAME.match(name):
        raise Refused([f"{name!r} is not a name that can be stored: letters, digits, dots, dashes and underscores, at most 128, starting with a letter or digit"])
    if name in RESERVED:
        raise Refused([f"{name} is a name the platform keeps for its own index"])
    _stale(tenant_id)
    run = _run(pipeline_run_id)
    target = _target(run, step)
    live = db.all_rows("select * from step_output where pipeline_run_id = %s and step = %s and status in ('open','complete','sealed')", (pipeline_run_id, step))
    if any(r["status"] == "sealed" for r in live):
        raise Refused(["this step's files were already sealed as a version"])
    for old in [r for r in live if r["name"] == name]:
        _drop(old, target)
    if len([r for r in live if r["name"] != name]) >= MAX_FILES:
        raise Refused([f"a step writes at most {MAX_FILES} files"])
    key = f"{target['prefix']}/{name}"
    upload = target["client"].create_multipart_upload(Bucket=target["bucket"], Key=key)["UploadId"]
    output_id = str(uuid.uuid4())
    db.execute("insert into step_output (id, tenant_id, pipeline_run_id, step, name, storage_key, storage_upload, status) values (%s, %s, %s, %s, %s, %s, %s, 'open') returning id",
               (output_id, tenant_id, pipeline_run_id, step, name, key, upload))
    return {"output_id": output_id, "part_bytes": PART, "max_parts": MAX_PARTS}


def _open_output(pipeline_run_id: str, step: str, name: str) -> dict:
    row = db.one("select * from step_output where pipeline_run_id = %s and step = %s and name = %s and status = 'open'", (pipeline_run_id, step, name))
    if not row:
        raise Refused([f"{name} is not open: open it first, and a finished file cannot be added to"])
    return row


def put_part(*, pipeline_run_id: str, step: str, name: str, part_number: int, data: bytes) -> dict:
    """One part of an open file, at most 8 MiB. Sending a part again replaces it, so a part that failed on a poor connection is simply sent again."""
    row = _open_output(pipeline_run_id, step, name)
    if not 1 <= part_number <= MAX_PARTS:
        raise Refused([f"a file has at most {MAX_PARTS} parts of {PART} bytes ({MAX_FILE_BYTES} bytes)"])
    if len(data) > PART:
        raise Refused([f"a part is at most {PART} bytes"])
    held = db.one("""select coalesce(sum(p.bytes), 0) as n from step_output_part p join step_output o on o.id = p.output_id
                      where o.pipeline_run_id = %s and o.step = %s and o.status in ('open','complete') and not (o.id = %s and p.part_number = %s)""",
                  (pipeline_run_id, step, str(row["id"]), part_number))["n"]
    if held + len(data) > MAX_TOTAL_BYTES:
        raise Refused([f"a step writes at most {MAX_TOTAL_BYTES} bytes in all"])
    run = _run(pipeline_run_id)
    target = _target(run, step)
    etag = target["client"].upload_part(Bucket=target["bucket"], Key=row["storage_key"], UploadId=row["storage_upload"], PartNumber=part_number, Body=data)["ETag"]
    db.execute("insert into step_output_part (output_id, part_number, tenant_id, etag, bytes) values (%s, %s, %s, %s, %s) "
               "on conflict (output_id, part_number) do update set etag = excluded.etag, bytes = excluded.bytes returning part_number",
               (str(row["id"]), part_number, row["tenant_id"], etag, len(data)))
    return {"part_number": part_number, "bytes": len(data)}


def complete_output(*, pipeline_run_id: str, step: str, name: str) -> dict:
    """Finish a file: every part from 1 must be there and every part but the last must be a full 8 MiB. Its size and fingerprint are taken from what was stored."""
    row = _open_output(pipeline_run_id, step, name)
    parts = db.all_rows("select part_number, etag, bytes from step_output_part where output_id = %s order by part_number", (str(row["id"]),))
    problems = []
    if not parts:
        problems.append(f"{name} has no parts: send at least one, which may be empty")
    if [p["part_number"] for p in parts] != list(range(1, len(parts) + 1)):
        problems.append(f"the parts of {name} are not 1 to {len(parts)} with none missing")
    if any(p["bytes"] != PART for p in parts[:-1]):
        problems.append(f"every part of {name} but the last must be exactly {PART} bytes")
    if problems:
        raise Refused(problems)
    run = _run(pipeline_run_id)
    target = _target(run, step)
    if sum(p["bytes"] for p in parts) == 0:
        # An empty file is not a multipart upload: storage does not complete one made of a single empty part, and there is nothing to assemble.
        target["client"].abort_multipart_upload(Bucket=target["bucket"], Key=row["storage_key"], UploadId=row["storage_upload"])
        target["client"].put_object(Bucket=target["bucket"], Key=row["storage_key"], Body=b"")
    else:
        target["client"].complete_multipart_upload(Bucket=target["bucket"], Key=row["storage_key"], UploadId=row["storage_upload"],
                                                   MultipartUpload={"Parts": [{"PartNumber": p["part_number"], "ETag": p["etag"]} for p in parts]})
    digest, size = hashlib.sha256(), 0
    for block in target["client"].get_object(Bucket=target["bucket"], Key=row["storage_key"])["Body"].iter_chunks(chunk_size=PART):
        digest.update(block)
        size += len(block)
    db.execute("update step_output set status = 'complete', storage_upload = null, bytes = %s, sha256 = %s, finished_at = now() where id = %s returning id",
               (size, digest.hexdigest(), str(row["id"])))
    return {"name": name, "bytes": size, "sha256": digest.hexdigest()}


def finish(*, tenant_id: str, pipeline_run_id: str, step: str, succeeded: bool, principal: str) -> dict:
    """The step has ended. Seal what it wrote as one version "under review" if it succeeded, and throw everything away if it did not.

    Answers {"status": "sealed" | "empty" | "discarded" | "refused", ...}. Asked again after it was sealed it answers the same version, so a worker that
    retried after a crash gets the same answer and makes no second version.
    """
    run = _run(pipeline_run_id)
    rows = db.all_rows("select * from step_output where pipeline_run_id = %s and step = %s and status in ('open','complete','sealed') order by name", (pipeline_run_id, step))
    target = _target(run, step) if (rows or succeeded) else None
    if any(r["status"] == "sealed" for r in rows):
        sealed = [r for r in rows if r["status"] == "sealed"]
        return {"status": "sealed", "files_written": len(sealed), "output_version_id": str(sealed[0]["version_id"]), "output_dataset": target["name"]}
    if not rows:
        return {"status": "empty", "files_written": 0}
    unfinished = [r["name"] for r in rows if r["status"] == "open"]
    if not succeeded or unfinished:
        for row in rows:
            _drop(row, target)
        if succeeded:
            return {"status": "refused", "files_written": 0, "reason": f"the step ended with files it did not finish ({', '.join(unfinished[:5])}), so nothing it wrote was kept"}
        return {"status": "discarded", "files_written": 0}
    if any(r["storage_key"].rsplit("/", 1)[0] != target["prefix"] for r in rows):
        for row in rows:
            _drop(row, target)
        return {"status": "refused", "files_written": 0,
                "reason": "another version of the output dataset was sealed while this step was running, so its place has moved. Nothing it wrote was kept: run it again"}

    index = [{"name": r["name"], "bytes": int(r["bytes"]), "sha256": r["sha256"]} for r in rows]
    payload = json.dumps(index).encode("utf-8")
    target["client"].put_object(Bucket=target["bucket"], Key=f"{target['prefix']}/records.json", Body=payload)
    manifest = [{"key": r["storage_key"], "bytes": int(r["bytes"]), "sha256": r["sha256"]} for r in rows]
    manifest.append({"key": f"{target['prefix']}/records.json", "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    contract = versions.register_contract(tenant_id, CONTRACT, FIELDS, ["name"])
    action = db.one("select id from dataset_action where tenant_id = %s and name = 'custom_step'", (tenant_id,))
    if not action:
        db.execute("insert into dataset_action (id, tenant_id, name, output_class) values (%s, %s, 'custom_step', 'UNDER_REVIEW') on conflict (tenant_id, name) do nothing returning id",
                   (str(uuid.uuid4()), tenant_id))
        action = db.one("select id from dataset_action where tenant_id = %s and name = 'custom_step'", (tenant_id,))
    key = f"custom-step-{pipeline_run_id}-{step}"
    existing = db.one("select id from action_run where idempotency_key = %s", (key,))
    run_id = str(existing["id"]) if existing else str(uuid.uuid4())
    if not existing:
        db.execute("""insert into action_run (id, tenant_id, action_id, code_hash, image_digest, params, input_versions, status, operator, idempotency_key,
                                              trigger_kind, triggered_by, schedule_id, pipeline_run_id)
                      values (%s, %s, %s, %s, %s, %s, %s, 'running', %s, %s, %s, %s, %s, %s) returning id""",
                   (run_id, tenant_id, str(action["id"]), run["config_hash"] or "none", "step-container:python:3.11-slim",
                    json.dumps({"step": step, "pipeline_version_id": str(run["pipeline_version_id"])}), [str(run["source_version_id"])], principal, key,
                    run["trigger_kind"] or "manual", run["triggered_by"], run["schedule_id"], pipeline_run_id))
    version = versions.seal(tenant_id=tenant_id, dataset_id=target["dataset_id"], schema_id=contract["id"], visibility_class="UNDER_REVIEW",
                            storage_backend=target["backend"], object_manifest=manifest, record_count=len(index), produced_by_run=run_id,
                            records_key=f"{target['prefix']}/records.json")
    for row in rows:
        db.execute("update step_output set status = 'sealed', version_id = %s where id = %s returning id", (version["id"], str(row["id"])))
    return {"status": "sealed", "files_written": len(index), "output_version_id": version["id"], "output_dataset": target["name"]}
