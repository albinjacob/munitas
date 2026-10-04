"""Uploading and fetching an agent version's own code.

Kept out of both `agents.py` and `ingest.py`, on purpose. `ingest.py`
names itself "the only module that accepts bytes" for a reason worth
keeping true: a reviewer asking where data can enter the platform should
get a one-file answer. Agent *code* is a second, separate kind of bytes
entering the platform, with its own destination (an agent version's own
storage prefix, never a dataset's) and its own consumer (the sandboxed
run engine in `worker/sandbox_run.py`, never `agent/tools.py`). Giving it
a third file keeps both of those one-file answers honest rather than
merging two different ingress stories into one.

`POST /agents/{id}/versions` in `agents.py` is untouched and still the
right call for a version this platform only ever *describes*: an
externally-hosted agent nobody expects Munitas to execute. The upload
endpoint here is for the opposite case: code the platform will actually
run, sandboxed. `code_hash` means something different on that path, and the
`agent_version.code_object_key` column comment explains exactly how.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from . import auth, config, db, seaweed
from .agents import _agent, file_egress_approval

router = APIRouter(tags=["agents"])

# Matches ingest.py's own ceiling (MAX_UPLOAD_BYTES); there is no reason for a code
# archive to be allowed more room than a dataset file gets.
MAX_UPLOAD_BYTES = 512 * 1024 * 1024

DEFAULT_ENTRYPOINT = "main.py"


def _code_prefix(tenant_id: str, agent_id: str, version: int) -> str:
    return f"{tenant_id}/agents/{agent_id}/v{version}"


@router.post("/agents/{agent_id}/versions/upload", status_code=201)
async def upload_version(
    agent_id: str,
    zip: UploadFile = File(...),
    model_id: str = Form(...),
    tool_scope: str = Form(""),
    data_access: str = Form("none"),
    requested_hosts: str = Form(""),
    registered_by: str = Form(...),
    identity: dict = Depends(auth.current_session),
) -> dict:
    """Upload a project as this agent's next version, sealed on arrival.

    `code_hash` is not a claim here, unlike the declare-only path: it is
    computed from the bytes actually received, the same discipline
    `ingest.py` already applies to dataset uploads via `dataset_source.
    checksum`. `tool_scope` arrives as a comma-separated string because this
    is a plain multipart form, not JSON, parsed and sorted the same way
    `scripts/admin/register-agent-version.py` already sorts its own `--tool` flags before
    sending them.
    """
    agent = _agent(agent_id)
    if agent["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such agent")
    auth.must_be(identity, person=registered_by)
    auth.require_code_registration_role(identity)

    if not db.one(
        "select id from directory where id = %s and tenant_id = %s",
        (registered_by, agent["tenant_id"]),
    ):
        raise HTTPException(
            403,
            {"reasons": [f"{registered_by!r} is not in this agent's directory"]},
        )

    if data_access not in ("none", "copy"):
        raise HTTPException(
            400,
            {"reasons": [
                f"data_access must be 'none' or 'copy', not {data_access!r}"
            ]},
        )

    payload = await zip.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            {"reasons": [
                f"{zip.filename!r} is {len(payload)} bytes, over the "
                f"{MAX_UPLOAD_BYTES} byte limit"
            ]},
        )
    if not payload:
        raise HTTPException(400, {"reasons": ["that file is empty"]})

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
        bad_member = archive.testzip()
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, {"reasons": [f"not a valid zip archive: {exc}"]}) from exc
    if bad_member is not None:
        raise HTTPException(400, {"reasons": [f"{bad_member!r} inside the archive is corrupt"]})

    names = archive.namelist()
    manifest: dict = {}
    if "munitas.json" in names:
        try:
            manifest = json.loads(archive.read("munitas.json"))
        except json.JSONDecodeError as exc:
            raise HTTPException(400, {"reasons": [f"munitas.json is not valid JSON: {exc}"]}) from exc

    entrypoint = manifest.get("entrypoint", DEFAULT_ENTRYPOINT)
    if entrypoint not in names:
        raise HTTPException(
            400,
            {"reasons": [f"the declared entrypoint {entrypoint!r} is not in the archive"]},
        )

    scopes = sorted({t.strip() for t in tool_scope.split(",") if t.strip()})
    hosts = sorted({h.strip() for h in requested_hosts.split(",") if h.strip()})

    row = db.one(
        "select coalesce(max(version), 0) as v from agent_version where agent_id = %s",
        (agent_id,),
    )
    version = row["v"] + 1

    checksum = hashlib.sha256(payload).hexdigest()
    code_hash = f"sha256:{checksum}"
    key = f"{_code_prefix(agent['tenant_id'], agent_id, version)}/code.zip"

    client = seaweed.admin_client(agent["tenant_id"])
    client.put_object(Bucket=seaweed.bucket(agent["tenant_id"]), Key=key, Body=payload)

    canonical = json.dumps(
        {
            "code_hash": code_hash,
            "source_path": "uploaded",
            "image_digest": "sandboxed:python3.11-slim",
            "model_id": model_id,
            "tool_scope": scopes,
        },
        sort_keys=True, separators=(",", ":"),
    )
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    version_id = str(uuid.uuid4())
    db.execute(
        """insert into agent_version
             (id, tenant_id, agent_id, version, code_hash, source_path,
              image_digest, model_id, tool_scope, registered_by,
              content_hash, sealed, code_object_key, code_bytes,
              entrypoint, manifest, data_access, requested_hosts)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true,
                   %s, %s, %s, %s, %s, %s)""",
        (version_id, agent["tenant_id"], agent_id, version, code_hash,
         "uploaded", "sandboxed:python3.11-slim", model_id, scopes,
         registered_by, content_hash, key, len(payload), entrypoint,
         json.dumps(manifest), data_access, hosts),
    )
    file_egress_approval(agent["tenant_id"], agent_id, version_id,
                         hosts, registered_by)

    return {
        "id": version_id,
        "agent_id": agent_id,
        "version": version,
        "code_hash": code_hash,
        "content_hash": content_hash,
        "entrypoint": entrypoint,
        "bytes": len(payload),
        "sealed": True,
        "data_access": data_access,
        "requested_hosts": hosts,
    }


@router.get("/agents/{agent_id}/versions/{version_id}/code")
def download_code(
    agent_id: str,
    version_id: str,
    x_worker_token: str | None = Header(default=None),
):
    """Stream an uploaded version's code archive back, for the worker only.

    Gated by a shared secret rather than a directory identity, because the
    caller is the worker process itself, not a person or a registered
    agent principal, so "who in the tenant's directory is this" does not
    apply to it. This endpoint is never reachable from `agentnet`: the
    sandboxed containers a version's code eventually runs inside have no
    route to this API at all except `POST /credentials`, a different
    endpoint entirely.
    """
    if not config.WORKER_TOKEN or x_worker_token != config.WORKER_TOKEN:
        raise HTTPException(403, {"reasons": ["missing or wrong worker token"]})

    version = db.one(
        "select code_object_key, tenant_id from agent_version "
        "where id = %s and agent_id = %s",
        (version_id, agent_id),
    )
    if not version or not version["code_object_key"]:
        raise HTTPException(404, "no uploaded code for that version")

    client = seaweed.admin_client(version["tenant_id"])
    obj = client.get_object(Bucket=seaweed.bucket(version["tenant_id"]), Key=version["code_object_key"])
    return StreamingResponse(obj["Body"], media_type="application/zip")
