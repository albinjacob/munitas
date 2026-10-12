"""The evidence for a release decision, and what has to be true before a result is released.

A step that writes files makes a version "under review". Somebody who holds the deciding role releases it to a wider audience or holds it back. This
module is what makes that decision informed instead of blind:

  * Checks run on every file of the version, by the platform, before anyone looks: the file is what its name says it is, it is not empty or far
    larger than what it was made from, and it is not a byte-for-byte copy of the original file it was made from (a step that only copied its
    input has made nothing, and releasing it would release the original). A failed check of the first or third kind stops a release.
  * The reviewer may open a bounded number of the files (the door's `review` basis) and what they opened is recorded with the decision.
  * A result that nothing measured (or that failed its measurement) cannot be released by somebody who looked at none of it.

The decision stores what it relied on: which checks, which files were opened, and by whom. Nothing here widens who can read the files afterwards:
a released version's files stay locked under their person's key, and who may open them is a separate question (`record_door`).
"""

from __future__ import annotations

import hashlib
import json
from typing import Callable

from crypto import DestroyedKeyError, NotOpenable, UnknownRecordError, read_range

from . import db, locked_files, logs, record_door, review_access

COPY_CHECK_MAX = 256 * 1024 * 1024       # larger files are listed as not compared, never silently passed
STEP = 1024 * 1024


class Evidence(dict):
    pass


def _reader(service, version: dict, found: dict, why: str) -> Callable[[int, int], bytes]:
    """A way to read plain bytes of one file inside the platform, for a check. One line goes in the key log for a locked file."""
    client, bucket = locked_files._storage({"backend": version["storage_backend"], "tenant": version["tenant_id"]})

    def fetch(at: int, size: int) -> bytes:
        return client.get_object(Bucket=bucket, Key=found["key"], Range=f"bytes={at}-{at + size - 1}")["Body"].read()

    key = None
    if found["locked"]:
        key = service.unwrap(version["tenant_id"], found["person"], "platform release check", why, audit=True)

    def read(at: int, size: int) -> bytes:
        size = max(0, min(size, found["bytes"] - at))
        if size == 0:
            return b""
        return read_range(key, version["tenant_id"], found["person"], found["name"], found["bytes"], fetch, at, size) if key else fetch(at, size)

    return read


def _sha256(read: Callable[[int, int], bytes], total: int) -> str:
    digest, at = hashlib.sha256(), 0
    while at < total:
        piece = read(at, STEP)
        digest.update(piece)
        at += len(piece) or total
    return digest.hexdigest()


def _kind_check(name: str, read: Callable[[int, int], bytes], total: int) -> dict:
    lower = name.lower()
    if lower.endswith(".wav"):
        head = read(0, 12)
        ok = head[:4] == b"RIFF" and head[8:12] == b"WAVE"
        return {"check": "type", "ok": ok, "blocking": True, "detail": "is a sound file, as its name says" if ok else "is named as a sound file but is not one"}
    if lower.endswith(".json"):
        if total > 16 * 1024 * 1024:
            return {"check": "type", "ok": True, "blocking": False, "detail": "is too large to read as JSON here, so its contents were not checked"}
        try:
            json.loads(read(0, total).decode("utf-8"))
            return {"check": "type", "ok": True, "blocking": True, "detail": "is valid JSON, as its name says"}
        except Exception:  # noqa: BLE001 - any failure to read it as JSON is the answer
            return {"check": "type", "ok": False, "blocking": True, "detail": "is named as JSON but is not valid JSON"}
    return {"check": "type", "ok": True, "blocking": False, "detail": "is a kind of file the platform has no content check for"}


def _source_files(source_version_id: str, tenant_id: str) -> tuple[dict | None, dict]:
    """The files the run was given, to compare what it made with. Empty when they are no longer there (a version whose files were deleted, as a
    development sample's are when it ends): the comparison is then said not to have been made, and the page still opens."""
    if not source_version_id:
        return None, {}
    try:
        version = record_door._version(source_version_id, tenant_id)
        return version, {f["record_id"]: f for f in record_door._files(version)}
    except Exception:  # noqa: BLE001 - a missing original must not stop the evidence about what was made
        return None, {}


