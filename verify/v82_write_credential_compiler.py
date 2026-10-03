"""U85: writing as pipeline_action now needs the same proof reading already does.

`pipeline_action`'s storage identity used to hold a standing, unconditional
`Write` grant across every bucket (`access.rego`'s old `every_bucket:
[Read, Write, List, Tagging]`) -- the one workload role, and the one verb,
still trusted on nothing but its static key. Every other role's storage
access was already request-justified; reading as `pipeline_action` was made
so by item 65 (`task_credential.py`); this closes the write half. See
docs/internal/design/write-credential-rationale.md for the full "why", and
SESSION_STATUS.md's "PENDING NEXT" entry (now closed) for the build plan
this script proves.

The design: `access.rego` narrows `pipeline_action` to `[Read, List,
Tagging]`; a new `write_grant` table records, per real task, the one prefix
it legitimately opened; `POST /write-credentials` mints a key scoped to
exactly that prefix, after proving the caller is a real, registered
`pipeline_action` workload presenting a `task_credential.py` token for a real
`action_run` or `pipeline_run` -- the same proof `/credentials` already
requires for reads, verified by the same shared helper
(`main.py`'s `_resolve_pipeline_task`).

Three things checked here, matching the build plan's own list: a real task's
write-credential is honoured for its own next version's prefix; a
fabricated/tampered token is refused; and the old static key can no longer
write outside a write_grant-covered prefix *at the S3 layer*, not merely
refused by the API -- the same "enforced by object storage, not just by the
API" standard v4 already holds reads to.

    docker compose exec -T munitas-api python /verify/v82_write_credential_compiler.py
"""

from __future__ import annotations

import os
import sys
import time
import uuid

from common import (PIPELINE, api, bucket_for, check, fixture_contract,
                    fixture_tenant, heading, require_api, s3_client, summary)

# The static role key pipeline_action has always used. No longer sufficient
# on its own for Write, which is exactly the claim this script proves; still
# used directly against S3 below to prove that at the object-storage layer,
# not only at the API.
#
# `or`, not a get() default: this runs inside munitas-api, where Compose
# passes S3_PIPELINE_KEY/SECRET through as an empty string when unset in
# .env, never absent -- os.environ.get(name, default) only falls back on
# absence, so it silently returned "" here and reused config.py's own exact
# comment about this gotcha would have been better than rediscovering it.
PIPELINE_S3_KEY = os.environ.get("S3_PIPELINE_KEY") or "pipeline-action"
PIPELINE_S3_SECRET = os.environ.get("S3_PIPELINE_SECRET") or "pipeline-action-secret"


def _action_run(tenant: str, contract: str, operator: str) -> dict:
    """A real action_run for `operator`, the same pattern v80's own helper
    uses, returning the response body (which carries the task_credential
    minted for it)."""
    action_id = str(uuid.uuid4())
    from common import db
    with db() as conn:
        conn.execute(
            """insert into dataset_action
                 (id, tenant_id, name, source_schema_id, target_schema_id, output_class)
               values (%s, %s, %s, %s, %s, 'UNDER_REVIEW')""",
            (action_id, tenant, f"v82-action-{action_id[:8]}", contract, contract),
        )
    r = api("POST", "/action-runs", json={
        "tenant_id": tenant,
        "action_id": action_id,
        "code_hash": "sha256:v82-codehash",
        "image_digest": "sha256:v82-imagedigest",
        "operator": operator,
        "idempotency_key": f"v82-{uuid.uuid4().hex}",
        "input_versions": [],
        "trigger_kind": "manual",
        "triggered_by": "canary-engineer",
    })
    r.raise_for_status()
    return r.json()


