"""U114: a derivation run reads with a key of its own, which opens only the inputs it was allowed and ends with the run.

A derivation used to be handed the pipeline role's key for its organisation, which opens the whole of that organisation's bucket for
reading: storage did not repeat the decision the policy had made for one version. It now gets a key of its own, compiled from the
reads the platform allowed that run (`task_read_grant`) while the run is running and inside its task credential's lifetime. This runs
a real derivation run against real storage and checks, one item at a time:

  * the key is the run's, not the role's, and its list is a Read on each allowed input's folder and nothing else;
  * it reads an allowed input and is refused (AccessDenied) on a sibling version in the same bucket, on a listing, on a write, and on
    another organisation's bucket, while the administrator's key reads the same objects, so each refusal is the key's own;
  * it opens exactly what was granted so far: an input the run has not yet been allowed is closed until the platform allows it;
  * a version outside the run's inputs is refused by the platform and added to nothing;
  * a retried request returns the same key and makes no second one;
  * the key ends with the run: when the derivation fails, when the run seals its output, and when it runs past its limit, and the
    limit also marks the run failed instead of leaving it running for ever;
  * a task that is not a derivation still gets the role's key (this change is limited to derivations, and says so).

    docker compose exec -T munitas-api python /verify/v114_task_read_keys.py
"""

from __future__ import annotations

import json
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


def eventually(call, want, seconds: int = 45) -> str:
    """Storage reads its permissions a moment after they are printed, and the platform's loop prints within seconds."""
    deadline, got = time.time() + seconds, None
    while time.time() < deadline:
        got = call()
        if got == want:
            return got
        time.sleep(1)
    return got


