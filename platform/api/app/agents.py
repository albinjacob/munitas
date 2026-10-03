"""Registering agents and sealing their versions.

An agent is registered the way a dataset is: an end user names it, gives it
an owning department, and states its purpose, before anything about its code
exists in the platform. A version is different from a dataset version in one
way that matters: its entire content (what code, what model, what tools) is
known at registration time, so there is no upload phase to wait on. Register
a version and it is sealed in the same call.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from psycopg import errors as pg_errors
from pydantic import BaseModel, Field

from . import auth, config, db, grants, opa, task_credential, temporal_client

router = APIRouter(tags=["agents"])


def _access_preflight(agent: dict, dataset_version_id: str) -> dict:
    """Would this agent be allowed to read this version, and if not, who decides.

    Asks the same policy question `POST /credentials` asks, and deliberately
    writes no `access_decision` row: nothing is being attempted here. A run that
    actually reads leaves its own audit trail through the agent's tools. Logging
    a denial every time a console dropdown changes would fill the log with
    refusals nobody ever made.

    The agent's own runtime identity is what gets asked about, not the person
    at the console. A console user with wide access does not lend it to an
    agent, which is the whole reason the agent has an identity of its own.
    """
    version = db.one(
        "select * from version_class where dataset_version_id = %s",
        (dataset_version_id,),
    )
    if not version:
        raise HTTPException(404, "no such dataset version")
    if version["tenant_id"] != agent["tenant_id"]:
        raise HTTPException(404, "no such dataset version")

    principal = db.one(
        "select id, tenant_id, roles from directory where id = %s",
        (agent["principal_id"],),
    )
    if not principal:
        raise HTTPException(
            500,
            {"reasons": [f"this agent's runtime identity {agent['principal_id']!r} "
                         "is not in the directory"]},
        )

    allowed, reasons = opa.evaluate({
        "principal": {
            "id": principal["id"],
            "tenant": principal["tenant_id"],
            "roles": principal["roles"],
            "leases": db.active_leases(agent["principal_id"], dataset_version_id),
        },
        "dataset": {
            "tenant": version["tenant_id"],
            "version_id": str(version["dataset_version_id"]),
            "visibility_class": version["current_class"],
        },
        "purpose": agent["purpose"],
    })

    custodian = db.one(
        "select custodian, department_name, dataset_name from version_custodian "
        "where dataset_version_id = %s",
        (dataset_version_id,),
    ) or {}

    approver = None
    if custodian.get("custodian"):
        row = db.one(
            "select label from directory where id = %s", (custodian["custodian"],)
        )
        approver = row["label"] if row else custodian["custodian"]

    return {
        "dataset_version_id": dataset_version_id,
        "visibility_class": version["current_class"],
        "allowed": allowed,
        "reasons": reasons,
        "needs_access_request": not allowed,
        "approver_label": approver,
        "department_name": custodian.get("department_name"),
        "dataset_name": custodian.get("dataset_name"),
    }


@router.get("/agents/{agent_id}/access-preflight")
def access_preflight(
    agent_id: str,
    dataset_version_id: str,
    identity: dict = Depends(auth.current_session),
) -> dict:
    """Whether starting a run against this version would need access requested.

    The console calls this when somebody picks a dataset, so the start button
    can say what pressing it will do before it does it. Requesting access in
    somebody's name without showing them first is the kind of thing that is
    technically consented to and actually a surprise.

    Previously took no session at all, unlike the console flow that calls
    it (a signed-in person choosing a dataset), and unlike its sibling
    `egress_status` below it is not workload-facing, so it had no comment
    justifying that. Tenant-checked the same way `deploy` further down
    already checks an agent against the caller's own tenant.
    """
    agent = _agent(agent_id)
    if identity["tenant_id"] != agent["tenant_id"]:
        raise HTTPException(404, "no such agent")
    return _access_preflight(agent, dataset_version_id)


class RegisterAgent(BaseModel):
    tenant_id: str
    name: str
    department_id: str | None = None
    registered_by: str
    purpose: str = Field(min_length=1)


class RegisterAgentVersion(BaseModel):
    # `model_id` collides with pydantic's own reserved `model_*` namespace
    # (`model_dump`, `model_config`, ...) without this; it is a field name
    # here, nothing to do with pydantic's model machinery.
    model_config = {"protected_namespaces": ()}

    code_hash: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    image_digest: str = "native:host-venv"
    model_id: str = Field(min_length=1)
    tool_scope: list[str] = Field(default_factory=list)
    registered_by: str
    # Empty by default: most versions call nothing outside the boundary.
    # Non-empty means this version cannot be deployed until a
    # `network_architect` approves exactly this list (see `deploy` below and
    # `docs/internal/diagrams/agent-egress-allowlist`).
    requested_hosts: list[str] = Field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _agent(agent_id: str) -> dict:
    row = db.one(
        """select a.*, dept.name as department_name
           from agent a
           left join department dept on dept.id = a.department_id
           where a.id = %s""",
        (agent_id,),
    )
    if not row:
        raise HTTPException(404, "no such agent")
    return row


def _execution_mode(version_id: str) -> str:
    """'sandboxed' if this version has its own uploaded code, else 'native'.

    The one place that answers this question, so `start_run` and
    `_start_waiting_on_access` agree with each other and with whatever
    `worker/agent_run_workflow.py` later branches on. All three read the
    same fact off `agent_version.code_object_key` rather than each
    re-deriving it.
    """
    row = db.one(
        "select code_object_key from agent_version where id = %s", (version_id,)
    )
    return "sandboxed" if row and row["code_object_key"] else "native"


def file_egress_approval(tenant_id: str, agent_id: str, version_id: str,
                         requested_hosts: list[str], submitted_by: str) -> None:
    """Open a pending approval the moment a version declares any hosts.

    Nothing separate to press: the request exists the instant there is
    something to request, the same way registering a lease request needs no
    extra step beyond asking. A version that names no hosts files nothing at
    all, since `deploy` below only checks for one when `requested_hosts` is
    non-empty.

    Shared by both registration paths (declared-only here, and the upload
    endpoint in `agent_upload.py`): either one can produce a version that
    actually runs, so either one can produce a version that needs this.
    """
    if not requested_hosts:
        return
    db.execute(
        """insert into agent_egress_approval
             (id, tenant_id, agent_id, agent_version_id, requested_hosts, submitted_by)
           values (%s, %s, %s, %s, %s, %s)""",
        (str(uuid.uuid4()), tenant_id, agent_id, version_id, requested_hosts, submitted_by),
    )


def _slugify(name: str) -> str:
    """A short, id-safe form of an agent's own name.

    Never a hash: the point is that a runtime identity's id stays
    recognisable next to the agent it belongs to in a raw audit query, not
    opaque like a content hash would be.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "agent"


