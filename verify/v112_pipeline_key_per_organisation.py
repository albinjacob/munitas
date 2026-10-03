"""U112: no storage key opens more than one organisation's data, and no role's own key opens any.

The pipeline used to hold one key, set in configuration, the same for every organisation, and the policy gave it Read and List on
every bucket. Anyone who held it, and the worker did, could read every organisation's data. It became one key per organisation, and
then, once every task had a key of its own (U114, U116, U117), the role's key was left with nothing to open. This checks the property
that matters, on the live permissions and with real requests, and not a list of keys:

  * the invariant: in the permissions storage actually holds, no identity but the platform administrator's names the bucket of more
    than one organisation, whatever its role;
  * the invariant: no identity holds a bucket-wide verb except the administrator's, an organisation's own upload key, and the listing a
    table job needs to write a table (names only, for the life of the job);
  * the old shared key is gone, and an organisation's pipeline key opens nothing at all;
  * the endpoint that used to hand a worker the organisation's key no longer exists.

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
        bucket_wide = sorted((i["name"], a) for i in live for a in i.get("actions", [])
                             if ":" in a and "/" not in a.split(":", 1)[1] and not (
                                 i["name"] == grants.ADMIN_IDENTITY or i["name"].startswith("ingest-")
                                 or (i["name"].startswith("tj-") and a.startswith("List:"))))
        check("no identity holds a bucket-wide verb except the administrator's, an organisation's upload key, and a table job's listing",
              not bucket_wide, str(bucket_wide[:3]))
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
        time.sleep(3)  # storage reads its permissions a moment after they are printed
        check("an organisation's pipeline key opens nothing of its own bucket: not an object, not a listing, not a write",
              attempt(lambda: mine.get_object(Bucket=bucket_one, Key=first["records_key"])) == "AccessDenied"
              and attempt(lambda: mine.list_objects_v2(Bucket=bucket_one)) == "AccessDenied"
              and attempt(lambda: mine.put_object(Bucket=bucket_one, Key=f"{one.id}/stray.txt", Body=b"x")) == "AccessDenied")
        check("nor anything of another organisation's",
              attempt(lambda: mine.get_object(Bucket=bucket_two, Key=theirs["records_key"])) == "AccessDenied"
              and attempt(lambda: mine.list_objects_v2(Bucket=bucket_two)) == "AccessDenied"
              and attempt(lambda: mine.put_object(Bucket=bucket_two, Key="stray.txt", Body=b"x")) == "AccessDenied")
        check("control: the platform administrator's key reads both objects, so these refusals are the key's own",
              attempt(lambda: s3_client(*ADMIN).get_object(Bucket=bucket_two, Key=theirs["records_key"])["Body"].read(5)) == "allowed"
              and attempt(lambda: s3_client(*ADMIN).get_object(Bucket=bucket_one, Key=first["records_key"])["Body"].read(5)) == "allowed")

        heading("The endpoint that used to give a worker the organisation's key")
        check("it no longer exists", httpx.post(f"{API}/storage-keys/pipeline", params={"tenant_id": one.id}, headers=WORKER_HEADERS, timeout=60).status_code in (404, 405))

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
