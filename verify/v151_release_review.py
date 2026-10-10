"""U155: a result waiting for a decision can be looked at before it is released, and cannot be released blind.

A step that writes files makes a version "under review". A reviewer decides to release it or hold it back. Before this the reviewer had nothing to look
at: the page said "it needs your full review" and showed no files. This checks, one item at a time against the live platform, that:

  * the reviewer (and nobody else) may open the files of a result while it waits, up to a fixed number, and the right ends when it is decided;
  * the person who started the run, and the person who uploaded the version of the pipeline that made it, cannot be the reviewer;
  * the platform checks every file before anyone looks: a file that is not what its name says, and a file that is a copy of the original it was made
    from, are found and stop a release (holding it back is still possible);
  * a result nothing measured cannot be released by somebody who opened none of it, and can be after they open one;
  * what the decision relied on (the checks and the files opened) is kept with it, and a released version's files stay locked.

Everything is made up here, in the verification organisation, with people of its own, and what can be taken away is.

    docker compose exec -T munitas-api python /verify/v151_release_review.py
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

import httpx  # noqa: E402

from common import API, CANARY, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402
from lifecycle_fixture import KRATOS_ADMIN  # noqa: E402
from v127_department_approvers import add_person  # noqa: E402
from v128_person_keys_erasure import CUSTODIAN, ENGINEER, register_dataset, reset_keys  # noqa: E402
from v137_locked_files import make_wav, upload_whole  # noqa: E402
from v140_custom_steps_and_files import complete_out, department, finish, opening, pass_for, reasons_of, upload_version  # noqa: E402
from v142_development_samples import ask, decide, open_run_as  # noqa: E402
from v145_locked_outputs import YAML_BOTH, open_locked, send  # noqa: E402


def records(version: str, who: str) -> httpx.Response:
    return api("GET", f"/dataset-versions/{version}/records", headers=bearer_for(who))


def open_it(version: str, record: str, who: str) -> httpx.Response:
    return api("POST", f"/dataset-versions/{version}/records/{record}/open", headers=bearer_for(who))


def stream(handle: str) -> httpx.Response:
    return httpx.get(f"{API}/record-stream/{handle}", timeout=60.0)


def review_of(decision: str, who: str) -> httpx.Response:
    return api("GET", f"/gate-decisions/{decision}/review", headers=bearer_for(who))


def make_result(src: str, plain: bytes, files: dict[str, bytes | None], tag: str, park: str) -> tuple[str, str, str]:
    """A step writes `files` from the first file of `src` (None means a copy of that file), the platform seals them under review, and a
    decision waits on the version. Returns (version, decision, pipeline run). The version is made an ordinary one: it is no longer development data.
    The sample the run read is handed to `park`, another person, instead of being ended: an ended sample's files are deleted by the platform a minute later,
    and the original the checks compare with would go. With another person holding it, the starter is not on development data, and the original stays."""
    sample = ask(ENGINEER, src, kind="debug", records=1, days=3, named=["r-0001"], purpose=f"u155 {tag}")
    sver = decide(CUSTODIAN, sample.json()["id"], "approve").json()["sample_version_id"]
    pid = api("POST", "/pipelines/register", json={"tenant_id": CANARY, "name": f"u155-{tag}", "department_id": department(), "registered_by": ENGINEER},
              headers=bearer_for(ENGINEER)).json()["id"]
    dev = upload_version(pid, YAML_BOTH).json()["id"]
    run = open_run_as(sver, dev, ENGINEER)
    token = pass_for(run)
    opening(token, "r-0001")
    for name, data in files.items():
        data = plain if data is None else data
        opened = open_locked(token, name, "r-0001", len(data))
        assert opened.status_code == 201, (name, opened.status_code, opened.text[:300])
        send(token, name, data)
        complete_out(token, name)
    version = finish(run).json()["output_version_id"]
    decision = str(uuid.uuid4())
    with db() as conn:
        conn.execute("update development_sample set principal = %s where id = %s", (park, sample.json()["id"]))
        # What a run on real data makes is not development data and does not expire. The made-up state: this version stands as an ordinary result.
        conn.execute("delete from development_output where version_id = %s", (version,))
        conn.execute(
            """insert into gate_decision (id, tenant_id, pipeline_run_id, dataset_version_id, to_class, score_card_id, metrics, recommendation, recommendation_reason,
                                          state, triggered_by) values (%s, %s, %s, %s, 'OPEN_FOR_ANNOTATION', 'u155', '{}', 'not_measured', 'this step changes the sound and does not look for identifying detail', 'pending', %s)""",
            (decision, CANARY, run["id"], version, ENGINEER))
    return version, decision, run["id"]


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    tag = uuid.uuid4().hex[:6]
    org = SimpleNamespace(id=CANARY, people={}, identities=[])
    try:
        r1 = add_person(org, f"u155-{tag}-r1", "deid_reviewer", "First reviewer")
        r2 = add_person(org, f"u155-{tag}-r2", "deid_reviewer", "Second reviewer")
        analyst = add_person(org, f"u155-{tag}-an", "analyst", "Analyst")

        dataset = register_dataset(ENGINEER)
        plain = make_wav(60_000)
        upload_whole(dataset, "r-0001.wav", plain, f"U155-{tag}-p")
        src = api("POST", f"/datasets/{dataset}/seal-files", headers=bearer_for(ENGINEER)).json()["id"]

        # The first result: 24 ordinary files, a copy of the original, and a "sound" file that is not sound.
        made = {f"x-{n:02d}.bin": os.urandom(2000 + n) for n in range(24)}
        made["same.wav"] = None
        made["notsound.wav"] = os.urandom(3000)
        version, decision, run_id = make_result(src, plain, made, f"{tag}-a", analyst)

        heading("Who may open the files of a result that is waiting")
        body = records(version, r2).json()
        check("a reviewer is told they may open them to judge the result, with the limit and what they have opened", body["basis"] == "review" and body["may_open"] is True
              and body["review"] == {"opened": [], "limit": 25} and body["total"] == 26, str(body)[:260])
        for who, label in ((ENGINEER, "the person who started the run"), (CUSTODIAN, "the department's approver"), (analyst, "an analyst")):
            got = records(version, who).json()
            check(f"{label} is not offered them", got["may_open"] is False and got["basis"] != "review", f"{got['basis']} {got['may_open']}")
        with db() as conn:
            conn.execute("update pipeline_version pv set uploaded_by = %s from pipeline_run pr where pr.id = %s and pv.id = pr.pipeline_version_id", (r1, run_id))
        check("the person who uploaded the version of the pipeline that made it is not offered them either", records(version, r1).json()["may_open"] is False)
        check("while the other reviewer still is", records(version, r2).json()["basis"] == "review")
        waiting = api("GET", f"/gate-decisions/{decision}", headers=bearer_for(ENGINEER)).json()
        check("and the result does not list the uploader as somebody it waits on", r1 not in [p["id"] for p in waiting["waiting_on"]] and r2 in [p["id"] for p in waiting["waiting_on"]], str(waiting["waiting_on"])[:200])
        run = waiting["run"] or {}
        check("the result says which run made it: the pipeline and its version, who uploaded it, who started the run, and what the run was given",
              str(run.get("pipeline_name", "")).startswith("u155-") and run.get("pipeline_version") == 1 and run.get("uploaded_by_label") == "First reviewer"  # made the uploader a few lines up
              and run.get("triggered_by_label") == "Canary engineer" and run.get("trigger_kind") and bool(run.get("source_version_id")) and bool(run.get("source_dataset_name")) and run.get("id") == run_id, str(run)[:300])
        check("with when it started and finished", bool(run.get("started_at")), str(run.get("started_at")))
        check("and each step by its own name, with when it ran", [s["step_name"] for s in waiting["steps"]] == ["inspect"] and bool(waiting["steps"][0]["started_at"]), str(waiting["steps"])[:200])
        r = api("POST", f"/gate-decisions/{decision}/refuse", json={"reason": "I wrote it"}, headers=bearer_for(r1))
        check("the uploader is refused if they try to decide it, and the reason says why", r.status_code == 403 and "uploaded" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
        with db() as conn:
            conn.execute("update pipeline_version pv set uploaded_by = %s from pipeline_run pr where pr.id = %s and pv.id = pr.pipeline_version_id", (ENGINEER, run_id))

        heading("What the reviewer opens is what the step made, and is recorded")
        opened = open_it(version, "x-00.bin", r2)
        check("the reviewer opens a file", opened.status_code == 200 and opened.json()["locked"] is True, f"{opened.status_code} {opened.text[:160]}")
        check("and it is exactly what the step sent, unlocked inside the platform", stream(opened.json()["handle"]).content == made["x-00.bin"])
        with db() as conn:
            log = conn.execute("select basis, locked from record_read_log where principal = %s and version_id = %s and record_id = 'x-00.bin'", (r2, version)).fetchone()
        check("the opening is in the read log marked as a review", log is not None and log["basis"] == "review" and log["locked"] is True, str(dict(log) if log else None))

        heading("A review may open a set number of files, not all of them")
        names = [f"x-{n:02d}.bin" for n in range(24)] + ["same.wav"]
        statuses = [open_it(version, n, r2).status_code for n in names]
        check("25 different files open", statuses == [200] * 25, str(statuses))
        r = open_it(version, "notsound.wav", r2)
        check("the 26th is refused, and says why", r.status_code == 403 and "most a review may open" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
        check("a file already opened opens again", open_it(version, "x-03.bin", r2).status_code == 200)
        check("the list says how many were opened", len(records(version, r2).json()["review"]["opened"]) == 25)

        heading("The platform checked every file before anyone looked")
        ev = review_of(decision, r2).json()
        by = {f["record_id"]: {c["check"]: c for c in f["checks"]} for f in ev["files"]}
        check("every file was checked", len(ev["files"]) == 26 and all(len(f["checks"]) == 3 for f in ev["files"]), str(len(ev["files"])))
        check("a copy of the original is found, and is said to be the original", by["same.wav"]["copy"]["ok"] is False and "original" in by["same.wav"]["copy"]["detail"], str(by["same.wav"]["copy"]))
        check("a file named as sound that is not sound is found", by["notsound.wav"]["type"]["ok"] is False and "not one" in by["notsound.wav"]["type"]["detail"], str(by["notsound.wav"]["type"]))
        check("an ordinary file passes every check", all(c["ok"] for c in by["x-00.bin"].values()))
        check("the page is told what stops a release, in words", len(ev["blockers"]) >= 2 and any("same.wav" in b for b in ev["blockers"]) and any("notsound.wav" in b for b in ev["blockers"]), str(ev["blockers"])[:300])
        check("and that this person may review it", ev["may_review"] is True and ev["checks_failed"] == 2, f"{ev['may_review']} {ev['checks_failed']}")
        engineer_view = review_of(decision, ENGINEER).json()
        check("the person who started the run is shown it but told they may not decide it", engineer_view["may_review"] is False and engineer_view["not_allowed"] and engineer_view["blockers"] == [])

        heading("A release rests on the checks")
        r = api("POST", f"/gate-decisions/{decision}/promote", json={"reason": "looks fine to me"}, headers=bearer_for(r2))
        check("releasing is refused while a file failed a check, and the reasons name the files", r.status_code == 409 and "same.wav" in r.text and "notsound.wav" in r.text, f"{r.status_code} {r.text[:200]}")
        check("and nothing was released", db_class(version) == "UNDER_REVIEW")
        r = api("POST", f"/gate-decisions/{decision}/refuse", json={"reason": "a copy and a file that is not sound"}, headers=bearer_for(r2))
        check("holding it back is allowed", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
        with db() as conn:
            kept = conn.execute("select state, handoff from gate_decision where id = %s", (decision,)).fetchone()
        review = kept["handoff"]["review"]
        check("what the decision relied on is kept with it: the outcome, the files opened and the checks that failed",
              kept["state"] == "refused" and review["outcome"] == "held back" and len(review["opened"]) == 25 and review["checks_failed"] == 2
              and {f["record_id"] for f in review["failed"]} == {"same.wav", "notsound.wav"}, str(review)[:300])
        check("once it is decided the reviewer's right to open its files ends", records(version, r2).json()["may_open"] is False and open_it(version, "x-01.bin", r2).status_code == 403)
        check("and a handle they already held stops at its next request", stream(opened.json()["handle"]).status_code == 403)

        heading("A result nothing measured is released only by somebody who looked")
        good = {"a.bin": os.urandom(1500), "b.bin": os.urandom(1700), "c.wav": make_wav(5_000)}
        version2, decision2, _run2 = make_result(src, plain, good, f"{tag}-b", r1)
        ev2 = review_of(decision2, r2).json()
        check("no check fails", ev2["checks_failed"] == 0 and ev2["opened"] == [], str(ev2["blockers"]))
        check("but it cannot be released with nothing opened, and the reason says so",
              ev2["blockers"] and "open at least one" in ev2["blockers"][0], str(ev2["blockers"]))
        r = api("POST", f"/gate-decisions/{decision2}/promote", json={"reason": "no need to look"}, headers=bearer_for(r2))
        check("the release is refused with that reason", r.status_code == 409 and "open at least one" in r.text, f"{r.status_code} {r.text[:200]}")
        check("a second person who has not looked is stopped in the same way", api("POST", f"/gate-decisions/{decision2}/promote", json={"reason": "fine"}, headers=bearer_for(r1)).status_code == 409)
        looked = open_it(version2, "a.bin", r2)
        check("after the reviewer opens a file", looked.status_code == 200)
        ev2 = review_of(decision2, r2).json()
        check("nothing stops the release now", ev2["blockers"] == [] and ev2["opened"] == ["a.bin"], str(ev2["blockers"]))
        r = api("POST", f"/gate-decisions/{decision2}/promote", json={"reason": "opened one file and the checks passed"}, headers=bearer_for(r2))
        check("it is released", r.status_code == 201 and r.json()["state"] == "promoted", f"{r.status_code} {r.text[:200]}")
        check("the version is now open for annotation", db_class(version2) == "OPEN_FOR_ANNOTATION", db_class(version2))
        with db() as conn:
            kept2 = conn.execute("select handoff from gate_decision where id = %s", (decision2,)).fetchone()["handoff"]["review"]
            still_locked = conn.execute("select count(*) as n from file_person where dataset_version_id = %s", (version2,)).fetchone()["n"]
        check("the decision records that it was released and which file was opened", kept2["outcome"] == "released" and kept2["opened"] == ["a.bin"] and kept2["checks_failed"] == 0, str(kept2))
        check("the files stay locked under their person's key after release", still_locked == 3, str(still_locked))
        check("and the right to open them for review is gone", open_it(version2, "b.bin", r2).status_code == 403)
    finally:
        # What the people made here held is taken back, so they cannot be named as anybody's reviewer by a later run; their rows go with the canary tidy.
        with db() as conn:
            conn.execute("update directory set roles = '{}' where id like %s", (f"canary-u155-{tag}-%",))
        for identity in org.identities:
            httpx.delete(f"{KRATOS_ADMIN}/admin/identities/{identity}", timeout=10.0)
        reset_keys()
    return summary("v151")


def db_class(version: str) -> str:
    with db() as conn:
        return conn.execute("select current_class from version_class where dataset_version_id = %s", (version,)).fetchone()["current_class"]


if __name__ == "__main__":
    sys.exit(main())