@router.post("/agents/register", status_code=201)
def register(body: RegisterAgent, identity: dict = Depends(auth.current_session)) -> dict:
    """Register an agent, and its own runtime identity along with it.

    No version yet, so nothing can run as this agent until one is
    registered. Nothing picks an *existing* identity to run as any more:
    the workload directory row is created here, named after this agent, in
    the same transaction as the agent row itself, so neither can exist
    without the other. `directory.id` is one global primary key across
    every tenant (see `infra/postgres/seed-canary.sql`'s own comment on
    this), which is why the id is tenant-prefixed rather than derived from
    the name alone: two different tenants naming an agent the same thing
    must not collide on one directory row.

    Sharing an identity between two agents is exactly the failure this
    closes. It means their tool calls cannot be told apart in the audit log,
    and a lease granted for one silently covers the other too.
    """
    auth.must_be(identity, tenant_id=body.tenant_id, person=body.registered_by)
    if not db.one(
        "select id from directory where id = %s and tenant_id = %s",
        (body.registered_by, body.tenant_id),
    ):
        raise HTTPException(
            403,
            {"reasons": [
                f"{body.registered_by!r} is not in {body.tenant_id!r}'s directory"
            ]},
        )

    if body.department_id and not db.one(
        "select id from department where id = %s and tenant_id = %s",
        (body.department_id, body.tenant_id),
    ):
        raise HTTPException(400, {"reasons": ["that department does not exist"]})

    agent_id = str(uuid.uuid4())
    principal_id = f"{body.tenant_id}-{_slugify(body.name)}-runtime"
    label = f"{body.name.strip()} (runtime account)"
    try:
        with db.cursor(commit=True) as cur:
            cur.execute(
                """insert into directory (id, tenant_id, label, kind, roles)
                   values (%s, %s, %s, 'workload', %s)""",
                (principal_id, body.tenant_id, label, ["agent_runtime"]),
            )
            cur.execute(
                """insert into agent
                     (id, tenant_id, name, department_id, registered_by, purpose,
                      principal_id)
                   values (%s, %s, %s, %s, %s, %s, %s)""",
                (agent_id, body.tenant_id, body.name, body.department_id,
                 body.registered_by, body.purpose, principal_id),
            )
    except Exception as exc:  # unique (tenant_id, name), or a name whose slug collides
        raise HTTPException(409, {"reasons": [f"could not register: {exc}"]}) from exc

    return {"id": agent_id, "name": body.name}


