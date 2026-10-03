"""U117: a writer is handed a key for its one output folder, and not the pipeline role's key.

`POST /write-credentials` used to return the pipeline role's key with one folder's Write added, so every writer also held the role's
bucket-wide Read, List and Tagging. It now returns the task's own key, which opens that folder for writing and nothing else, for as long
as the task is running and keeps asking. This checks, one item at a time, against real storage:

  * the key is the task's, not the role's, and the role's key can no longer write the folder at all;
  * it writes a small object and a large one (a multipart upload, which a Write-only key must be able to complete);
  * it is refused a read, a listing, a write into a sibling folder, and anything of another organisation, and the administrator's key
    is not refused, so each refusal is the key's own;
  * a second task's key is a different one and cannot write the first task's folder;
  * the key ends when the task ends, and a task that is already over is refused a new one;
  * a Hugging Face fetch job, which only writes, is handled the same way.

    docker compose exec -T munitas-api python /verify/v117_writer_keys.py
"""

from __future__ import annotations

import sys
import tempfile
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
    from app import grants, seaweed, task_credential
    from app import db as app_db

    if app_db.pool.closed:
        app_db.pool.open()
    org, other = make_org(), make_org()
    try:
        contract = fixture_tabular_contract(org.id)
        sibling = fixture_tabular_version(org.id, dataset_name="sibling", schema_id=contract)
        theirs = fixture_tabular_version(other.id, dataset_name="theirs", schema_id=fixture_tabular_contract(other.id))
        bucket, their_bucket = bucket_for(org.id), bucket_for(other.id)
        workload, person = f"{org.id}-pipeline", org.people["member"]
        action_id = str(uuid.uuid4())
        with db() as conn:
            conn.execute("insert into directory (id, tenant_id, label, kind, roles) values (%s, %s, 'Pipeline', 'workload', '{pipeline_action}')",
                         (workload, org.id))
            conn.execute("insert into dataset_action (id, tenant_id, name) values (%s, %s, 'write')", (action_id, org.id))
        role_access, role_secret = grants.tenant_role_key("pipeline_action", org.id)
        role_client, admin_client = s3_client(role_access, role_secret), s3_client(*ADMIN)

        def dataset(name: str) -> str:
            made = api("POST", "/datasets", json={"tenant_id": org.id, "name": name})
            made.raise_for_status()
            return made.json()["id"]

        def new_run() -> dict:
            made = api("POST", "/action-runs", json={
                "tenant_id": org.id, "action_id": action_id, "code_hash": uuid.uuid4().hex, "image_digest": "sha256:verify",
                "operator": workload, "idempotency_key": f"u117-{uuid.uuid4()}", "input_versions": [],
                "params": {}, "trigger_kind": "manual", "triggered_by": person})
            made.raise_for_status()
            return {"id": made.json()["id"], "token": made.json()["task_credential"]}

        def ask_write(token: str, dataset_id: str):
            return api("POST", "/write-credentials", json={
                "principal": workload, "principal_kind": "workload", "roles": ["pipeline_action"], "tenant_id": org.id,
                "dataset_id": dataset_id, "purpose": "u117 output", "task_credential": token})

        def live_actions(name: str) -> list[str]:
            for i in seaweed.load_identities().get("identities", []):
                if i["name"] == name:
                    return i.get("actions", [])
            return []

        def put(client, key: str, body: bytes = b"{}") -> str:
            return attempt(lambda: client.put_object(Bucket=bucket, Key=key, Body=body))

        def stopped(client, key: str) -> str:
            return "stopped" if put(client, key) in ("AccessDenied", "InvalidAccessKeyId") else "open"

        heading("A pipeline step asks for a place to write")
        run, data_id = new_run(), dataset("written")
        got = ask_write(run["token"], data_id)
        check("the platform grants it", got.status_code == 200, f"{got.status_code} {got.text[:140]}")
        key, folder = got.json(), got.json()["prefix"].rstrip("/")
        check("the key is the task's own: the answer says so, and it is not the organisation's role key",
              key.get("identity") == "task" and key["access_key"] != role_access and key["access_key"].startswith("run-"), f"{key.get('identity')}")
        writer = s3_client(key["access_key"], key["secret_key"])
        check("it writes a small object into its folder", eventually(lambda: put(writer, f"{folder}/small.json"), "allowed") == "allowed")

        heading("A large object, which is a multipart upload")
        big = tempfile.NamedTemporaryFile(delete=False)
        big.write(b"x" * (20 * 1024 * 1024))
        big.close()
        check("a 20 MB upload completes with a key that can only write", attempt(lambda: writer.upload_file(big.name, bucket, f"{folder}/large.bin")) == "allowed")
        check("and the object is there (read with the administrator's key)",
              attempt(lambda: admin_client.head_object(Bucket=bucket, Key=f"{folder}/large.bin")) == "allowed")

        heading("What the writer's key does not open")
        check("its list is a Write on that one folder, both forms, and nothing else",
              sorted(live_actions(key["access_key"])) == sorted([f"Write:{bucket}/{folder}/*", f"Write:{bucket}/{folder}"]), str(live_actions(key["access_key"]))[:200])
        check("it cannot read back what it wrote", attempt(lambda: writer.get_object(Bucket=bucket, Key=f"{folder}/small.json")) == "AccessDenied")
        check("it cannot read a sibling version", attempt(lambda: writer.get_object(Bucket=bucket, Key=sibling["records_key"])) == "AccessDenied")
        check("it cannot list its own bucket", attempt(lambda: writer.list_objects_v2(Bucket=bucket)) == "AccessDenied")
        check("it cannot write into a sibling folder", put(writer, f"{sibling['prefix'].rstrip('/')}/stray.json") == "AccessDenied")
        check("it cannot write into another organisation's bucket",
              attempt(lambda: writer.put_object(Bucket=their_bucket, Key="stray.json", Body=b"{}")) == "AccessDenied")
        check("control: the administrator's key reads the sibling, so these refusals are the key's own",
              attempt(lambda: admin_client.get_object(Bucket=bucket, Key=sibling["records_key"])["Body"].read(5)) == "allowed")
        check("the organisation's role key can no longer write that folder", put(role_client, f"{folder}/by-the-role.json") in ("AccessDenied", "InvalidAccessKeyId"))
        role_writes = [a for a in live_actions(f"pipeline_action~{org.id}") if a.startswith("Write:")]
        check("and the role's key carries no Write entry at all", role_writes == [], str(role_writes)[:160])

        heading("A second task")
        run2, data2 = new_run(), dataset("written-two")
        got2 = ask_write(run2["token"], data2)
        writer2 = s3_client(got2.json()["access_key"], got2.json()["secret_key"])
        check("it has a key of its own", got2.status_code == 200 and got2.json()["access_key"] != key["access_key"])
        check("it writes its own folder", eventually(lambda: put(writer2, f"{got2.json()['prefix'].rstrip('/')}/a.json"), "allowed") == "allowed")
        check("and cannot write the first task's folder", put(writer2, f"{folder}/intruder.json") == "AccessDenied")
        check("a retried request returns the same key", ask_write(run2["token"], data2).json()["access_key"] == got2.json()["access_key"])

        heading("The key ends with the task")
        fixture_tabular_version(org.id, dataset_name="written", schema_id=contract, produced_by_run=run["id"], dataset_id=data_id)
        with db() as conn:
            status = conn.execute("select status from action_run where id = %s", (run["id"],)).fetchone()["status"]
        check("sealing the output ends the run", status == "succeeded", status)
        check("and its key stops working", eventually(lambda: stopped(writer, f"{folder}/late.json"), "stopped") == "stopped")
        check("a task that is over is refused a new key", ask_write(run["token"], data_id).status_code == 403)

        heading("A Hugging Face fetch job, which only writes")
        job_id, job_data = str(uuid.uuid4()), dataset("fetched")
        with db() as conn:
            conn.execute("insert into huggingface_fetch_job (id, dataset_id, repo_id, revision, fetched_by, status, workflow_id) "
                         "values (%s, %s, 'u117/repo', 'main', %s, 'running', %s)", (job_id, job_data, person, f"u117-{uuid.uuid4()}"))
        job_token = task_credential.mint(principal=workload, task_kind="huggingface_fetch_job", task_id=job_id, tenant_id=org.id)
        got3 = ask_write(job_token, job_data)
        check("the job is granted a key of its own", got3.status_code == 200 and got3.json().get("identity") == "task", f"{got3.status_code} {got3.text[:120]}")
        job_writer = s3_client(got3.json()["access_key"], got3.json()["secret_key"])
        job_folder = got3.json()["prefix"].rstrip("/")
        check("it writes its folder", eventually(lambda: put(job_writer, f"{job_folder}/file.bin"), "allowed") == "allowed")
        check("and not a sibling folder", put(job_writer, f"{sibling['prefix'].rstrip('/')}/stray.json") == "AccessDenied")
        with db() as conn:
            conn.execute("update huggingface_fetch_job set status = 'failed', ended_at = now() where id = %s", (job_id,))
        check("when the job ends its key stops working", eventually(lambda: stopped(job_writer, f"{job_folder}/late.bin"), "stopped") == "stopped")
    finally:
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        for o in (org, other):
            finish_org(o, priya, ravi)
            drop_org(o)
    return summary("U117")


if __name__ == "__main__":
    sys.exit(main())
