"""U153: a run on development data is never put in front of a reviewer to release.

A development sample only points at real files and ends within days, and what a development run makes is deleted with it. The pipeline's decision
step used to make a pending "release this" decision for it anyway, which nobody could judge and which would have moved a sample's class. This runs the
decision step itself (the activity, with the SDK's own test environment) one case at a time:

  * on a development sample, no decision is made, and the answer says why;
  * on files declared synthetic, the same;
  * on an ordinary version, a pending decision is made, as before.

It writes to the verification organisation, with versions made here, and takes its rows away.

    .venv\\Scripts\\python.exe verify\\v149_development_gate.py        (needs MUNITAS_PG_DSN, as the host checks do)
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from temporalio.testing import ActivityEnvironment  # noqa: E402

from common import CANARY, check, db, fixture_contract, fixture_tenant, fixture_version, heading, summary  # noqa: E402
from worker.activities import record_simple_gate_decision  # noqa: E402


def decisions_of(version: str) -> int:
    with db() as conn:
        return conn.execute("select count(*) as n from gate_decision where dataset_version_id = %s", (version,)).fetchone()["n"]


def params(version: str) -> dict:
    return {"version_id": version, "to_class": "OPEN_FOR_ANNOTATION", "recommendation": "not_measured", "recommendation_reason": "u153 fixture",
            "triggered_by": "canary-engineer", "pipeline_run_id": None}


def open_run_as(source: str, pipeline_version_id: str | None, who: str) -> dict:
    """A pipeline run that was started by `who` on `source`: the row the decision step reads, made here without a worker."""
    run = str(uuid.uuid4())
    with db() as conn:
        conn.execute("insert into pipeline_run (id, tenant_id, dataset, workflow_id, triggered_by, source_version_id, pipeline_version_id) values (%s, %s, 'u153', %s, %s, %s, %s)",
                     (run, CANARY, f"u153-{uuid.uuid4()}", who, source, pipeline_version_id))
    return {"id": run}


def main() -> int:
    fixture_tenant(CANARY)
    contract = fixture_contract(CANARY)
    ordinary, sample, synthetic, old_sample, source = (fixture_version(CANARY, contract)["id"] for _ in range(5))
    env = ActivityEnvironment()
    try:
        with db() as conn:
            conn.execute("insert into development_output (version_id, tenant_id, kind, expires_at) values (%s, %s, 'sample', now() + interval '1 day')", (sample, CANARY))
            # A sample made before expiry was recorded has only its row in the sample table, and that is enough to know it.
            conn.execute("""insert into development_sample (id, tenant_id, source_version_id, sample_version_id, principal, kind, purpose, requested_count, days, status)
                            values (%s, %s, %s, %s, 'canary-engineer', 'develop', 'u153 invented', 1, 1, 'ended')""", (str(uuid.uuid4()), CANARY, source, old_sample))
            conn.execute("insert into development_data_declaration (version_id, tenant_id, declared_by, note) values (%s, %s, 'canary-custodian', 'u153 invented')", (synthetic, CANARY))

        heading("A development sample")
        out = env.run(record_simple_gate_decision, params(sample))
        check("the step makes no decision, and says it was not applicable", out["gate_decision_id"] is None and out.get("not_applicable") is True and out["promoted"] is False, str(out))
        check("and says why, in words", "development data" in out["reason"] and "never released" in out["reason"], out["reason"])
        check("nothing is waiting for anyone", decisions_of(sample) == 0)

        heading("A sample with no expiry record, as the earliest ones are")
        out = env.run(record_simple_gate_decision, params(old_sample))
        check("no decision is made for it either", out["gate_decision_id"] is None and out.get("not_applicable") is True and decisions_of(old_sample) == 0, str(out))

        heading("Files declared synthetic")
        out = env.run(record_simple_gate_decision, params(synthetic))
        check("the step makes no decision either", out["gate_decision_id"] is None and out.get("not_applicable") is True, str(out))
        check("nothing is waiting for anyone", decisions_of(synthetic) == 0)

        heading("An ordinary version")
        out = env.run(record_simple_gate_decision, params(ordinary))
        check("a pending decision is made, as before", out["gate_decision_id"] is not None and out["promoted"] is False and "not_applicable" not in out, str(out))
        with db() as conn:
            row = conn.execute("select state, recommendation, triggered_by from gate_decision where id = %s", (out["gate_decision_id"],)).fetchone()
        check("it waits for a person, and records who started the run", row is not None and row["state"] == "pending" and row["triggered_by"] == "canary-engineer", str(row))

        heading("A run that made files: the decision is about what it made, not what it was given")
        given, made_a, made_b = (fixture_version(CANARY, contract)["id"] for _ in range(3))
        run = open_run_as(given, None, "canary-engineer")
        with db() as conn:
            for number, version in enumerate((made_a, made_b)):
                conn.execute("""insert into step_output (id, tenant_id, pipeline_run_id, step, name, storage_key, status, bytes, sha256, version_id)
                                values (%s, %s, %s, 'inspect', %s, %s, 'sealed', 1, 'x', %s)""", (str(uuid.uuid4()), CANARY, run["id"], f"u153-{number}.bin", f"u153/{number}", version))
        params_run = {**params(given), "pipeline_run_id": run["id"]}
        out = env.run(record_simple_gate_decision, params_run)
        with db() as conn:
            rows = conn.execute("select dataset_version_id from gate_decision where pipeline_run_id = %s", (run["id"],)).fetchall()
        released = sorted(str(r["dataset_version_id"]) for r in rows)
        check("a decision is made for each version the run made", released == sorted([made_a, made_b]), str(released))
        check("and none for the files it was given, which nobody reviewed", given not in released)
        check("the step says which versions it is about", sorted(out["released_version_ids"]) == sorted([made_a, made_b]) and len(out["gate_decision_ids"]) == 2, str(out))
        with db() as conn:
            run2 = open_run_as(given, None, "canary-engineer")
        out = env.run(record_simple_gate_decision, {**params(given), "pipeline_run_id": run2["id"]})
        check("a run that made nothing is about the version it was given, as before", out["released_version_ids"] == [given] and decisions_of(given) == 1, str(out))
    finally:
        with db() as conn:
            conn.execute("delete from gate_decision where recommendation_reason = 'u153 fixture' and tenant_id = %s", (CANARY,))
            conn.execute("delete from step_output where name like 'u153-%%' and tenant_id = %s", (CANARY,))
            conn.execute("delete from development_sample where purpose = 'u153 invented'")
    return summary("v149")


if __name__ == "__main__":
    sys.exit(main())