@router.post("/agents/{agent_id}/versions", status_code=201)
def register_version(agent_id: str, body: RegisterAgentVersion, identity: dict = Depends(auth.current_session)) -> dict:
    """Register and seal a version in one call.

    Sealed on creation, the same as a dataset version, and for the same
    reason there is no update path anywhere in this file: a version somebody
    could edit after the fact proves nothing about what actually ran.
    """
    agent = _agent(agent_id)
    if agent["tenant_id"] != identity["tenant_id"]:
        raise HTTPException(404, "no such agent")
    auth.must_be(identity, person=body.registered_by)

    if not db.one(
        "select id from directory where id = %s and tenant_id = %s",
        (body.registered_by, agent["tenant_id"]),
    ):
        raise HTTPException(
            403,
            {"reasons": [
                f"{body.registered_by!r} is not in this agent's directory"
            ]},
        )

    row = db.one(
        "select coalesce(max(version), 0) as v from agent_version where agent_id = %s",
        (agent_id,),
    )
    version = row["v"] + 1

    canonical = json.dumps(
        {
            "code_hash": body.code_hash,
            "source_path": body.source_path,
            "image_digest": body.image_digest,
            "model_id": body.model_id,
            "tool_scope": sorted(body.tool_scope),
        },
        sort_keys=True, separators=(",", ":"),
    )
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    version_id = str(uuid.uuid4())
    db.execute(
        """insert into agent_version
             (id, tenant_id, agent_id, version, code_hash, source_path,
              image_digest, model_id, tool_scope, registered_by,
              content_hash, sealed, requested_hosts)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s)""",
        (version_id, agent["tenant_id"], agent_id, version, body.code_hash,
         body.source_path, body.image_digest, body.model_id, body.tool_scope,
         body.registered_by, content_hash, body.requested_hosts),
    )
    file_egress_approval(agent["tenant_id"], agent_id, version_id,
                         body.requested_hosts, body.registered_by)

    return {
        "id": version_id,
        "agent_id": agent_id,
        "version": version,
        "content_hash": content_hash,
        "sealed": True,
    }


class DeployAgent(BaseModel):
    agent_version_id: str
    note: str | None = None


@router.post("/agents/{agent_id}/deploy", status_code=201)
def deploy(agent_id: str, body: DeployAgent,
          identity: dict = Depends(auth.current_session)) -> dict:
    """Mark a version as the one this agent runs.

    Never an update. A new row is appended to `agent_deployment`; "the
    active version" is read back as the most recent one. Redeploying an
    earlier version is just another row with an earlier agent_version_id,
    which is how a rollback happens here, not a special case.
    """
    agent = _agent(agent_id)

    version = db.one(
        "select id, requested_hosts from agent_version "
        "where id = %s and agent_id = %s",
        (body.agent_version_id, agent_id),
    )
    if not version:
        raise HTTPException(
            400, {"reasons": ["that version does not belong to this agent"]}
        )

    if identity["tenant_id"] != agent["tenant_id"]:
        raise HTTPException(
            403, {"reasons": [
                f"{identity['id']!r} is in {identity['tenant_id']!r}, not "
                f"{agent['tenant_id']!r}"
            ]}
        )

    # A version that names no hosts needs no approval at all; one that does
    # cannot deploy until every host it named has one on file. The most
    # recent approval decides, not "any approval ever": a version's
    # requested_hosts never changes once sealed, but this still reads the
    # latest row rather than assuming there is exactly one, the same
    # defensive choice `_pending_gate` makes about `gate_decision`.
    if version["requested_hosts"]:
        approval = db.one(
            """select state from agent_egress_approval
                where agent_version_id = %s
                order by created_at desc limit 1""",
            (body.agent_version_id,),
        )
        if not approval or approval["state"] != "approved":
            state = approval["state"] if approval else "not yet requested"
            raise HTTPException(409, {"reasons": [
                f"this version asks to call {', '.join(version['requested_hosts'])}, "
                f"and that request is {state}, not approved -- a network_architect "
                "has to approve it before this version can be deployed"
            ]})

    deployment_id = str(uuid.uuid4())
    db.execute(
        """insert into agent_deployment
             (id, tenant_id, agent_id, agent_version_id, deployed_by, note)
           values (%s, %s, %s, %s, %s, %s)""",
        (deployment_id, agent["tenant_id"], agent_id, body.agent_version_id,
         identity["id"], body.note),
    )
    return {"id": deployment_id, "agent_id": agent_id,
            "agent_version_id": body.agent_version_id}


