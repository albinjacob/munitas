"""U116: a pipeline run and an agent run read with a key of their own, which opens only what the platform allowed and ends with the task.

U114 did this for a derivation's run. A pipeline run (the step that adopts a sealed version) and an agent run read through the same
`POST /credentials`, and were handed the role's key, which opens the whole of the organisation's bucket for reading. They now get a key
of their own. Neither records an end reliably (live: 31 pipeline runs and 16 agent runs still marked running from days ago), and an
agent run may wait a long time for a person and still be alive, so a key is live only while its task has not ended AND has asked within
the task credential's lifetime. This checks, one item at a time, against real storage:

  * a pipeline run's key is its own, reads its input, and is refused on a sibling version, a listing, a write and another organisation;
  * it ends when the run ends;
  * an agent run's key is its own and is limited the same way, and the platform still refuses a version the run was not launched for;
  * it ends when the run halts;
  * a run that has not asked for longer than the lifetime loses its key, and gets one again by asking, which is how a run that waited
    for a person resumes;
  * the controls: the administrator's key and the organisation's role key read the same objects, so each refusal is the key's own.

    docker compose exec -T munitas-api python /verify/v116_pipeline_and_agent_read_keys.py
"""

from __future__ import annotations

import sys
import time
import uuid

sys.path.insert(0, "/app")

from common import (ADMIN, WORKER_HEADERS, api, bearer_for, bucket_for, check, db, fixture_tabular_contract, fixture_tabular_version,  # noqa: E402
                    heading, require_api, s3_client, summary)
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, finish_org, make_org  # noqa: E402


def attempt(call) -> str:
    try:
        call()
        return "allowed"
    except Exception as exc:  # noqa: BLE001
        return getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)


def eventually(call, want, seconds: int = 45):
    deadline, got = time.time() + seconds, None
    while time.time() < deadline:
        got = call()
        if got == want:
            return got
        time.sleep(1)
    return got


