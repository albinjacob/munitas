"""U166: a person reads the few real files an approver allowed them to open in a notebook, through the client library, and through nothing else.

A lease made for a notebook is not a lease made for the page: it is asked for, decided and fixed the same way, but it is held only by a notebook. The client library
asks the platform for a files token (one version, an hour at most, never past the lease), lists the files the lease names, and opens one as a file to read, by
range, through the same door the console uses. Every request asks again whether the lease is in force. Checked here with the real client library, as a
researcher, one item at a time against the live platform:

  * without a lease for a notebook there is no token and no list, and a lease for the page does not count, whichever the order;
  * an approver may allow fewer files and fewer hours, never more, and what is allowed is fixed;
  * what the library lists is exactly the files the lease names, and what it reads is exactly what was stored before it was locked, whole, by range and
    when it seeks, with no storage address and no key anywhere in what it is given;
  * a file the lease does not name is refused, and the refusal is written down;
  * every opening is in the read log as a notebook's, under the lease, with the bytes served;
  * a notebook lease does not open the page, and the token opens nothing but the notebook routes: not a session route, not a storage credential;
  * ending a lease, or its time running out, stops the next request of a token and of a file already open.

    docker compose exec -T munitas-api python /verify/v159_notebook_files.py
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")
sys.path.insert(0, "/client")
sys.path.insert(0, "/step-sdk")

import httpx  # noqa: E402

from common import API, CANARY, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402
from v128_person_keys_erasure import CUSTODIAN, ENGINEER, RESEARCHER, register_dataset, reset_keys  # noqa: E402
from v137_locked_files import make_wav, upload_whole  # noqa: E402
from v140_custom_steps_and_files import reasons_of  # noqa: E402
from v157_file_leases import ask as ask_lease, decide as decide_lease, records  # noqa: E402
from munitas_client import FileErased, Munitas, MunitasError  # noqa: E402
from v160_notebook_sign_in import approve_in_browser  # noqa: E402


def _error(call):
    try:
        call()
    except MunitasError as exc:
        return exc
    return None


def me() -> Munitas:
    return Munitas(API, bearer_for(RESEARCHER)["Authorization"].split(" ", 1)[1])


def log_rows(version: str) -> list[dict]:
    with db() as conn:
        return conn.execute("select * from record_read_log where version_id = %s and principal = %s order by at", (version, RESEARCHER)).fetchall()


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    from app import db as app_db
    if app_db.pool.closed:
        app_db.pool.open()
    tag = uuid.uuid4().hex[:8]

    heading("Eight locked files, sealed as one version, and another version nobody holds a lease on")
    dataset = register_dataset(ENGINEER)
    plain: dict[str, bytes] = {}
    for i in range(8):
        record = f"n-{i + 1:04d}"
        plain[record] = make_wav(60_000 + i * 5_000)
        upload_whole(dataset, f"{record}.wav", plain[record], f"N-{tag}-{i}")
    sealed = api("POST", f"/datasets/{dataset}/seal-files", headers=bearer_for(ENGINEER))
    check("the files are sealed", sealed.status_code == 201, sealed.text[:140])
    src = sealed.json()["id"]
    other_dataset = register_dataset(ENGINEER)
    upload_whole(other_dataset, "z-1.wav", make_wav(40_000), f"Z-{tag}")
    other = api("POST", f"/datasets/{other_dataset}/seal-files", headers=bearer_for(ENGINEER)).json()["id"]

    heading("Without a lease made for a notebook there is nothing to read")
    try:
        me().files_token(version_id=src)
        refused = None
    except MunitasError as exc:
        refused = str(exc)
    check("no token without a notebook lease, and the reason says how to ask", refused is not None and "choose that you will open them in a notebook" in refused, str(refused)[:200])
    try:
        me().list_files(version_id=src)
        listed = None
    except MunitasError as exc:
        listed = str(exc)
    check("and no list", listed is not None and "has allowed it" in listed, str(listed)[:200])
    for_page = ask_lease(src, RESEARCHER, records=2)
    page_lease = decide_lease(for_page.json()["id"], CUSTODIAN, "approve", records=2)
    check("a lease for the page is allowed", for_page.status_code == 201 and page_lease.status_code == 200, f"{for_page.status_code} {page_lease.status_code}")
    try:
        me().files_token(version_id=src)
        by_page = None
    except MunitasError as exc:
        by_page = str(exc)
    check("and does not make a notebook lease: still no token", by_page is not None and "choose that you will open them in a notebook" in by_page)
    decide_lease(for_page.json()["id"], RESEARCHER, "end")

    heading("Asking, and what is allowed")
    asked = ask_lease(src, RESEARCHER, kind="notebook", records=5, hours=4)
    check("a researcher asks for 5 files for 4 hours in a notebook", asked.status_code == 201 and asked.json()["kind"] == "notebook", f"{asked.status_code} {asked.text[:140]}")
    lease = asked.json()["id"]
    check("an approver may not allow more files than were asked for", decide_lease(lease, CUSTODIAN, "approve", records=6).status_code == 400)
    check("nor more hours", decide_lease(lease, CUSTODIAN, "approve", hours=5).status_code == 400)
    granted = decide_lease(lease, CUSTODIAN, "approve", records=3, hours=2, note="three, for two hours")
    check("they allow 3 of the 5, for 2 hours", granted.status_code == 200 and granted.json()["granted_count"] == 3 and granted.json()["granted_hours"] == 2, f"{granted.status_code} {granted.text[:160]}")
    chosen = sorted(granted.json()["records"])
    with db() as conn:
        stored = conn.execute("select kind, expires_at, decided_at from file_lease where id = %s", (lease,)).fetchone()
    check("it is a lease for a notebook, and ends two hours from the decision", stored["kind"] == "notebook" and 7100 < (stored["expires_at"] - stored["decided_at"]).total_seconds() <= 7200)
    screen = api("GET", f"/dataset-versions/{src}/file-lease", headers=bearer_for(RESEARCHER)).json()
    check("the page is told the limits of both ways and holds the lease", screen["kinds"] == ["console", "notebook"] and screen["caps"]["notebook"] == {"records": 200, "hours": 8}
          and any(r["id"] == lease and r["kind"] == "notebook" for r in screen["leases"]), str(screen)[:200])

    heading("The library lists and reads exactly what the lease names")
    client = me()
    token = client.files_token(version_id=src)
    check("the researcher gets a token, and it is not a session", isinstance(token, str) and len(token) > 40 and token != bearer_for(RESEARCHER)["Authorization"].split(" ", 1)[1])
    found = client.list_files(version_id=src)
    check("the list is exactly the 3 files the lease names, with their sizes", sorted(f["record_id"] for f in found) == chosen and all(f["bytes"] == len(plain[f["record_id"]]) for f in found), str(found)[:240])
    check("the list carries no person, no key and no address", all(set(f) <= {"record_id", "kind", "content_type", "locked", "erased", "bytes"} for f in found), str(found[0])[:200])
    first = chosen[0]
    whole = client.read_file(first, version_id=src)
    check("the whole file read is exactly what was stored before it was locked", whole == plain[first], f"{len(whole)} bytes of {len(plain[first])}")
    with client.open_file(first, version_id=src) as source:
        source.seek(100)
        piece = source.read(100)
        source.seek(-50, 2)
        tail = source.read()
    check("seeking and reading in the middle and from the end reads exactly those bytes", piece == plain[first][100:200] and tail == plain[first][-50:])
    target = Path("/tmp") / f"u166-{tag}.wav"
    saved = client.save_file(chosen[1], str(target), version_id=src)
    check("saving writes the whole file in pieces", saved == len(plain[chosen[1]]) and target.read_bytes() == plain[chosen[1]])
    target.unlink(missing_ok=True)

    heading("What it refuses, and what is written down")
    outside = sorted(set(plain) - set(chosen))[0]
    try:
        client.open_file(outside, version_id=src)
        outside_refusal = None
    except MunitasError as exc:
        outside_refusal = str(exc)
    check("a file the lease does not name is refused, and the reason says so", outside_refusal is not None and "not one of the files your lease opens" in outside_refusal, str(outside_refusal)[:160])
    with db() as conn:
        refused_rows = conn.execute("select count(*) as n from access_decision where tenant_id = %s and principal = %s and purpose = 'open a record' and allowed = false and dataset_version_id = %s "
                                    "and reasons::text like %s", (CANARY, RESEARCHER, src, "%one of the files your lease opens%")).fetchone()["n"]
    check("the refusal is written down", refused_rows >= 1)
    rows = log_rows(src)
    check("every opening is in the log as a notebook's, under the lease, with the bytes served",
          len(rows) >= 3 and all(r["via"] == "notebook" and r["basis"] == "lease" and str(r["file_lease_id"]) == lease and r["locked"] for r in rows)
          and all(r["bytes_served"] > 0 for r in rows), str(rows[:1])[:240])
    audit = api("GET", "/audit/locked-columns?limit=500", headers=bearer_for("canary-dpo")).json()
    by_notebook = [e for e in audit["file_reads"] if e["allowed"] and e["lease_id"] == lease]
    check("the audit evidence shows the openings as a notebook's, under the lease, and the notebook lease as one for a notebook",
          len(by_notebook) >= 3 and all(e["via"] == "notebook" and e["basis"] == "lease" for e in by_notebook)
          and any(l["id"] == lease and l["kind"] == "notebook" for l in audit["file_leases"]), str(by_notebook[:1])[:240])
    in_notebook = [k for k in audit["key_uses"] if k["action"] == "unwrap" and (k["reason"] or "").startswith("To open a locked file in a notebook")]
    check("the key log says a notebook opened them, in words", len(in_notebook) >= 3 and all("with an approver's permission" in k["reason"] and k["table"] for k in in_notebook), str(in_notebook[:1]))
    try:
        me().files_token(version_id=other)
        elsewhere = None
    except MunitasError as exc:
        elsewhere = str(exc)
    check("a lease on one version gives nothing on another", elsewhere is not None and "choose that you will open them in a notebook" in elsewhere)

    heading("The notebook lease does not open the page, and the token opens nothing else")
    page = records(src, RESEARCHER).json()
    check("the page still says the files are closed", page["basis"] == "none" and page["may_open"] is False)
    held = client._files_tokens[src]
    bearer = {"Authorization": f"Bearer {held}"}
    check("the token is refused by a route that wants a session", httpx.get(f"{API}/dataset-versions/{src}/records", headers=bearer, timeout=30.0).status_code == 401)
    check("and by one that takes a run's credential", httpx.get(f"{API}/organisation?tenant_id={CANARY}", headers={"X-Task-Credential": held}, timeout=30.0).status_code in (401, 403))
    asked_credential = httpx.post(f"{API}/credentials", json={"principal": RESEARCHER, "principal_kind": "human", "roles": ["notebook_explore"], "tenant_id": CANARY,
                                                              "dataset_version_id": src, "purpose": "x", "task_credential": held}, timeout=30.0)
    check("and cannot be used to ask for a storage credential", asked_credential.status_code == 403, f"{asked_credential.status_code} {asked_credential.text[:140]}")
    check("a token that is not one is refused", httpx.get(f"{API}/notebook/v1/files", headers={"Authorization": "Bearer nonsense"}, timeout=30.0).status_code == 401)
    check("with no token there is no answer", httpx.get(f"{API}/notebook/v1/files", timeout=30.0).status_code == 401)
    check("somebody else's session cannot use the researcher's token on their behalf: the token is what names the person",
          httpx.get(f"{API}/notebook/v1/files", headers={"Authorization": bearer_for(CUSTODIAN)["Authorization"]}, timeout=30.0).status_code == 401)

    heading("A lease ends when it is ended, and when it runs out")
    opened = httpx.post(f"{API}/notebook/v1/files/{first}/open", headers=bearer, timeout=30.0)
    handle = opened.json()["handle"]
    check("a handle is made for a file the lease names", opened.status_code == 200 and httpx.get(f"{API}/record-stream/{handle}", timeout=30.0).status_code == 200)
    ended = api("POST", f"/file-leases/{lease}/end", headers=bearer_for(CUSTODIAN))
    check("an approver ends it", ended.status_code == 200 and ended.json()["status"] == "ended", f"{ended.status_code} {ended.text[:140]}")
    check("the token is refused at its next request", httpx.get(f"{API}/notebook/v1/files", headers=bearer, timeout=30.0).status_code == 403)
    check("what was being read stops at its next request", httpx.get(f"{API}/record-stream/{handle}", timeout=30.0).status_code == 403)
    try:
        me().files_token(version_id=src)
        after = None
    except MunitasError as exc:
        after = str(exc)
    check("and no new token is made", after is not None)

    second = ask_lease(src, RESEARCHER, kind="notebook", records=2, hours=1)
    approved = decide_lease(second.json()["id"], CUSTODIAN, "approve")
    check("a new lease may be asked for and allowed", second.status_code == 201 and approved.status_code == 200, f"{second.status_code} {approved.status_code}")
    second_client = me()
    fresh = second_client.list_files(version_id=src)
    check("the new lease lists its own files", len(fresh) == 2)
    with db() as conn:
        conn.execute("update file_lease set expires_at = now() - interval '1 minute' where id = %s", (second.json()["id"],))
    try:
        second_client.list_files(version_id=src)
        expired = None
    except MunitasError as exc:
        expired = str(exc)
    check("with the time gone the library's next request is refused, with the token still in hand", expired is not None, str(expired)[:160])

    heading("The two ways are separate, and both can be held")
    both_page = ask_lease(src, RESEARCHER, records=2)
    both_note = ask_lease(src, RESEARCHER, kind="notebook", records=2, hours=1)
    check("a lease for the page and one for a notebook can be waiting together", both_page.status_code == 201 and both_note.status_code == 201, f"{both_page.status_code} {both_note.status_code}")
    again = ask_lease(src, RESEARCHER, kind="notebook", records=2, hours=1)
    check("but not two for the same way", again.status_code == 409)
    check("the approver's list names the way each is for", {r["kind"] for r in api("GET", "/file-leases", headers=bearer_for(CUSTODIAN)).json()["to_decide"] if r["dataset_version_id"] == src} == {"console", "notebook"})
    decide_lease(both_page.json()["id"], RESEARCHER, "end")
    decide_lease(both_note.json()["id"], RESEARCHER, "end")
    heading("The same, signed in the way a notebook really does it")
    import re
    import threading

    third = ask_lease(src, RESEARCHER, kind="notebook", records=2, hours=1)
    decide_lease(third.json()["id"], CUSTODIAN, "approve")
    lines: list[str] = []
    made: dict = {}
    threading.Thread(target=lambda: made.update(nb=Munitas.sign_in(API, "U167 files", show=lines.append)), daemon=True).start()
    for _ in range(60):
        if lines:
            break
        time.sleep(0.5)
    approve_in_browser(RESEARCHER, {"verification_uri_complete": re.search(r"open (\S+) and", lines[-1]).group(1), "user_code": re.search(r"code (\w+)\.", lines[-1]).group(1)})
    for _ in range(60):
        if "nb" in made:
            break
        time.sleep(0.5)
    notebook = made["nb"]
    got = notebook.list_files(version_id=src)
    check("a notebook signed in with its own pass lists the 2 files its lease names", len(got) == 2, str(got)[:160])
    check("and reads one exactly as it was stored", notebook.read_file(got[0]["record_id"], version_id=src) == plain[got[0]["record_id"]])
    held_token = notebook._files_tokens[src]
    notebook.sign_out()
    check("signing out stops the notebook", "not signed in" in str(_error(lambda: notebook.list_files(version_id=src))))
    check("and stops a files token it had already been given, though the lease for the files is still in force",
          httpx.get(f"{API}/notebook/v1/files", headers={"Authorization": f"Bearer {held_token}"}, timeout=30.0).status_code == 401)
    decide_lease(third.json()["id"], RESEARCHER, "end")
    with db() as conn:
        conn.execute("delete from notebook_sign_in where label = 'U167 files'")
        conn.commit()
    return summary("U166")


if __name__ == "__main__":
    sys.exit(main())