class EgressDecisionIn(BaseModel):
    reason: str = Field(min_length=1)


def _pending_egress_approval(approval_id: str) -> dict:
    row = db.one("select * from agent_egress_approval where id = %s", (approval_id,))
    if not row:
        raise HTTPException(404, "no such egress approval")
    if row["state"] != "pending":
        raise HTTPException(409, f"this request is already {row['state']}")
    return row


def _egress_permission(row: dict, identity: dict) -> None:
    permitted, reasons = opa.may_approve_egress_hosts({
        "approver": {"id": identity["id"], "roles": identity["roles"]},
        "version": {"submitted_by": row["submitted_by"]},
    })
    if not permitted:
        raise HTTPException(403, {"decided": False, "reasons": reasons})


@router.post("/egress-approvals/{approval_id}/approve", status_code=201)
def approve_egress_hosts(approval_id: str, body: EgressDecisionIn,
                         identity: dict = Depends(auth.current_session)) -> dict:
    """Approve the hosts a version asked to call. Deploy can now proceed.

    The identity comes from the session, never the body, the same reason
    `decide_gate_promote` does: who approved a version's network reach has
    to be a fact the caller could not assert about themselves.
    """
    row = _pending_egress_approval(approval_id)
    _egress_permission(row, identity)

    db.execute(
        """update agent_egress_approval
             set state = 'approved', decided_by = %s, decided_at = now(),
                 decision_reason = %s
           where id = %s and state = 'pending'""",
        (identity["id"], body.reason, approval_id),
    )
    return {"decided": True, "state": "approved",
            "agent_version_id": str(row["agent_version_id"])}


@router.post("/egress-approvals/{approval_id}/refuse", status_code=201)
def refuse_egress_hosts(approval_id: str, body: EgressDecisionIn,
                        identity: dict = Depends(auth.current_session)) -> dict:
    """Refuse the request. The version stays undeployable."""
    row = _pending_egress_approval(approval_id)
    _egress_permission(row, identity)

    db.execute(
        """update agent_egress_approval
             set state = 'refused', decided_by = %s, decided_at = now(),
                 decision_reason = %s
           where id = %s and state = 'pending'""",
        (identity["id"], body.reason, approval_id),
    )
    return {"decided": True, "state": "refused",
            "agent_version_id": str(row["agent_version_id"])}


@router.get("/agent-versions/{version_id}/egress-status")
def egress_status(version_id: str, scope: str | None = Depends(auth.organisation_scope)) -> dict:
    """What this version may call, and whether that has been approved.

    Called by the agent's own runtime (`agent/tools.py`'s `fetch_url`), a
    workload with no Kratos session to present. (`GET /dataset-versions/{id}`
    used to take no session either, and no longer does: it carries a version's
    storage keys, so it answers a session, the worker token or a run credential
    only.) A version's requested hosts and approval state are not sensitive in the
    way tenant data is, and this is read on every network call an agent
    makes, not once at deploy time. Scoped by version id alone, no tenant
    check: an agent already knows only its own `agent_version_id`, and a
    stranger who guessed one learns a host list and a state, nothing about
    any tenant's actual data.

    The most recent approval decides, not "any approval ever", the same
    defensive read `deploy` above and `_pending_gate` elsewhere both make.
    """
    version = db.one(
        "select requested_hosts, tenant_id from agent_version where id = %s",
        (version_id,),
    )
    # Answered to the agent's own run credential (it names the organisation), a session, or a worker, and a version of
    # another organisation is not found rather than refused.
    if not version or (scope is not None and version["tenant_id"] != scope):
        raise HTTPException(404, "no such agent version")

    approval = db.one(
        """select state from agent_egress_approval
            where agent_version_id = %s
            order by created_at desc limit 1""",
        (version_id,),
    )
    return {
        "requested_hosts": version["requested_hosts"],
        "state": approval["state"] if approval else None,
    }


