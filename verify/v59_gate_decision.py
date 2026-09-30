"""U59: the pipeline no longer promotes, and a qualified person does.

Promotion is the one act that widens who can see clinical data, and it
cannot be undone. It used to happen inside `promote_gate` with nobody
looking at the evidence. This proves the machine now only recommends,
that the evidence reaches the person who decides, that the identifiers
never reach MLflow, and that the decision is refused for everybody it
should be refused for.

Runs on the host, not inside the munitas-api container, because two
checks call `worker.activities.record_gate_decision` directly and
`worker/` is not mounted there. Calling the activity is the only honest
way to prove the pipeline stopped promoting: grepping for a removed
function name proves nothing about behaviour.

    .venv\\Scripts\\python.exe verify\\v59_gate_decision.py

Needs PG_DSN, VERIFY_KRATOS and S3_ENDPOINT pointing at the published
localhost ports, since verify/common.py defaults them to the container
hostnames, which do not resolve outside the Compose network.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg

from common import (ADMIN, CANARY, ENGINEER, REVIEWER, api, bearer_for, check,
                    db, fixture_contract, fixture_tenant, fixture_version,
                    heading, require_api, s3_client, summary)
from worker.activities import record_gate_decision
from worker.scoring import ScoreCard, pseudonymised, score_record

CUSTODIAN = "canary-custodian"

# Enough to make the etag comparison mean something. A version with no
# objects under its prefix compares two empty sets, which passes without
# testing anything, and that is exactly the shape of a check nobody
# notices is broken.
OBJECTS = {"part-0.json": b'{"record_id": "r-1"}', "part-1.json": b'{"record_id": "r-2"}'}

SCORE = {
    "score_card_id": "u59-score-card",
    "metrics": {
        "ground_truth_spans": 100,
        "destroyed_by_asr": 10,
        "recall_effective": 0.99,
        "leaks_total": 1,
        "leaks_direct": 0,
        "leaks_quasi": 1,
    },
}

LEAKS = [{
    "record_id": "r-0041", "span_start": 512, "span_end": 526,
    "entity": "ADDRESS", "identifier": "14 Marlborough Road",
    "left_in_the_clear": "Marlborough", "coverage": 0.6, "direct": False,
}]

made: list[str] = []


def snapshot(s3, bucket: str, prefix: str) -> dict[str, str]:
    out: dict[str, str] = {}
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents", []):
            out[obj["Key"]] = obj["ETag"]
        if not page.get("IsTruncated"):
            return out
        token = page.get("NextContinuationToken")


def bucket_for(tenant_id: str, backend: str) -> str:
    with db() as conn:
        row = conn.execute(
            "select bucket from tenant_storage_provision "
            "where tenant_id = %s and backend = %s", (tenant_id, backend)
        ).fetchone()
    if not row:
        raise RuntimeError(f"{tenant_id} has no {backend} bucket recorded in tenant_storage_provision")
    return row["bucket"]


def new_decision(version_id: str, to_class: str = "OPEN_FOR_ANNOTATION",
                 triggered_by: str = ENGINEER) -> dict:
    out = record_gate_decision({
        "version_id": version_id,
        "to_class": to_class,
        "score_card": SCORE,
        "leak_detail": LEAKS,
        "triggered_by": triggered_by,
    })
    made.append(out["gate_decision_id"])
    return out


def row_for(decision_id: str) -> dict:
    with db() as conn:
        return conn.execute(
            "select * from gate_decision where id = %s", (decision_id,)
        ).fetchone()


def class_of(version_id: str) -> str:
    with db() as conn:
        return conn.execute(
            "select current_class from version_class where dataset_version_id = %s",
            (version_id,),
        ).fetchone()["current_class"]


def teardown() -> None:
    with db() as conn:
        for decision_id in made:
            # gate_leak cascades from the decision, which is itself one of
            # the things this proves.
            conn.execute("delete from gate_decision where id = %s", (decision_id,))
        conn.execute("delete from action_run where operator = 'u59-correlation'")
        conn.execute("delete from pipeline_run where workflow_id like 'u59-%'")


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    schema_id = fixture_contract(CANARY)

    try:
        heading("U59: the pipeline records a decision instead of promoting")

        version = fixture_version(CANARY, schema_id)
        version_id = version["id"]
        before_class = class_of(version_id)

        out = new_decision(version_id)
        row = row_for(out["gate_decision_id"])

        check("the activity reports promoting nothing",
              out["promoted"] is False, f"promoted={out['promoted']}")
        check("a decision is recorded, waiting for a person",
              row["state"] == "pending", row["state"])
        check("the version's class is what it was before the gate ran",
              class_of(version_id) == before_class,
              f"{before_class} then {class_of(version_id)}")

        heading("U59: the evidence lands under the tenant that owns it")

        with db() as conn:
            leaks = conn.execute(
                "select gl.*, gd.tenant_id from gate_leak gl "
                "join gate_decision gd on gd.id = gl.gate_decision_id "
                "where gl.gate_decision_id = %s", (out["gate_decision_id"],)
            ).fetchall()
        check("the identifiers are recorded for the reviewer to judge",
              len(leaks) == len(LEAKS), f"{len(leaks)} rows")
        check("and carry the tenant that owns the data",
              all(leak["tenant_id"] == CANARY for leak in leaks),
              ", ".join(sorted({leak["tenant_id"] for leak in leaks})))

        heading("U59: what reaches MLflow carries no identifier")

        card = ScoreCard()
        score_record(card, "Grainne Church attended", "Grain Church attended",
                     [{"entity": "PERSON", "start": 0, "end": 14,
                       "text": "Grainne Church"}], [], record_id="r-0041")
        safe = pseudonymised(card.leak_detail)
        blob = repr(safe)
        check("the MLflow form drops the identifier and the residue",
              all("identifier" not in e and "left_in_the_clear" not in e for e in safe),
              ", ".join(sorted(safe[0])))
        check("and the name appears nowhere in it at all",
              "Grainne" not in blob and "Grain Church" not in blob,
              "checked the whole serialized form, not only the keys")
        check("while the coordinate that names the leak survives",
              safe[0]["record_id"] == "r-0041" and safe[0]["span_start"] == 0,
              f"{safe[0]['record_id']} at {safe[0]['span_start']}")

        heading("U59: who may decide, and who may not")

        decision_id = out["gate_decision_id"]

        anonymous = api("POST", f"/gate-decisions/{decision_id}/promote",
                        json={"reason": "no session at all"})
        check("a decision with no session is refused",
              anonymous.status_code == 401, f"HTTP {anonymous.status_code}")

        wrong_role = api("POST", f"/gate-decisions/{decision_id}/promote",
                         headers=bearer_for(CUSTODIAN),
                         json={"reason": "the custodian trying"})
        check("a custodian cannot clear a de-identification gate",
              wrong_role.status_code == 403, f"HTTP {wrong_role.status_code}")
        check("and the refusal says it is about the role",
              "no role that may decide" in wrong_role.text, wrong_role.text[:120])

        own_run = api("POST", f"/gate-decisions/{decision_id}/promote",
                      headers=bearer_for(ENGINEER),
                      json={"reason": "the person who ran it trying"})
        check("whoever started the run cannot clear it either",
              own_run.status_code == 403, f"HTTP {own_run.status_code}")

        heading("U59: and the database refuses it even with the API bypassed")

        with db() as conn:
            try:
                conn.execute(
                    "update gate_decision set state = 'promoted', "
                    "decided_by = %s, decided_at = now() where id = %s",
                    (ENGINEER, decision_id))
                refused, detail = False, "the update was accepted"
            except psycopg.errors.CheckViolation as exc:
                refused = "gate_decision_no_self_decision" in str(exc)
                detail = "gate_decision_no_self_decision"
        check("a self-decision written straight to the database is refused",
              refused, detail)

        heading("U59: a reviewer decides, and no bytes move")

        s3 = s3_client(*ADMIN)
        bucket = bucket_for(CANARY, version.get("storage_backend", "seaweedfs"))
        for name, body in OBJECTS.items():
            s3.put_object(Bucket=bucket, Key=f"{version['storage_prefix']}/{name}",
                          Body=body)
        before = snapshot(s3, bucket, version["storage_prefix"])

        promoted = api("POST", f"/gate-decisions/{decision_id}/promote",
                       headers=bearer_for(REVIEWER),
                       json={"reason": "one quasi-identifier, partially redacted, acceptable"})
        check("the reviewer's decision is accepted",
              promoted.status_code == 201, f"HTTP {promoted.status_code}")

        after = snapshot(s3, bucket, version["storage_prefix"])
        check("the objects under the version are the same set afterwards",
              set(before) == set(after), f"{len(before)} before, {len(after)} after")
        check("and every etag is identical, so nothing was rewritten",
              before == after, "compared etag by etag")
        check("the check had objects to compare, so it means something",
              len(before) == len(OBJECTS), f"{len(before)} objects")

        with db() as conn:
            trans = conn.execute(
                "select decided_by, decided_by_kind, gate_evidence, to_class "
                "from class_transition where dataset_version_id = %s "
                "order by at desc limit 1", (version_id,)).fetchone()
        check("the transition names the reviewer, from their session",
              trans["decided_by"] == REVIEWER, trans["decided_by"])
        check("and records that a human decided it",
              trans["decided_by_kind"] == "human", trans["decided_by_kind"])
        check("the evidence keeps both the machine's view and the person's",
              "machine_recommendation" in trans["gate_evidence"]
              and "decided_because" in trans["gate_evidence"],
              ", ".join(sorted(trans["gate_evidence"])))
        check("the class actually widened",
              class_of(version_id) == "OPEN_FOR_ANNOTATION", class_of(version_id))

        again = api("POST", f"/gate-decisions/{decision_id}/refuse",
                    headers=bearer_for(REVIEWER), json={"reason": "changed my mind"})
        check("a decision already made cannot be made again",
              again.status_code == 409, f"HTTP {again.status_code}")

        heading("U59: a refusal leaves the data exactly where it was")

        second = fixture_version(CANARY, schema_id)
        refusal = new_decision(second["id"])
        held = api("POST", f"/gate-decisions/{refusal['gate_decision_id']}/refuse",
                   headers=bearer_for(REVIEWER),
                   json={"reason": "a full name survived in the clear"})
        check("the refusal is accepted", held.status_code == 201,
              f"HTTP {held.status_code}")
        refused_row = row_for(refusal["gate_decision_id"])
        check("it records who refused and why",
              refused_row["decided_by"] == REVIEWER
              and bool(refused_row["decision_reason"]),
              f"{refused_row['decided_by']}: {refused_row['decision_reason']}")
        check("and the class is untouched",
              class_of(second["id"]) == "RAW", class_of(second["id"]))

        heading("U59: one run's steps are findable together")

        with db() as conn:
            action = conn.execute(
                "select id from dataset_action where tenant_id = %s limit 1",
                (CANARY,)).fetchone()
            if not action:
                action_id = str(uuid.uuid4())
                conn.execute(
                    "insert into dataset_action (id, tenant_id, name, output_class) "
                    "values (%s, %s, 'u59-action', 'UNDER_REVIEW')",
                    (action_id, CANARY))
            else:
                action_id = str(action["id"])

        mine = api("POST", "/pipeline-runs", json={
            "tenant_id": CANARY, "dataset": "u59", "workflow_id": "u59-run-a",
            "triggered_by": ENGINEER}).json()["pipeline_run_id"]
        other = api("POST", "/pipeline-runs", json={
            "tenant_id": CANARY, "dataset": "u59", "workflow_id": "u59-run-b",
            "triggered_by": ENGINEER}).json()["pipeline_run_id"]

        replay = api("POST", "/pipeline-runs", json={
            "tenant_id": CANARY, "dataset": "u59", "workflow_id": "u59-run-a",
            "triggered_by": ENGINEER}).json()
        check("a replayed workflow resolves to the same run, not a second one",
              replay["pipeline_run_id"] == mine and replay["created"] is False,
              f"created={replay['created']}")

        for label, run in (("step-one", mine), ("step-two", mine), ("elsewhere", other)):
            api("POST", "/action-runs", json={
                "tenant_id": CANARY, "action_id": action_id,
                "code_hash": "sha256:u59", "image_digest": "sha256:u59",
                "operator": "u59-correlation",
                "idempotency_key": f"u59-{label}-{uuid.uuid4().hex[:8]}",
                "trigger_kind": "manual", "triggered_by": ENGINEER,
                "pipeline_run_id": run,
            })

        with db() as conn:
            counted = conn.execute(
                "select count(*) as n from action_run where pipeline_run_id = %s",
                (mine,)).fetchone()["n"]
            leaked_across = conn.execute(
                "select count(*) as n from action_run where pipeline_run_id = %s",
                (other,)).fetchone()["n"]
        check("every step of one run carries that run's id",
              counted == 2, f"{counted} steps")
        check("and a different run's steps are not among them",
              leaked_across == 1, f"{leaked_across} step elsewhere")

        third = fixture_version(CANARY, schema_id)
        correlated = record_gate_decision({
            "version_id": third["id"], "to_class": "OPEN_FOR_ANNOTATION", "score_card": SCORE,
            "leak_detail": LEAKS, "triggered_by": ENGINEER,
            "pipeline_run_id": mine,
        })
        made.append(correlated["gate_decision_id"])
        detail = api("GET", f"/gate-decisions/{correlated['gate_decision_id']}",
                    headers=bearer_for(ENGINEER)).json()
        check("and the decision hands back that run's steps in one read",
              len(detail["steps"]) == 2, f"{len(detail['steps'])} steps")

        return summary("U59")
    finally:
        teardown()


if __name__ == "__main__":
    sys.exit(main())
