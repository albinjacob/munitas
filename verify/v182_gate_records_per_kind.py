r"""U193: the run keeps the counts for each kind of identifier with its decision, and judges itself by the platform's built-in bar, never by a default of its own.

Runs on the host, for the same reason U59 does: it calls `worker.activities.record_gate_decision` directly, and `worker/` is not in the API container.

    .venv\Scripts\python.exe verify\v182_gate_records_per_kind.py

The bar a result is released under is chosen at release, from the per-kind counts the run kept (U192). For that the counts have to reach the decision, and the recommendation the run records
has to be made under one bar the platform states (0.98 overall and no direct identifier left in the clear), not under a figure the workflow or a command-line script defaulted to on its own.
Until 2026-10-11 they did: the workflow and both scripts defaulted to 0.95 and so overrode the platform's own 0.98, and the tour said 0.98. This proves:

  * a run's score card counts each kind of identifier, the counts add up to the totals, and the figures MLflow is given are still all numbers (the counts go beside them, not in them);
  * the decision a run records holds those counts, the bar it was judged by (0.98 when the run was given none, an explicit figure when it was), and the recommendation under it;
  * the workflow and the two scripts no longer carry a bar of their own;
  * the judgement with no bar named gives the answers the gate always gave, and the worker's and the API's copies of the judgement are the same file.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common import CANARY, ENGINEER, check, db, fixture_contract, fixture_tenant, fixture_version, heading, require_api, summary  # noqa: E402
from worker import gate_bars  # noqa: E402
from worker.activities import record_gate_decision  # noqa: E402
from worker.scoring import GATE_RECALL, ScoreCard, evaluate_gate, score_record  # noqa: E402

made: list[str] = []

# Two records with known answers. In the first a name and a date are spoken and both are removed; in the second a phone number is spoken and survives.
REFERENCE_1 = "Grainne Church was born on 4 May 1980"
TRUTH_1 = [{"entity": "PERSON", "start": 0, "end": 14, "text": "Grainne Church"}, {"entity": "DATE", "start": 27, "end": 37, "text": "4 May 1980"}]
CANDIDATES_1 = [{"start": 0, "end": 14}, {"start": 27, "end": 37}]
REFERENCE_2 = "call 07700 900123 today"
TRUTH_2 = [{"entity": "PHONE_NUMBER", "start": 5, "end": 17, "text": "07700 900123"}]


def teardown() -> None:
    with db() as conn:
        for decision in made:
            conn.execute("delete from gate_decision where id = %s", (decision,))


def stored(decision: str) -> dict:
    with db() as conn:
        return conn.execute("select * from gate_decision where id = %s", (decision,)).fetchone()


def run(version: str, score: dict, **more) -> str:
    out = record_gate_decision({"version_id": version, "to_class": "OPEN_FOR_ANNOTATION", "score_card": score, "leak_detail": [], "triggered_by": ENGINEER, **more})
    made.append(out["gate_decision_id"])
    return out["gate_decision_id"]


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    schema_id = fixture_contract(CANARY)
    try:
        heading("U193: a run's score card counts each kind of identifier")
        card = ScoreCard()
        score_record(card, REFERENCE_1, REFERENCE_1, TRUTH_1, CANDIDATES_1, record_id="r-1")
        score_record(card, REFERENCE_2, REFERENCE_2, TRUTH_2, [], record_id="r-2")
        kinds = card.per_entity
        check("a name, a date and a phone number are each counted", set(kinds) == {"PERSON", "DATE", "PHONE_NUMBER"}, str(sorted(kinds)))
        check("the name and the date that were covered are counted as removed", kinds["PERSON"]["effective"] == 1 and kinds["DATE"]["effective"] == 1, str(kinds))
        check("the phone number nobody found is counted as left in the clear", kinds["PHONE_NUMBER"] == {"total": 1, "destroyed": 0, "effective": 0, "leaked": 1}, str(kinds["PHONE_NUMBER"]))
        check("the counts add up to the totals", sum(k["total"] for k in kinds.values()) == card.total and sum(k["effective"] for k in kinds.values()) == card.effective
              and sum(k["leaked"] for k in kinds.values()) == card.leaked, f"{card.total} / {card.effective} / {card.leaked}")
        check("the figures MLflow is given are all still numbers", all(isinstance(v, (int, float)) for v in card.as_metrics().values()), str({k: type(v).__name__ for k, v in card.as_metrics().items() if not isinstance(v, (int, float))}))

        heading("U193: the decision a run records holds the counts and the bar it was judged by")
        version = fixture_version(CANARY, schema_id)["id"]
        counts = {"PERSON": {"total": 50, "destroyed": 0, "effective": 50, "leaked": 0}, "DATE": {"total": 100, "destroyed": 0, "effective": 96, "leaked": 4}}
        score = {"score_card_id": "u193", "metrics": {"ground_truth_spans": 150, "destroyed_by_asr": 0, "recall_effective": 146 / 150, "leaks_total": 4, "leaks_direct": 0, "leaks_quasi": 4,
                                                      "per_entity": counts}}
        given_none = stored(run(version, score))
        check("a run given no bar is judged by the platform's 0.98", given_none["metrics"]["recall_threshold"] == 0.98 == GATE_RECALL, str(given_none["metrics"].get("recall_threshold")))
        check("146 of 150 is 0.973, below 0.98, so it is recorded as a fail", given_none["recommendation"] == "fail" and "Removed 97.3% of identifying detail, below the 98% asked" in given_none["recommendation_reason"], given_none["recommendation_reason"])
        check("the counts for each kind are kept with the decision", given_none["metrics"]["per_entity"] == counts, str(given_none["metrics"].get("per_entity"))[:100])
        left_out = stored(run(version, score, recall_threshold=None))
        check("a run whose bar is explicitly empty is judged the same way", left_out["metrics"]["recall_threshold"] == 0.98 and left_out["recommendation"] == "fail")
        explicit = stored(run(version, score, recall_threshold=0.95))
        check("a figure given to the run is still honoured: at 0.95 the same counts pass", explicit["metrics"]["recall_threshold"] == 0.95 and explicit["recommendation"] == "pass", explicit["recommendation_reason"])
        without_counts = stored(run(version, {"score_card_id": "u193", "metrics": {**score["metrics"], "per_entity": None}}))
        check("a score with no per-kind counts is still recorded, as it was before they were kept", without_counts["recommendation"] == "fail" and without_counts["metrics"].get("per_entity") in (None, {}))

        heading("U193: nothing but the platform's own bar is written into the workflow or the scripts")
        for path, needle in (("worker/workflows.py", 'params.get("recall_threshold", 0.95)'), ("worker/run_pipeline.py", "default=0.95"), ("worker/schedule_pipeline.py", "default=0.95")):
            text = (ROOT / path).read_text(encoding="utf-8")
            check(f"{path} has no bar of its own", needle not in text and "0.95" not in "\n".join(line for line in text.splitlines() if "recall" in line.lower() and "threshold" in line.lower()), needle)
        check("the workflow passes on what it was given and nothing else", 'params.get("recall_threshold")' in (ROOT / "worker/workflows.py").read_text(encoding="utf-8"))

        heading("U193: with no bar named, the judgement gives the gate's old answers")
        cases = [
            ({"PERSON": {"total": 10, "destroyed": 0, "effective": 10, "leaked": 0}}, True),
            ({"PERSON": {"total": 100, "destroyed": 0, "effective": 99, "leaked": 1}}, False),
            ({"DATE": {"total": 100, "destroyed": 0, "effective": 97, "leaked": 3}}, False),
            ({"DATE": {"total": 100, "destroyed": 0, "effective": 99, "leaked": 1}}, True),
            ({"DATE": {"total": 100, "destroyed": 20, "effective": 79, "leaked": 1}}, True),
            ({"PHONE_NUMBER": {"total": 20, "destroyed": 0, "effective": 19, "leaked": 1}, "DATE": {"total": 80, "destroyed": 0, "effective": 80, "leaked": 0}}, False),
        ]
        for per_entity, expected in cases:
            old = ScoreCard()
            old.per_entity = per_entity
            old.total = sum(k["total"] for k in per_entity.values())
            old.destroyed = sum(k["destroyed"] for k in per_entity.values())
            old.effective = sum(k["effective"] for k in per_entity.values())
            old.leaked = sum(k["leaked"] for k in per_entity.values())
            old.direct_leaks = sum(k["leaked"] for code, k in per_entity.items() if code in gate_bars.DIRECT_KINDS)
            old.quasi_leaks = old.leaked - old.direct_leaks
            by_the_old_figures = evaluate_gate(old)[0]
            by_the_bars = evaluate_gate(old, bars=gate_bars.default_bars())[0]
            check(f"{sorted(per_entity)} gives {'a pass' if expected else 'a fail'} both ways", by_the_old_figures == by_the_bars == expected, f"figures {by_the_old_figures}, bars {by_the_bars}")

        heading("U193: the worker's copy and the API's copy of the judgement are one file")
        check("platform/gate_bars.py and worker/gate_bars.py are the same file", (ROOT / "platform/gate_bars.py").read_bytes() == (ROOT / "worker/gate_bars.py").read_bytes())
        check("the API image copies it in", "COPY gate_bars.py ./gate_bars.py" in (ROOT / "platform/api/Dockerfile").read_text(encoding="utf-8"))
    finally:
        teardown()
    return summary("U193")


if __name__ == "__main__":
    sys.exit(main())
