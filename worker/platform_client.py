"""Talking to the control plane and to object storage.

Every dataset version the pipeline creates goes through the API rather than
straight into PostgreSQL. That is not ceremony: the API is where the policy
decision, the audit row and the contract check happen, and a worker that wrote
to the database directly would bypass all three while still producing something
that looked like a valid lineage record.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import boto3
import httpx
from botocore.config import Config

from . import config
from .contracts import Contract


class ControlPlaneError(Exception):
    pass


def _post(path: str, payload: dict) -> dict:
    response = httpx.post(f"{config.API}{path}", json=payload, timeout=30.0,
                          verify=config.api_verify())
    if response.status_code >= 400:
        raise ControlPlaneError(f"{path} returned {response.status_code}: {response.text[:300]}")
    return response.json()


# The object-storage backends this worker can actually talk to.
#
# One, and `s3()` below is why: it builds a SeaweedFS client from SeaweedFS
# credentials at a SeaweedFS endpoint, with nothing anywhere that would build
# an R2 client instead. A dataset can already be marked `r2`, so this is a real
# gap rather than a hypothetical, and naming it here is what lets the run be
# refused instead of writing an R2-backed version's objects into SeaweedFS
# under the R2 bucket's name and sealing a version that says seaweedfs.
SERVED_BACKENDS = ("seaweedfs",)


def s3():
    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT,
        aws_access_key_id=config.PIPELINE_KEY,
        aws_secret_access_key=config.PIPELINE_SECRET,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


class CredentialPending(Exception):
    """Policy allowed this read, but storage permissions are not live yet.

    Raised as a plain exception, not wrapped `non_retryable`, so Temporal's
    own activity retry policy is what waits this out -- the same contract
    `POST /credentials`'s 202 already establishes for an agent run, applied
    here without building separate parking machinery for a read that has a
    short timeout of its own already (see adopt_version's own activity
    timeout comment in workflows.py).
    """


def s3_scoped(task_credential: str, dataset_version_id: str, tenant_id: str,
              principal: str, purpose: str) -> "boto3.client":
    """A read-only S3 client scoped to exactly one dataset version.

    Requests it through the same `/credentials` every human and agent read
    goes through, presenting `task_credential` as proof this call really is
    the task it claims (an `action_run` or `pipeline_run`; see
    task_credential.py) rather than the worker's static, all-purpose key.
    Read-only and single-version on purpose: nothing here ever writes, so
    nothing here needs the write-wide static key at all.
    """
    response = httpx.post(f"{config.API}/credentials", json={
        "principal": principal, "principal_kind": "workload",
        "roles": [config.PIPELINE_ROLE], "tenant_id": tenant_id,
        "dataset_version_id": dataset_version_id, "purpose": purpose,
        "task_credential": task_credential,
    }, timeout=30.0, verify=config.api_verify())

    if response.status_code == 202:
        raise CredentialPending(
            f"storage permissions for {dataset_version_id} are not active "
            f"yet: {response.json()}"
        )
    if response.status_code >= 400:
        raise ControlPlaneError(
            f"/credentials refused this read: HTTP {response.status_code} "
            f"{response.text[:300]}"
        )

    creds = response.json()
    # config.S3_ENDPOINT, not creds["endpoint"]: the API answers with its own
    # internal view of storage (the Docker-network hostname, "seaweedfs",
    # reachable from inside the containers it runs among), and this worker
    # runs on the host, which reaches the same SeaweedFS through a published
    # port on localhost instead -- config.S3_ENDPOINT already knows which of
    # those it is, the same way s3() above never trusts a caller-supplied
    # endpoint either.
    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT,
        aws_access_key_id=creds["access_key"],
        aws_secret_access_key=creds["secret_key"],
        aws_session_token=creds.get("session_token"),
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def s3_scoped_write(task_credential: str, dataset_id: str, tenant_id: str,
                    principal: str, purpose: str) -> "boto3.client":
    """A write-only S3 client scoped to exactly the next version-location
    for one dataset, minted by proving a real action_run or pipeline_run
    task rather than the worker's static, all-purpose key.

    The write-side counterpart to s3_scoped above. `pipeline_action`'s
    storage identity no longer holds standing Write at all (see
    docs/internal/design/write-credential-rationale.md); every real write this worker
    makes has to go through this first. Unlike s3_scoped, which names the
    exact dataset_version_id being read, this names the *dataset* being
    written into: the version being written does not exist yet, and
    /write-credentials computes its prefix server-side, the same "authority
    from what the platform can verify, never from the caller's own say-so"
    rule s3_scoped's own read-scope check already applies.
    """
    response = httpx.post(f"{config.API}/write-credentials", json={
        "principal": principal, "principal_kind": "workload",
        "roles": [config.PIPELINE_ROLE], "tenant_id": tenant_id,
        "dataset_id": dataset_id, "purpose": purpose,
        "task_credential": task_credential,
    }, timeout=30.0, verify=config.api_verify())

    if response.status_code == 202:
        raise CredentialPending(
            f"storage permissions for writing dataset {dataset_id} are not "
            f"active yet: {response.json()}"
        )
    if response.status_code >= 400:
        raise ControlPlaneError(
            f"/write-credentials refused this write: HTTP {response.status_code} "
            f"{response.text[:300]}"
        )

    creds = response.json()
    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT,
        aws_access_key_id=creds["access_key"],
        aws_secret_access_key=creds["secret_key"],
        aws_session_token=creds.get("session_token"),
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def ensure_bucket(bucket: str) -> None:
    """Create the bucket if it is missing.

    A convenience for the corpus path against a development stack. A real
    tenant's bucket is provisioned by the control plane when the tenant is
    created, not by whatever happens to write first.
    """
    client = s3()
    try:
        client.create_bucket(Bucket=bucket)
    except Exception:
        pass


def register_contract(contract: Contract, tenant_id: str | None = None) -> str:
    return _post(
        "/schema-contracts", contract.as_payload(tenant_id or config.TENANT)
    )["id"]


def ensure_dataset(name: str, tenant_id: str | None = None) -> str:
    return _post(
        "/datasets", {"tenant_id": tenant_id or config.TENANT, "name": name}
    )["id"]


def next_location(dataset_id: str, tenant_id: str | None = None) -> dict:
    """Where the next version's objects belong: which bucket, and which prefix.

    Asked rather than assumed, both halves. The control plane owns the storage
    layout, and a worker that invented either would produce versions whose
    prefix-scoped credentials grant access to nothing, with no error anywhere.

    This used to return only the prefix and leave the bucket to `config.BUCKET`,
    which is right for every tenant grandfathered onto the shared bucket and
    wrong for every tenant with its own. Returning both is what makes the two
    impossible to get out of step.
    """
    response = httpx.get(
        f"{config.API}/datasets/{dataset_id}/next-version",
        params={"tenant_id": tenant_id or config.TENANT}, timeout=15.0,
        verify=config.api_verify(),
    )
    if response.status_code >= 400:
        raise ControlPlaneError(f"next-version returned {response.status_code}")
    body = response.json()
    return {"prefix": body["storage_prefix"], "bucket": body["bucket"],
            "backend": body["storage_backend"],
            # Carried through so a refusal can say why the bucket is missing.
            # Dropping it left the activity's message with an empty reason,
            # which is the kind of refusal that sends somebody looking in the
            # wrong place.
            "bucket_error": body.get("bucket_error")}


def start_run(
    action_id: str, idempotency_key: str, inputs: list[str], params: dict,
    *,
    operator: str = "munitas-worker",
    trigger_kind: str = "manual",
    triggered_by: str | None = None,
    schedule_id: str | None = None,
    pipeline_run_id: str | None = None,
    tenant_id: str | None = None,
) -> dict:
    """Start an action run. Returns `{"id", "task_credential"}`.

    The idempotency key is derived by the caller from the action and its inputs,
    so a workflow replayed by Temporal after a crash resolves to the same run
    rather than starting a second one. This is the half of V6 that slice 1 could
    not test.

    `trigger_kind`/`triggered_by`/`schedule_id` say how the run started: a
    manual run names the human who ran it, a scheduled one names its schedule
    instead. The defaults keep every existing caller working unchanged, but a
    default of `trigger_kind="manual"` with no `triggered_by` will be refused
    by the endpoint, on purpose: an unattributed manual run is exactly the
    audit gap this exists to close, not a case to fall back into quietly.

    Used to return only the id. `/action-runs` has minted a task_credential.py
    token for this run all along (needed for an eventual read through
    s3_scoped, and now also for a write through s3_scoped_write); this hands
    it back to the caller instead of discarding it.

    `operator` used to be cosmetic, a label for "which workload ran this",
    always the literal "munitas-worker" from every real caller. It stopped
    being cosmetic the moment the task_credential above started mattering:
    the token's own claimed principal is exactly this string
    (task_credential.mint's principal=body.operator, in the API), and
    s3_scoped_write checks that claim against the caller's real, registered
    pipeline_action workload identity. Every real caller in
    worker/activities.py now passes operator=_pipeline_principal(tenant) for
    exactly this reason; the literal default stays only for callers (tests,
    scripts) that never present this token to /write-credentials at all.
    """
    result = _post("/action-runs", {
        "tenant_id": tenant_id or config.TENANT,
        "action_id": action_id,
        "code_hash": code_hash(),
        "image_digest": "native:host-venv",
        "operator": operator,
        "idempotency_key": idempotency_key,
        "input_versions": inputs,
        "params": params,
        "trigger_kind": trigger_kind,
        "triggered_by": triggered_by,
        "schedule_id": schedule_id,
        "pipeline_run_id": pipeline_run_id,
    })
    return {"id": result["id"], "task_credential": result["task_credential"]}


def end_pipeline_run(run_id: str, status: str, error: str | None = None) -> None:
    """Mark this run finished, and how. Idempotent on the control plane's
    side: the first ending recorded is the one that stands."""
    _post(f"/pipeline-runs/{run_id}/end", {"status": status, "error": error})


def start_pipeline_run(tenant_id: str, dataset: str, workflow_id: str, *,
                       trigger_kind: str = "manual",
                       triggered_by: str | None = None,
                       schedule_id: str | None = None,
                       input_versions: list[str] | None = None,
                       principal: str | None = None) -> dict:
    """Open one pipeline run, so its steps can be found together afterwards.

    Idempotent on workflow_id, because Temporal replays a workflow after a
    crash and a replay is the same run rather than a second one.

    `input_versions`/`principal`, when given, are what let the response
    carry a task_credential.py token scoped to this run -- see that
    module's own docstring for why a whole pipeline run, not just an
    `action_run`, is a real Type A workload: `adopt_version` reads an
    existing dataset version but never opens an `action_run` of its own.
    """
    return _post("/pipeline-runs", {
        "tenant_id": tenant_id,
        "dataset": dataset,
        "workflow_id": workflow_id,
        "trigger_kind": trigger_kind,
        "triggered_by": triggered_by,
        "schedule_id": schedule_id,
        "input_versions": input_versions or [],
        "principal": principal,
    })


def seal_version(
    dataset_id: str, schema_id: str, visibility_class: str,
    manifest: list[dict], record_count: int, run_id: str,
    tenant_id: str | None = None, storage_backend: str = "seaweedfs",
) -> dict:
    """Seal a version, under the tenant this run belongs to.

    The tenant was `config.TENANT` here, read from the worker's own environment
    rather than from the run. That made the worker a single-tenant process
    wearing a multi-tenant platform's clothes: correct only while the tenant it
    was configured for happened to be the tenant whose data it was processing,
    and enforced by nothing.
    """
    return _post("/dataset-versions", {
        "tenant_id": tenant_id or config.TENANT,
        "dataset_id": dataset_id,
        "schema_id": schema_id,
        "visibility_class": visibility_class,
        "object_manifest": manifest,
        "record_count": record_count,
        "produced_by_run": run_id,
        # What the objects were actually written to, passed through rather than
        # left to the endpoint's default. The default is seaweedfs, so a
        # version written anywhere else used to record the wrong backend and a
        # credential minted from it would look in the wrong place entirely.
        "storage_backend": storage_backend,
    })


def promote(version_id: str, to_class: str, evidence: dict, grant_roles: list[str]) -> dict:
    return _post(f"/dataset-versions/{version_id}/promote", {
        "to_class": to_class,
        "decided_by": "munitas-worker",
        "decided_by_kind": "workload",
        "gate_evidence": evidence,
        "grant_roles": grant_roles,
    })


# Every object call takes the bucket it was told to use, and there is no
# default any more. The fallback to `config.BUCKET` was the bug this file
# had, and it was still live: the answer-key write in activities.py omitted
# the bucket, so every `.truth.json` went to the shared bucket while its
# sibling objects from the same step went to the tenant's own. Required
# arguments are what stop that recurring, since a caller that forgets one
# now fails at the call rather than writing somewhere plausible.
#
# Both now take the client to write with, rather than reaching for the
# module's own static s3(). pipeline_action's storage identity no longer
# holds standing Write (see docs/internal/design/write-credential-rationale.md), so
# there is no default client left here that could still write anything;
# every caller passes the s3_scoped_write() client it minted for its own
# action_run or pipeline_run.


def put_json(client, key: str, payload: object, bucket: str) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    client.put_object(Bucket=bucket, Key=key, Body=body)
    return {"key": key, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def put_file(client, key: str, path: Path, bucket: str) -> dict:
    body = path.read_bytes()
    client.put_object(Bucket=bucket, Key=key, Body=body)
    return {"key": key, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def get_json(key: str, bucket: str) -> object:
    return json.loads(
        s3().get_object(Bucket=bucket, Key=key)["Body"].read()
    )


_code_hash: str | None = None


def code_hash() -> str:
    """Hash the worker source, so lineage records what actually ran.

    Falls back to hashing the files directly when there is no git repository,
    which is the case here. A lineage row claiming a code version it cannot
    substantiate is worse than one that admits the source of its answer.
    """
    global _code_hash
    if _code_hash:
        return _code_hash

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            _code_hash = f"git:{out.stdout.strip()}"
            return _code_hash
    except (OSError, subprocess.SubprocessError):
        pass

    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.read_bytes())
    _code_hash = f"sha256:{digest.hexdigest()[:32]}"
    return _code_hash
