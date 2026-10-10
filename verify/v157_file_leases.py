"""U164: a person opens a few of a version's real files under a lease, and a lease is nothing more than that.

Real files are not opened by a role alone. A person asks for a few of them for a few hours, for a stated purpose; an approver of the department that owns
them, who is not that person, decides and may grant less; the platform draws the files and fixes them; and the lease ends on its own or when somebody ends
it. While it is in force the file door lists and opens exactly the files it names, and nothing else, and writes every opening to the log under the lease.

Checked here one item at a time, against the live platform: the limits and who may ask and decide, that the draw is the platform's and can be reproduced,
what the door lists, opens and refuses, that what is streamed is exactly what was stored before it was locked, what is written down, that a lease that is
ended or has run out stops what was playing at its next request, and that a lease is not a development sample: holding one never lets a pipeline version
that is still in development run on the real files.

    docker compose exec -T munitas-api python /verify/v157_file_leases.py
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

import httpx  # noqa: E402

from common import API, CANARY, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402
from v128_person_keys_erasure import CUSTODIAN, ENGINEER, RESEARCHER, register_dataset, reset_keys  # noqa: E402
from v137_locked_files import make_wav, upload_whole  # noqa: E402
from v140_custom_steps_and_files import department, reasons_of, upload_version  # noqa: E402
from v142_development_samples import YAML  # noqa: E402
from app import development_samples  # noqa: E402


def stream(handle: str) -> httpx.Response:
    return httpx.get(f"{API}/record-stream/{handle}", timeout=60.0)


def records(version: str, who: str) -> httpx.Response:
    return api("GET", f"/dataset-versions/{version}/records", headers=bearer_for(who))


def open_it(version: str, record: str, who: str) -> httpx.Response:
    return api("POST", f"/dataset-versions/{version}/records/{record}/open", headers=bearer_for(who))


def ask(version: str, who: str, **over) -> httpx.Response:
    body = {"kind": "console", "purpose": "listen to a few calls for a study of how long a call runs", "records": 5, "hours": 1, **over}
    return api("POST", f"/dataset-versions/{version}/file-leases", json=body, headers=bearer_for(who))


def decide(lease: str, who: str, verb: str, **body) -> httpx.Response:
    return api("POST", f"/file-leases/{lease}/{verb}", json=body, headers=bearer_for(who))


def row(lease: str) -> dict:
    with db() as conn:
        return conn.execute("select * from file_lease where id = %s", (lease,)).fetchone()


def count(sql: str, params: tuple = ()) -> int:
    with db() as conn:
        return conn.execute(sql, params).fetchone()["n"]


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    from app import db as app_db
    if app_db.pool.closed:
        app_db.pool.open()
    tag = uuid.uuid4().hex[:8]

    heading("Twelve files of twelve people, sealed as one version")
    dataset = register_dataset(ENGINEER)
    plain = {}
    for i in range(12):
        record = f"r-{i + 1:04d}"
        plain[record] = make_wav(40_000 + i * 3_000)
        upload_whole(dataset, f"{record}.wav", plain[record], f"L-{tag}-{i}")
    sealed = api("POST", f"/datasets/{dataset}/seal-files", headers=bearer_for(ENGINEER))
    check("they seal", sealed.status_code == 201, sealed.text[:160])
    src = sealed.json()["id"]

    heading("The limits, and who may ask")
    asked = {
        "a console lease for 11 files": ask(src, RESEARCHER, records=11),
        "a console lease for 2 hours": ask(src, RESEARCHER, hours=2),
        "a lease for no files": ask(src, RESEARCHER, records=0),
        "a lease with no purpose": ask(src, RESEARCHER, purpose="   "),
        "a lease for a download": ask(src, RESEARCHER, kind="download"),
    }
    for label, r in asked.items():
        check(f"{label} is refused, in words", r.status_code == 403 and bool(r.json()["detail"]["reasons"]), f"{r.status_code} {reasons_of(r)[:150]}")
    check("the refusal for 11 files says what the limit is", "a console lease holds between 1 and 10 files" in reasons_of(asked["a console lease for 11 files"]))
    check("the refusal for 2 hours says what the limit is", "a console lease lasts at most 1 hour" in reasons_of(asked["a console lease for 2 hours"]))
    too_many = ask(src, RESEARCHER, kind="notebook", records=201, hours=4)
    check("a lease for a notebook holds at most 200 files, and the refusal says so", too_many.status_code == 403 and "a notebook lease holds between 1 and 200 files" in reasons_of(too_many), f"{too_many.status_code} {reasons_of(too_many)[:150]}")
    too_long = ask(src, RESEARCHER, kind="notebook", records=50, hours=9)
    check("and lasts at most 8 hours, and the refusal says so", too_long.status_code == 403 and "a notebook lease lasts at most 8 hours" in reasons_of(too_long), f"{too_long.status_code} {reasons_of(too_long)[:150]}")
    check("a request with no session is refused", httpx.post(f"{API}/dataset-versions/{src}/file-leases", json={"kind": "console", "purpose": "x", "records": 1, "hours": 1}, timeout=30.0).status_code == 401)
    check("a version that does not exist is not found", api("POST", f"/dataset-versions/{uuid.uuid4()}/file-leases", json={"kind": "console", "purpose": "x", "records": 1, "hours": 1},
                                                              headers=bearer_for(RESEARCHER)).status_code == 404)
    screen = api("GET", f"/dataset-versions/{src}/file-lease", headers=bearer_for(RESEARCHER)).json()
    check("the page is told the limits, the kinds that can be asked for, and that somebody can decide",
          screen["kinds"] == ["console", "notebook"] and screen["caps"] == {"console": {"records": 10, "hours": 1}, "notebook": {"records": 200, "hours": 8}} and screen["has_approver"] is True and screen["leases"] == [], str(screen)[:220])

    heading("Before it is decided, the files stay closed")
    first = ask(src, RESEARCHER, records=5)
    check("a researcher asks for 5 files for an hour, and the request waits", first.status_code == 201 and first.json()["status"] == "requested", f"{first.status_code} {first.text[:160]}")
    lease = first.json()["id"]
    check("the request names the table, by name and version, and the person, by name", first.json()["dataset_version"] == 1 and bool(first.json()["dataset_name"]) and first.json()["principal_label"] != RESEARCHER)
    again = ask(src, RESEARCHER, records=3)
    check("asking again for the same files the same way is refused: one at a time", again.status_code == 409, f"{again.status_code} {reasons_of(again)[:140]}")
    listed = records(src, RESEARCHER).json()
    check("until it is decided they may list the files but not open them, and the reason points at asking an approver", listed["basis"] == "none" and listed["may_open"] is False and "has allowed it" in " ".join(listed["reasons"]) and listed["total"] == 12)
    before = count("select count(*) as n from record_read_log where tenant_id = %s and principal = %s", (CANARY, RESEARCHER))
    check("opening one is refused", open_it(src, "r-0001", RESEARCHER).status_code == 403)
    check("and nothing was opened", count("select count(*) as n from record_read_log where tenant_id = %s and principal = %s", (CANARY, RESEARCHER)) == before)

    heading("Deciding it")
    own = decide(lease, RESEARCHER, "approve")
    check("the person who asked may not decide it", own.status_code == 403 and "nobody decides their own request" in reasons_of(own), f"{own.status_code} {reasons_of(own)[:140]}")
    check("a data engineer, who does not answer for the department's data, may not either", decide(lease, ENGINEER, "approve").status_code == 403)
    wait_view = api("GET", "/file-leases", headers=bearer_for(CUSTODIAN)).json()
    check("the approver sees it waiting for them, and the researcher sees it among their own",
          any(r["id"] == lease for r in wait_view["to_decide"]) and any(r["id"] == lease for r in api("GET", "/file-leases", headers=bearer_for(RESEARCHER)).json()["mine"]))
    check("declining needs a note saying why", decide(lease, CUSTODIAN, "decline").status_code == 400)
    more = decide(lease, CUSTODIAN, "approve", records=6)
    check("an approver may not raise what was asked for", more.status_code == 400 and "never raise it" in reasons_of(more), f"{more.status_code} {reasons_of(more)[:140]}")
    approved = decide(lease, CUSTODIAN, "approve", records=3, note="three is enough for a study of call length")
    check("they grant 3 of the 5, for the hour", approved.status_code == 200 and approved.json()["status"] == "approved" and approved.json()["granted_count"] == 3 and approved.json()["granted_hours"] == 1, f"{approved.status_code} {approved.text[:200]}")
    chosen = approved.json()["records"]
    check("the platform drew 3 different files of this version", len(chosen) == 3 == len(set(chosen)) and set(chosen) <= set(plain), str(chosen))
    stored = row(lease)
    with db() as conn:
        candidates = [{"record_id": r["record_id"], "person_code": r["person_code"], "bytes": int(r["plain_bytes"]), "erased": False}
                      for r in conn.execute("select record_id, person_code, plain_bytes from file_person where dataset_version_id = %s order by record_id", (src,)).fetchall()]
    again_drawn = development_samples.draw(candidates, 3, stored["salt"])
    check("the draw is reproduced from the salt kept with the lease, so it was not hand-picked", again_drawn["chosen"] == sorted(chosen), f"{again_drawn['chosen']} vs {sorted(chosen)}")
    check("it ends an hour from now, no more", stored["status"] == "approved" and 3500 < (stored["expires_at"] - stored["decided_at"]).total_seconds() <= 3600)
    check("a request already decided cannot be decided again", decide(lease, CUSTODIAN, "approve").status_code == 409)

    heading("What the door lists, opens and refuses under the lease")
    body = records(src, RESEARCHER).json()
    check("the researcher is told why they may open, and sees exactly the 3 files, with what the lease is",
          body["basis"] == "lease" and body["may_open"] is True and sorted(r["record_id"] for r in body["records"]) == sorted(chosen) and body["total"] == 3
          and body["lease"]["kind"] == "console" and body["lease"]["files"] == 3 and body["lease"]["id"] == lease, str(body)[:260])
    others = records(src, CUSTODIAN).json()
    check("somebody else, the approver included, is not helped by it: it is the researcher's", others["basis"] == "none" and others["may_open"] is False and others["total"] == 12)
    outside = sorted(set(plain) - set(chosen))[0]
    refused = open_it(src, outside, RESEARCHER)
    check("a file the lease does not name is refused, and the reason says so", refused.status_code == 403 and "not one of the files your lease opens" in reasons_of(refused), f"{refused.status_code} {reasons_of(refused)[:150]}")
    check("the refusal is written down, with who and why", count(
        "select count(*) as n from access_decision where tenant_id = %s and principal = %s and purpose = 'open a record' and allowed = false and dataset_version_id = %s and reasons::text like %s",
        (CANARY, RESEARCHER, src, "%one of the files your lease opens%")) == 1)
    inside = chosen[0]
    opened = open_it(src, inside, RESEARCHER)
    check("a file it names opens, and the answer gives its size and type", opened.status_code == 200 and opened.json()["bytes"] == len(plain[inside]) and opened.json()["content_type"] == "audio/wav", f"{opened.status_code} {opened.text[:160]}")
    handle = opened.json()["handle"]
    played = stream(handle)
    check("what is streamed is exactly what was stored before it was locked", played.status_code == 200 and played.content == plain[inside], f"{played.status_code} {len(played.content)} bytes")
    log = row_log(src, inside)
    check("the opening is in the log: who, which file, under which lease, and why", log is not None and log["basis"] == "lease" and str(log["file_lease_id"]) == lease and log["principal"] == RESEARCHER and log["locked"] is True, str(log))
    check("the number of files a lease opens never grows: the other nine stay closed however many are asked for", all(
        open_it(src, r, RESEARCHER).status_code == 403 for r in sorted(set(plain) - set(chosen))))

    heading("A lease ends when it is ended, and when it runs out")
    ended = api("POST", f"/file-leases/{lease}/end", headers=bearer_for(CUSTODIAN))
    check("it is ended", ended.status_code == 200 and ended.json()["status"] == "ended" and ended.json()["ended_by"] == CUSTODIAN, f"{ended.status_code} {ended.text[:160]}")
    check("what was being played is stopped at its next request", stream(handle).status_code == 403)
    after = records(src, RESEARCHER).json()
    check("the files are closed again, and the reason points at asking an approver", after["basis"] == "none" and after["may_open"] is False and "has allowed it" in " ".join(after["reasons"]))
    check("ending it again is refused", api("POST", f"/file-leases/{lease}/end", headers=bearer_for(CUSTODIAN)).status_code == 409)
    second = ask(src, RESEARCHER, records=2)
    check("the person may ask again once it has ended", second.status_code == 201, f"{second.status_code} {second.text[:140]}")
    approved2 = decide(second.json()["id"], CUSTODIAN, "approve")
    check("and it is approved for what they asked", approved2.status_code == 200 and approved2.json()["granted_count"] == 2)
    second_id = second.json()["id"]
    live = open_it(src, approved2.json()["records"][0], RESEARCHER)
    check("it opens", live.status_code == 200, f"{live.status_code} {live.text[:120]}")
    with db() as conn:
        conn.execute("update file_lease set expires_at = now() - interval '1 minute' where id = %s", (second_id,))
    check("with the time gone, what was playing stops at its next request, before any tidy has run", stream(live.json()["handle"]).status_code == 403)
    check("and the files are closed", records(src, RESEARCHER).json()["basis"] == "none")
    mine = api("GET", "/file-leases", headers=bearer_for(RESEARCHER)).json()["mine"]
    check("it shows as ended, not in force", [r["status"] for r in mine if r["id"] == second_id] == ["ended"], str([(r["id"][:6], r["status"]) for r in mine]))
    nobody = api("POST", f"/file-leases/{second_id}/end", headers=bearer_for(RESEARCHER))
    check("what has run out cannot be ended again", nobody.status_code == 409)

    heading("A lease is not a development sample")
    asked3 = ask(src, ENGINEER, records=4)
    got = decide(asked3.json()["id"], CUSTODIAN, "approve")
    check("an engineer holds a lease on the real files", asked3.status_code == 201 and got.status_code == 200 and got.json()["status"] == "approved", f"{asked3.status_code} {got.status_code}")
    check("the platform's own answer to 'may a pipeline in development run on this?' is still no", development_samples.is_development_data(CANARY, ENGINEER, src) is False)
    pipeline = api("POST", "/pipelines/register", json={"tenant_id": CANARY, "name": f"u164-{tag}", "department_id": department(), "registered_by": ENGINEER}, headers=bearer_for(ENGINEER))
    dev = upload_version(pipeline.json()["id"], YAML)
    check("a pipeline version that is still in development exists", dev.status_code == 201 and dev.json()["stage"] == "development", dev.text[:160])
    started = api("POST", f"/datasets/{dataset}/versions/{src}/deidentify", json={"pipeline_kind": "dag", "pipeline_version_id": dev.json()["id"]}, headers=bearer_for(ENGINEER))
    check("and a run of it on the real files is refused, pointing at a sample, though the engineer holds a lease on them",
          started.status_code == 400 and "development data" in reasons_of(started) and "Ask for a sample" in reasons_of(started), f"{started.status_code} {reasons_of(started)[:200]}")
    with db() as conn:
        samples = conn.execute("select count(*) as n from development_sample where principal = %s and source_version_id = %s", (ENGINEER, src)).fetchone()["n"]
    check("the lease made no sample, and no sample was needed to hold it", samples == 0)

    heading("What the log says about it, and who can see what")
    with db() as conn:
        under = conn.execute("select count(*) as n from record_read_log where file_lease_id = %s", (lease,)).fetchone()["n"]
    check("every opening under the first lease names it", under >= 1)
    check("another organisation's person is told there is no such version", api("GET", f"/dataset-versions/{src}/file-lease", headers=bearer_for("neighbour-custodian")).status_code == 404)
    check("and cannot see its leases", all(r["dataset_version_id"] != src for r in api("GET", "/file-leases", headers=bearer_for("neighbour-custodian")).json()["to_decide"]))

    heading("What the audit evidence says about it")
    evidence = api("GET", "/audit/locked-columns?limit=500", headers=bearer_for("canary-dpo"))
    check("the data protection officer reads the audit evidence", evidence.status_code == 200, f"{evidence.status_code} {evidence.text[:120]}")
    ev = evidence.json()
    check("somebody who is not the auditor or the officer does not", api("GET", "/audit/locked-columns", headers=bearer_for(ENGINEER)).status_code == 403)
    opened_ev = [e for e in ev["file_reads"] if e["allowed"] and e["lease_id"] == lease]
    check("the evidence lists the openings under the lease: who, where, why it was allowed, and in which table",
          len(opened_ev) >= 1 and all(e["principal_label"] == "Canary researcher" and e["via"] == "console" and e["basis"] == "lease" and e["table"] and ", version " in e["table"] for e in opened_ev),
          str(opened_ev[:1])[:260])
    refused_ev = [e for e in ev["file_reads"] if not e["allowed"] and e["reason"] and "not one of the files your lease opens" in e["reason"]]
    check("and the refusal of a file the lease does not name, with its reason", len(refused_ev) >= 1 and all(e["principal_label"] == "Canary researcher" and e["table"] for e in refused_ev))
    check("the newest line is first", [e["at"] for e in ev["file_reads"]] == sorted((e["at"] for e in ev["file_reads"]), reverse=True))
    mine = [l for l in ev["file_leases"] if l["id"] == lease]
    check("the lease itself is listed with who asked, what for, how many files for how long, and who decided it",
          len(mine) == 1 and mine[0]["principal_label"] == "Canary researcher" and mine[0]["kind"] == "console" and mine[0]["purpose"] and mine[0]["requested_count"] >= 1
          and mine[0]["decided_by_label"] and mine[0]["decided_at"] and mine[0]["dataset_name"], str(mine)[:300])
    named = [e for e in ev["file_reads"] if e["lease_id"] == lease]
    check("an opening names the file, to the auditor and the officer", len(named) >= 1 and any(e["file"] == inside for e in named), str(named[:2]))
    check("a refusal of a file the lease does not name now names that file", any(not e["allowed"] and e["file"] == outside and e["principal_label"] == "Canary researcher" for e in ev["file_reads"]))
    check("a lease does not list the files it names, and carries nothing of how they were drawn", all("records" not in l and "salt" not in l and "strata" not in l for l in ev["file_leases"]))
    check("the evidence of locked columns says which table each read and each group-size finding is about",
          all("table" in r for r in ev["reads"]) and all("table" in g for g in ev["group_sizes"]) and any(r["table"] for r in ev["reads"]) and any(g["table"] for g in ev["group_sizes"]))
    check("the key log shows people by name, never as the key service writes them, and the researcher's openings of these files are there",
          all(not str(k["actor_label"]).startswith("person ") for k in ev["key_uses"])
          and any(k["action"] == "unwrap" and k["actor_label"] == "Canary researcher" for k in ev["key_uses"]),
          str([k["actor_label"] for k in ev["key_uses"][:6]]))
    with db() as conn:
        src_name = conn.execute("select d.name from dataset_version v join dataset d on d.id = v.dataset_id where v.id = %s", (src,)).fetchone()["name"]
    keys = ev["key_uses"]
    check("each key line says why in words and which table when that is known, and carries none of the platform's own text",
          # The actor's own name is shown in words ("platform release check"), so what must not appear is the platform's wording in the reason, the table and the file.
          all("reason" in k and "table" in k and "purpose" not in k for k in keys)
          and not any(w in json.dumps([{f: k.get(f) for f in ("reason", "table", "file")} for k in keys]) for w in ("lock part", "release check", "open record")))
    mine = [k for k in keys if k["action"] == "unwrap" and k["actor_label"] == "Canary researcher" and k["table"] == f"{src_name}, version 1"]
    check("the researcher's openings of these files are keys opened, with the reason, the way and the table",
          len(mine) >= 1 and all(k["reason"] == "To open a locked file in the page, with an approver's permission" for k in mine), str(mine[:1]))
    check("a key made or destroyed has no reason to give", all(k["reason"] is None for k in keys if k["action"] != "unwrap"))
    return summary("U164")


def row_log(version: str, record: str):
    with db() as conn:
        return conn.execute("select * from record_read_log where version_id = %s and record_id = %s and basis = 'lease' order by at desc limit 1", (version, record)).fetchone()


if __name__ == "__main__":
    sys.exit(main())
