"""Registering an operator's own pipeline, and uploading a sealed version of it.

Kept apart from pipeline.py the way agent_upload.py is kept apart from
agents.py: that file starts a run against a version that already exists;
this one is where a version comes to exist at all, the one place this kind
of pipeline's own bytes (its config, its scripts) enter the platform.
"""

from __future__ import annotations

import hashlib
import json
import uuid
import zipfile
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import config, db, opa, seaweed
from . import auth
from .auth import current_session
from .pipeline_dag import DagConfigError, parse_dag_config, steps_asking_for_recordings, validate_dag

router = APIRouter(tags=["pipelines"])

MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # matches agent_upload.py's own ceiling


class RegisterPipeline(BaseModel):
    tenant_id: str
    name: str
    department_id: str
    registered_by: str


def _pipeline(pipeline_id: str, tenant_id: str | None = None) -> dict:
    row = db.one(
        """select p.*, d.name as department_name, dir.label as registered_by_label
             from pipeline p
             left join department d on d.id = p.department_id
             left join directory dir on dir.id = p.registered_by
            where p.id = %s""",
        (pipeline_id,),
    )
    if not row or (tenant_id and row["tenant_id"] != tenant_id):
        raise HTTPException(404, "no such pipeline")
    return row


def _code_prefix(tenant_id: str, pipeline_id: str, version: int) -> str:
    return f"{tenant_id}/pipelines/{pipeline_id}/v{version}"


@router.post("/pipelines/register", status_code=201)
def register(body: RegisterPipeline, identity: dict = Depends(current_session)) -> dict:
    """Register a pipeline. No version yet, so nothing about it can run
    until one is sealed, the same rule an agent's registration already
    follows.
    """
    auth.must_be(identity, tenant_id=body.tenant_id, person=body.registered_by)
    auth.require_code_registration_role(identity)
    if not db.one(
        "select id from directory where id = %s and tenant_id = %s",
        (body.registered_by, body.tenant_id),
    ):
        raise HTTPException(
            403,
            {"reasons": [f"{body.registered_by!r} is not in {body.tenant_id!r}'s directory"]},
        )
    if not db.one(
        "select id from department where id = %s and tenant_id = %s",
        (body.department_id, body.tenant_id),
    ):
        raise HTTPException(400, {"reasons": ["that department does not exist"]})

    pipeline_id = str(uuid.uuid4())
    try:
        db.execute(
            """insert into pipeline (id, tenant_id, name, department_id, registered_by)
               values (%s, %s, %s, %s, %s)""",
            (pipeline_id, body.tenant_id, body.name, body.department_id, body.registered_by),
        )
    except Exception as exc:  # unique (tenant_id, name)
        raise HTTPException(409, {"reasons": [f"could not register: {exc}"]}) from exc

    return {"id": pipeline_id, "name": body.name}


@router.post("/pipelines/{pipeline_id}/versions/upload", status_code=201)
async def upload_version(
    pipeline_id: str,
    config_file: UploadFile = File(...),
    scripts: UploadFile = File(...),
    registered_by: str = Form(...),
    identity: dict = Depends(current_session),
) -> dict:
    """Upload a version: one YAML file naming the steps, one zip holding every
    script those steps reference. Sealed on arrival, the same as an agent
    version: this platform never lets a version be edited after the fact,
    only superseded by the next one.
    """
    pipeline = _pipeline(pipeline_id, identity["tenant_id"])
    auth.must_be(identity, person=registered_by)
    auth.require_code_registration_role(identity)

    if not db.one(
        "select id from directory where id = %s and tenant_id = %s",
        (registered_by, pipeline["tenant_id"]),
    ):
        raise HTTPException(
            403, {"reasons": [f"{registered_by!r} is not in this pipeline's directory"]}
        )

    yaml_bytes = await config_file.read()
    if not yaml_bytes or len(yaml_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, {"reasons": ["the config file is empty or too large"]})

    try:
        dag_config = parse_dag_config(yaml_bytes.decode("utf-8"))
    except DagConfigError as exc:
        raise HTTPException(400, {"reasons": [str(exc)]}) from exc

    zip_bytes = await scripts.read()
    if len(zip_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, {"reasons": [
            f"the scripts archive is over the {MAX_UPLOAD_BYTES} byte limit"
        ]})

    try:
        archive = zipfile.ZipFile(BytesIO(zip_bytes))
        bad_member = archive.testzip()
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, {"reasons": [f"not a valid zip archive: {exc}"]}) from exc
    if bad_member is not None:
        raise HTTPException(400, {"reasons": [f"{bad_member!r} inside the archive is corrupt"]})

    script_names = set(archive.namelist())
    problems = validate_dag(dag_config, script_names)
    if problems:
        raise HTTPException(400, {"reasons": problems})

    row = db.one(
        "select coalesce(max(version), 0) as v from pipeline_version where pipeline_id = %s",
        (pipeline_id,),
    )
    version = row["v"] + 1

    config_hash = hashlib.sha256(
        json.dumps(dag_config, sort_keys=True).encode("utf-8")
    ).hexdigest()
    key = f"{_code_prefix(pipeline['tenant_id'], pipeline_id, version)}/scripts.zip"

    client = seaweed.admin_client(pipeline["tenant_id"])
    client.put_object(Bucket=seaweed.bucket(pipeline["tenant_id"]), Key=key, Body=zip_bytes)

    version_id = str(uuid.uuid4())
    db.execute(
        """insert into pipeline_version
             (id, pipeline_id, version, dag_config, config_hash, code_object_key, sealed, uploaded_by)
           values (%s, %s, %s, %s, %s, %s, true, %s)""",
        (version_id, pipeline_id, version, json.dumps(dag_config), config_hash, key, registered_by),
    )

    asking = steps_asking_for_recordings(dag_config)
    return {
        "id": version_id, "pipeline_id": pipeline_id, "version": version,
        "config_hash": config_hash, "sealed": True, "step_count": len(dag_config["steps"]),
        "steps_given_recordings": asking, "needs_approval": bool(asking),
    }


