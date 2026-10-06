"""U131: a gate can say "not measured", which is not a pass and not a fail.

A de-identification run on real recordings has no answer key, so nothing measured whether it worked. Until now the gate could only say `pass`
or `fail`, and the run said `fail`, which reads as "measured and found wanting". `not_measured` says what happened: nobody measured this
batch, so a person has to judge it. This checks, one item at a time:

  * the database accepts the three values and refuses any other, whoever writes the row;
  * a pipeline definition may name `not_measured` as its gate's recommendation, a reference to a step's output, or `pass` or `fail`, and a
    literal that is none of those is refused when the pipeline is registered, with the three values listed, and not minutes later in a run;
  * the same row still starts pending, and a person still decides it, whatever it recommends.

    docker compose exec -T munitas-api python /verify/v131_gate_not_measured.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")

import psycopg  # noqa: E402

from common import CANARY, check, db, fixture_contract, fixture_tenant, fixture_version, heading, require_api, summary  # noqa: E402


def insert_gate(conn, version_id: str, recommendation: str) -> None:
    conn.execute(
        """insert into gate_decision (id, tenant_id, dataset_version_id, to_class, score_card_id, metrics, recommendation, recommendation_reason, state)
           values (%s, %s, %s, 'OPEN_FOR_ANNOTATION', 'u131', '{}', %s, 'a check of the recommendation values', 'pending')""",
        (str(uuid.uuid4()), CANARY, version_id, recommendation))


def dag(recommendation: str) -> dict:
    return {"steps": [{"name": "score", "kind": "builtin", "block": "verify"},
                      {"name": "gate", "kind": "gate", "depends_on": ["score"], "to_class": "OPEN_FOR_ANNOTATION",
                       "inputs": {"recommendation": recommendation, "recommendation_reason": "x"}}]}


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    version = fixture_version(CANARY, fixture_contract(CANARY))["id"]
    from app import pipeline_dag

    heading("The database")
    for value in ("pass", "fail", "not_measured"):
        try:
            with db() as conn:
                insert_gate(conn, version, value)
            took = True
        except psycopg.errors.CheckViolation:
            took = False
        check(f"a gate decision recommending {value!r} is accepted", took)
    for value in ("maybe", "", "NOT_MEASURED", "not measured"):
        try:
            with db() as conn:
                insert_gate(conn, version, value)
            took = True
        except psycopg.errors.CheckViolation:
            took = False
        check(f"and {value!r} is refused", not took)
    with db() as conn:
        row = conn.execute("select state, decided_by from gate_decision where dataset_version_id = %s and recommendation = 'not_measured'", (version,)).fetchone()
    check("a 'not_measured' row starts pending with nobody having decided it", row is not None and row["state"] == "pending" and row["decided_by"] is None, str(row))

    heading("A pipeline definition")
    for good in ("not_measured", "pass", "fail", "${steps.score.recommendation}"):
        problems = pipeline_dag.validate_dag(dag(good), set())
        check(f"a gate recommending {good!r} registers", not any("recommendation" in p for p in problems), str(problems))
    problems = pipeline_dag.validate_dag(dag("maybe"), set())
    check("a gate recommending 'maybe' is refused, and the refusal lists the three values",
          any("maybe" in p and "pass" in p and "fail" in p and "not_measured" in p for p in problems), str(problems))
    check("the values the definition accepts are the ones the database accepts",
          set(pipeline_dag.RECOMMENDATIONS) == {"pass", "fail", "not_measured"})
    return summary("v131")


if __name__ == "__main__":
    sys.exit(main())