class StartAgentRun(BaseModel):
    purpose: str = Field(min_length=1)
    agent_version_id: str | None = None
    # Optional here, but not yet safe to leave out. A run started with no
    # `dataset_version_id` fails: `agent/graph.py`'s `inspect` node calls
    # `read_dataset_version` on whatever `state["version_id"]` holds, `None`
    # included, and that reaches `/credentials`, where
    # `CredentialRequest.dataset_version_id` is a required `str` and rejects it
    # with a 422. So the untargeted path is a known, filed limitation, not a
    # graceful "nothing to inspect" no-op. No verify script exercises it
    # end-to-end either: `verify/v50_agent_deploy_and_runs.py` hands its run a
    # fixture dataset version specifically to route around this.
    dataset_version_id: str | None = None
    documents: list[dict] = []
    # Set by the console once it has shown the user that starting will ask the
    # owning custodian for access on the agent's behalf. Without it a run that
    # needs access is refused rather than quietly filing a request in the
    # caller's name, because a request recorded as "Devi asked for this" should
    # only exist if Devi saw the question.
    request_access: bool = False
    # Sandboxed runs only (worker/sandbox_run.py); ignored by the native
    # path. Optional so the ordinary case takes the default wall-clock
    # ceiling (worker.sandbox_run.DEFAULT_RUN_TIMEOUT_SECONDS) without the
    # caller having to know that number. Exists mainly so a verification
    # fixture can prove the kill-and-record-honestly path without waiting
    # out a five-minute default.
    timeout_seconds: int | None = None


# How long the agent's borrowed access lasts. Short on purpose: this is a lease
# to do one job, not a standing grant, and a run that has not finished within
# this window has a problem a longer lease would only hide.
ACCESS_REQUEST_TTL_HOURS = 4


async def _start_waiting_on_access(agent: dict, agent_id: str, run_id: str,
                                   version_id: str, body: StartAgentRun,
                                   execution_mode: str, requested_by: str) -> dict:
    """File a lease request for the agent, and park the run until it is decided.

    An on-behalf-of request, which the platform already understands: the agent
    is the `principal` that will read, and the human who pressed start is the
    `requested_by` who asked. Both are recorded, so "which agent read this" and
    "who wanted it to" stay separately answerable, and the custodian approving
    it can see it is an agent they are granting, not a person.

    Nothing is started here. The run exists so it is visible and so the approval
    has something to find, but no workflow runs until access is granted. That is
    why the status is its own value rather than `running`: a run that has not
    begun should not claim to be under way.
    """
    # A dataset with no owning department has no custodian, and policy refuses
    # an approval where there is nobody entitled to give it. Filing the request
    # anyway would park the run behind a decision no one can make, so this is
    # refused up front and says which thing is missing.
    custodian = db.one(
        "select custodian from version_custodian where dataset_version_id = %s",
        (body.dataset_version_id,),
    ) or {}
    if not custodian.get("custodian"):
        raise HTTPException(409, {"reasons": [
            "that dataset has no owning department, so there is no custodian "
            "who could grant this agent access to it"
        ]})

    request_id = str(uuid.uuid4())
    db.execute(
        """insert into lease_request
             (id, tenant_id, principal, requested_by, dataset_version_id,
              purpose, justification, requested_ttl_hours, state)
           values (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')""",
        (request_id, agent["tenant_id"], agent["principal_id"], requested_by,
         body.dataset_version_id, body.purpose,
         f"requested automatically so the agent {agent['name']!r} can run: {body.purpose}",
         ACCESS_REQUEST_TTL_HOURS),
    )

    run_secret = task_credential.mint(
        principal=agent["principal_id"], task_kind="agent_run",
        task_id=run_id, tenant_id=agent["tenant_id"],
    )
    db.execute(
        """insert into agent_run
             (id, tenant_id, agent_id, agent_version_id, status, purpose,
              requested_by, lease_request_id, dataset_version_id, execution_mode,
              run_secret)
           values (%s, %s, %s, %s, 'awaiting_access', %s, %s, %s, %s, %s, %s)""",
        (run_id, agent["tenant_id"], agent_id, version_id, body.purpose,
         requested_by, request_id, body.dataset_version_id, execution_mode,
         run_secret),
    )

    return {
        "run_id": run_id,
        "status": "awaiting_access",
        "lease_request_id": request_id,
    }


