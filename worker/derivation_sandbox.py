"""Running one derivation: copy the inputs in, run the query with no network,
upload the result.

Three containers from one image (config.DERIVE_IMAGE), each doing one thing:

  * a fetch container copies each input's table files from storage, using a read
    key the platform minted for exactly that input version
  * the query container runs the SQL with no network, no credential, a read-only
    root and no capabilities, over the copied files
  * an upload container writes the result under the version's own prefix, using
    a write key the platform minted for exactly that prefix

The worker itself holds each key only long enough to hand it to a container.
Nothing here decides who may read what: every key comes from the same
POST /credentials and POST /write-credentials every other workload uses, as
the run's own registered pipeline workload, so a version that is not one of the
run's declared inputs cannot be read.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

import docker
import httpx

from . import config
from .sandbox_run import (SANDBOX_UID, AwaitingActivation, _last_json_object,
                          _run_and_wait)

log = logging.getLogger("munitas.worker.derivation")

HERE = Path(__file__).parent
RUNNER = HERE / "derive" / "runner.py"
FETCH = HERE / "sandbox_stage_fetch.py"
PUT = HERE / "sandbox_stage_put.py"
STAGE_TIMEOUT_SECONDS = 180
RUN_TIMEOUT_SECONDS = 900
# The staging limit the sandbox already applies, which the API checks first.
MAX_STAGED_BYTES = 1024 * 1024 * 1024


class DerivationFailed(Exception):
    """A failure that retrying cannot fix, with a reason safe to show the person."""


def _headers() -> dict:
    return {"x-worker-token": config.WORKER_TOKEN}


def _get(path: str) -> dict:
    r = httpx.get(f"{config.API}{path}", headers=_headers(), timeout=30.0, verify=config.api_verify())
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict, *, worker: bool = False) -> httpx.Response:
    return httpx.post(f"{config.API}{path}", json=body, timeout=30.0, verify=config.api_verify(),
                      headers=_headers() if worker else None)


def _key_for(job: dict, token: str, version_id: str) -> dict:
    r = _post("/credentials", {
        "principal": job["workload"], "principal_kind": "workload", "roles": ["pipeline_action"],
        "tenant_id": job["tenant_id"], "dataset_version_id": version_id,
        "purpose": job["purpose"], "task_credential": token})
    if r.status_code == 202:
        raise AwaitingActivation(f"access to {version_id} is allowed and waiting to take effect")
    if r.status_code != 200:
        raise DerivationFailed(f"the platform refused a read key for an input (HTTP {r.status_code})")
    return r.json()


def _s3_env(grant: dict) -> dict:
    return {"MUNITAS_S3_ENDPOINT": grant["endpoint"], "MUNITAS_S3_ACCESS_KEY": grant["access_key"],
            "MUNITAS_S3_SECRET_KEY": grant["secret_key"], "MUNITAS_S3_BUCKET": grant["bucket"]}


def run(params: dict) -> dict:
    derivation_id = params["derivation_id"]
    job = _get(f"/derivations/{derivation_id}/job")
    if not job.get("workload"):
        raise DerivationFailed("this organisation has no registered pipeline workload to run it as")
    client = docker.from_env()
    try:
        image = client.images.get(config.DERIVE_IMAGE)
    except docker.errors.ImageNotFound as exc:
        raise RuntimeError(f"the query image {config.DERIVE_IMAGE} is not built on this machine") from exc

    runner_bytes = RUNNER.read_bytes()
    started = _post("/action-runs", {
        "tenant_id": job["tenant_id"], "action_id": job["action_id"],
        "code_hash": hashlib.sha256(runner_bytes + b"\0" + job["sql"].encode("utf-8")).hexdigest(),
        "image_digest": image.id, "operator": job["workload"],
        "idempotency_key": f"derivation-{derivation_id}",
        "input_versions": [i["version_id"] for i in job["inputs"]],
        "params": {"derivation_id": derivation_id, "runner": job["runner_version"]},
        "trigger_kind": "manual", "triggered_by": job["submitted_by"],
    })
    if started.status_code >= 400:
        raise DerivationFailed(f"the run could not be recorded (HTTP {started.status_code})")
    action_run_id, token = started.json()["id"], started.json()["task_credential"]
    _post(f"/derivations/{derivation_id}/running", {"action_run_id": action_run_id}, worker=True).raise_for_status()

    run_dir = config.WORK / "derivations" / derivation_id
    shutil.rmtree(run_dir, ignore_errors=True)
    data_dir, out_dir, job_dir = run_dir / "data", run_dir / "out", run_dir / "job"
    for d in (data_dir, out_dir, job_dir):
        d.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o777)  # the query runs as nobody and writes its result here
    try:
        total = 0
        for item in job["inputs"]:
            strip = item["storage_prefix"] + "/"
            entries = [{"key": e["key"], "relative_path": e["key"][len(strip):],
                        "bytes": e.get("bytes"), "sha256": e.get("sha256")}
                       for e in item["object_manifest"]
                       if "/iceberg/data/" in e["key"] and e["key"].endswith(".parquet")]
            if not entries:
                raise DerivationFailed(f"input {item['alias']} has no table files to copy")
            total += sum(int(e.get("bytes") or 0) for e in entries)
            if total > MAX_STAGED_BYTES:
                raise DerivationFailed("the inputs are larger than a query that copies them in can take")
            target = data_dir / item["alias"]
            target.mkdir(parents=True, exist_ok=True)
            (run_dir / f"{item['alias']}.manifest.json").write_text(json.dumps({"objects": entries}))
            grant = _key_for(job, token, item["version_id"])
            code, logs, timed_out = _run_and_wait(
                client, image=config.DERIVE_IMAGE, command=["python", "/stage/fetch.py"],
                environment=_s3_env(grant),
                volumes={str(FETCH): {"bind": "/stage/fetch.py", "mode": "ro"},
                         str(run_dir / f"{item['alias']}.manifest.json"): {"bind": "/stage/manifest.json", "mode": "ro"},
                         str(target): {"bind": "/out", "mode": "rw"}},
                network=config.DATA_NETWORK, timeout=STAGE_TIMEOUT_SECONDS,
                label="derivation staging", run_id=derivation_id)
            if timed_out or code != 0:
                raise RuntimeError(f"copying input {item['alias']} failed (exit={code}, timed_out={timed_out})")

        (job_dir / "job.json").write_text(json.dumps({
            "sql": job["sql"], "primary_key": job["primary_key"],
            "inputs": [{"alias": i["alias"]} for i in job["inputs"]],
            "fields": [{"name": f["name"], "type": f["type"]} for f in job["fields"]]}))
        code, logs, timed_out = _run_and_wait(
            client, image=config.DERIVE_IMAGE, command=["python", "/app/runner.py"],
            environment={"HOME": "/tmp"},
            volumes={str(data_dir): {"bind": "/data", "mode": "ro"},
                     str(out_dir): {"bind": "/out", "mode": "rw"},
                     str(job_dir / "job.json"): {"bind": "/job/job.json", "mode": "ro"},
                     str(RUNNER): {"bind": "/app/runner.py", "mode": "ro"}},
            network="none", timeout=RUN_TIMEOUT_SECONDS, label="derivation query", run_id=derivation_id,
            user=SANDBOX_UID, cap_drop=["ALL"], security_opt=["no-new-privileges"],
            read_only=True, tmpfs={"/tmp": "size=128m"})
        if timed_out:
            raise DerivationFailed(f"the query ran longer than {RUN_TIMEOUT_SECONDS} seconds and was stopped")
        outcome = _last_json_object(logs.decode("utf-8", errors="replace")) or {}
        if code == 137:
            raise DerivationFailed("the query ran out of memory")
        if outcome.get("status") != "ok":
            raise DerivationFailed(outcome.get("reason") or f"the query container stopped without a result (exit={code})")

        records = out_dir / "records.ndjson"
        if records.stat().st_size != outcome["bytes"]:
            raise RuntimeError("the result on disk is not the size the query reported")
        digest = hashlib.sha256()
        with records.open("rb") as handle:  # in pieces: a result may be hundreds of megabytes
            for piece in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(piece)
        if digest.hexdigest() != outcome["sha256"]:
            raise RuntimeError("the result on disk is not the one the query reported")

        w = _post("/write-credentials", {
            "principal": job["workload"], "principal_kind": "workload", "roles": ["pipeline_action"],
            "tenant_id": job["tenant_id"], "dataset_id": job["dataset_id"],
            "purpose": job["purpose"], "task_credential": token})
        if w.status_code == 202:
            raise AwaitingActivation("the write key is allowed and waiting to take effect")
        if w.status_code != 200:
            raise DerivationFailed(f"the platform refused a write key (HTTP {w.status_code})")
        grant = w.json()
        records_key = f"{grant['prefix']}/records.ndjson"
        code, logs, timed_out = _run_and_wait(
            client, image=config.DERIVE_IMAGE, command=["python", "/stage/put.py"],
            environment={**_s3_env(grant), "MUNITAS_PUT_KEY": records_key, "MUNITAS_PUT_FILE": "records.ndjson"},
            volumes={str(PUT): {"bind": "/stage/put.py", "mode": "ro"},
                     str(out_dir): {"bind": "/out", "mode": "ro"}},
            network=config.DATA_NETWORK, timeout=STAGE_TIMEOUT_SECONDS,
            label="derivation upload", run_id=derivation_id)
        if timed_out or code != 0:
            raise RuntimeError(f"uploading the result failed (exit={code}, timed_out={timed_out})")
        log.info("derivation ran", extra={"derivation_id": derivation_id, "rows": outcome["rows"]})
        return {"action_run_id": action_run_id, "records_key": records_key, "rows": outcome["rows"],
                "bytes": outcome["bytes"], "sha256": outcome["sha256"],
                "dataset_id": job["dataset_id"], "schema_id": job["schema_id"],
                "output_class": job["output_class"], "tenant_id": job["tenant_id"]}
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
