"""U62: a whole de-identification, started by a person, end to end.

U61 proves every way a run is refused. This proves the one that is not: three
real consultation recordings uploaded, sealed as recordings, de-identified from
the console's own endpoint, and left waiting for a reviewer who is not the
person who started it.

It runs on the host rather than inside the API image, for the reason U55 and
U59 do: the synthetic corpus lives on this machine and is not mounted into the
container, and using it is the point. Silence in a generated wav would exercise
the same code and prove nothing about de-identification, because there is
nothing in it to de-identify.

    .venv\\Scripts\\python.exe verify\\v62_console_pipeline_run.py

Needs PG_DSN, VERIFY_KRATOS and S3_ENDPOINT pointing at the published
localhost ports, the same as U59, since verify/common.py defaults them to the
container hostnames, which do not resolve outside the Compose network. It also
needs `python -m worker.main` running on the machine with the graphics card,
and says so rather than hanging if it is not.

Expect this to take several minutes. Whisper loads before it transcribes.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import (ADMIN, CANARY, ENGINEER, REVIEWER, api, bearer_for, bucket_for, check, db, s3_client,
                    heading, require_api, summary)

TENANT = CANARY
if os.environ.get("MUNITAS_CORPUS"):
    CORPUS = Path(os.environ["MUNITAS_CORPUS"])
elif os.environ.get("MUNITAS_DATA"):
    CORPUS = Path(os.environ["MUNITAS_DATA"].rstrip("/\\") + "/synthetic")
else:
    sys.exit("Set MUNITAS_CORPUS, or MUNITAS_DATA (whose synthetic folder is the corpus).")
RECORDS = 3

# Whisper loads before it transcribes, and a cold model cache is minutes on its
# own. Generous, and still a deadline: a run that has not finished by now has
# stalled rather than slowed, and reporting that is the whole point of having a
# ceiling instead of waiting forever.
CEILING_SECONDS = 20 * 60
POLL_SECONDS = 15


def reasons_of(response) -> list[str]:
    """The refusal's own reasons, or nothing.

    Read from the parsed body rather than grepped out of the raw text, because
    an id named in a refusal appears in success responses too.
    """
    if response.status_code < 400:
        return []
    try:
        detail = response.json().get("detail")
    except Exception:  # noqa: BLE001 - a non-JSON error body carries no reasons
        return []
    if isinstance(detail, dict):
        return [str(r) for r in detail.get("reasons", [])]
    return [str(detail)] if detail else []


def department(name: str) -> str:
    with db() as conn:
        row = conn.execute(
            "select id from department where tenant_id = %s and name = %s",
            (TENANT, name),
        ).fetchone()
    if not row:
        raise RuntimeError(
            f"department {name!r} is missing from tenant {TENANT!r}. Apply "
            "infra/postgres/seed-canary.sql."
        )
    return str(row["id"])


def corpus_records(how_many: int) -> list[dict]:
    """Records from the synthetic corpus that have audio beside them."""
    chosen = []
    for path in sorted(CORPUS.glob("synth-*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        audio = CORPUS / "audio" / f"{record['record_id']}.wav"
        if audio.exists():
            chosen.append({"record": record, "audio": audio})
        if len(chosen) == how_many:
            break
    return chosen


def answer_key(record: dict) -> bytes:
    """The answer key, in exactly the shape the pipeline's own ingest writes.

    Restated here rather than imported, so this is two independently written
    definitions agreeing. Importing the worker's version would compare the
    pipeline with itself.
    """
    return json.dumps({
        "spans": record["spans"],
        "hazard": record["asr_hazard"],
        "hazards": record["hazards"],
        "template": record["template"],
        "reference_transcript": record["transcript"],
    }).encode("utf-8")


def put(dataset_id: str, name: str, payload: bytes):
    return api("POST", f"/datasets/{dataset_id}/files",
               files={"file": (name, io.BytesIO(payload),
                               "application/octet-stream")})


def main() -> int:
    require_api()

    heading("U62a: three real recordings are brought in and sealed as recordings")

    if not CORPUS.exists():
        check("the synthetic corpus is present", False, f"{CORPUS} is missing")
        return summary("U62")

    chosen = corpus_records(RECORDS)
    check(f"{RECORDS} records with audio are available",
          len(chosen) == RECORDS, f"found {len(chosen)} in {CORPUS}")
    if len(chosen) != RECORDS:
        return summary("U62")

    engineer = bearer_for(ENGINEER)
    dataset = api("POST", "/datasets/register", json={
        "tenant_id": TENANT,
        "name": f"clinic-audio-{uuid.uuid4().hex[:8]}",
        "department_id": department("Verification"),
        "registered_by": ENGINEER,
        "provenance": "internal_regulated",
        "declared_class": "RAW",
        "source_kind": "upload",
    }).json()["id"]

    uploaded = 0
    for item in chosen:
        record_id = item["record"]["record_id"]
        wav = put(dataset, f"{record_id}.wav", item["audio"].read_bytes())
        key = put(dataset, f"{record_id}.truth.json", answer_key(item["record"]))
        if wav.status_code == 201 and key.status_code == 201:
            uploaded += 1
    check("every recording and its answer key uploaded",
          uploaded == RECORDS, f"{uploaded} of {RECORDS} pairs")

    sealed = api("POST", f"/datasets/{dataset}/seal-audio", headers=engineer)
    check("the set seals as recordings", sealed.status_code == 201,
          f"HTTP {sealed.status_code} {sealed.text[:200]}")
    if sealed.status_code != 201:
        return summary("U62")
    version = sealed.json()["id"]
    check("and covers all three", sealed.json().get("records") == RECORDS,
          f"records {sealed.json().get('records')}")

    heading("U62b: the operator starts it, and nobody else could have")

    started = api(
        "POST", f"/datasets/{dataset}/versions/{version}/deidentify",
        headers=engineer,
    )
    check("the run starts", started.status_code == 202,
          f"HTTP {started.status_code} {started.text[:200]}")
    if started.status_code != 202:
        return summary("U62")
    run_id = started.json()["pipeline_run_id"]
    print(f"    run {run_id}, workflow {started.json()['workflow_id']}")

    heading("U62c: it runs to completion")

    began = time.monotonic()
    deadline = began + CEILING_SECONDS
    run: dict = {}
    seen: set[str] = set()
    while time.monotonic() < deadline:
        run = api("GET", f"/pipeline-runs/{run_id}", headers=engineer).json()
        for step in run["steps"]:
            if step["action"] not in seen:
                seen.add(step["action"])
                print(f"    {step['action']} started")
        if run["status"] != "running":
            break
        time.sleep(POLL_SECONDS)

    finished = run.get("status") == "succeeded"
    # The elapsed time, not the ceiling. A detail prints on a pass as well as a
    # failure, and "after 20 minutes" beside a green result reads as though the
    # run took the whole budget when it may have taken four.
    check("the run completes", finished,
          f"status {run.get('status')} {run.get('error') or ''} after "
          f"{(time.monotonic() - began) / 60:.1f} minutes "
          f"(ceiling {CEILING_SECONDS // 60}); workflow {run.get('workflow_id')}")

    # Four, not five. adopt_version opens no action_run, because it transforms
    # nothing and seals nothing, and a run opened there could never be closed.
    steps = {s["action"]: s["status"] for s in run.get("steps", [])}
    check("four steps ran, and none is still open",
          sorted(steps) == ["detect", "handoff", "redact", "transcribe"]
          and all(v == "succeeded" for v in steps.values()),
          f"steps {steps}")

    with db() as conn:
        correlated = conn.execute(
            "select count(*) as n from action_run where pipeline_run_id = %s",
            (run_id,),
        ).fetchone()["n"]
    check("all four carry the one run id, so the run is followable",
          correlated == 4, f"{correlated} action_run row(s)")

    heading("U62d: it ends at a decision, not at a promotion")

    gate = run.get("gate_decision")
    check("a gate decision is waiting", bool(gate) and gate["state"] == "pending",
          f"gate {gate and gate['state']}")
    if not gate:
        return summary("U62")
    check("it carries a verdict the machine reached",
          gate["recommendation"] in ("pass", "fail"),
          f"recommendation {gate['recommendation']}")
    check("and the recall it measured, from real speech",
          "recall_effective" in (gate.get("metrics") or {}),
          f"metrics {sorted((gate.get('metrics') or {}))}")
    check("nothing was promoted by the run itself",
          gate["state"] == "pending",
          "promotion is a person's act, taken against this row")

    heading("U62e: the person who started it cannot clear it")

    mine = api("POST", f"/gate-decisions/{gate['id']}/promote",
               json={"reason": "verification"}, headers=engineer)
    check("the operator who started the run is refused", mine.status_code == 403,
          f"HTTP {mine.status_code}")
    said = reasons_of(mine)
    check("and told it is because they started it",
          any("cannot be cleared by whoever started it" in r for r in said),
          f"reasons {said}")

    queue = api("GET", "/gate-decisions", params={"tenant_id": TENANT, "limit": 500},
                headers=bearer_for(REVIEWER))
    ids = [row["id"] for row in queue.json()["gate_decisions"]] if queue.status_code == 200 else []
    check("the reviewer can see it in their queue", gate["id"] in ids,
          f"{len(ids)} decision(s) listed")

    heading("U62f: the artefacts v14_aligned has been waiting for exist")

    with db() as conn:
        redacted = conn.execute(
            """select dv.storage_prefix from dataset_version dv
                 join action_run a on a.output_version = dv.id
                 join dataset_action act on act.id = a.action_id
               where a.pipeline_run_id = %s and act.name = 'detect'""",
            (run_id,),
        ).fetchone()
    check("the detect step sealed a version", redacted is not None,
          f"prefix {redacted and redacted['storage_prefix']}")

    if redacted:
        from worker import platform_client as cp

        # detected.json, by name. This is the file verify/v14_aligned.py opens,
        # and the reason it has never run is that no completed pipeline had
        # ever written one into the bucket.
        prefix = redacted["storage_prefix"]
        rows: list = []
        try:
            rows = json.loads(s3_client(*ADMIN).get_object(Bucket=bucket_for(TENANT), Key=f"{prefix}/detected.json")["Body"].read())
            found: object = len(rows)
        except Exception as exc:  # noqa: BLE001 - reported as a failed check
            found = f"unreadable: {exc}"
        check("detected.json exists, which is what v14_aligned opens",
              found == RECORDS, f"{found} record(s) at {prefix}/detected.json")
        # The exact keys v14_aligned.py subscripts, named one at a time rather
        # than as "it looks about right". A file with the right name and the
        # wrong shape would still fail that script, several minutes later and
        # with a KeyError instead of an explanation.
        needed = ("record_id", "transcript", "candidates")
        check("and every record carries what v14_aligned subscripts",
              bool(rows) and all(all(k in r for k in needed) for r in rows),
              f"needs {list(needed)}, first record has "
              f"{sorted(rows[0]) if rows else 'no records'}")

    return summary("U62")


if __name__ == "__main__":
    sys.exit(main())
