"""U83: a principal's identity is checked against the directory, not just claimed.

`POST /credentials` used to evaluate policy against `body.roles` -- whatever
the caller typed in the request, for every principal, human or workload.
`pipeline_action` reads any class (role floor 0), and nothing stopped a
registered-but-ordinary principal from simply claiming that role in the
request body and reading data their real registration never earned them.
The fix mirrors this same endpoint's own existing rule for `tenant`
(authority comes from the directory, not the name in the body): a
registered principal's real roles are read from `directory.roles` and used
instead of whatever the request claims.

A companion fix closes the other half of the same shape of claim: a
*workload* principal with no directory row at all (a name nobody ever
registered) is now refused outright, before any role or run check even asks
what it claims to be -- no legitimate workload is ever unregistered at the
moment it asks for a credential.

A third fix closes what was left open here: `pipeline_action` now needs a
signed task_credential.py token, minted by `POST /action-runs` at the moment
a real action_run starts, the same "spawn-time proof of identity" already
built for `agent_runtime` via `agent_run.run_secret` -- see task_credential.py
and item 60's plan in SESSION_STATUS.md for the design (Type A workloads: a
task with a clear "this begins now" moment; not `training_job`, `model_eval`,
or `annotation_tool`, none of which are real running services yet, so none
of them gained this check).

A fourth, item 61's own follow-up: the token may also be minted by
`POST /pipeline-runs`, task_kind `pipeline_run` rather than `action_run`,
for the one real read (`adopt_version`, worker/activities.py) that has no
`action_run` of its own to bind to -- it reads an existing sealed version
but seals nothing itself, so nothing would ever close an `action_run`
opened for it. `pipeline_run` already opens before that read and closes
once, at the end of the whole pipeline, so its token covers exactly that
gap. Checked the same way as `action_run`'s: honoured within its own
declared `input_versions`, refused outside them.

    docker compose exec -T munitas-api python /verify/v80_credential_role_authority.py
"""

from __future__ import annotations

import sys
import uuid

from common import (ENGINEER, PIPELINE, RESEARCHER, api, check, db,
                    fixture_contract, fixture_tenant, fixture_version,
                    heading, require_api, summary)


def _action_run(tenant: str, contract: str, input_versions: list[str],
                operator: str) -> dict:
    """A real action_run for `operator`, returning the response body
    (which carries the task_credential minted for it), the same pattern
    v2_lineage.py uses to get a real, FK-satisfying dataset_action row."""
    action_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """insert into dataset_action
                 (id, tenant_id, name, source_schema_id, target_schema_id, output_class)
               values (%s, %s, %s, %s, %s, 'UNDER_REVIEW')""",
            (action_id, tenant, f"v80-action-{action_id[:8]}", contract, contract),
        )
    r = api("POST", "/action-runs", json={
        "tenant_id": tenant,
        "action_id": action_id,
        "code_hash": "sha256:v80-codehash",
        "image_digest": "sha256:v80-imagedigest",
        "operator": operator,
        "idempotency_key": f"v80-{uuid.uuid4().hex}",
        "input_versions": input_versions,
        "trigger_kind": "manual",
        "triggered_by": ENGINEER,
    })
    r.raise_for_status()
    return r.json()


def _pipeline_run(tenant: str, input_versions: list[str], principal: str) -> dict:
    """A real pipeline_run for `principal`, returning the response body
    (which carries the task_credential minted for it)."""
    r = api("POST", "/pipeline-runs", json={
        "tenant_id": tenant,
        "dataset": f"v80-pipeline-{uuid.uuid4().hex[:8]}",
        "workflow_id": f"v80-workflow-{uuid.uuid4().hex}",
        "trigger_kind": "manual",
        "triggered_by": ENGINEER,
        "input_versions": input_versions,
        "principal": principal,
    })
    r.raise_for_status()
    return r.json()


