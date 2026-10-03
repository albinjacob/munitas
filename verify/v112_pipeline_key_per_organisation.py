"""U112: no storage key opens more than one organisation's data, and the pipeline's is one key per organisation.

The pipeline used to hold one key, set in configuration, the same for every organisation, and the policy gave it Read and List on
every bucket. Anyone who held it, and the worker did, could read every organisation's data, and the "scoped" credentials the
platform handed out were that same key with one folder added. It is now one key per organisation (`scope: own_tenant` in the
policy), each opening that organisation's bucket and what the register justifies in it. This checks the property that matters, on
the live permissions and with real requests, and not a list of keys:

  * the invariant: in the permissions storage actually holds, no identity but the platform administrator's names the bucket of
    more than one organisation, whatever its role;
  * the old shared key is gone, and an organisation's key reads its own bucket and nothing of another's;
  * the endpoint that hands a worker its key gives a task the key of its own organisation only.

    docker compose exec -T munitas-api python /verify/v112_pipeline_key_per_organisation.py
"""

from __future__ import annotations

import sys
import uuid

sys.path.insert(0, "/app")

import httpx  # noqa: E402

from common import ADMIN, API, WORKER_HEADERS, bearer_for, bucket_for, check, fixture_tabular_contract, fixture_tabular_version, heading, require_api, s3_client, summary  # noqa: E402
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, finish_org, make_org  # noqa: E402