def main() -> int:
    require_api()

    tenant = fixture_tenant()
    contract = fixture_contract(tenant)
    bucket = bucket_for(tenant)

    heading("U85: neither the old shared key nor the organisation's own key can write outside a granted prefix")

    def refused(client, key: str) -> str:
        try:
            client.put_object(Bucket=bucket, Key=key, Body=b"{}")
            return "written"
        except Exception as exc:  # noqa: BLE001 - the refusal itself is the assertion
            return "AccessDenied" if "AccessDenied" in str(exc) or "403" in str(exc) else type(exc).__name__ + ": " + str(exc)[:80]

    sys.path.insert(0, "/app")
    from app import grants as app_grants

    stray_key = f"{tenant}/v82-stray/{uuid.uuid4().hex}.json"
    shared = refused(s3_client(PIPELINE_S3_KEY, PIPELINE_S3_SECRET), stray_key)
    check("the old shared pipeline-action key no longer exists, so it cannot write at all",
          shared != "written" and "InvalidAccessKeyId" in shared, shared)
    own_key, own_secret = app_grants.tenant_role_key("pipeline_action", tenant)
    own = refused(s3_client(own_key, own_secret), stray_key)
    check("the organisation's own pipeline key cannot PutObject with no write_grant", own == "AccessDenied", own)

    heading("U85: a dataset with no registered pipeline_action workload cannot ask")

    r = api("POST", "/datasets", json={"tenant_id": tenant, "name": f"v82-{uuid.uuid4().hex[:8]}"})
    r.raise_for_status()
    dataset_id = r.json()["id"]

    not_pipeline = api("POST", "/write-credentials", json={
        "principal": "canary-researcher",
        "principal_kind": "human",
        "roles": ["notebook_explore"],
        "tenant_id": tenant,
        "dataset_id": dataset_id,
        "purpose": "not a pipeline_action workload at all",
        "task_credential": "whatever-this-is-ignored",
    })
    check("a non-pipeline_action principal is refused before the token is even read",
          not_pipeline.status_code == 403,
          f"HTTP {not_pipeline.status_code}: {not_pipeline.text}")

    heading("U85: naming a real pipeline_action workload with no proof is refused")

    unproven = api("POST", "/write-credentials", json={
        "principal": PIPELINE,
        "principal_kind": "workload",
        "roles": ["pipeline_action"],
        "tenant_id": tenant,
        "dataset_id": dataset_id,
        "purpose": "naming a real workload, proving nothing",
        "task_credential": "",
    })
    check("a registered pipeline_action workload with no task credential is refused",
          unproven.status_code == 403, f"HTTP {unproven.status_code}: {unproven.text}")

    heading("U85: a fabricated or tampered task credential is refused")

    run = _action_run(tenant, contract, PIPELINE)
    token = run["task_credential"]
    tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")

    rejected = api("POST", "/write-credentials", json={
        "principal": PIPELINE,
        "principal_kind": "workload",
        "roles": ["pipeline_action"],
        "tenant_id": tenant,
        "dataset_id": dataset_id,
        "purpose": "a token that does not verify",
        "task_credential": tampered,
    })
    check("a tampered task credential is refused",
          rejected.status_code == 403, f"HTTP {rejected.status_code}: {rejected.text}")

    heading("U85: a real action_run's own task credential is honoured")

    granted = api("POST", "/write-credentials", json={
        "principal": PIPELINE,
        "principal_kind": "workload",
        "roles": ["pipeline_action"],
        "tenant_id": tenant,
        "dataset_id": dataset_id,
        "purpose": "this run's own sealed output",
        "task_credential": token,
    })
    check("a real action_run's own task credential is honoured",
          granted.status_code == 200, f"HTTP {granted.status_code}: {granted.text}")
    body = granted.json() if granted.status_code == 200 else {}

    heading("U85: the minted credential actually writes, at the S3 layer, into the "
            "exact prefix the platform computed")

    if body:
        scoped_client = s3_client(body["access_key"], body["secret_key"],
                                  body.get("session_token"))
        own_key = f"{body['prefix']}/records.json"
        wrote = False
        try:
            scoped_client.put_object(Bucket=body["bucket"], Key=own_key, Body=b"[]")
            wrote = True
        except Exception as exc:  # noqa: BLE001
            wrote = False
        check("the scoped credential writes into its own granted prefix",
              wrote, "" if wrote else "PutObject into the granted prefix failed")

        heading("U85: the same credential is refused outside its own granted prefix")

        outside_key = f"{tenant}/v82-elsewhere/{uuid.uuid4().hex}.json"
        blocked = False
        try:
            scoped_client.put_object(Bucket=body["bucket"], Key=outside_key, Body=b"{}")
        except Exception as exc:  # noqa: BLE001
            blocked = "AccessDenied" in str(exc) or "403" in str(exc)
        check("the scoped credential cannot write outside its own granted prefix",
              blocked, "" if blocked else "the write outside the granted prefix succeeded")

        heading("U85: the credential is the organisation's own key, and ends when the task stops asking")

        from app import grants as app_grants

        mine = app_grants.tenant_role_key("pipeline_action", tenant)
        check("the key handed out is this organisation's pipeline key, not a key shared by every organisation",
              (body["access_key"], body["secret_key"]) == mine, body["access_key"])
        # A grant lasts as long as its task keeps asking. Here the task is taken to have stopped asking a day and an hour ago.
        from common import db as database
        with database() as conn:
            conn.execute("update write_grant set renewed_at = now() - interval '25 hours' where tenant_id = %s", (tenant,))
        from app import db as app_db
        if app_db.pool.closed:
            app_db.pool.open()
        app_grants.reconcile(trigger="manual")
        time.sleep(4)
        still = True
        try:
            scoped_client.put_object(Bucket=body["bucket"], Key=f"{body['prefix']}/after-the-grant-ended.json", Body=b"{}")
        except Exception as exc:  # noqa: BLE001
            still = False if ("AccessDenied" in str(exc) or "403" in str(exc)) else exc
        check("once the task has stopped asking, the key can no longer write into the folder it was granted", still is False, repr(still)[:120])
        again = api("POST", "/write-credentials", json={
            "principal": PIPELINE, "principal_kind": "workload", "roles": ["pipeline_action"], "tenant_id": tenant,
            "dataset_id": dataset_id, "purpose": "this run's own sealed output", "task_credential": token})
        check("a task that asks again is granted again", again.status_code == 200, f"HTTP {again.status_code}")
        time.sleep(4)
        wrote_again = True
        try:
            scoped_client.put_object(Bucket=body["bucket"], Key=f"{body['prefix']}/after-it-asked-again.json", Body=b"{}")
        except Exception as exc:  # noqa: BLE001
            wrote_again = False
        check("and the same key writes into the folder again", wrote_again)
    else:
        check("the minted credential actually writes into the granted prefix",
              False, "no credential was granted, so this could not be checked")
        check("the same credential is refused outside its own granted prefix",
              False, "no credential was granted, so this could not be checked")

    heading("U85: a retried request for the same task and prefix does not "
            "duplicate the register")

    retried = api("POST", "/write-credentials", json={
        "principal": PIPELINE,
        "principal_kind": "workload",
        "roles": ["pipeline_action"],
        "tenant_id": tenant,
        "dataset_id": dataset_id,
        "purpose": "the same run asking again",
        "task_credential": token,
    })
    check("a second request for the same task and prefix is honoured, not refused",
          retried.status_code == 200, f"HTTP {retried.status_code}: {retried.text}")
    if retried.status_code == 200:
        from common import db
        with db() as conn:
            n = conn.execute(
                "select count(*) as n from write_grant "
                "where tenant_id = %s and task_id = %s and storage_prefix = %s",
                (tenant, run["id"], retried.json()["prefix"]),
            ).fetchone()["n"]
        check("the register holds exactly one row for this task and prefix, not two",
              n == 1, f"found {n} rows")

    return summary("U85: write-credential compiler")


if __name__ == "__main__":
    sys.exit(main())
