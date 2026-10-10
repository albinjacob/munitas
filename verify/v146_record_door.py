"""U149: a person opens the files of a version through the platform: listed, opened, played, logged, and stopped when the reason ends.

A person is never given a key or a storage address. They ask the platform, which decides from what it holds why they may open a version's files
(a sample drawn for them, files declared synthetic, what they made from either), unlocks a locked file with its patient's key inside the
platform, and sends the bytes. Real files are not opened by a role alone. Every opening is in a log, and the key log for a locked file.

Checked here one item at a time, against the live platform: who may open what and why not, that what comes back is exactly what was stored before
it was locked, that a range opens exactly as the same bytes would in a whole read, what the handle can and cannot do, that a sample that ends or a
patient who is erased stops what was playing at its next request, and what is written down.

    docker compose exec -T munitas-api python /verify/v146_record_door.py
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

import httpx  # noqa: E402

from common import API, CANARY, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402
from v128_person_keys_erasure import CUSTODIAN, ENGINEER, RESEARCHER, good_contract, identity, register_dataset, reset_keys, rows_for  # noqa: E402
from v137_locked_files import KIND, make_wav, upload_whole  # noqa: E402
from v140_custom_steps_and_files import complete_out, department, finish, opening, pass_for, put_out, reasons_of, step_call, upload_version  # noqa: E402
from v142_development_samples import ask, decide, open_run_as  # noqa: E402
from v145_locked_outputs import YAML_BOTH, open_locked, send  # noqa: E402
from app import person_keys  # noqa: E402


def stream(handle: str, range_header: str | None = None) -> httpx.Response:
    headers = {"Range": range_header} if range_header else {}
    return httpx.get(f"{API}/record-stream/{handle}", headers=headers, timeout=60.0)


def records(version: str, who: str) -> httpx.Response:
    return api("GET", f"/dataset-versions/{version}/records", headers=bearer_for(who))


def open_it(version: str, record: str, who: str) -> httpx.Response:
    return api("POST", f"/dataset-versions/{version}/records/{record}/open", headers=bearer_for(who))


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
    service = person_keys.key_service()

    heading("Six files of four patients, one of them long enough to span several chunks")
    dataset = register_dataset(ENGINEER)
    patients = [f"D-{tag}-{i}" for i in range(4)]
    plan = [("r-0001", 0, 40_000), ("r-0002", 0, 52_000), ("r-0003", 1, 61_000), ("r-0004", 2, 70_000), ("r-0005", 3, 80_000), ("r-0006", 3, 2_600_000)]
    plain = {}
    for record, p, size in plan:
        plain[record] = make_wav(size)
        upload_whole(dataset, f"{record}.wav", plain[record], patients[p])
    sealed = api("POST", f"/datasets/{dataset}/seal-files", headers=bearer_for(ENGINEER))
    check("they seal", sealed.status_code == 201, sealed.text[:160])
    src = sealed.json()["id"]
    with db() as conn:
        owners = {r["record_id"]: r["person_code"] for r in conn.execute("select record_id, person_code from file_person where dataset_version_id = %s", (src,)).fetchall()}

    heading("Real files are not opened by a role alone")
    before = count("select count(*) as n from record_read_log where tenant_id = %s", (CANARY,))
    listed = records(src, ENGINEER)
    body = listed.json()
    check("an engineer may list them: the platform says what is there and that they may not open them, and why",
          listed.status_code == 200 and body["basis"] == "none" and body["may_open"] is False and "has allowed it" in " ".join(body["reasons"]) and body["total"] == 6, listed.text[:240])
    check("the list names no patient, and carries each file's size and whether it is locked",
          not any(code in listed.text for code in owners.values()) and {r["record_id"]: r["bytes"] for r in body["records"]} == {k: len(v) for k, v in plain.items()}
          and all(r["locked"] and not r["erased"] for r in body["records"]))
    check("listing writes nothing to the log, because listing is not reading", count("select count(*) as n from record_read_log where tenant_id = %s", (CANARY,)) == before)
    for who, label in ((ENGINEER, "the engineer"), (CUSTODIAN, "the owning department's approver"), (RESEARCHER, "a researcher")):
        r = open_it(src, "r-0001", who)
        check(f"{label} is refused when opening one, with the reason", r.status_code == 403 and "has allowed it" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    refusals = count("select count(*) as n from access_decision where tenant_id = %s and purpose = 'open a record' and allowed = false and dataset_version_id = %s", (CANARY, src))
    check("each refusal is written down, with who and why", refusals == 3, str(refusals))
    table_dataset = register_dataset(ENGINEER)
    table = api("POST", f"/datasets/{table_dataset}/seal-table", headers=bearer_for(ENGINEER), json={"contract": good_contract(), "rows": rows_for(f"R-{tag}", f"S-{tag}")})
    check("a table made of rows, made here, is available to check that rows are not files", table.status_code == 201, f"{table.status_code} {table.text[:120]}")
    if table.status_code == 201:
        rows_listing = records(table.json()["id"], ENGINEER)
        check("a version of rows lists no files and does not break: it holds nothing to open", rows_listing.status_code == 200 and rows_listing.json()["total"] == 0, f"{rows_listing.status_code} {rows_listing.text[:160]}")
    check("a version of another organisation is not found, as if it did not exist", api("GET", f"/dataset-versions/{uuid.uuid4()}/records", headers=bearer_for(ENGINEER)).status_code == 404)
    with db() as conn:
        other = conn.execute("select id from dataset_version where tenant_id <> %s limit 1", (CANARY,)).fetchone()
    if other:
        check("nor is one that belongs to another organisation", api("GET", f"/dataset-versions/{other['id']}/records", headers=bearer_for(ENGINEER)).status_code == 404)

    heading("A sample drawn for the engineer: its files open, to them")
    sample = ask(ENGINEER, src, kind="debug", records=2, days=3, named=["r-0001", "r-0006"], purpose="listen to what the step makes of these two")
    approved = decide(CUSTODIAN, sample.json()["id"], "approve")
    check("a debug sample of the short and the long file is approved", approved.status_code == 200, approved.text[:160])
    sver = approved.json()["sample_version_id"]
    body = records(sver, ENGINEER).json()
    check("on the sample the platform says why they may open it, and lists the two", body["basis"] == "own_sample" and body["may_open"] is True
          and sorted(r["record_id"] for r in body["records"]) == ["r-0001", "r-0006"], str(body)[:240])
    r = records(sver, CUSTODIAN).json()
    check("the approver, who approved it, may not open somebody else's sample: it is for the person it was drawn for", r["basis"] == "none" and r["may_open"] is False)
    check("nor may a researcher", records(sver, RESEARCHER).json()["may_open"] is False)
    short = open_it(sver, "r-0001", ENGINEER)
    check("the engineer opens the short file and is told its size and type", short.status_code == 200 and short.json()["bytes"] == len(plain["r-0001"]) and short.json()["content_type"] == "audio/wav"
          and short.json()["locked"] is True, f"{short.status_code} {short.text[:160]}")
    h_short = short.json()["handle"]
    whole = stream(h_short)
    check("streamed whole, it is exactly the file that was uploaded, unlocked inside the platform", whole.status_code == 200 and whole.content == plain["r-0001"])
    check("with the headers a player needs, and nothing that lets a browser cache it or guess its type", whole.headers.get("accept-ranges") == "bytes" and whole.headers.get("content-type") == "audio/wav"
          and whole.headers.get("cache-control") == "no-store" and whole.headers.get("x-content-type-options") == "nosniff" and int(whole.headers.get("content-length")) == len(plain["r-0001"]))
    r = stream(h_short, "bytes=100-199")
    check("a range comes back as 206 with its place in the file, and is exactly those bytes", r.status_code == 206 and r.content == plain["r-0001"][100:200]
          and r.headers.get("content-range") == f"bytes 100-199/{len(plain['r-0001'])}", f"{r.status_code} {r.headers.get('content-range')}")
    check("a range with no end runs to the end", stream(h_short, "bytes=1000-").content == plain["r-0001"][1000:])
    check("the last bytes of a file can be asked for", stream(h_short, "bytes=-50").content == plain["r-0001"][-50:])
    r = stream(h_short, f"bytes={len(plain['r-0001']) + 10}-")
    check("a range beyond the end is refused as unsatisfiable", r.status_code == 416, str(r.status_code))
    check("more than one range is not served as ranges: the whole file comes back", stream(h_short, "bytes=0-9,20-29").content == plain["r-0001"])
    big = open_it(sver, "r-0006", ENGINEER).json()
    h_big = big["handle"]
    r = stream(h_big, "bytes=1048000-1049999")
    check("a range that crosses the boundary between two locked chunks opens exactly as the same bytes would", r.status_code == 206 and r.content == plain["r-0006"][1048000:1050000], f"{r.status_code} {len(r.content)}")
    check("the whole long file streams, across all its chunks, exactly", stream(h_big).content == plain["r-0006"])

    heading("What the handle can and cannot do")
    check("a handle that is not a handle is refused", stream("not-a-handle").status_code == 401)
    r = httpx.get(f"{API}/datasets/{dataset}", headers={"x-task-credential": h_short}, timeout=30.0)
    check("a handle is refused by the routes that take a run's credential, and says what it is for", r.status_code == 401 and "handle to an opened file" in r.text, f"{r.status_code} {r.text[:160]}")
    r = httpx.get(f"{API}/step/v1/files", headers={"Authorization": f"Bearer {h_short}"}, timeout=30.0)
    check("nor does it open the step routes", r.status_code == 401, f"{r.status_code}")

    heading("Everything an opening does is written down")
    opened = count("select count(*) as n from record_read_log where tenant_id = %s and principal = %s and version_id = %s", (CANARY, ENGINEER, sver))
    check("the two openings are in the log", opened == 2, str(opened))
    with db() as conn:
        log = conn.execute("select basis, bytes_total, bytes_served, locked, via from record_read_log where tenant_id = %s and principal = %s and version_id = %s and record_id = 'r-0001'", (CANARY, ENGINEER, sver)).fetchone()
    check("each says why the platform let the person, how big the file is, that it was locked, and how many bytes were streamed", log["basis"] == "own_sample" and log["bytes_total"] == len(plain["r-0001"])
          and log["locked"] is True and log["via"] == "console" and log["bytes_served"] >= len(plain["r-0001"]), str(dict(log)))
    with db() as conn:
        lines = [r["purpose"] for r in conn.execute("select purpose from key_use_log where tenant_id = %s and action = 'unwrap' and actor = %s", (CANARY, f"person {ENGINEER}")).fetchall()]
    mine_here = [x for x in lines if f"of version {sver} " in x]
    check("the key log has one line for each opening, naming the person, the file, the version and the reason, and none for each range (seven ranges were read)",
          sorted(mine_here) == sorted([f"open record r-0001 of version {sver} (own_sample)", f"open record r-0006 of version {sver} (own_sample)"]), str(mine_here))

    heading("The reason ends, and what was playing stops at its next request")
    ended = decide(CUSTODIAN, sample.json()["id"], "end")
    check("an approver ends the sample", ended.status_code == 200)
    r = stream(h_short, "bytes=0-99")
    check("the handle already given stops working at its next request, and says the sample has ended", r.status_code == 403 and "ended" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    check("so does the other", stream(h_big, "bytes=0-99").status_code == 403)
    check("and the engineer is no longer told they may open it", records(sver, ENGINEER).json()["may_open"] is False)

    heading("A patient who is erased: what was opened stops, and so does opening it")
    sample2 = ask(ENGINEER, src, kind="debug", records=2, days=3, named=["r-0004", "r-0005"], purpose="a second look")
    approved2 = decide(CUSTODIAN, sample2.json()["id"], "approve")
    sver2 = approved2.json()["sample_version_id"]
    h_four = open_it(sver2, "r-0004", ENGINEER).json()["handle"]
    check("a handle for a file of the patient to be erased works before", stream(h_four, "bytes=0-9").content == plain["r-0004"][:10])
    out = person_keys.erase_person(service, CANARY, KIND, patients[2], identity(CUSTODIAN, ["data_custodian"]), "asked to be forgotten")
    check("the patient is erased", out["key_destroyed"] is True)
    r = stream(h_four, "bytes=0-9")
    check("the handle stops at its next request: gone, with the reason", r.status_code == 410 and "erased" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    r = open_it(sver2, "r-0004", ENGINEER)
    check("it cannot be opened again", r.status_code == 410, f"{r.status_code} {reasons_of(r)[:160]}")
    listed2 = {x["record_id"]: x for x in records(sver2, ENGINEER).json()["records"]}
    check("the list says that file is erased and the other is not", listed2["r-0004"]["erased"] is True and listed2["r-0005"]["erased"] is False)
    check("the other patient's file still plays", stream(open_it(sver2, "r-0005", ENGINEER).json()["handle"]).content == plain["r-0005"])

    heading("What a run made from the sample is the person's to open, while the sample is in force")
    decide(CUSTODIAN, sample2.json()["id"], "end")
    sample3 = ask(ENGINEER, src, kind="debug", records=1, days=3, named=["r-0001"], purpose="make something from it")
    sver3 = decide(CUSTODIAN, sample3.json()["id"], "approve").json()["sample_version_id"]
    pid = api("POST", "/pipelines/register", json={"tenant_id": CANARY, "name": f"u149b-{tag}", "department_id": department(), "registered_by": ENGINEER}, headers=bearer_for(ENGINEER)).json()["id"]
    dev = upload_version(pid, YAML_BOTH).json()["id"]
    run = open_run_as(sver3, dev, ENGINEER)
    token = pass_for(run)
    opening(token, "r-0001")
    made = os.urandom(150_000)
    open_locked(token, "r-0001.silenced.bin", "r-0001", len(made))
    send(token, "r-0001.silenced.bin", made)
    complete_out(token, "r-0001.silenced.bin")
    step_call("POST", "/step/v1/outputs/notes.txt", token)
    put_out(token, "notes.txt", 1, b"the step listened and found nothing")
    complete_out(token, "notes.txt")
    sealed_out = finish(run).json()
    ov = sealed_out["output_version_id"]
    body = records(ov, ENGINEER).json()
    check("the version the run made is under review, and the engineer who started the run may open its files", sealed_out["status"] == "sealed" and body["basis"] == "own_development_output"
          and body["may_open"] is True, str(body)[:240])
    kinds = {r["record_id"]: r for r in body["records"]}
    check("it lists a locked file and a plain one, with their sizes", sorted(kinds) == ["notes.txt", "r-0001.silenced.bin"] and kinds["r-0001.silenced.bin"]["locked"] is True
          and kinds["r-0001.silenced.bin"]["bytes"] == len(made) and kinds["notes.txt"]["locked"] is False and kinds["notes.txt"]["content_type"].startswith("text/plain"), str(kinds))
    locked_out = stream(open_it(ov, "r-0001.silenced.bin", ENGINEER).json()["handle"])
    check("the locked file opens to exactly what the step sent, unlocked inside the platform", locked_out.status_code == 200 and locked_out.content == made)
    plain_out = stream(open_it(ov, "notes.txt", ENGINEER).json()["handle"], "bytes=4-11")
    check("a plain file is served too, ranges and all", plain_out.status_code == 206 and plain_out.content == b"the step listened and found nothing"[4:12])
    for who in (CUSTODIAN, RESEARCHER):
        check(f"nobody else opens it: {'the approver' if who == CUSTODIAN else 'a researcher'} is refused", open_it(ov, "notes.txt", who).status_code == 403)
    decide(CUSTODIAN, sample3.json()["id"], "end")
    check("when the sample it came from ends, the engineer no longer opens it either", records(ov, ENGINEER).json()["may_open"] is False and open_it(ov, "notes.txt", ENGINEER).status_code == 403)

    heading("Files declared synthetic are open to those who work with them")
    syn_dataset = register_dataset(ENGINEER)
    syn_wav = make_wav(50_000)
    upload_whole(syn_dataset, "s-0001.wav", syn_wav, f"SYN-{tag}")
    syn = api("POST", f"/datasets/{syn_dataset}/seal-files", headers=bearer_for(ENGINEER)).json()["id"]
    check("before the declaration they are real as far as the platform knows, and are not opened", open_it(syn, "s-0001", ENGINEER).status_code == 403)
    api("POST", f"/dataset-versions/{syn}/declare-synthetic", json={"note": "read aloud from invented scripts"}, headers=bearer_for(CUSTODIAN))
    check("after it, the engineer opens them, and they are exactly what was uploaded", stream(open_it(syn, "s-0001", ENGINEER).json()["handle"]).content == syn_wav)
    check("so does the approver", open_it(syn, "s-0001", CUSTODIAN).status_code == 200)
    r = open_it(syn, "s-0001", RESEARCHER)
    check("a role that is not one of those that work with files does not", r.status_code == 403 and "holds no role" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")

    reset_keys()
    return summary("v146")


if __name__ == "__main__":
    sys.exit(main())