async def runs_waiting_on_lease(request_id: str, granted: bool,
                                reason: str | None = None) -> list[str]:
    """Carry on, or close, the runs that were parked on this lease request.

    Called by the lease approve and reject handlers. A parked run never started,
    so granting access starts it normally rather than resuming anything; the
    resume path belongs to the separate approval gate the graph stops at once
    the work is done.

    Matched on `lease_request_id` rather than on principal and version, because
    the same agent may have more than one run parked against one dataset and
    only the one this decision answers should move.

    A failure to start is written onto the run rather than raised. The custodian
    approved the access and that approval stands; the run failing to start
    afterwards is the run's problem, and turning it into an error on their
    request would suggest their decision did not take effect.
    """
    parked = db.all_rows(
        """select ar.id, ar.agent_id, ar.agent_version_id, ar.tenant_id,
                  ar.purpose, ar.execution_mode, a.principal_id,
                  lr.dataset_version_id
           from agent_run ar
           join agent a on a.id = ar.agent_id
           join lease_request lr on lr.id = ar.lease_request_id
           where ar.lease_request_id = %s and ar.status = 'awaiting_access'""",
        (request_id,),
    )
    if not parked:
        return []

    if not granted:
        db.execute(
            """update agent_run
                 set status = 'failed', error = %s, ended_at = now()
               where lease_request_id = %s and status = 'awaiting_access'""",
            (reason or "the access this run needed was refused", request_id),
        )
        return [str(r["id"]) for r in parked]

    started = []
    for run in parked:
        run_id = str(run["id"])
        try:
            # Minted fresh here, not read back from the row's own
            # insert-time token: a run can sit `awaiting_access` for as long
            # as a custodian takes to decide, which the token's short TTL
            # does not know to wait for. Reusing the original would let a
            # slow approval hand a run a credential that is already expired
            # the moment it actually starts.
            #
            # Written back onto the row, not only carried in the workflow's
            # own input: a native run's activities (worker/
            # agent_run_activities.py's _context_for) re-read run_secret
            # from this column on every single tool call rather than from
            # whatever this workflow started with, so the column has to
            # carry the live value or a native run resumes with a payload
            # nothing downstream of the first call ever looks at.
            fresh_secret = task_credential.mint(
                principal=run["principal_id"], task_kind="agent_run",
                task_id=run_id, tenant_id=run["tenant_id"],
            )
            db.execute(
                "update agent_run set run_secret = %s where id = %s",
                (fresh_secret, run_id),
            )
            client = temporal_client.get()
            await client.start_workflow(
                "AgentRunWorkflow",
                {
                    "run_id": run_id,
                    "agent_id": str(run["agent_id"]),
                    "agent_version_id": str(run["agent_version_id"]),
                    "principal_id": run["principal_id"],
                    "tenant_id": run["tenant_id"],
                    "purpose": run["purpose"],
                    "dataset_version_id": str(run["dataset_version_id"]),
                    "execution_mode": run["execution_mode"],
                    "documents": [],
                    "run_secret": fresh_secret,
                },
                id=f"agent-run-{run_id[:10]}",
                task_queue=config.AGENT_RUN_TASK_QUEUE,
            )
        except Exception as exc:
            db.execute(
                """update agent_run
                     set status = 'failed', error = %s, ended_at = now()
                   where id = %s and status = 'awaiting_access'""",
                (f"access was granted but the run could not start: {exc}", run_id),
            )
            continue

        db.execute(
            "update agent_run set status = 'running' where id = %s "
            "and status = 'awaiting_access'",
            (run_id,),
        )
        started.append(run_id)
    return started


async def resume_activated_runs() -> list[str]:
    """Carry on the runs that parked because their storage access was allowed
    but not yet in effect, now that it is.

    A run parks with the moment it did so (`awaiting_activation_since`), which
    is after the decision it waits on, so a successful print that started later
    contains that decision. Nothing is inferred from timing beyond that one
    comparison, and nothing here decides access: every resumed run asks
    `POST /credentials` again.

    Each run is claimed with a conditional update before its workflow starts,
    so two ticks can never resume one run twice. An ordinary run carries on
    from its checkpoint, re-running the step whose credential request was
    answered 202; a sandboxed run starts afresh from staging, which is safe
    because none of its code had run. A run that cannot be restarted is marked
    failed rather than left parked forever.
    """
    served_from = grants.last_success_started_at()
    if served_from is None:
        return []
    parked = db.all_rows(
        """select ar.id, ar.agent_id, ar.agent_version_id, ar.tenant_id,
                  ar.purpose, ar.execution_mode, ar.dataset_version_id,
                  a.principal_id
             from agent_run ar
             join agent a on a.id = ar.agent_id
            where ar.status = 'awaiting_activation'
              and ar.awaiting_activation_since < %s""",
        (served_from,),
    )
    if not parked:
        return []
    try:
        client = temporal_client.get()
    except temporal_client.TemporalUnavailable:
        # Left parked: the next tick tries again once Temporal is back.
        return []

    resumed = []
    for run in parked:
        run_id = str(run["id"])
        claimed = db.execute(
            """update agent_run
                  set status = 'running', awaiting_activation_since = null
                where id = %s and status = 'awaiting_activation'
            returning id""",
            (run_id,),
        )
        if not claimed:
            continue
        # A run can park more than once, and a workflow id is unique among
        # open and recently closed workflows, so each resume gets its own.
        attempt = uuid.uuid4().hex[:8]
        # Minted fresh and written back onto the row, the same reason
        # runs_waiting_on_lease above does both: the token this row was
        # created with may already be stale by the time activation actually
        # resumes it, and a native run's activities re-read this column
        # directly rather than anything this workflow starts with, so the
        # column is what has to carry the live value.
        fresh_secret = task_credential.mint(
            principal=run["principal_id"], task_kind="agent_run",
            task_id=run_id, tenant_id=run["tenant_id"],
        )
        db.execute(
            "update agent_run set run_secret = %s where id = %s",
            (fresh_secret, run_id),
        )
        try:
            if run["execution_mode"] == "sandboxed":
                await client.start_workflow(
                    "AgentRunWorkflow",
                    {
                        "run_id": run_id,
                        "agent_id": str(run["agent_id"]),
                        "agent_version_id": str(run["agent_version_id"]),
                        "principal_id": run["principal_id"],
                        "tenant_id": run["tenant_id"],
                        "purpose": run["purpose"],
                        "dataset_version_id": (str(run["dataset_version_id"])
                                               if run["dataset_version_id"] else None),
                        "execution_mode": run["execution_mode"],
                        "documents": [],
                        "run_secret": fresh_secret,
                    },
                    id=f"agent-run-{run_id[:10]}-{attempt}",
                    task_queue=config.AGENT_RUN_TASK_QUEUE,
                )
            else:
                await client.start_workflow(
                    "AgentRunResumeWorkflow",
                    {"run_id": run_id},
                    id=f"agent-resume-{run_id[:10]}-{attempt}",
                    task_queue=config.AGENT_RUN_TASK_QUEUE,
                )
        except Exception as exc:
            db.execute(
                """update agent_run
                     set status = 'failed', error = %s, ended_at = now()
                   where id = %s and status = 'running'""",
                (f"storage access took effect but the run could not continue: {exc}",
                 run_id),
            )
            continue
        resumed.append(run_id)
    return resumed