@router.get("/pipelines")
def list_pipelines(
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
    identity: dict = Depends(current_session),
) -> dict:
    """Every pipeline in the caller's own tenant, with the total for real
    page controls.

    Previously took a caller-supplied `tenant_id` with no session at all --
    any tenant's full pipeline list, names, departments and who registered
    them, readable by guessing the tenant id. Scoped to the session's own
    tenant now, the same fix every read endpoint in `read_models.py`
    already has for this exact shape of gap.

    Also previously had no `limit`/`offset` at all, unlike every other list
    endpoint -- missed in the console-wide pagination pass this was found
    during, since nothing had ever flagged it as unbounded before an
    organisation actually had enough pipelines for that to matter. Same
    `{pipelines, shown, total, limit, offset}` envelope as the rest.
    """
    tenant_id = identity["tenant_id"]
    rows = db.all_rows(
        """select p.id, p.name, d.name as department_name, dir.label as registered_by_label,
                  p.created_at,
                  count(pv.id) as version_count,
                  max(pv.version) as latest_version,
                  (select pv2.id from pipeline_version pv2
                    where pv2.pipeline_id = p.id
                    order by pv2.version desc limit 1) as latest_version_id
             from pipeline p
             left join department d on d.id = p.department_id
             left join directory dir on dir.id = p.registered_by
             left join pipeline_version pv on pv.pipeline_id = p.id
            where p.tenant_id = %(tenant)s
            group by p.id, d.name, dir.label
            order by p.created_at desc
            limit %(limit)s offset %(offset)s""",
        {"tenant": tenant_id, "limit": limit, "offset": offset},
    )
    total = db.one(
        "select count(*) as n from pipeline where tenant_id = %s", (tenant_id,)
    )
    return {
        "pipelines": rows,
        "shown": len(rows),
        "total": total["n"],
        "limit": limit,
        "offset": offset,
    }


@router.get("/pipelines/{pipeline_id}")
def read_pipeline(
    pipeline_id: str, identity: dict = Depends(current_session)
) -> dict:
    """One pipeline and its versions, scoped to the caller's own tenant.

    Previously took no session at all: any pipeline's config and version
    history, by anyone who knew or guessed its id, cross-tenant.
    """
    pipeline = _pipeline(pipeline_id, identity["tenant_id"])
    versions = db.all_rows(
        """select pv.id, pv.version, pv.dag_config, pv.config_hash, pv.created_at, pv.uploaded_by, u.label as uploaded_by_label,
                  a.approved_by, ab.label as approved_by_label, a.approved_at
             from pipeline_version pv
             left join directory u on u.id = pv.uploaded_by
             left join pipeline_version_approval a on a.pipeline_version_id = pv.id and a.withdrawn_at is null
             left join directory ab on ab.id = a.approved_by
            where pv.pipeline_id = %s order by pv.version desc""",
        (pipeline_id,),
    )
    for v in versions:
        asking = steps_asking_for_recordings(v["dag_config"])
        v["steps_given_recordings"] = asking
        v["needs_approval"] = bool(asking)
        v["approved"] = v["approved_by"] is not None
    return {**pipeline, "versions": versions}