def evidence(service, identity: dict, decision: dict) -> Evidence:
    """The checks on every file of the version a decision is about, and what the caller has opened so far."""
    tenant_id, version_id = identity["tenant_id"], str(decision["dataset_version_id"])
    version = record_door._version(version_id, tenant_id)
    run = db.one("select source_version_id from pipeline_run where id = %s", (decision["pipeline_run_id"],)) if decision["pipeline_run_id"] else None
    source_version, sources = _source_files(str(run["source_version_id"]) if run and run["source_version_id"] else "", tenant_id)
    made_from = {r["name"]: r["source_record"] for r in db.all_rows(
        "select name, source_record from step_output where version_id = %s and source_record is not null", (version_id,))}
    files = []
    for f in record_door._files(version):
        checks = []
        try:
            read = _reader(service, version, f, f"release check of file {f['record_id']} for decision {decision['id']}")
            checks.append(_kind_check(f["name"], read, f["bytes"]))
            source = sources.get(made_from.get(f["record_id"]))
            if f["bytes"] == 0:
                checks.append({"check": "size", "ok": False, "blocking": True, "detail": "is empty"})
            elif source and f["bytes"] > 2 * max(1, source["bytes"]):
                checks.append({"check": "size", "ok": False, "blocking": False,
                               "detail": f"is more than twice the size of the file it was made from ({f['bytes']} bytes against {source['bytes']})"})
            else:
                checks.append({"check": "size", "ok": True, "blocking": False, "detail": "has a size that fits the file it was made from" if source else "is not empty"})
            if source and source_version is not None:
                if max(f["bytes"], source["bytes"]) > COPY_CHECK_MAX:
                    checks.append({"check": "copy", "ok": True, "blocking": False, "detail": "is too large to compare with its original here, so that was not checked"})
                elif f["bytes"] == source["bytes"] and _sha256(read, f["bytes"]) == _sha256(
                        _reader(service, source_version, source, f"release check of file {f['record_id']} against its original, for decision {decision['id']}"), source["bytes"]):
                    checks.append({"check": "copy", "ok": False, "blocking": True,
                                   "detail": "is identical to the original file it was made from, so nothing was made, and releasing it would release the original"})
                else:
                    checks.append({"check": "copy", "ok": True, "blocking": True, "detail": "is not a copy of the original file it was made from"})
            elif made_from.get(f["record_id"]):
                checks.append({"check": "copy", "ok": True, "blocking": False, "detail": "could not be compared with its original, because the original is no longer there"})
            else:
                checks.append({"check": "copy", "ok": True, "blocking": False, "detail": "has no original file named for it"})
        except (DestroyedKeyError, NotOpenable, locked_files.PersonErased):
            checks = [{"check": "readable", "ok": False, "blocking": False, "detail": "is about a person who was erased, so it cannot be checked"}]
        except UnknownRecordError:
            checks = [{"check": "readable", "ok": False, "blocking": False, "detail": "cannot be checked, because no key is held for the person it is about"}]
        except Exception:  # noqa: BLE001 - one file that cannot be read is reported as that, not as an error in the whole page
            logs.get_logger("release_review").exception("a file could not be checked", extra={"dataset_version_id": version_id})
            checks = [{"check": "readable", "ok": False, "blocking": False, "detail": "could not be read to check it"}]
        files.append({"record_id": f["record_id"], "bytes": f["bytes"], "locked": f["locked"], "content_type": f["content_type"], "checks": checks})
    failed = [(f["record_id"], c) for f in files for c in f["checks"] if not c["ok"]]
    return Evidence(version_id=version_id, files=files, failed=len(failed),
                    blocking=[f"The file {record} {c['detail']}." for record, c in failed if c["blocking"]],
                    opened=review_access.opened_by(identity["id"], version_id), limit=review_access.limit())


def blockers(found: Evidence, decision: dict, recommendation: str | None = None) -> list[str]:
    """What stops this caller releasing the result now, in words, from the same evidence the page shows. Empty when nothing does.

    `recommendation` is the one under the de-identification standard the person chose; without it, the recommendation the run recorded.
    """
    recommendation = recommendation or decision["recommendation"]
    reasons = list(found["blocking"])
    if found["files"] and recommendation != "pass" and not found["opened"]:
        what = "No automatic measurement was made of this result" if recommendation == "not_measured" else "Its automatic measurement did not pass"
        reasons.append(f"{what}, so you must open at least one of its files before you can release it.")
    return reasons


def record(decision_id: str, found: Evidence, outcome: str) -> None:
    """Keep with the decision what it relied on: the checks that ran and the files the person opened."""
    summary = {"outcome": outcome, "files": len(found["files"]), "checks_failed": found["failed"], "opened": found["opened"], "limit": found["limit"],
               "failed": [{"record_id": f["record_id"], "check": c["check"], "detail": c["detail"]} for f in found["files"] for c in f["checks"] if not c["ok"]]}
    db.execute("update gate_decision set handoff = coalesce(handoff, '{}'::jsonb) || jsonb_build_object('review', %s::jsonb) where id = %s returning id",
               (json.dumps(summary), decision_id))