@router.post("/agents/{agent_id}/runs", status_code=202)
async def start_run(agent_id: str, body: StartAgentRun,
                    identity: dict = Depends(auth.current_session)) -> dict:
    """Start a real run of this agent, in the background.

    Not synchronous, for the same reason `POST /datasets/{id}/fetch-huggingface`
    is not: a model call can run long under GPU contention with the
    de-identification pipeline (agent/model.py's own comment notes a
    300-second timeout for exactly this), and a slow or stuck run has no
    business holding a browser tab or the process serving every other
    request.

    The version pinned onto the created row is resolved once, here, and
    never re-derived later: whatever is active right now (or the version
    named explicitly) is what this run is defined to have used, even if
    somebody deploys a different version before it finishes.
    """
    agent = _agent(agent_id)

    if identity["tenant_id"] != agent["tenant_id"]:
        raise HTTPException(
            403, {"reasons": [
                f"{identity['id']!r} is in {identity['tenant_id']!r}, not "
                f"{agent['tenant_id']!r}"
            ]}
        )

    version_id = body.agent_version_id
    if not version_id:
        active = db.one(
            "select agent_version_id from agent_active_version where agent_id = %s",
            (agent_id,),
        )
        if not active:
            raise HTTPException(
                400, {"reasons": ["this agent has no deployed version, and none was named"]}
            )
        version_id = str(active["agent_version_id"])
    elif not db.one(
        "select id from agent_version where id = %s and agent_id = %s",
        (version_id, agent_id),
    ):
        raise HTTPException(
            400, {"reasons": ["that version does not belong to this agent"]}
        )

    run_id = str(uuid.uuid4())
    workflow_id = f"agent-run-{run_id[:10]}"
    execution_mode = _execution_mode(version_id)

    # Can this agent read what it is being pointed at? The review queue the
    # de-identification pipeline produces is RAW and an agent reaches PUBLISHED,
    # the ordinary answer is no, and the ordinary remedy is a lease somebody
    # approves. Asking now rather than letting the run start and be refused
    # mid-flight means the person who pressed start finds out immediately, and
    # the request is filed while they are still there to see it.
    if body.dataset_version_id:
        preflight = _access_preflight(agent, body.dataset_version_id)
        if preflight["needs_access_request"]:
            if not body.request_access:
                raise HTTPException(409, {
                    "needs_access_request": True,
                    "reasons": preflight["reasons"],
                    "approver_label": preflight["approver_label"],
                    "department_name": preflight["department_name"],
                    "note": (
                        "This agent may not read that dataset. Starting will ask "
                        "its owning custodian for access on the agent's behalf. "
                        "Send request_access to confirm."
                    ),
                })
            return await _start_waiting_on_access(
                agent, agent_id, run_id, version_id, body, execution_mode,
                identity["id"],
            )

    try:
        client = temporal_client.get()
    except temporal_client.TemporalUnavailable as exc:
        raise HTTPException(
            503, {"reasons": [f"the background job runner is unavailable: {exc}"]}
        ) from exc

    run_secret = task_credential.mint(
        principal=agent["principal_id"], task_kind="agent_run",
        task_id=run_id, tenant_id=agent["tenant_id"],
    )
    db.execute(
        """insert into agent_run
             (id, tenant_id, agent_id, agent_version_id, status, purpose,
              requested_by, dataset_version_id, execution_mode, run_secret)
           values (%s, %s, %s, %s, 'running', %s, %s, %s, %s, %s)""",
        (run_id, agent["tenant_id"], agent_id, version_id, body.purpose,
         identity["id"], body.dataset_version_id, execution_mode, run_secret),
    )

    try:
        await client.start_workflow(
            "AgentRunWorkflow",
            {
                "run_id": run_id,
                "agent_id": agent_id,
                "agent_version_id": version_id,
                "principal_id": agent["principal_id"],
                "tenant_id": agent["tenant_id"],
                "purpose": body.purpose,
                "dataset_version_id": body.dataset_version_id,
                "execution_mode": execution_mode,
                "timeout_seconds": body.timeout_seconds,
                "documents": body.documents,
                "run_secret": run_secret,
            },
            id=workflow_id,
            task_queue=config.AGENT_RUN_TASK_QUEUE,
        )
    except Exception as exc:
        db.execute(
            "update agent_run set status = 'failed', error = %s, ended_at = now() where id = %s",
            (f"could not start the background job: {exc}", run_id),
        )
        raise HTTPException(
            502, {"reasons": [f"could not start the background job: {exc}"]}
        ) from exc

    return {"run_id": run_id, "status": "running"}


