"""U132: a practice run measures the method on files whose contents are known, and writes nothing into any organisation.

A de-identification run on real files has no answer key, so nothing can be said about whether it worked on them. What can be measured is
whether the method works: the same models, run on files made for the purpose, whose every name and number was invented and is known.
This calls the practice step itself, on the machine with the models, and checks one item at a time:

  * it scores what it caught, and the reason it writes states the figures it measured and no others;
  * it says in its own words that it describes the method and says nothing about the batch;
  * it picks the same files every time, spread across the corpus, and never more than were asked for;
  * every leak it reports is marked as a practice leak, and carries what the gate evidence needs;
  * it writes nothing: no dataset, no version, no action run, no pipeline run, no gate decision, no object in any bucket;
  * where the practice files are not on the machine it does not fail the run: it says so, plainly, and measured nothing.

Runs on the host, not inside the munitas-api container, because it calls `worker.activities.practice_run` directly and loads the models
(the same reason as U59). A couple of minutes on the machine with the graphics card.

    $env:MUNITAS_PG_DSN = "postgresql://munitas:munitas@localhost:5432/platform"
    $env:MUNITAS_VERIFY_KRATOS = "http://localhost:4433"
    $env:MUNITAS_S3_ENDPOINT = "http://localhost:8333"
    .venv\\Scripts\\python.exe verify\\v132_practice_run.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from temporalio.testing import ActivityEnvironment  # noqa: E402

from common import ADMIN, check, db, heading, s3_client, summary  # noqa: E402
from worker import config  # noqa: E402
from worker.activities import _practice_records, practice_run  # noqa: E402

TABLES = ("dataset", "dataset_version", "action_run", "pipeline_run", "pipeline_step_run", "gate_decision", "gate_leak", "access_decision")


def counts() -> dict:
    with db() as conn:
        return {t: conn.execute(f"select count(*) as n from {t}").fetchone()["n"] for t in TABLES}


def objects() -> int:
    client, total = s3_client(*ADMIN), 0
    for bucket in client.list_buckets().get("Buckets", []):
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket["Name"]):
            total += page.get("KeyCount", 0)
    return total


def run_practice(params: dict) -> dict:
    """The activity as Temporal runs it: with an activity context, which its heartbeat needs, and not by a bare call that would skip the heartbeat."""
    return ActivityEnvironment().run(practice_run, params)


def main() -> int:
    corpus = config.require_corpus_dir()

    heading("Which files")
    a, b = _practice_records(corpus, 4), _practice_records(corpus, 4)
    check("it picks the same files every time", a == b and len(a) == 4, str([p.stem for p in a]))
    check("four different files, spread across the corpus and not the first four",
          len({p.stem for p in a}) == 4 and a[0].stem == "synth-0000" and a[-1].stem != "synth-0003", str([p.stem for p in a]))
    check("never more than were asked for, and never more than there are", len(_practice_records(corpus, 2)) == 2 and len(_practice_records(corpus, 10_000)) <= 60)
    check("none, when none are asked for", _practice_records(corpus, 0) == [])

    heading("Running it")
    before, stored = counts(), objects()
    result = run_practice({"practice_records": 2})
    after = counts()
    m = result["metrics"]
    check("it ran, on the two files asked for", result["ran"] is True and m["practice_ran"] == 1 and m["practice_records"] == 2, str(m))
    check("it measured: spans in the files, how many survived transcription, how many were left in the clear",
          m["practice_ground_truth_spans"] > 0 and 0 <= m["practice_survived_asr"] <= m["practice_ground_truth_spans"]
          and 0 <= m["practice_leaks_total"] <= m["practice_survived_asr"], str(m))
    caught = m["practice_survived_asr"] - m["practice_leaks_total"]
    check("the reason states the figures it measured: how many it caught, of how many, and the direct leaks",
          f"caught {caught} of the {m['practice_survived_asr']} identifiers" in result["reason"] and f"left {m['practice_leaks_direct']} naming a person directly" in result["reason"],
          result["reason"][:220])
    check("it says there is no ground truth for the batch, and that this describes the method and not the batch",
          "no ground truth for this file" in result["reason"] and "describes the method and says nothing about this batch" in result["reason"])
    check("it does not claim the batch was measured: no batch metric is among its figures",
          not any(k in m for k in ("recall_effective", "leaks_total", "leaks_direct", "ground_truth_spans")), str(sorted(m)))
    check("it says whether the bar was met, and the reason agrees with that",
          isinstance(result["cleared_bar"], bool) and ("that meets the bar" in result["reason"]) == result["cleared_bar"])
    leaks = result["leak_detail"]
    check("it found as many leaks as it counted", len(leaks) == m["practice_leaks_total"], f"{len(leaks)} vs {m['practice_leaks_total']}")
    check("every leak is marked as a practice leak, with the evidence the gate shows",
          all(l.get("practice") is True and {"record_id", "span_start", "span_end", "entity", "identifier", "left_in_the_clear", "coverage", "direct"} <= set(l) for l in leaks),
          str(leaks[:1]))
    check("it reports which code ran", bool(result.get("code_hash")))

    heading("Writing nothing")
    check("no row in any organisation's tables: no dataset, version, action run, pipeline run, gate decision or access decision", after == before, f"{before} -> {after}")
    check("no object in any bucket", objects() == stored, f"{stored} -> {objects()}")

    heading("Without the practice files")
    with tempfile.TemporaryDirectory() as empty:
        os.environ["MUNITAS_CORPUS"] = empty
        try:
            none = run_practice({"practice_records": 2})
        finally:
            os.environ.pop("MUNITAS_CORPUS", None)
    check("it does not fail the run: it returns, having measured nothing", none["ran"] is False and none["metrics"] == {"practice_ran": 0} and none["leak_detail"] == [])
    check("and says plainly that a practice run could not be made, and why",
          "could not be made" in none["reason"] and "there are no practice files to run" in none["reason"], none["reason"][:200])
    check("it still says there is no ground truth for the batch", "no ground truth for this file" in none["reason"])
    return summary("v132")


if __name__ == "__main__":
    sys.exit(main())