def main() -> int:
    require_api()
    from app import grants, seaweed
    from app import db as app_db

    if app_db.pool.closed:
        app_db.pool.open()
    org, other = make_org(), make_org()
    try:
        contract = fixture_tabular_contract(org.id)
        v1 = fixture_tabular_version(org.id, dataset_name="input-one", schema_id=contract)
        v2 = fixture_tabular_version(org.id, dataset_name="input-two", schema_id=contract)
        v3 = fixture_tabular_version(org.id, dataset_name="not-an-input", schema_id=contract)
        theirs = fixture_tabular_version(other.id, dataset_name="theirs", schema_id=fixture_tabular_contract(other.id))
        bucket, their_bucket = bucket_for(org.id), bucket_for(other.id)
        workload = f"{org.id}-pipeline"
        person = org.people["member"]
        action_id = str(uuid.uuid4())
        with db() as conn:
            conn.execute("insert into directory (id, tenant_id, label, kind, roles) values (%s, %s, 'Pipeline', 'workload', '{pipeline_action}')",
                         (workload, org.id))
            conn.execute("insert into dataset_action (id, tenant_id, name) values (%s, %s, 'derive')", (action_id, org.id))

        def new_run(inputs: list[str], derivation: bool = True) -> dict:
            made = api("POST", "/action-runs", json={
                "tenant_id": org.id, "action_id": action_id, "code_hash": uuid.uuid4().hex, "image_digest": "sha256:verify",
                "operator": workload, "idempotency_key": f"u114-{uuid.uuid4()}", "input_versions": inputs,
                "params": {}, "trigger_kind": "manual", "triggered_by": person})
            made.raise_for_status()
            run = {"id": made.json()["id"], "token": made.json()["task_credential"]}
            if derivation:
                run["derivation_id"] = str(uuid.uuid4())
                with db() as conn:
                    conn.execute(
                        "insert into derivation (id, tenant_id, submitted_by, status, sql, target_name, purpose, inputs, primary_key, "
                        "proposed_fields, output_class, action_run_id) values (%s, %s, %s, 'running', 'select 1', 'out', 'verification', "
                        "'[]'::jsonb, '{record_id}', '[]'::jsonb, 'RAW', %s)",
                        (run["derivation_id"], org.id, person, run["id"]))
            return run

        def ask(run: dict, version: dict):
            return api("POST", "/credentials", json={
                "principal": workload, "principal_kind": "workload", "roles": ["pipeline_action"], "tenant_id": org.id,
                "dataset_version_id": version["id"], "purpose": "verification", "task_credential": run["token"]})

        def client_of(answer) -> object:
            return s3_client(answer.json()["access_key"], answer.json()["secret_key"])

        def live_actions(name: str) -> list[str] | None:
            for i in seaweed.load_identities().get("identities", []):
                if i["name"] == name:
                    return i.get("actions", [])
            return None

        def read(client, version: dict) -> str:
            return attempt(lambda: client.get_object(Bucket=bucket, Key=version["records_key"])["Body"].read(5))

        heading("A derivation run asks to read its first input")
        run = new_run([v1["id"], v2["id"]])
        first = ask(run, v1)
        check("the platform allows it", first.status_code == 200, f"{first.status_code} {first.text[:160]}")
        key = first.json()
        role_access = grants.tenant_role_key("pipeline_action", org.id)[0]
        check("the key is the run's own: the answer says so, and it is not the organisation's role key",
              key.get("identity") == "task" and key["access_key"] != role_access, f"{key.get('identity')} {key['access_key']}")
        mine = client_of(first)
        check("the run's key reads its first input", eventually(lambda: read(mine, v1), "allowed") == "allowed")
        actions = live_actions(key["access_key"]) or []
        folder = v1["prefix"].rstrip("/")
        check("its list in storage is a Read on that one folder, both forms, and nothing else",
              sorted(actions) == sorted([f"Read:{bucket}/{folder}/*", f"Read:{bucket}/{folder}"]), str(actions)[:200])
        check("there is no bucket-wide entry and no List, Write or Tagging in it",
              all("/" in a and a.startswith("Read:") for a in actions))

        heading("What that key does not open")
        check("a sibling version in the same bucket is refused", read(mine, v3) == "AccessDenied")
        check("so is the run's second input, which the platform has not yet allowed it", read(mine, v2) == "AccessDenied")
        check("a listing of its own bucket is refused", attempt(lambda: mine.list_objects_v2(Bucket=bucket)) == "AccessDenied")
        check("a write into its own bucket is refused", attempt(lambda: mine.put_object(Bucket=bucket, Key=f"{folder}/stray.txt", Body=b"x")) == "AccessDenied")
        check("another organisation's object is refused", attempt(lambda: mine.get_object(Bucket=their_bucket, Key=theirs["records_key"])) == "AccessDenied")
        check("control: the administrator's key reads those same objects, so each refusal is the key's own",
              all(attempt(lambda b=b, k=k: s3_client(*ADMIN).get_object(Bucket=b, Key=k)["Body"].read(5)) == "allowed"
                  for b, k in ((bucket, v2["records_key"]), (bucket, v3["records_key"]), (their_bucket, theirs["records_key"]))))
        control = s3_client(grants.tenant_role_key("pipeline_action", org.id)[0], grants.tenant_role_key("pipeline_action", org.id)[1])
        check("the organisation's role key opens nothing of its bucket, so the run's key replaced no standing reach",
              read(control, v3) == "AccessDenied")

        heading("The platform's own decisions still stand")
        check("a version that is not one of the run's inputs is refused by the platform", ask(run, v3).status_code == 403)
        check("and nothing was added to the key for it", read(mine, v3) == "AccessDenied" and len(live_actions(key["access_key"]) or []) == 2)
        second = ask(run, v2)
        check("the second input, once the platform allows it, comes with the same key", second.status_code == 200
              and second.json()["access_key"] == key["access_key"], f"{second.status_code}")
        check("and then the key opens it", eventually(lambda: read(mine, v2), "allowed") == "allowed")
        check("and still not the sibling", read(mine, v3) == "AccessDenied")
        again = ask(run, v1)
        check("a retried request returns the same key", again.status_code == 200 and again.json()["access_key"] == key["access_key"])
        with db() as conn:
            made = conn.execute("select count(*) as n from storage_identity where action_run_id = %s", (run["id"],)).fetchone()["n"]
            grants_n = conn.execute("select count(*) as n from task_read_grant where action_run_id = %s", (run["id"],)).fetchone()["n"]
        check("and it made one key and two grants, not more", made == 1 and grants_n == 2, f"{made} keys, {grants_n} grants")

        heading("The key ends with the run: a failed derivation")
        failed = api("POST", f"/derivations/{run['derivation_id']}/fail", json={"reason": "the query ran out of memory"}, headers=WORKER_HEADERS)
        check("the platform accepts the failure", failed.status_code == 200, f"{failed.status_code}")
        with db() as conn:
            row = conn.execute("select status, failure_reason, ended_at from action_run where id = %s", (run["id"],)).fetchone()
        check("the run is failed, with the reason and an end time (it used to stay running)",
              row["status"] == "failed" and row["failure_reason"] == "the query ran out of memory" and row["ended_at"] is not None, str(row))
        refused = eventually(lambda: "refused" if read(mine, v1) in ("AccessDenied", "InvalidAccessKeyId") else "open", "refused")
        check("and the key stops working within the activator's next passes", refused == "refused", str(refused))

        heading("The key ends with the run: a run that seals its output")
        done = new_run([v1["id"]])
        got = ask(done, v1)
        done_client = client_of(got)
        check("a second run has a key of its own, not the first run's", got.status_code == 200 and got.json()["access_key"] != key["access_key"])
        check("it reads its input", eventually(lambda: read(done_client, v1), "allowed") == "allowed")
        fixture_tabular_version(org.id, dataset_name="made-by-run", schema_id=contract, produced_by_run=done["id"])
        with db() as conn:
            status = conn.execute("select status from action_run where id = %s", (done["id"],)).fetchone()["status"]
        check("sealing its output marks the run succeeded", status == "succeeded", status)
        check("and its key stops working", eventually(lambda: "refused" if read(done_client, v1) in ("AccessDenied", "InvalidAccessKeyId") else "open", "refused") == "refused")

        heading("The key ends with the run: a run that never finishes")
        stuck = new_run([v1["id"]])
        got = ask(stuck, v1)
        stuck_client = client_of(got)
        check("a third run's key reads its input", eventually(lambda: read(stuck_client, v1), "allowed") == "allowed")
        with db() as conn:
            conn.execute("update action_run set started_at = now() - interval '7 hours' where id = %s", (stuck["id"],))
        check("past the limit the platform ends the run as failed and says why",
              eventually(lambda: _status(stuck["id"]), "failed") == "failed")
        with db() as conn:
            row = conn.execute("select failure_reason from action_run where id = %s", (stuck["id"],)).fetchone()
        check("the reason names the limit", "6 hours" in (row["failure_reason"] or ""), str(row))
        check("and its key stops working", eventually(lambda: "refused" if read(stuck_client, v1) in ("AccessDenied", "InvalidAccessKeyId") else "open", "refused") == "refused")

        heading("Every running action run has a key of its own, and a caller with no running task has none")
        plain = new_run([v1["id"]], derivation=False)
        answer = ask(plain, v1)
        check("an action run that is not a derivation also gets a key of its own, not the organisation's",
              answer.status_code == 200 and answer.json().get("identity") == "task" and answer.json()["access_key"] != role_access,
              f"{answer.status_code} {answer.json().get('identity') if answer.status_code == 200 else ''}")
        over = new_run([v1["id"]], derivation=False)
        with db() as conn:
            conn.execute("update action_run set status = 'failed', ended_at = now() where id = %s", (over["id"],))
        late = ask(over, v1)
        check("a run that is already over is refused, and told why, not handed a key that fails at storage",
              late.status_code == 403 and "no longer running" in str(late.json()), f"{late.status_code} {late.text[:140]}")
    finally:
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        for o in (org, other):
            finish_org(o, priya, ravi)
            drop_org(o)
    return summary("U114")


def _status(run_id: str) -> str:
    with db() as conn:
        return conn.execute("select status from action_run where id = %s", (run_id,)).fetchone()["status"]


if __name__ == "__main__":
    sys.exit(main())
