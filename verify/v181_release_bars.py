r"""U192: the bar a de-identified result is judged by is the customer's, set per purpose, and worked out again at the moment of release.

Run in the API container, which holds the API's code and reaches the database:

    docker compose exec -T munitas-api python /verify/v181_release_bars.py

A result is released by a person, and what the platform recommends depends on the bar they judge it by. This proves, in the test organisation only:

  * the judgement itself (platform/gate_bars.py): the platform's built-in bar gives the same pass or fail as the gate always did, each limit of a bar makes the difference
    it says it does, and a bar that is not well formed is refused in words;
  * who may set a bar (the data protection officer alone, and the refusal says so in the words of the role table), that a name is unique and the platform's own name is not
    available, and that a change adds a version and never edits one (the database refuses an edit even with the API bypassed);
  * the recommendation under a chosen bar is worked out here, from the counts the run kept, and not believed from the caller; a result nothing measured stays "not measured"
    under every bar; a result with no counts can only be judged by the built-in bar, and says so;
  * a decision keeps the bar it was judged by, its version and its limits as they were, and later versions of the bar do not change what an earlier decision says;
  * a bar chosen at the release changes what stops a release (release_review.blockers).

It makes up its own results: each gate decision row is written here with counts that are known, and nothing is borrowed from live data. It removes them, and the bars it made, at the end.
"""

from __future__ import annotations

import json
import sys
import uuid

sys.path.insert(0, "/verify")
sys.path.insert(0, "/app")

import pgdb  # noqa: E402
from common import (CANARY, CANARY_ADMIN, CUSTODIAN, ENGINEER, OVERSIGHT, REVIEWER, api, bearer_for, check, db,  # noqa: E402
                    fixture_contract, fixture_tenant, fixture_version, heading, require_api, summary)

import gate_bars  # noqa: E402
from app import release_review  # noqa: E402

made_decisions: list[str] = []
made_bars: list[str] = []

# Counts that are known. 150 identifiers survived transcription: 50 names, all removed, and 100 dates, 96 removed and 4 left in the clear. Overall 146 of 150.
COUNTS = {
    "PERSON": {"total": 50, "destroyed": 0, "effective": 50, "leaked": 0},
    "DATE": {"total": 100, "destroyed": 0, "effective": 96, "leaked": 4},
}


def metrics(per_entity: dict | None = COUNTS) -> dict:
    out = {"ground_truth_spans": 150, "destroyed_by_asr": 0, "recall_effective": 146 / 150, "leaks_total": 4, "leaks_direct": 0, "leaks_quasi": 4, "recall_threshold": 0.98}
    if per_entity is not None:
        out["per_entity"] = per_entity
    return out


def new_decision(version_id: str, recommendation: str = "fail", per_entity: dict | None = COUNTS, measured: bool = True) -> str:
    """A pending decision with known counts, written directly: the run's own recommendation under the built-in bar is 'fail' (146 of 150 is 0.9733, below 0.98)."""
    decision = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """insert into gate_decision (id, tenant_id, dataset_version_id, to_class, score_card_id, metrics, recommendation, recommendation_reason, state, triggered_by, handoff)
               values (%s, %s, %s, 'OPEN_FOR_ANNOTATION', 'u192', %s, %s, %s, 'pending', %s, '{}'::jsonb)""",
            (decision, CANARY, version_id, json.dumps(metrics(per_entity) if measured else {}), recommendation,
             "Removed 97.3% of identifying detail, below the 98% asked (4 of 150 left in the clear)" if recommendation == "fail" else "nothing was counted", ENGINEER))
    made_decisions.append(decision)
    return decision


def row_for(decision: str) -> dict:
    with db() as conn:
        return conn.execute("select * from gate_decision where id = %s", (decision,)).fetchone()


def reasons_of(response) -> str:
    try:
        body = response.json().get("detail", {})
        return " ".join(body.get("reasons", [])) if isinstance(body, dict) else str(body)
    except ValueError:
        return response.text[:200]