def main() -> int:
    require_api()
    from app import config, grants, seaweed, task_credential
    from app import db as app_db

    if app_db.pool.closed:
        app_db.pool.open()
    org, other = make_org(), make_org()
    try:
        contract = fixture_tabular_contract(org.id)
        v1 = fixture_tabular_version(org.id, dataset_name="input", schema_id=contract, klass="PUBLISHED")
        v3 = fixture_tabular_version(org.id, dataset_name="sibling", schema_id=contract, klass="PUBLISHED")
        theirs = fixture_tabular_version(other.id, dataset_name="theirs", schema_id=fixture_tabular_contract(other.id))
        bucket, their_bucket = bucket_for(org.id), bucket_for(other.id)
        pipeline_principal, agent_principal = f"{org.id}-pipeline", f"{org.id}-agent-runtime"
        person = org.people["member"]
        agent_id, agent_version_id = str(uuid.uuid4()), str(uuid.uuid4())
        with db() as conn:
            conn.execute("insert into directory (id, tenant_id, label, kind, roles) values (%s, %s, 'Pipeline', 'workload', '{pipeline_action}')",
                         (pipeline_principal, org.id))
            conn.execute("insert into directory (id, tenant_id, label, kind, roles) values (%s, %s, 'Agent runtime', 'workload', '{agent_runtime}')",
                         (agent_principal, org.id))
            conn.execute("insert into agent (id, tenant_id, name, registered_by, purpose, principal_id) values (%s, %s, 'reader', %s, 'verification', %s)",
                         (agent_id, org.id, org.people["custodian"], agent_principal))
            conn.execute("insert into agent_version (id, tenant_id, agent_id, version, code_hash, source_path, model_id, registered_by, content_hash, sealed) "
                         "values (%s, %s, %s, 1, 'h', 'agent.py', 'm', %s, 'c', true)", (agent_version_id, org.id, agent_id, org.people["custodian"]))

        role_access, role_secret = grants.tenant_role_key("pipeline_action", org.id)
        role_client = s3_client(role_access, role_secret)
        admin_client = s3_client(*ADMIN)

        def read(client, version: dict) -> str:
            return attempt(lambda: client.get_object(Bucket=bucket, Key=version["records_key"])["Body"].read(5))

        def refused(client, version: dict) -> str:
            return "refused" if read(client, version) in ("AccessDenied", "InvalidAccessKeyId") else "open"

        def live_actions(name: str) -> list[str]:
            for i in seaweed.load_identities().get("identities", []):
                if i["name"] == name:
                    return i.get("actions", [])
            return []

        def limits(client, version_in: dict, version_out: dict, folder: str) -> None:
            check("it reads its input", eventually(lambda: read(client, version_in), "allowed") == "allowed")
            check("a sibling version in the same bucket is refused", read(client, version_out) == "AccessDenied")
            check("a listing of its own bucket is refused", attempt(lambda: client.list_objects_v2(Bucket=bucket)) == "AccessDenied")
            check("a write into its own bucket is refused", attempt(lambda: client.put_object(Bucket=bucket, Key=f"{folder}/stray.txt", Body=b"x")) == "AccessDenied")
            check("another organisation's object is refused",
                  attempt(lambda: client.get_object(Bucket=their_bucket, Key=theirs["records_key"])) == "AccessDenied")

        heading("Controls: the refusals below are the key's own")
        check("the administrator's key reads the sibling and the other organisation's object",
              read(admin_client, v3) == "allowed" and attempt(lambda: admin_client.get_object(Bucket=their_bucket, Key=theirs["records_key"])["Body"].read(5)) == "allowed")
        check("the organisation's role key opens nothing of its bucket, so a task's own key replaced no standing reach",
              read(role_client, v3) == "AccessDenied")

        # -------------------------------------------------------------------------------------------------- a pipeline run
        heading("A pipeline run adopts a sealed version")
        started = api("POST", "/pipeline-runs", json={
            "tenant_id": org.id, "dataset": "input", "workflow_id": f"u116-{uuid.uuid4()}", "input_versions": [v1["id"]],
            "principal": pipeline_principal, "triggered_by": person})
        started.raise_for_status()
        pipeline_run, pipeline_token = started.json()["pipeline_run_id"], started.json()["task_credential"]

        def ask_pipeline(version: dict):
            return api("POST", "/credentials", json={
                "principal": pipeline_principal, "principal_kind": "workload", "roles": ["pipeline_action"], "tenant_id": org.id,
                "dataset_version_id": version["id"], "purpose": "adopt a sealed version", "task_credential": pipeline_token})

        got = ask_pipeline(v1)
        check("the platform allows the read", got.status_code == 200, f"{got.status_code} {got.text[:140]}")
        key = got.json()
        check("the key is the run's own, not the role's", key.get("identity") == "task" and key["access_key"] != role_access, f"{key.get('identity')}")
        pipeline_client = s3_client(key["access_key"], key["secret_key"])
        folder = v1["prefix"].rstrip("/")
        limits(pipeline_client, v1, v3, folder)
        check("its list is a Read on that one folder, both forms, and nothing else",
              sorted(live_actions(key["access_key"])) == sorted([f"Read:{bucket}/{folder}/*", f"Read:{bucket}/{folder}"]), str(live_actions(key["access_key"]))[:160])
        check("a version outside the run's inputs is refused by the platform", ask_pipeline(v3).status_code == 403)
        check("a retried request returns the same key", ask_pipeline(v1).json()["access_key"] == key["access_key"])
        ended = api("POST", f"/pipeline-runs/{pipeline_run}/end", json={"status": "succeeded"}, headers=WORKER_HEADERS)
        check("the run ends", ended.status_code == 200, f"{ended.status_code} {ended.text[:100]}")
        check("and its key stops working", eventually(lambda: refused(pipeline_client, v1), "refused") == "refused")

        # ----------------------------------------------------------------------------------------------------- an agent run
        heading("An agent run reads the version it was launched for")

        def new_agent_run() -> dict:
            run_id = str(uuid.uuid4())
            with db() as conn:
                conn.execute("insert into agent_run (id, tenant_id, agent_id, agent_version_id, status, purpose, requested_by, dataset_version_id) "
                             "values (%s, %s, %s, %s, 'running', 'verification', %s, %s)", (run_id, org.id, agent_id, agent_version_id, person, v1["id"]))
            return {"id": run_id, "token": task_credential.mint(principal=agent_principal, task_kind="agent_run", task_id=run_id, tenant_id=org.id)}

        def ask_agent(run: dict, version: dict):
            return api("POST", "/credentials", json={
                "principal": agent_principal, "principal_kind": "workload", "roles": ["agent_runtime"], "tenant_id": org.id,
                "dataset_version_id": version["id"], "purpose": "verification", "agent_run_id": run["id"], "run_secret": run["token"]})

        run = new_agent_run()
        got = ask_agent(run, v1)
        check("the platform allows the read", got.status_code == 200, f"{got.status_code} {got.text[:140]}")
        agent_key = got.json()
        agent_role_access = grants.tenant_role_key("agent_runtime", org.id)[0]
        check("the key is the run's own, not the agent role's",
              agent_key.get("identity") == "task" and agent_key["access_key"] != agent_role_access, f"{agent_key.get('identity')}")
        agent_client = s3_client(agent_key["access_key"], agent_key["secret_key"])
        limits(agent_client, v1, v3, folder)
        check("its list is a Read on that one folder, both forms, and nothing else",
              sorted(live_actions(agent_key["access_key"])) == sorted([f"Read:{bucket}/{folder}/*", f"Read:{bucket}/{folder}"]), str(live_actions(agent_key["access_key"]))[:160])
        check("a version the run was not launched for is refused by the platform", ask_agent(run, v3).status_code == 403)
        check("a retried request returns the same key", ask_agent(run, v1).json()["access_key"] == agent_key["access_key"])
        with db() as conn:
            conn.execute("update agent_run set status = 'halted', ended_at = now() where id = %s", (run["id"],))
        check("when the run halts its key stops working", eventually(lambda: refused(agent_client, v1), "refused") == "refused")

        heading("A run that waits for a person is still alive, and one that goes silent is not")
        waiting = new_agent_run()
        got = ask_agent(waiting, v1)
        waiting_client = s3_client(got.json()["access_key"], got.json()["secret_key"])
        check("a new run's key reads its input", eventually(lambda: read(waiting_client, v1), "allowed") == "allowed")
        # Time cannot be moved back past the last print, which is when the platform last wrote the key into storage: so the run is made to
        # have last asked just under the lifetime ago, and the lifetime then runs out a few seconds from now, as it would in real life.
        with db() as conn:
            conn.execute("update task_read_grant set renewed_at = now() - make_interval(secs => %s) + interval '6 seconds' where agent_run_id = %s",
                         (task_credential.DEFAULT_TTL_SECONDS, waiting["id"]))
        check("once the lifetime has run out without it asking, its key stops working", eventually(lambda: refused(waiting_client, v1), "refused", 60) == "refused")
        again = ask_agent(waiting, v1)
        check("asking again gives the same key", again.status_code == 200 and again.json()["access_key"] == got.json()["access_key"], f"{again.status_code}")
        check("and the key works again, which is how a run that waited resumes", eventually(lambda: read(waiting_client, v1), "allowed") == "allowed")
        check("still not the sibling", read(waiting_client, v3) == "AccessDenied")

        heading("The roles' own keys open nothing")
        check("the organisation's pipeline role key cannot read the input", read(role_client, v1) == "AccessDenied")
        agent_role_client = s3_client(*grants.tenant_role_key("agent_runtime", org.id))
        check("nor can the organisation's agent role key, whatever the platform has allowed the agent runs", eventually(lambda: read(agent_role_client, v1), "AccessDenied") == "AccessDenied")
        check("and the agent role's key carries no entry at all", live_actions(f"agent_runtime~{org.id}") == [], str(live_actions(f"agent_runtime~{org.id}"))[:120])
        no_task = api("POST", "/credentials", json={
            "principal": agent_principal, "principal_kind": "workload", "roles": ["agent_runtime"], "tenant_id": org.id,
            "dataset_version_id": v1["id"], "purpose": "verification", "agent_run_id": run["id"], "run_secret": run["token"]})
        check("an agent whose run has ended is refused, and told why, not handed a key that fails at storage",
              no_task.status_code == 403, f"{no_task.status_code} {no_task.text[:140]}")
    finally:
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        for o in (org, other):
            finish_org(o, priya, ravi)
            drop_org(o)
    return summary("U116")


if __name__ == "__main__":
    sys.exit(main())
