"""U144: a pipeline version in development runs only on development data, and a development sample is drawn by the platform, as a list of pointers.

A pipeline version with a custom step starts in development. There it may run only on recordings declared synthetic, or on a sample of real ones that an
approver of the department that owns them approved for the person who asked. The platform draws the sample: one recording for each patient, three length
bands, ranked by a hash of a salt and the identifiers, so the same person asking for the same purpose of the same version gets the same sample. The sample
is a new version that names the same stored files. Nothing is copied, nothing is stored in the clear, and erasing a patient makes their recording unreadable
in the sample too.

Checked here one item at a time: the draw by itself, who may ask and who may decide, that the sample holds pointers and not copies, that a run in
development reads only the sample and only for the person it was drawn for, that it stops at the next request when the sample ends or runs out, erasure,
and the declaration of synthetic recordings. U145 runs a real step on the host.

    docker compose exec -T munitas-api python /verify/v142_development_samples.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

import httpx  # noqa: E402

from common import API, CANARY, WORKER_HEADERS, api, bearer_for, bucket_for, check, db, fixture_tenant, heading, require_api, s3_client, summary, ADMIN  # noqa: E402
from v128_person_keys_erasure import CUSTODIAN, ENGINEER, RESEARCHER, identity, register_dataset, reset_keys  # noqa: E402
from v137_locked_recordings import KIND, fixture_version_id, make_wav, upload_whole  # noqa: E402
from v140_custom_steps_and_recordings import (ELSEWHERE, WORKLOAD, YAML, department, listing, make_pass, opening, pass_for, ranged,  # noqa: E402
                                              reasons_of, upload_version)
from app import development_samples, person_keys  # noqa: E402

RUN_DATASET = f"u144-{uuid.uuid4().hex[:8]}"


def open_run_as(source: str, pipeline_version_id: str | None, who: str) -> dict:
    made = httpx.post(f"{API}/pipeline-runs", headers=WORKER_HEADERS, timeout=30.0, json={
        "tenant_id": CANARY, "dataset": RUN_DATASET, "workflow_id": f"u144-{uuid.uuid4()}", "trigger_kind": "manual",
        "triggered_by": who, "input_versions": [source], "principal": WORKLOAD})
    made.raise_for_status()
    run = made.json()
    with db() as conn:
        conn.execute("update pipeline_run set pipeline_version_id = %s, source_version_id = %s where id = %s", (pipeline_version_id, source, run["pipeline_run_id"]))
    return {"id": run["pipeline_run_id"], "token": run["task_credential"]}


def ask(who: str, version: str, kind: str = "develop", days: int = 5, records: int = 4, named: list[str] | None = None, purpose: str = "tune the redaction step") -> httpx.Response:
    body = {"kind": kind, "purpose": purpose, "days": days, "records": records}
    if named is not None:
        body["named_records"] = named
    return api("POST", f"/dataset-versions/{version}/development-samples", json=body, headers=bearer_for(who))


def decide(who: str, sample: str, verb: str, **body) -> httpx.Response:
    return api("POST", f"/development-samples/{sample}/{verb}", json=body, headers=bearer_for(who))


def sample_row(sample: str) -> dict:
    with db() as conn:
        return conn.execute("select * from development_sample where id = %s", (sample,)).fetchone()


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    from app import db as app_db
    if app_db.pool.closed:
        app_db.pool.open()
    tag = uuid.uuid4().hex[:8]

    # ---------------------------------------------------------------------------------------------- the draw alone
    heading("The draw, by itself")
    people = [f"P{i:03d}" for i in range(120)]
    pool = []
    for i in range(200):
        person = people[i % 120]
        pool.append({"record_id": f"r{i:04d}", "person_code": person, "bytes": 1000 + (i * 7919) % 90000})
    salt = development_samples.salt_for("v", "someone", "develop")
    first = development_samples.draw(pool, 20, salt)
    chosen = first["chosen"]
    owner = {c["record_id"]: c["person_code"] for c in pool}
    check("it chooses exactly the number asked for", len(chosen) == 20 and len(set(chosen)) == 20, str(len(chosen)))
    check("never two recordings of one patient", len({owner[r] for r in chosen}) == 20)
    check("twenty is divided 7, 7 and 6 across short, medium and long", [s["chosen"] for s in first["strata"]] == [7, 7, 6] and [s["band"] for s in first["strata"]] == ["short", "medium", "long"], str(first["strata"]))
    everything = development_samples.draw(pool, 10_000, salt)
    check("taking all of it gives one recording for each patient, which is the pool the bands are cut from", len(everything["chosen"]) == 120 == everything["pool"] == everything["people"])
    by_length = sorted(everything["chosen"], key=lambda r: next((c["bytes"], c["record_id"]) for c in pool if c["record_id"] == r))
    thirds = [set(by_length[:40]), set(by_length[40:80]), set(by_length[80:])]
    check("the bands are thirds of that pool by length, and the sample takes its share from each", [len(set(chosen) & t) for t in thirds] == [7, 7, 6], str([len(set(chosen) & t) for t in thirds]))
    check("the same salt gives the same sample", development_samples.draw(pool, 20, salt)["chosen"] == chosen)
    check("another salt gives another sample", development_samples.draw(pool, 20, development_samples.salt_for("v", "someone else", "develop"))["chosen"] != chosen)
    check("the rank is the SHA-256 of the salt, the patient and the recording", development_samples.rank("s", "P", "r") == hashlib.sha256(b"s|P|r").hexdigest())
    check("when fewer qualify, it gives what there is", len(development_samples.draw(pool[:5], 20, salt)["chosen"]) == 5)
    check("and nothing when nothing does", development_samples.draw([], 20, salt)["chosen"] == [])
    tally: dict[str, int] = {}
    uniform = [{"record_id": f"u{i:03d}", "person_code": f"U{i:03d}", "bytes": 1000 + i} for i in range(60)]
    for n in range(600):
        for r in development_samples.draw(uniform, 6, f"salt-{n}")["chosen"]:
            tally[r] = tally.get(r, 0) + 1
    rates = [tally.get(c["record_id"], 0) / 600 for c in uniform]
    check("over many salts every recording is chosen about as often as any other in its band, so nothing favours any", 0.04 < min(rates) and max(rates) < 0.17, f"{min(rates):.3f} to {max(rates):.3f}")

    # ---------------------------------------------------------------------------------------------- recordings to draw from
    heading("Recordings to draw from: twelve, from eight patients, two with an answer key")
    dataset = register_dataset(ENGINEER)
    patients = [f"S-{tag}-{i}" for i in range(8)]
    plan = [("r-0001", 0), ("r-0002", 0), ("r-0003", 0), ("r-0004", 1), ("r-0005", 1), ("r-0006", 2), ("r-0007", 2), ("r-0008", 3), ("r-0009", 4), ("r-0010", 5), ("r-0011", 6), ("r-0012", 7)]
    plain: dict[str, bytes] = {}
    for n, (record, p) in enumerate(plan):
        plain[record] = make_wav(20_000 + n * 9_000)
        upload_whole(dataset, f"{record}.wav", plain[record], patients[p])
    truth = json.dumps({"reference_transcript": "the patient was seen on Tuesday", "spans": [], "hazard": "none"}).encode()
    for record, p in (("r-0001", 0), ("r-0004", 1)):
        upload_whole(dataset, f"{record}.truth.json", truth, patients[p])
    sealed = api("POST", f"/datasets/{dataset}/seal-recordings", headers=bearer_for(ENGINEER))
    check("the recordings seal", sealed.status_code == 201, sealed.text[:200])
    src = sealed.json()["id"]
    with db() as conn:
        source_row = conn.execute("select object_manifest, storage_prefix, visibility_class from dataset_version where id = %s", (src,)).fetchone()
        owners = {r["record_id"]: r["person_code"] for r in conn.execute("select record_id, person_code from recording_person where dataset_version_id = %s", (src,)).fetchall()}
    person_codes = set(owners.values())

    # ---------------------------------------------------------------------------------------------- asking
    heading("Who may ask, and for what")
    check("a version that holds no locked recordings has no samples", ask(ENGINEER, fixture_version_id()).status_code == 404)
    check("a researcher, who holds no role that may ask, is refused", ask(RESEARCHER, src).status_code == 403)
    r = ask(ENGINEER, src, days=15)
    check("more than 14 days is refused, and the answer says the limit", r.status_code == 403 and "between 1 and 14 days" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    r = ask(ENGINEER, src, records=51)
    check("more than 50 recordings is refused", r.status_code == 403 and "between 1 and 50 recordings" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    check("a sample needs a purpose", ask(ENGINEER, src, purpose="  ").status_code == 403)
    check("a kind that is neither developing nor debugging is refused", ask(ENGINEER, src, kind="anything").status_code == 403)
    r = ask(ENGINEER, src, kind="debug", records=2)
    check("a debug sample must name the recordings that went wrong", r.status_code == 403 and "names the recordings" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    r = ask(ENGINEER, src, kind="debug", records=2, named=["r-0001", "r-9999"])
    check("a recording that is not in this version is refused, and named", r.status_code == 400 and "r-9999 is not a recording of this version" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    made = ask(ENGINEER, src, records=4, days=5)
    check("an engineer's request is taken, and waits", made.status_code == 201 and made.json()["status"] == "requested" and made.json()["principal"] == ENGINEER, f"{made.status_code} {made.text[:200]}")
    first_request = made.json()["id"]
    r = ask(ENGINEER, src)
    check("asking twice for the same thing is refused while one is waiting", r.status_code == 409, f"{r.status_code} {reasons_of(r)[:160]}")
    body = made.text
    check("the answer holds no patient code and no recording", not any(code in body for code in person_codes) and "r-00" not in body)

    # ---------------------------------------------------------------------------------------------- deciding
    heading("Who may decide")
    r = decide(ENGINEER, first_request, "approve")
    check("nobody decides their own request", r.status_code == 403 and "nobody decides their own request" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    check("a researcher may not decide", decide(RESEARCHER, first_request, "approve").status_code == 403)
    r = decide(ELSEWHERE, first_request, "approve")
    check("another department's custodian may not, and is told why", r.status_code == 403 and "department that owns these recordings" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    r = decide(CUSTODIAN, first_request, "approve", records=5)
    check("an approver may lower what was asked for, never raise it", r.status_code == 400 and "never raise" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    r = decide(CUSTODIAN, first_request, "approve", days=6)
    check("nor the days", r.status_code == 400)
    check("a request is still waiting after those refusals", sample_row(first_request)["status"] == "requested")
    from app import opa
    both = opa.may_decide_development_sample({"approver": {"id": CUSTODIAN, "roles": ["data_custodian"]}, "request": {"principal": CUSTODIAN}, "dataset": {"approvers": [CUSTODIAN]}})
    check("even an approver does not decide their own request, which matters for a person who holds both roles", both[0] is False and any("nobody decides their own request" in r for r in both[1]), str(both))
    other = opa.may_decide_development_sample({"approver": {"id": CUSTODIAN, "roles": ["data_custodian"]}, "request": {"principal": ENGINEER}, "dataset": {"approvers": [CUSTODIAN]}})
    check("and the same approver does decide somebody else's, so the refusal above is about the person and not about the approver", other[0] is True, str(other))

    named_request = ask(ENGINEER, src, kind="debug", records=2, named=["r-0001", "r-0004"], days=2, purpose="two recordings went wrong in production").json()["id"]
    declined = decide(CUSTODIAN, named_request, "decline", note="not these two")
    check("declining records the note, and the person who declined", declined.status_code == 200 and declined.json()["status"] == "declined" and declined.json()["decision_note"] == "not these two"
          and declined.json()["decided_by"] == CUSTODIAN, f"{declined.status_code} {declined.text[:200]}")
    second_debug = ask(ENGINEER, src, kind="debug", records=2, named=["r-0001", "r-0004"], days=2, purpose="names missed in two recordings")
    check("a declined request no longer occupies the place, so the person may ask again", second_debug.status_code == 201, f"{second_debug.status_code} {second_debug.text[:160]}")
    check("a request decided already cannot be decided again", decide(CUSTODIAN, named_request, "approve").status_code == 409)

    approved = decide(CUSTODIAN, first_request, "approve", records=3, days=3)
    body = approved.json()
    check("the approver approves it, lowering it to three recordings and three days", approved.status_code == 200 and body["status"] == "approved" and body["granted_count"] == 3 and body["granted_days"] == 3,
          f"{approved.status_code} {approved.text[:240]}")
    sample = body
    row = sample_row(first_request)
    check("the platform kept what it needs to reproduce the draw: the method, the salt, the strata and the identifiers chosen",
          row["method"] == "sample:v1" and row["salt"] == development_samples.salt_for(src, ENGINEER, "develop") and sum(s["chosen"] for s in row["strata"]) == 3 and len(row["chosen_records"]) == 3)
    chosen_here = set(row["chosen_records"])
    seconds = (row["expires_at"] - row["decided_at"]).total_seconds()
    check("it ends in three days", 3 * 86400 - 5 < seconds < 3 * 86400 + 5, str(seconds))
    check("the answer says how many came from each band and names nobody", [s["band"] for s in body["strata"]] == ["short", "medium", "long"] and not any(c in approved.text for c in person_codes) and "r-00" not in approved.text)
    with db() as conn:
        stored_rows = conn.execute("select record_id, person_code, plain_bytes from recording_person where dataset_version_id = %s", (src,)).fetchall()
    redrawn = development_samples.draw([{"record_id": r["record_id"], "person_code": r["person_code"], "bytes": int(r["plain_bytes"])} for r in stored_rows], 3, row["salt"])
    check("the draw is reproducible from what was kept: the same salt over the same recordings gives the same three", set(redrawn["chosen"]) == chosen_here, f"{redrawn['chosen']} {sorted(chosen_here)}")
    check("one recording of each patient", len({owners[r] for r in chosen_here}) == len(chosen_here))

    # ---------------------------------------------------------------------------------------------- pointers
    heading("The sample is a list of pointers")
    with db() as conn:
        version = conn.execute("select v.id, v.object_manifest, v.storage_prefix, v.visibility_class, v.record_count, d.name, d.department_id from dataset_version v join dataset d on d.id = v.dataset_id where v.id = %s",
                               (sample["sample_version_id"],)).fetchone()
        src_dept = conn.execute("select d.department_id from dataset_version v join dataset d on d.id = v.dataset_id where v.id = %s", (src,)).fetchone()["department_id"]
        sample_rows = conn.execute("select record_id, person_code, plain_bytes, answer_key_bytes from recording_person where dataset_version_id = %s", (sample["sample_version_id"],)).fetchall()
        source_rows = {r["record_id"]: r for r in conn.execute("select record_id, person_code, plain_bytes, answer_key_bytes from recording_person where dataset_version_id = %s", (src,)).fetchall()}
        lineage = conn.execute("select action_name, input_versions from lineage where dataset_version_id = %s", (sample["sample_version_id"],)).fetchone()
    manifest = {m["key"]: m for m in version["object_manifest"]}
    source_keys = {m["key"] for m in source_row["object_manifest"]}
    own_prefix = version["storage_prefix"] + "/"
    pointed = [k for k in manifest if not k.startswith(own_prefix)]
    check("the sample holds three recordings, and is sealed as the most protected class", version["record_count"] == 3 and version["visibility_class"] == "RAW")
    check("every file it names is a stored file the original already names, and no other", len(pointed) >= 3 and all(k in source_keys for k in pointed), str(pointed))
    check("each of those keys belongs to the original's folder, not the sample's", all(k.startswith(source_row["storage_prefix"]) for k in pointed) and not any(k.startswith(version["storage_prefix"]) for k in pointed))
    client = s3_client(*ADMIN)
    under = [o["Key"] for o in client.list_objects_v2(Bucket=bucket_for(CANARY), Prefix=version["storage_prefix"] + "/").get("Contents", [])]
    check("under the sample's own folder there is no audio and no answer key, only its list and the table that describes it",
          not any(k.endswith(".wav") or k.endswith(".truth.json") for k in under) and any(k.endswith("/records.json") for k in under), str(under[:6]))
    check("the sample's recordings are the chosen ones, with the same patient, size and answer key as in the original",
          {r["record_id"] for r in sample_rows} == chosen_here and all(source_rows[r["record_id"]]["person_code"] == r["person_code"] and source_rows[r["record_id"]]["plain_bytes"] == r["plain_bytes"]
                                                                          and source_rows[r["record_id"]]["answer_key_bytes"] == r["answer_key_bytes"] for r in sample_rows))
    check("an answer key comes with its recording, and only if there is one", all((f"{r['record_id']}.truth.json" in {k.rsplit('/', 1)[-1] for k in pointed}) == (r["answer_key_bytes"] is not None) for r in sample_rows))
    check("the sample belongs to the department of the recordings it was drawn from", version["department_id"] == src_dept)
    check("it records what made it: the development sample action, from the original", lineage and lineage["action_name"] == "development_sample" and [str(v) for v in lineage["input_versions"]] == [src], str(lineage))
    check("the dataset is named for the original and the request", version["name"].endswith(f"-dev-sample-{first_request[:8]}"), version["name"])
    r = ask(ENGINEER, sample["sample_version_id"])
    check("a sample is not drawn from a sample", r.status_code == 400 and "not drawn from a sample" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")

    # ---------------------------------------------------------------------------------------------- what the screen is told
    heading("What a screen is told")
    mine = api("GET", f"/dataset-versions/{src}/development-samples", headers=bearer_for(ENGINEER)).json()
    check("the person it was drawn for sees it, may not decide, and sees the limits", any(s["id"] == first_request for s in mine["mine"]) and mine["may_decide"] is False
          and mine["limits"] == {"days": 14, "records": 50}, str({k: mine[k] for k in ("may_decide", "limits")}))
    approver = api("GET", f"/dataset-versions/{src}/development-samples", headers=bearer_for(CUSTODIAN))
    check("the approver sees what is in force, the counts by band, and nothing that names a patient", any(s["id"] == first_request for s in approver.json()["granted"]) and approver.json()["may_decide"] is True
          and not any(c in approver.text for c in person_codes), approver.text[:200])
    check("a researcher is told they may not ask", api("GET", f"/dataset-versions/{src}/development-samples", headers=bearer_for(RESEARCHER)).json()["may_ask"] is False)
    on_sample = api("GET", f"/dataset-versions/{sample['sample_version_id']}/development-samples", headers=bearer_for(ENGINEER)).json()
    check("on the sample itself the screen is told it is a sample, whose, and when it ends, and offers no asking", on_sample["is_sample"]["mine"] is True and on_sample["is_sample"]["source_version_id"] == src
          and on_sample["is_sample"]["status"] == "approved" and on_sample["may_ask"] is False)

    # ---------------------------------------------------------------------------------------------- running in development
    heading("A version in development runs only on development data")
    pipeline = api("POST", "/pipelines/register", json={"tenant_id": CANARY, "name": f"u144-{tag}", "department_id": department(), "registered_by": ENGINEER}, headers=bearer_for(ENGINEER))
    pid = pipeline.json()["id"]
    dev = upload_version(pid, YAML)
    wv = dev.json()["id"]
    check("the version is in development", dev.status_code == 201 and dev.json()["stage"] == "development", dev.text[:200])
    on_real = open_run_as(src, wv, ENGINEER)
    r = make_pass(on_real)
    check("on the real recordings a step is given no pass, and told it is in development", r.status_code == 403 and "in development" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:200]}")
    started = api("POST", f"/datasets/{dataset}/versions/{src}/deidentify", json={"pipeline_kind": "dag", "pipeline_version_id": wv}, headers=bearer_for(ENGINEER))
    check("and a run of it on the real recordings is refused at the start, pointing at a sample", started.status_code == 400 and "development data" in reasons_of(started) and "Ask for a sample" in reasons_of(started), f"{started.status_code} {reasons_of(started)[:220]}")
    sample_dataset = sample["sample_version_id"]
    with db() as conn:
        sample_dataset_id = str(conn.execute("select dataset_id from dataset_version where id = %s", (sample_dataset,)).fetchone()["dataset_id"])
    on_sample_run = open_run_as(sample_dataset, wv, ENGINEER)
    made = make_pass(on_sample_run)
    check("on the sample, drawn for this person, a step is given a pass", made.status_code == 200, f"{made.status_code} {reasons_of(made)[:200]}")
    token = made.json()["pass"]
    listed = listing(token)
    names = {r["record_id"] for r in listed.json()["recordings"]} if listed.status_code == 200 else set()
    check("it is told of the three recordings in the sample and of no others", names == chosen_here, f"{listed.status_code} {sorted(names)} {sorted(chosen_here)}")
    some = sorted(chosen_here)[0]
    handle = opening(token, some)
    check("it opens one", handle.status_code == 200, f"{handle.status_code} {reasons_of(handle)[:160]}")
    got = ranged(token, some, handle.json()["handle"], 0, 4000)
    check("what it reads is the original recording, unlocked with the patient's own key inside the platform", got.status_code == 200 and got.content == plain[some][:4000],
          f"{got.status_code} {len(got.content)}")
    other = next(r for r in owners if r not in chosen_here)
    check("a recording of the original that is not in the sample is not found", opening(token, other).status_code in (403, 404), str(opening(token, other).status_code))
    elsewhere = open_run_as(sample_dataset, wv, CUSTODIAN)
    r = make_pass(elsewhere)
    check("a run started by somebody else on the same sample is given no pass: the sample is for the person it was drawn for", r.status_code == 403 and "in development" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")

    heading("The sample ends")
    r = decide(RESEARCHER, first_request, "end")
    check("somebody with no part in it may not end it", r.status_code == 403, f"{r.status_code}")
    r = decide(CUSTODIAN, first_request, "end")
    check("an approver ends it", r.status_code == 200 and r.json()["status"] == "ended" and r.json()["ended_by"] == CUSTODIAN, f"{r.status_code} {r.text[:160]}")
    r = listing(token)
    check("a pass already issued stops working at its next request", r.status_code == 403 and "in development" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    check("so does a handle already issued", ranged(token, some, handle.json()["handle"], 0, 10).status_code == 403)
    check("no new pass is made on an ended sample", make_pass(open_run_as(sample_dataset, wv, ENGINEER)).status_code == 403)
    r = decide(CUSTODIAN, first_request, "end")
    check("ending it twice is refused", r.status_code == 409)

    again_request = ask(ENGINEER, src, records=4, days=5)
    check("the person may ask again", again_request.status_code == 201, f"{again_request.status_code} {again_request.text[:160]}")
    again = decide(CUSTODIAN, again_request.json()["id"], "approve", records=3, days=3)
    check("and the same person asking for the same purpose of the same version is given the same recordings", again.status_code == 200
          and set(sample_row(again_request.json()["id"])["chosen_records"]) == chosen_here, f"{again.status_code} {again.text[:160]}")
    second_version = again.json()["sample_version_id"]
    expiring = open_run_as(second_version, wv, ENGINEER)
    etoken = pass_for(expiring)
    check("it can be used", listing(etoken).status_code == 200)
    with db() as conn:
        conn.execute("update development_sample set expires_at = now() - interval '1 minute' where id = %s", (again_request.json()["id"],))
    r = listing(etoken)
    check("when its time has run out the next request is refused, though nobody ended it", r.status_code == 403 and "in development" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    after = api("GET", f"/dataset-versions/{src}/development-samples", headers=bearer_for(ENGINEER)).json()
    check("and the screen is told it ended", next(s for s in after["mine"] if s["id"] == again_request.json()["id"])["status"] == "ended")
    once_more = ask(ENGINEER, src, records=4, days=5)
    check("so the person may ask once more", once_more.status_code == 201, f"{once_more.status_code} {once_more.text[:160]}")

    # ---------------------------------------------------------------------------------------------- a debug sample
    heading("A debug sample names the recordings, and nothing is drawn")
    debug_id = second_debug.json()["id"]
    r = decide(CUSTODIAN, debug_id, "approve", records=1)
    row = sample_row(debug_id)
    check("lowering it to one gives the first recording named", r.status_code == 200 and row["chosen_records"] == ["r-0001"] and row["strata"] == [{"band": "named", "pool": 2, "chosen": 1}], f"{r.status_code} {row['chosen_records']}")

    # ---------------------------------------------------------------------------------------------- erasure
    heading("Erasing a patient reaches the sample")
    third = decide(CUSTODIAN, once_more.json()["id"], "approve", records=3, days=3)
    third_version = third.json()["sample_version_id"]
    token2 = pass_for(open_run_as(third_version, wv, ENGINEER))
    victim_record = sorted(chosen_here)[0]
    victim_code = owners[victim_record]
    victim_value = None
    for value in patients:
        if person_keys.find_person(person_keys.key_service(), CANARY, KIND, value) == victim_code:
            victim_value = value
            break
    check("the patient is found by the identifier", victim_value is not None)
    erased = person_keys.erase_person(person_keys.key_service(), CANARY, KIND, victim_value, identity(CUSTODIAN, ["data_custodian"]), "asked to be forgotten")
    check("erasing lists the original and the sample among the versions that hold their recordings", {src, third_version} <= set(erased["recording_versions"]), str(erased["recording_versions"]))
    listed = listing(token2)
    flags = {r["record_id"]: r["erased"] for r in listed.json()["recordings"]}
    check("the step is told that recording is erased, and the others are not", flags.get(victim_record) is True and all(not v for k, v in flags.items() if owners[k] != victim_code), str(flags))
    r = opening(token2, victim_record)
    check("and it cannot open it", r.status_code == 410, f"{r.status_code} {reasons_of(r)[:120]}")

    # ---------------------------------------------------------------------------------------------- synthetic
    heading("Recordings declared synthetic")
    syn_dataset = register_dataset(ENGINEER)
    upload_whole(syn_dataset, "s-0001.wav", make_wav(30_000), f"SYN-{tag}")
    syn = api("POST", f"/datasets/{syn_dataset}/seal-recordings", headers=bearer_for(ENGINEER)).json()["id"]
    declare = lambda who, note="made up for tests": api("POST", f"/dataset-versions/{syn}/declare-synthetic", json={"note": note}, headers=bearer_for(who))  # noqa: E731
    check("before the declaration, a run in development on them is refused", make_pass(open_run_as(syn, wv, ENGINEER)).status_code == 403)
    check("the engineer who made them may not declare them synthetic", declare(ENGINEER).status_code == 403)
    r = declare(ELSEWHERE)
    check("another department's custodian may not", r.status_code == 403 and "department that owns these recordings" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    r = declare(CUSTODIAN, note="  ")
    check("a declaration needs a reason", r.status_code == 403 and "needs a reason" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    r = declare(CUSTODIAN)
    check("the owning department's approver declares them, with a reason, and it is recorded", r.status_code == 201 and r.json()["declared_by"] == CUSTODIAN and r.json()["note"] == "made up for tests", f"{r.status_code} {r.text[:160]}")
    check("declaring twice is refused", declare(CUSTODIAN).status_code == 409)
    check("a run in development on them is given a pass, for the engineer", make_pass(open_run_as(syn, wv, ENGINEER)).status_code == 200)
    check("and for anybody else in the organisation, since they hold no real person", make_pass(open_run_as(syn, wv, RESEARCHER)).status_code == 200)
    r = api("POST", f"/dataset-versions/{second_version}/declare-synthetic", json={"note": "no"}, headers=bearer_for(CUSTODIAN))
    check("a development sample holds real recordings and cannot be declared synthetic", r.status_code == 400 and "cannot be declared synthetic" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    screen = api("GET", f"/dataset-versions/{syn}/development-samples", headers=bearer_for(ENGINEER)).json()
    check("the screen is told they are synthetic, by whom and why", screen["synthetic"]["declared_by"] == CUSTODIAN and screen["synthetic"]["note"] == "made up for tests")

    reset_keys()
    return summary("v142")


if __name__ == "__main__":
    sys.exit(main())