def is_approved(pipeline_version_id: str) -> bool:
    """Whether a second person's approval of this version stands (not withdrawn)."""
    return db.one("select 1 as x from pipeline_version_approval where pipeline_version_id = %s and withdrawn_at is null", (pipeline_version_id,)) is not None


class Decision(BaseModel):
    note: str = ""


def _version_of(pipeline_id: str, version_id: str, identity: dict) -> tuple[dict, dict]:
    pipeline = _pipeline(pipeline_id, identity["tenant_id"])
    version = db.one("select id, version, dag_config, uploaded_by from pipeline_version where id = %s and pipeline_id = %s", (version_id, pipeline_id))
    if not version:
        raise HTTPException(404, "no such pipeline version")
    return pipeline, version


def _approvers(pipeline: dict) -> list[str]:
    from .locked_leases import approvers_of
    return approvers_of(pipeline["department_id"])


@router.post("/pipelines/{pipeline_id}/versions/{version_id}/approve")
def approve_version(pipeline_id: str, version_id: str, body: Decision, identity: dict = Depends(current_session)) -> dict:
    """Approve a version whose custom step is given the recordings. The department's approver, never the person who uploaded it.

    Until this is done a run of the version is refused, and the recordings are refused to its steps if a run somehow started.
    """
    pipeline, version = _version_of(pipeline_id, version_id, identity)
    if not steps_asking_for_recordings(version["dag_config"]):
        raise HTTPException(400, {"reasons": ["no step of this version is given the recordings, so there is nothing to approve"]})
    allowed, reasons = opa.may_approve_pipeline_version({
        "approver": {"id": identity["id"], "roles": identity["roles"]}, "version": {"uploaded_by": version["uploaded_by"]},
        "dataset": {"approvers": _approvers(pipeline)}})
    if not allowed:
        raise HTTPException(403, {"reasons": reasons})
    if is_approved(version_id):
        raise HTTPException(409, {"reasons": ["this version is already approved"]})
    approval_id = str(uuid.uuid4())
    db.execute("insert into pipeline_version_approval (id, pipeline_version_id, tenant_id, approved_by, note) values (%s, %s, %s, %s, %s) returning id",
               (approval_id, version_id, pipeline["tenant_id"], identity["id"], body.note.strip() or None))
    return {"id": approval_id, "pipeline_version_id": version_id, "approved_by": identity["id"], "approved": True}


@router.post("/pipelines/{pipeline_id}/versions/{version_id}/withdraw-approval")
def withdraw_approval(pipeline_id: str, version_id: str, body: Decision, identity: dict = Depends(current_session)) -> dict:
    """Take an approval back. A run already going keeps going; no later run, and no later opening of a recording by a step, is allowed."""
    pipeline, _version = _version_of(pipeline_id, version_id, identity)
    allowed, reasons = opa.may_withdraw_pipeline_approval({
        "actor": {"id": identity["id"], "roles": identity["roles"]}, "dataset": {"approvers": _approvers(pipeline)}})
    if not allowed:
        raise HTTPException(403, {"reasons": reasons})
    row = db.execute(
        """update pipeline_version_approval set withdrawn_by = %s, withdrawn_at = now()
            where pipeline_version_id = %s and withdrawn_at is null returning id""", (identity["id"], version_id))
    if row is None:
        raise HTTPException(409, {"reasons": ["this version has no approval standing"]})
    return {"pipeline_version_id": version_id, "approved": False}


@router.get("/pipelines/{pipeline_id}/versions/{version_id}/code")
def download_code(
    pipeline_id: str,
    version_id: str,
    x_worker_token: str | None = Header(default=None),
):
    """Stream a version's scripts.zip back, for the worker only.

    Gated the same way agent_upload.py's own download_code is: a shared
    secret, not a directory identity, because the caller is the worker
    process itself. Never reachable from agentnet.
    """
    if not config.WORKER_TOKEN or x_worker_token != config.WORKER_TOKEN:
        raise HTTPException(403, {"reasons": ["missing or wrong worker token"]})

    version = db.one(
        """select pv.code_object_key, p.tenant_id
             from pipeline_version pv join pipeline p on p.id = pv.pipeline_id
            where pv.id = %s and pv.pipeline_id = %s""",
        (version_id, pipeline_id),
    )
    if not version:
        raise HTTPException(404, "no such pipeline version")

    client = seaweed.admin_client(version["tenant_id"])
    obj = client.get_object(
        Bucket=seaweed.bucket(version["tenant_id"]), Key=version["code_object_key"]
    )
    return StreamingResponse(obj["Body"], media_type="application/zip")
