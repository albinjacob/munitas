"""U115: a real derivation, run by the real worker and its staging containers, reads its input with the run's own key.

U114 proves the key by calling the credential API directly. This proves the other half, that the worker's real staging code works
with that key: it submits a query as a person, confirms it, lets the worker run it, and then checks what the platform recorded.

Host only, and not in run_all.py, for the same reason as U110: it needs the derivation worker and the sandbox worker running, and
it waits for a real query to finish.

    docker compose exec -T munitas-api python /verify/v115_derivation_end_to_end_key.py
"""

from __future__ import annotations

import sys
import time
import uuid

from common import CANARY, RESEARCHER, api, bearer_for, check, db, fixture_tabular_version, heading, require_api, summary


def main() -> int:
    require_api()
    heading("Submit a query over a published table, as a person")
    source = fixture_tabular_version(CANARY, klass="PUBLISHED")
    target = f"u115-{uuid.uuid4().hex[:10]}"
    alias = "src"
    drafted = api("POST", "/derivations", json={
        "inputs": [{"dataset": source["dataset_name"], "version": source["version"], "alias": alias}],
        "sql": f"select record_id, count from {alias}", "target_name": target,
        "primary_key": ["record_id"], "purpose": "end to end check of the run key"}, headers=bearer_for(RESEARCHER))
    check("the draft is accepted", drafted.status_code == 201, f"{drafted.status_code} {drafted.text[:200]}")
    if drafted.status_code != 201:
        return summary("U115")
    derivation_id = drafted.json()["id"]
    confirmed = api("POST", f"/derivations/{derivation_id}/confirm", json={"sensitivities": {"record_id": "quasi"}},
                    headers=bearer_for(RESEARCHER))
    check("confirming starts the run", confirmed.status_code == 202, f"{confirmed.status_code} {confirmed.text[:200]}")

    heading("The real worker runs it")
    deadline, state = time.time() + 240, {}
    while time.time() < deadline:
        state = api("GET", f"/derivations/{derivation_id}", headers=bearer_for(RESEARCHER)).json()
        if state.get("status") in ("succeeded", "failed", "expired"):
            break
        time.sleep(3)
    check("the derivation succeeds", state.get("status") == "succeeded", f"{state.get('status')} {state.get('error')}")

    heading("What the platform recorded about the key")
    with db() as conn:
        row = conn.execute("select action_run_id::text as run from derivation where id = %s", (derivation_id,)).fetchone()
        run = row["run"] if row else None
        grant = conn.execute("select count(*) as n from task_read_grant where action_run_id = %s and dataset_version_id = %s",
                             (run, source["id"])).fetchone()["n"]
        key = conn.execute("select identity_name from storage_identity where action_run_id = %s", (run,)).fetchone()
        status = conn.execute("select status, ended_at from action_run where id = %s", (run,)).fetchone()
    check("the run exists", bool(run))
    check("the platform recorded the read of the one input for that run", grant == 1, f"{grant}")
    check("the run was given a key of its own", bool(key) and key["identity_name"].startswith("run-"), str(key))
    check("the run is succeeded, with an end time", bool(status) and status["status"] == "succeeded" and status["ended_at"] is not None, str(status))
    return summary("U115")


if __name__ == "__main__":
    sys.exit(main())