def main() -> int:
    require_api()
    from app import config, grants, seaweed, task_credential
    from app import db as app_db

    if app_db.pool.closed:
        app_db.pool.open()
    one, two = make_org(), make_org()
    try:
        first = fixture_tabular_version(one.id, dataset_name="mine", schema_id=fixture_tabular_contract(one.id))
        theirs = fixture_tabular_version(two.id, dataset_name="theirs", schema_id=fixture_tabular_contract(two.id))
        bucket_one, bucket_two = bucket_for(one.id), bucket_for(two.id)
        grants.reconcile(trigger="manual")

        heading("The invariant: no storage identity reaches two organisations")
        owner = {b: t for t, bs in grants._tenant_buckets().items() for b in bs}
        live = seaweed.load_identities().get("identities", [])
        wide = []
        for identity in live:
            if identity["name"] == grants.ADMIN_IDENTITY:
                continue
            reached = {owner[a.split(":", 1)[1].split("/", 1)[0]] for a in identity.get("actions", [])
                       if ":" in a and a.split(":", 1)[1].split("/", 1)[0] in owner}
            if len(reached) > 1:
                wide.append((identity["name"], sorted(reached)[:3]))
        check(f"of {len(live)} identities in the live permissions, none but the administrator's names more than one organisation's bucket",
              not wide, str(wide[:2]))
        check("the pipeline has no key shared by every organisation: no identity is the bare role",
              "pipeline_action" not in {i["name"] for i in live}, sorted(i["name"] for i in live if i["name"].startswith("pipeline"))[:3])
        names = {i["name"] for i in live}
        check("it has one for each organisation, including these two", {f"pipeline_action~{one.id}", f"pipeline_action~{two.id}"} <= names)

        heading("The old shared key, and an organisation's own")
        old_key, old_secret = config.ROLE_STORAGE_KEYS["pipeline_action"]

        def attempt(call) -> str:
            try:
                call()
                return "allowed"
            except Exception as exc:  # noqa: BLE001
                return getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)

        shared = s3_client(old_key, old_secret)
        check("the old shared key opens no bucket: it no longer exists",
              attempt(lambda: shared.list_objects_v2(Bucket=bucket_one)) == "InvalidAccessKeyId"
              and attempt(lambda: shared.get_object(Bucket=bucket_two, Key=theirs["records_key"])) == "InvalidAccessKeyId")
        access, secret = grants.tenant_role_key("pipeline_action", one.id)
        mine = s3_client(access, secret)
        import time
        for _ in range(20):  # storage reads its permissions a moment after they are printed
            if attempt(lambda: mine.get_object(Bucket=bucket_one, Key=first["records_key"])["Body"].read(5)) == "allowed":
                break
            time.sleep(1)
        check("an organisation's key reads its own bucket", attempt(lambda: mine.get_object(Bucket=bucket_one, Key=first["records_key"])["Body"].read(5)) == "allowed"
              and attempt(lambda: mine.list_objects_v2(Bucket=bucket_one)) == "allowed")
        check("and reads nothing of another organisation's: not an object, not a listing",
              attempt(lambda: mine.get_object(Bucket=bucket_two, Key=theirs["records_key"])) == "AccessDenied"
              and attempt(lambda: mine.list_objects_v2(Bucket=bucket_two)) == "AccessDenied")
        check("and writes nowhere without a write grant, its own bucket included",
              attempt(lambda: mine.put_object(Bucket=bucket_one, Key=f"{one.id}/stray.txt", Body=b"x")) == "AccessDenied"
              and attempt(lambda: mine.put_object(Bucket=bucket_two, Key="stray.txt", Body=b"x")) == "AccessDenied")
        check("control: the platform administrator's key reads the other organisation's object, so these refusals are the key's own",
              attempt(lambda: s3_client(*ADMIN).get_object(Bucket=bucket_two, Key=theirs["records_key"])["Body"].read(5)) == "allowed")

        heading("The endpoint that gives a worker its key")

        def ask(headers: dict, tenant: str | None = None):
            return httpx.post(f"{API}/storage-keys/pipeline", params={"tenant_id": tenant} if tenant else None, headers=headers, timeout=60)

        def token(kind: str, tenant: str) -> dict:
            return {"X-Task-Credential": task_credential.mint(principal="x", task_kind=kind, task_id=str(uuid.uuid4()), tenant_id=tenant)}

        check("no credential is refused", ask({}).status_code == 403)
        check("the worker token without an organisation is refused", ask(WORKER_HEADERS).status_code == 403)
        check("a made-up task credential is refused", ask({"X-Task-Credential": "not.a.credential"}).status_code == 403)
        r = ask(token("pipeline_run", one.id))
        check("a pipeline task is given the key of its own organisation", r.status_code == 200
              and (r.json()["access_key"], r.json()["secret_key"]) == (access, secret) and r.json()["bucket"] == bucket_one, f"{r.status_code}")
        check("an action run is given it too", ask(token("action_run", one.id)).status_code == 200)
        check("a task cannot ask for another organisation's key", ask(token("pipeline_run", one.id), two.id).status_code == 403)
        check("a table job's credential is not a pipeline task's", ask(token("table_write_job", one.id)).status_code == 403)
        check("nor is an agent run's", ask(token("agent_run", one.id)).status_code == 403)
        r = ask(WORKER_HEADERS, two.id)
        check("the worker token with an organisation named is given that organisation's key (what the worker could always do)",
              r.status_code == 200 and r.json()["access_key"] == grants.tenant_role_key("pipeline_action", two.id)[0])
        check("an organisation with no storage has no key to give", ask(WORKER_HEADERS, f"nobody-{uuid.uuid4().hex[:8]}").status_code == 404)

        heading("A production start refuses the secrets this repository publishes")
        import os
        import subprocess

        def start(**extra) -> subprocess.CompletedProcess:
            env = {**os.environ, "MUNITAS_ENV": "production", **extra}
            return subprocess.run([sys.executable, "-c", "import app.config"], capture_output=True, text=True, env=env, cwd="/app")

        refused = start(S3_ADMIN_SECRET="munitas-admin-secret", MUNITAS_WORKER_TOKEN="dev-worker-token-not-for-production")
        check("with the published storage secret and worker token it will not start, and names them", refused.returncode != 0
              and "S3_ADMIN_SECRET" in refused.stderr and "MUNITAS_WORKER_TOKEN" in refused.stderr, refused.stderr.strip()[-160:])
        check("and it does not print either value", "munitas-admin-secret" not in refused.stderr.split("RuntimeError:")[-1]
              and "dev-worker-token-not-for-production" not in refused.stderr.split("RuntimeError:")[-1])
        check("with no worker token at all it will not start either", start(S3_ADMIN_SECRET="a-secret-of-my-own", MUNITAS_WORKER_TOKEN="").returncode != 0)
        check("with a secret of its own for each it starts", start(S3_ADMIN_SECRET="a-secret-of-my-own", MUNITAS_WORKER_TOKEN="a-token-of-my-own").returncode == 0)
        check("and outside production the published values are accepted, as they are on a laptop",
              subprocess.run([sys.executable, "-c", "import app.config"], capture_output=True, text=True, cwd="/app",
                             env={**os.environ, "MUNITAS_ENV": "development"}).returncode == 0)
    finally:
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        for org in (one, two):
            finish_org(org, priya, ravi)
            drop_org(org)
    return summary("U112")


if __name__ == "__main__":
    sys.exit(main())