def main() -> int:
    require_api()

    tenant = fixture_tenant()
    contract = fixture_contract(tenant)

    heading("U83: a registered principal cannot claim a role it does not hold")

    raw_version = fixture_version(tenant, contract, "RAW")

    # canary-researcher is really registered as notebook_explore (role floor:
    # PUBLISHED). Claiming pipeline_action (role floor: RAW, reads anything)
    # in the request body is exactly the shape of claim this fix refuses to
    # trust.
    escalated = api("POST", "/credentials", json={
        "principal": RESEARCHER,
        "principal_kind": "human",
        "roles": ["pipeline_action"],
        "tenant_id": tenant,
        "dataset_version_id": raw_version["id"],
        "purpose": "claiming a role this principal does not really hold",
    })
    check("claiming pipeline_action for a real notebook_explore principal is refused",
          escalated.status_code == 403, f"HTTP {escalated.status_code}: {escalated.text}")

    heading("U83: the same principal's real, registered role still governs normally")

    published_version = fixture_version(tenant, contract, "PUBLISHED")
    genuine = api("POST", "/credentials", json={
        "principal": RESEARCHER,
        "principal_kind": "human",
        "roles": ["pipeline_action"],  # still claims the wrong role...
        "tenant_id": tenant,
        "dataset_version_id": published_version["id"],
        "purpose": "reading published data, which the real role reaches anyway",
    })
    check("...but the real role (notebook_explore) still reaches PUBLISHED on its own",
          genuine.status_code == 200, f"HTTP {genuine.status_code}: {genuine.text}")

    heading("U83: a workload name with no directory row at all is refused outright")

    fake_principal = f"nobody-{uuid.uuid4().hex[:8]}"
    unregistered = api("POST", "/credentials", json={
        "principal": fake_principal,
        "principal_kind": "workload",
        "roles": ["pipeline_action"],
        "tenant_id": tenant,
        "dataset_version_id": raw_version["id"],
        "purpose": "a name with no directory row at all",
    })
    check("an unregistered workload's claim is refused before role or run checks run",
          unregistered.status_code == 403, f"HTTP {unregistered.status_code}: {unregistered.text}")

    heading("U83: naming a real pipeline_action workload with no proof is now refused")

    # canary-pipeline is a genuinely registered pipeline_action workload.
    # Naming it used to be enough on its own; it no longer is.
    impersonated = api("POST", "/credentials", json={
        "principal": PIPELINE,
        "principal_kind": "workload",
        "roles": ["pipeline_action"],
        "tenant_id": tenant,
        "dataset_version_id": raw_version["id"],
        "purpose": "naming a real service, proving nothing",
    })
    check("naming a real pipeline_action workload with no task credential is refused",
          impersonated.status_code == 403, f"HTTP {impersonated.status_code}: {impersonated.text}")

    heading("U83: a real action_run's own task credential is honoured")

    other_input = fixture_version(tenant, contract, "RAW")
    run = _action_run(tenant, contract,
                      [raw_version["id"], other_input["id"]], PIPELINE)
    token = run["task_credential"]

    proven = api("POST", "/credentials", json={
        "principal": PIPELINE,
        "principal_kind": "workload",
        "roles": ["pipeline_action"],
        "tenant_id": tenant,
        "dataset_version_id": raw_version["id"],
        "purpose": "reading one of this run's own declared inputs",
        "task_credential": token,
    })
    check("a real action_run's own task credential is honoured",
          proven.status_code == 200, f"HTTP {proven.status_code}: {proven.text}")

    heading("U83: the token only reaches this run's own declared inputs")

    outside_scope = fixture_version(tenant, contract, "RAW")
    scoped_out = api("POST", "/credentials", json={
        "principal": PIPELINE,
        "principal_kind": "workload",
        "roles": ["pipeline_action"],
        "tenant_id": tenant,
        "dataset_version_id": outside_scope["id"],
        "purpose": "a version this run was never launched against",
        "task_credential": token,
    })
    check("a version outside the run's own input_versions is refused",
          scoped_out.status_code == 403, f"HTTP {scoped_out.status_code}: {scoped_out.text}")

    heading("U83: a tampered task credential is refused")

    tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
    rejected = api("POST", "/credentials", json={
        "principal": PIPELINE,
        "principal_kind": "workload",
        "roles": ["pipeline_action"],
        "tenant_id": tenant,
        "dataset_version_id": raw_version["id"],
        "purpose": "a token that does not verify",
        "task_credential": tampered,
    })
    check("a tampered task credential is refused",
          rejected.status_code == 403, f"HTTP {rejected.status_code}: {rejected.text}")

    heading("U83: a pipeline_run's own task credential is honoured (item 61's follow-up)")

    # The step this covers (adopt_version) opens no action_run of its own --
    # it reads an existing version but seals nothing -- so this is the other
    # real Type A anchor: the whole pipeline_run, not one step inside it.
    pipeline_other_input = fixture_version(tenant, contract, "RAW")
    prun = _pipeline_run(
        tenant, [raw_version["id"], pipeline_other_input["id"]], PIPELINE,
    )
    prun_token = prun["task_credential"]

    prun_proven = api("POST", "/credentials", json={
        "principal": PIPELINE,
        "principal_kind": "workload",
        "roles": ["pipeline_action"],
        "tenant_id": tenant,
        "dataset_version_id": raw_version["id"],
        "purpose": "adopt_version reading the version this pipeline starts from",
        "task_credential": prun_token,
    })
    check("a real pipeline_run's own task credential is honoured",
          prun_proven.status_code == 200, f"HTTP {prun_proven.status_code}: {prun_proven.text}")

    heading("U83: a pipeline_run's token only reaches its own declared inputs")

    prun_outside = fixture_version(tenant, contract, "RAW")
    prun_scoped_out = api("POST", "/credentials", json={
        "principal": PIPELINE,
        "principal_kind": "workload",
        "roles": ["pipeline_action"],
        "tenant_id": tenant,
        "dataset_version_id": prun_outside["id"],
        "purpose": "a version this pipeline was never started against",
        "task_credential": prun_token,
    })
    check("a version outside the pipeline_run's own input_versions is refused",
          prun_scoped_out.status_code == 403, f"HTTP {prun_scoped_out.status_code}: {prun_scoped_out.text}")

    return summary("U83: credential role authority")


if __name__ == "__main__":
    sys.exit(main())