def teardown() -> None:
    with db() as conn:
        for decision in made_decisions:
            conn.execute("delete from gate_decision where id = %s", (decision,))
        for bar in made_bars:
            conn.execute("delete from gate_purpose_version where purpose_id = %s", (bar,))
            conn.execute("delete from gate_purpose where id = %s", (bar,))


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    schema_id = fixture_contract(CANARY)
    try:
        # ------------------------------------------------------------------ the judgement
        heading("U192: the built-in bar gives the gate's old answers")
        default = gate_bars.default_bars()
        ok, why = gate_bars.evaluate({"PERSON": {"total": 10, "destroyed": 0, "effective": 10, "leaked": 0}}, default)
        check("everything removed passes", ok, why)
        ok, why = gate_bars.evaluate({"PERSON": {"total": 100, "destroyed": 0, "effective": 99, "leaked": 1}}, default)
        check("one name left in the clear fails, even when 99.0% was removed", not ok and "a person's name: 1 left in the clear, and none are allowed" in why, why)
        ok, why = gate_bars.evaluate({"DATE": {"total": 100, "destroyed": 0, "effective": 97, "leaked": 3}}, default)
        check("97.0% removed with only dates left fails on the overall figure", not ok and "Removed 97.0% of identifying detail, below the 98% asked" in why, why)
        ok, why = gate_bars.evaluate({"DATE": {"total": 100, "destroyed": 0, "effective": 99, "leaked": 1}}, default)
        check("99.0% removed with one date left passes: a date does not name a person directly", ok and "0 that name a person directly, 1 that identify only in combination" in why, why)
        ok, why = gate_bars.evaluate({"DATE": {"total": 100, "destroyed": 20, "effective": 79, "leaked": 1}}, default)
        check("what speech recognition had already lost does not count against the pipeline", ok and "Removed 98.8% of identifying detail" in why, why)
        ok, why = gate_bars.evaluate({}, default)
        check("a result with nothing that survived is not a pass of the overall figure", not ok and "Removed 0.0% of identifying detail" in why, why)

        heading("U192: each limit makes the difference it says")
        loose = {"overall_recall": None, "types": {}}
        check("a bar with no limit passes anything", gate_bars.evaluate(COUNTS, loose)[0] is True)
        check("the overall figure alone fails 146 of 150 at 0.98 and passes it at 0.97",
              not gate_bars.evaluate(COUNTS, {"overall_recall": 0.98, "types": {}})[0] and gate_bars.evaluate(COUNTS, {"overall_recall": 0.97, "types": {}})[0])
        ok, why = gate_bars.evaluate(COUNTS, {"overall_recall": None, "types": {"DATE": {"recall": 0.99, "max_leaks": None}}})
        check("a recall limit on dates fails dates at 0.96 and names the kind in words", not ok and why.startswith("a date: removed 96.0%, below the 99% asked (4 of 100 left in the clear)"), why)
        check("and the same limit on a kind that is not in the counts changes nothing",
              gate_bars.evaluate(COUNTS, {"overall_recall": None, "types": {"PHONE_NUMBER": {"recall": 0.99, "max_leaks": 0}}})[0])
        ok, why = gate_bars.evaluate(COUNTS, {"overall_recall": None, "types": {"DATE": {"recall": None, "max_leaks": 3}}})
        check("a limit on the most left in the clear fails 4 dates against 3", not ok and "a date: 4 left in the clear, and at most 3 are allowed" in why, why)
        check("and passes them against 4", gate_bars.evaluate(COUNTS, {"overall_recall": None, "types": {"DATE": {"recall": None, "max_leaks": 4}}})[0])
        ok, why = gate_bars.evaluate(COUNTS, {"overall_recall": 0.99, "types": {"DATE": {"recall": 0.99, "max_leaks": 0}}})
        check("every limit that was missed is named, not only the first", not ok and why.count(";") == 2, why)

        heading("U192: a name is one kind, however the answer key labelled it")
        by_role = {"PERSON_PATIENT": {"total": 10, "destroyed": 0, "effective": 9, "leaked": 1}, "PERSON_CLINICIAN": {"total": 5, "destroyed": 0, "effective": 5, "leaked": 0}}
        ok, why = gate_bars.evaluate(by_role, default)
        check("a name labelled by the part the person plays counts as a person's name: one left in the clear fails the built-in bar", not ok and "a person's name: 1 left in the clear, and none are allowed" in why, why)
        ok, why = gate_bars.evaluate(by_role, {"overall_recall": None, "types": {"PERSON": {"recall": 0.95, "max_leaks": None}}})
        check("a limit on a person's name sees the labels added together: 14 of 15 is below 95 percent", not ok and "removed 93.3%, below the 95% asked (1 of 15 left in the clear)" in why, why)
        check("the labels folded into it are not kinds a bar can name", bool(gate_bars.check_bars({"overall_recall": None, "types": {"PERSON_PATIENT": {"max_leaks": 0}}})))
        check("a person's name is the only kind of name a bar offers", [k for k in gate_bars.KINDS if k.startswith("PERSON")] == ["PERSON"], str(gate_bars.KINDS))
        check("the scoring still counts every one of those labels as naming a person directly", set(gate_bars.DIRECT_ENTITY_CODES) >= {"PERSON", "PERSON_PATIENT", "PERSON_CLINICIAN"})

        heading("U192: a bar that is not well formed is refused in words")
        for label, bars, words in [
            ("a recall above 1", {"overall_recall": 1.5, "types": {}}, "from 0 to 1"),
            ("a negative number of leaks", {"overall_recall": None, "types": {"DATE": {"max_leaks": -1}}}, "whole number"),
            ("leaks that are not a whole number", {"overall_recall": None, "types": {"DATE": {"max_leaks": 1.5}}}, "whole number"),
            ("a true or false in place of a number", {"overall_recall": True, "types": {}}, "from 0 to 1"),
            ("a kind the platform does not measure", {"overall_recall": None, "types": {"SHOE_SIZE": {"recall": 0.5}}}, "not a kind"),
            ("a limit the platform does not have", {"overall_recall": None, "types": {"DATE": {"recall": 0.5, "colour": "red"}}}, "only a recall"),
            ("an unknown top-level limit", {"overall_recall": None, "types": {}, "extra": 1}, "unknown limits"),
            ("something that is not an object", [1, 2], "must be an object"),
        ]:
            problems = gate_bars.check_bars(bars)
            check(f"{label} is refused", bool(problems) and words in " ".join(problems), "; ".join(problems))
        check("the built-in bar itself is well formed", gate_bars.check_bars(gate_bars.default_bars()) == [])

        # ------------------------------------------------------------------ who may set a bar, and the history
        heading("U192: who may set a bar")
        listed = api("GET", "/release-bars", headers=bearer_for(REVIEWER))
        check("a reviewer can read the bars", listed.status_code == 200, f"HTTP {listed.status_code}")
        body = listed.json()
        check("and is told they may not set one, in the words of the role table", body["may_set"] is False and "only the data protection officer may set a release bar" in " ".join(body["may_set_reasons"]), str(body["may_set_reasons"]))
        check("the built-in bar and every kind of identifier come with it", body["platform_default"]["name"] == "Platform default" and len(body["kinds"]) == len(gate_bars.KINDS), f"{len(body['kinds'])} kinds")
        check("a person who is not signed in is refused", api("GET", "/release-bars", headers={}).status_code == 401)

        strict = {"name": f"U192 public release {uuid.uuid4().hex[:6]}", "reason": "released beyond the organisation",
                  "bars": {"overall_recall": 0.99, "types": {"DATE": {"recall": 0.99, "max_leaks": 0}, "PERSON": {"recall": None, "max_leaks": 0}}}}
        for who, label in ((REVIEWER, "the reviewer"), (CANARY_ADMIN, "the platform administrator"), (CUSTODIAN, "a data custodian"), (ENGINEER, "a data engineer")):
            refused = api("POST", "/release-bars", headers=bearer_for(who), json=strict)
            check(f"{label} cannot create a bar", refused.status_code == 403 and "the data protection officer" in reasons_of(refused), f"HTTP {refused.status_code}: {reasons_of(refused)[:90]}")
        made = api("POST", "/release-bars", headers=bearer_for(OVERSIGHT), json=strict)
        check("the data protection officer creates one", made.status_code == 201, f"HTTP {made.status_code}: {made.text[:120]}")
        public = made.json()["release_bar"]
        made_bars.append(public["id"])
        check("at version 1, with who set it and why", public["version"] == 1 and public["set_by"] == OVERSIGHT and public["reason"] == strict["reason"], str(public)[:160])
        check("the limits are kept as given, with every unset limit empty",
              public["bars"] == {"overall_recall": 0.99, "types": {"DATE": {"recall": 0.99, "max_leaks": 0}, "PERSON": {"recall": None, "max_leaks": 0}}}, str(public["bars"]))

        again = api("POST", "/release-bars", headers=bearer_for(OVERSIGHT), json={**strict, "name": strict["name"].upper()})
        check("a second bar with the same name in other letters is refused", again.status_code == 409, f"HTTP {again.status_code}")
        own = api("POST", "/release-bars", headers=bearer_for(OVERSIGHT), json={**strict, "name": "platform DEFAULT"})
        check("the platform's own name is not available", own.status_code == 409 and "Platform default" in reasons_of(own), reasons_of(own)[:100])
        bad = api("POST", "/release-bars", headers=bearer_for(OVERSIGHT), json={**strict, "name": "U192 bad", "bars": {"overall_recall": 7, "types": {}}})
        check("limits that are not well formed are refused with the reason", bad.status_code == 400 and "from 0 to 1" in reasons_of(bad), reasons_of(bad)[:100])
        noreason = api("POST", "/release-bars", headers=bearer_for(OVERSIGHT), json={**strict, "name": "U192 no reason", "reason": ""})
        check("a bar with no reason is refused", noreason.status_code == 422, f"HTTP {noreason.status_code}")

        project = api("POST", "/release-bars", headers=bearer_for(OVERSIGHT), json={
            "name": f"U192 project A {uuid.uuid4().hex[:6]}", "reason": "a closed research team",
            "bars": {"overall_recall": 0.95, "types": {"DATE": {"recall": 0.90, "max_leaks": None}, "PERSON": {"recall": None, "max_leaks": 0}}}})
        project_bar = project.json()["release_bar"]
        made_bars.append(project_bar["id"])
        check("a second, looser bar can be made beside it", project.status_code == 201, f"HTTP {project.status_code}")

        heading("U192: a change is a new version, and nothing is edited")
        changed = api("POST", f"/release-bars/{public['id']}/versions", headers=bearer_for(REVIEWER), json={"reason": "x", "bars": strict["bars"]})
        check("the reviewer cannot change a bar", changed.status_code == 403, f"HTTP {changed.status_code}")
        v2 = api("POST", f"/release-bars/{public['id']}/versions", headers=bearer_for(OVERSIGHT),
                 json={"reason": "dates are now allowed one in a hundred", "bars": {"overall_recall": 0.98, "types": {"DATE": {"recall": 0.99, "max_leaks": None}}}})
        check("the data protection officer adds a version", v2.status_code == 201 and v2.json()["release_bar"]["version"] == 2, f"HTTP {v2.status_code}: {v2.text[:100]}")
        versions = api("GET", f"/release-bars/{public['id']}/versions", headers=bearer_for(REVIEWER)).json()["versions"]
        check("both versions are kept, newest first", [v["version"] for v in versions] == [2, 1], str([v["version"] for v in versions]))
        check("and version 1 still says what it said", versions[1]["bars"]["overall_recall"] == 0.99 and versions[1]["reason"] == strict["reason"])
        check("each says who set it", all(v["set_by"] == OVERSIGHT for v in versions))
        check("a version of a bar that does not exist is a 404", api("POST", f"/release-bars/{uuid.uuid4()}/versions", headers=bearer_for(OVERSIGHT), json={"reason": "x", "bars": strict["bars"]}).status_code == 404)
        check("and so is one that is not even an identifier", api("GET", "/release-bars/not-an-id/versions", headers=bearer_for(REVIEWER)).status_code == 404)
        with db() as conn:
            try:
                conn.execute("update gate_purpose_version set bars = '{}'::jsonb where purpose_id = %s and version = 1", (public["id"],))
                refused = False
            except pgdb.errors.DatabaseError as exc:
                refused = "never edited" in str(exc)
        check("the database refuses an edit of a version, with the API bypassed", refused)
        with db() as conn:
            try:
                conn.execute("update gate_purpose set name = 'renamed' where id = %s", (public["id"],))
                refused = False
            except pgdb.errors.DatabaseError as exc:
                refused = "never edited" in str(exc)
        check("and of a bar's name", refused)

        # ------------------------------------------------------------------ the judgement of a waiting result
        version = fixture_version(CANARY, schema_id)["id"]
        decision = new_decision(version)
        heading("U192: a waiting result is judged under the bar chosen, here")
        built_in = api("GET", f"/gate-decisions/{decision}/judgement", headers=bearer_for(REVIEWER)).json()
        check("with no bar named, it is the recommendation the run recorded", built_in["recommendation"] == "fail" and built_in["bar"]["name"] == "Platform default" and built_in["worked_out"] is False, str(built_in)[:160])
        loose = api("GET", f"/gate-decisions/{decision}/judgement", headers=bearer_for(REVIEWER), params={"release_bar_id": project_bar["id"]}).json()
        check("under the looser bar the same counts pass", loose["recommendation"] == "pass" and loose["worked_out"] is True and loose["bar"]["name"] == project_bar["name"], str(loose)[:200])
        strict_now = api("GET", f"/gate-decisions/{decision}/judgement", headers=bearer_for(REVIEWER), params={"release_bar_id": public["id"]}).json()
        check("under the stricter bar, version 2, they fail and the reason names dates", strict_now["recommendation"] == "fail" and strict_now["bar"]["version"] == 2 and "a date: removed 96.0%" in strict_now["reason"], str(strict_now)[:220])
        check("a bar that is not the organisation's is a 404", api("GET", f"/gate-decisions/{decision}/judgement", headers=bearer_for(REVIEWER), params={"release_bar_id": str(uuid.uuid4())}).status_code == 404)
        check("and so is a decision that is not", api("GET", f"/gate-decisions/{uuid.uuid4()}/judgement", headers=bearer_for(REVIEWER)).status_code == 404)

        nothing = new_decision(version, recommendation="not_measured", measured=False)
        unmeasured = api("GET", f"/gate-decisions/{nothing}/judgement", headers=bearer_for(REVIEWER), params={"release_bar_id": project_bar["id"]}).json()
        check("a result nothing measured stays not measured under every bar", unmeasured["recommendation"] == "not_measured" and unmeasured["worked_out"] is False, str(unmeasured)[:160])

        old = new_decision(version, per_entity=None)
        refused = api("GET", f"/gate-decisions/{old}/judgement", headers=bearer_for(REVIEWER), params={"release_bar_id": project_bar["id"]})
        check("a result with no counts cannot be judged by a bar, and says why in words", refused.status_code == 409 and "has no counts for each kind of identifier" in reasons_of(refused), reasons_of(refused)[:120])
        check("but is judged by the built-in bar as it always was", api("GET", f"/gate-decisions/{old}/judgement", headers=bearer_for(REVIEWER)).json()["recommendation"] == "fail")

        held_uncounted = api("POST", f"/gate-decisions/{old}/refuse", headers=bearer_for(REVIEWER), json={"reason": "held back, nothing was counted by kind"})
        uncounted = row_for(old)
        check("a result with no counts, decided with no bar chosen, records no bar: there was nothing to judge it by",
              held_uncounted.status_code == 201 and uncounted["purpose_name"] is None and uncounted["bars_used"] is None and uncounted["release_recommendation"] is None,
              f"HTTP {held_uncounted.status_code}: {uncounted['purpose_name']} / {uncounted['release_recommendation']}")
        check("and the judgement says it carried no counts", api("GET", f"/gate-decisions/{nothing}/judgement", headers=bearer_for(REVIEWER)).json()["counted"] is False)

        heading("U192: the bar chosen changes what stops a release")
        found = {"blocking": [], "files": [{"record_id": "r-1"}], "opened": []}
        row = {"recommendation": "fail"}
        check("a failing recommendation with nothing opened stops a release", len(release_review.blockers(found, row)) == 1)
        check("the same result under a bar it passes is not stopped", release_review.blockers(found, row, "pass") == [])
        check("a passing recommendation under a bar it fails is stopped", len(release_review.blockers(found, {"recommendation": "pass"}, "fail")) == 1)
        check("a result nothing measured is stopped, with its own words", "No automatic measurement" in " ".join(release_review.blockers(found, row, "not_measured")))
        check("opening a file lifts it", release_review.blockers({**found, "opened": ["r-1"]}, row) == [])

        # ------------------------------------------------------------------ what a decision keeps
        heading("U192: a decision keeps the bar it was judged by")
        promoted = api("POST", f"/gate-decisions/{decision}/promote", headers=bearer_for(REVIEWER), json={"reason": "a closed research team", "release_bar_id": project_bar["id"]})
        check("the reviewer releases it under the looser bar", promoted.status_code == 201, f"HTTP {promoted.status_code}: {promoted.text[:160]}")
        kept = row_for(decision)
        check("the decision keeps which bar and which version", kept["purpose_name"] == project_bar["name"] and kept["purpose_version"] == 1 and str(kept["purpose_id"]) == project_bar["id"])
        check("and the limits as they were", kept["bars_used"] == project_bar["bars"], str(kept["bars_used"])[:120])
        check("and the recommendation under that bar, beside the one the run recorded", kept["release_recommendation"] == "pass" and kept["recommendation"] == "fail" and "identifying detail" in kept["release_reason"], f"{kept['release_recommendation']} / {kept['recommendation']}")
        api("POST", f"/release-bars/{project_bar['id']}/versions", headers=bearer_for(OVERSIGHT), json={"reason": "tightened afterwards", "bars": {"overall_recall": 0.999, "types": {}}})
        after = row_for(decision)
        check("a later version of the bar does not change what the decision says", after["purpose_version"] == 1 and after["bars_used"] == project_bar["bars"] and after["release_recommendation"] == "pass")
        detail = api("GET", f"/gate-decisions/{decision}", headers=bearer_for(REVIEWER)).json()
        check("and the page's own read of it carries all of that", detail["purpose_name"] == project_bar["name"] and detail["release_recommendation"] == "pass" and detail["bars_used"] == project_bar["bars"])

        built_in_decision = new_decision(fixture_version(CANARY, schema_id)["id"])
        api("POST", f"/gate-decisions/{built_in_decision}/refuse", headers=bearer_for(REVIEWER), json={"reason": "held back under the built-in bar"})
        kept = row_for(built_in_decision)
        check("a decision with no bar chosen records the platform's built-in bar by name", kept["state"] == "refused" and kept["purpose_name"] == "Platform default" and kept["purpose_version"] is None and kept["purpose_id"] is None, str({k: kept[k] for k in ("state", "purpose_name", "purpose_version")}))
        check("with its limits as they were", kept["bars_used"] == gate_bars.default_bars())

        strict_decision = new_decision(fixture_version(CANARY, schema_id)["id"])
        held = api("POST", f"/gate-decisions/{strict_decision}/promote", headers=bearer_for(REVIEWER), json={"reason": "to the public", "release_bar_id": public["id"]})
        kept = row_for(strict_decision)
        check("a release under a bar the result fails is still the person's to make: a fail is advice, and it is recorded as a fail",
              held.status_code == 201 and kept["state"] == "promoted" and kept["release_recommendation"] == "fail" and kept["purpose_version"] == 2,
              f"HTTP {held.status_code}: {reasons_of(held)[:120]} / {kept['state']} / {kept['release_recommendation']} / version {kept['purpose_version']}")
        elsewhere = api("POST", f"/gate-decisions/{new_decision(fixture_version(CANARY, schema_id)['id'])}/promote", headers=bearer_for(REVIEWER),
                        json={"reason": "a bar that is not ours", "release_bar_id": str(uuid.uuid4())})
        check("naming a bar the organisation does not have refuses the decision", elsewhere.status_code == 404, f"HTTP {elsewhere.status_code}")
    finally:
        teardown()
    return summary("U192")


if __name__ == "__main__":
    sys.exit(main())