@router.post("/agents/runs/{run_id}/approve", status_code=202)
async def approve_run(run_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    """Let a run that stopped at its approval gate carry on.

    Self-approval is rejected by a check constraint on `agent_run`, not by the
    branch below. The branch produces a readable error; the constraint is what
    makes the guarantee hold even if this code is wrong. Same two layers
    `approve_lease` uses, and for the same reason.

    The graph's state is not held in a waiting process while a human decides:
    it sits in the checkpointer's Postgres tables, so a run can be approved
    days after it paused, by somebody who was not there when it started.
    """
    run = db.one("select * from agent_run where id = %s", (run_id,))
    if not run:
        raise HTTPException(404, "no such run")

    if run["status"] != "awaiting_approval":
        raise HTTPException(
            400,
            {"reasons": [f"this run is {run['status']}, so there is nothing waiting "
                         "to be approved"]},
        )

    if identity["tenant_id"] != run["tenant_id"]:
        raise HTTPException(
            403, {"reasons": [
                f"{identity['id']!r} is in {identity['tenant_id']!r}, not "
                f"{run['tenant_id']!r}"
            ]}
        )

    if identity["id"] == run["requested_by"]:
        raise HTTPException(
            403,
            {"reasons": ["a run cannot be approved by the person who started it; "
                         "somebody else has to look at it"]},
        )

    try:
        client = temporal_client.get()
    except temporal_client.TemporalUnavailable as exc:
        raise HTTPException(
            503, {"reasons": [f"the background job runner is unavailable: {exc}"]}
        ) from exc

    try:
        # Back to `running` in the same statement that records the approval:
        # the resume is about to start, and a row left at `awaiting_approval`
        # would tell the console to keep offering an Approve button for work
        # already under way.
        db.execute(
            """update agent_run
                 set approved_by = %s, approved_at = now(), status = 'running'
               where id = %s""",
            (identity["id"], run_id),
        )
    except pg_errors.CheckViolation as exc:
        if "agent_run_no_self_approval" in str(exc):
            raise HTTPException(
                403,
                "a run cannot be approved by the person who started it",
            ) from exc
        raise

    try:
        await client.start_workflow(
            "AgentRunResumeWorkflow",
            {"run_id": run_id},
            id=f"agent-resume-{run_id[:10]}",
            task_queue=config.AGENT_RUN_TASK_QUEUE,
        )
    except Exception as exc:
        db.execute(
            "update agent_run set status = 'failed', error = %s, ended_at = now() where id = %s",
            (f"could not start the background job: {exc}", run_id),
        )
        raise HTTPException(
            502, {"reasons": [f"could not start the background job: {exc}"]}
        ) from exc

    return {"run_id": run_id, "status": "running"}
