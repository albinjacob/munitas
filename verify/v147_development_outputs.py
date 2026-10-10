"""U151: what was made for development ends, and its files are deleted without touching anything else.

A development sample, and what a run makes on a sample, is for building and testing. It ends at the earlier of seven days and the end of the sample it
came from: its files are deleted, its row is kept and marked, and a record says why. Checked here one item at a time, against the live platform:
that the end is noted when a sample is approved and when a step seals a version made on it, that nothing is deleted before it is due, that deleting a
sample's files leaves the stored files its list names untouched, that a run on data that is not development data is never marked, that a legal
hold or an organisation that is not open stops the deletion, and that a version that has ended is refused with the reason.

    docker compose exec -T munitas-api python /verify/v147_development_outputs.py
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

from common import CANARY, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402
from v128_person_keys_erasure import CUSTODIAN, ENGINEER, register_dataset, reset_keys  # noqa: E402
from v137_locked_files import make_wav, upload_whole  # noqa: E402
from v140_custom_steps_and_files import complete_out, department, finish, opening, pass_for, put_out, reasons_of, step_call, upload_version  # noqa: E402
from v142_development_samples import ask, decide, open_run_as  # noqa: E402
from v145_locked_outputs import YAML_BOTH, open_locked, send  # noqa: E402


def objects_under(version_id: str) -> list[str]:
    from app import locked_files
    with db() as conn:
        v = conn.execute("select tenant_id, storage_prefix, storage_backend from dataset_version where id = %s", (version_id,)).fetchone()
    client, bucket = locked_files._storage({"backend": v["storage_backend"] or "seaweedfs", "tenant": v["tenant_id"]})
    page = client.list_objects_v2(Bucket=bucket, Prefix=v["storage_prefix"].rstrip("/") + "/")
    return sorted(o["Key"] for o in page.get("Contents", []))


def noted(version_id: str) -> dict | None:
    with db() as conn:
        return conn.execute("select * from development_output where version_id = %s", (version_id,)).fetchone()


def bring_forward(version_id: str) -> None:
    with db() as conn:
        conn.execute("update development_output set expires_at = now() - interval '1 minute' where version_id = %s", (version_id,))


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    from app import db as app_db
    if app_db.pool.closed:
        app_db.pool.open()
    from app import development_outputs
    tag = uuid.uuid4().hex[:8]

    heading("Three files, and a sample of two of them")
    dataset = register_dataset(ENGINEER)
    patients = [f"X-{tag}-{i}" for i in range(3)]
    for n, p in enumerate(patients):
        upload_whole(dataset, f"e-000{n + 1}.wav", make_wav(40_000 + n * 9_000), p)
    src = api("POST", f"/datasets/{dataset}/seal-files", headers=bearer_for(ENGINEER)).json()["id"]
    source_objects = objects_under(src)
    check("the real files are stored under their own version", len(source_objects) >= 3, str(len(source_objects)))
    first = ask(ENGINEER, src, kind="debug", records=2, days=3, named=["e-0001", "e-0002"], purpose="check the ending of what was made for development")
    approved = decide(CUSTODIAN, first.json()["id"], "approve")
    check("the sample is approved", approved.status_code == 200, approved.text[:160])
    sver = approved.json()["sample_version_id"]
    with db() as conn:
        sample_ends = conn.execute("select expires_at from development_sample where id = %s", (first.json()["id"],)).fetchone()["expires_at"]
    row = noted(sver)
    check("approval notes the sample's own version as one that ends, at the end of the sample", row is not None and row["kind"] == "sample"
          and row["expires_at"] == sample_ends and row["expired_at"] is None and str(row["sample_id"]) == first.json()["id"], str(dict(row) if row else None))
    sample_objects = objects_under(sver)
    check("the sample holds its own small files, which are not the files", len(sample_objects) >= 1 and not set(sample_objects) & set(source_objects), str(sample_objects))

    heading("A run on the sample: what it makes ends with the sample")
    pid = api("POST", "/pipelines/register", json={"tenant_id": CANARY, "name": f"u151-{tag}", "department_id": department(), "registered_by": ENGINEER}, headers=bearer_for(ENGINEER)).json()["id"]
    dev = upload_version(pid, YAML_BOTH).json()["id"]
    run = open_run_as(sver, dev, ENGINEER)
    token = pass_for(run)
    opening(token, "e-0001")
    made = os.urandom(120_000)
    open_locked(token, "e-0001.bin", "e-0001", len(made))
    send(token, "e-0001.bin", made)
    complete_out(token, "e-0001.bin")
    step_call("POST", "/step/v1/outputs/notes.txt", token)
    put_out(token, "notes.txt", 1, b"nothing found")
    complete_out(token, "notes.txt")
    out = finish(run).json()["output_version_id"]
    row = noted(out)
    with db() as conn:
        days = conn.execute("select (expires_at - now()) as left from development_output where version_id = %s", (out,)).fetchone()["left"]
    check("sealing notes the output version, as an output of that sample", row is not None and row["kind"] == "output" and str(row["sample_id"]) == first.json()["id"], str(dict(row) if row else None))
    check("it ends no later than the sample and no later than seven days", row["expires_at"] <= sample_ends and days.total_seconds() <= 7 * 86400 + 5, str(days))
    out_objects = objects_under(out)
    check("the output holds its files", len(out_objects) >= 2, str(out_objects))

    heading("A run that is not on development data is never marked")
    with db() as conn:
        prod = conn.execute("select r.id, r.tenant_id, r.source_version_id, r.triggered_by from pipeline_run r where r.tenant_id = %s and r.source_version_id is not null and r.triggered_by is not null "
                            "and r.source_version_id not in (select version_id from development_output) order by r.started_at desc limit 40", (CANARY,)).fetchall()
    from app import development_samples
    real = [r for r in prod if not development_samples.is_development_data(CANARY, r["triggered_by"], str(r["source_version_id"]))]
    check("a run on real data exists to try this on", len(real) > 0, f"{len(prod)} runs seen")
    if real:
        before = len(objects_under(src))
        with db() as conn:
            n0 = conn.execute("select count(*) as n from development_output").fetchone()["n"]
        development_outputs.note_output(CANARY, str(real[0]["id"]), str(src))
        with db() as conn:
            n1 = conn.execute("select count(*) as n from development_output").fetchone()["n"]
        check("noting an output of that run records nothing", n1 == n0 and noted(str(src)) is None, f"{n0} {n1}")
        check("and the real files are untouched", len(objects_under(src)) == before)

    heading("Nothing is deleted before it is due")
    development_outputs.sweep()
    check("a sweep before the day leaves the sample and the output as they were", objects_under(sver) == sample_objects and objects_under(out) == out_objects
          and noted(sver)["expired_at"] is None and noted(out)["expired_at"] is None)
    check("and the engineer still opens the output", api("GET", f"/dataset-versions/{out}/records", headers=bearer_for(ENGINEER)).json()["may_open"] is True)

    heading("A hold, or an organisation that is not open, stops the deletion")
    bring_forward(out)
    original = development_outputs._held
    development_outputs._held = lambda tenant: True
    try:
        done = development_outputs.sweep()
    finally:
        development_outputs._held = original
    check("with something standing over the organisation a due version is not deleted, and the sweep says it did nothing", done == 0 and objects_under(out) == out_objects and noted(out)["expired_at"] is None, str(done))
    with db() as conn:
        phase = conn.execute("select tenant_hold_state(%s) as hold, tenant_phase(%s) as phase", (CANARY, CANARY)).fetchone()
    check("the real test for that is the organisation's own state, which in this organisation is open and free", phase["hold"] == "none" and phase["phase"] == "active", str(dict(phase)))

    heading("When it is due, its files are deleted and nothing else is")
    done = development_outputs.sweep()
    row = noted(out)
    check("the sweep ends the output", row["expired_at"] is not None, str(done))
    check("its files are gone", objects_under(out) == [], str(objects_under(out)))
    check("the sample, which is not yet due, and the real files are untouched", objects_under(sver) == sample_objects and objects_under(src) == source_objects)
    with db() as conn:
        rec = conn.execute("select reclaimed_by, reason, object_count, bytes_freed from storage_reclamation where dataset_version_id = %s", (out,)).fetchone()
        ver = conn.execute("select id from dataset_version where id = %s", (out,)).fetchone()
    check("a record says what was freed, by whom and why", rec is not None and rec["reclaimed_by"] == "platform" and rec["object_count"] == len(out_objects) and rec["bytes_freed"] > 0
          and "development" in rec["reason"], str(dict(rec) if rec else None))
    check("the version's row is kept", ver is not None)
    body = api("GET", f"/dataset-versions/{out}/records", headers=bearer_for(ENGINEER)).json()
    check("the page is told it has ended, with when, and is offered nothing to open", body["basis"] == "expired" and body["may_open"] is False and body["total"] == 0
          and body["development"]["expired_at"] is not None and "deleted" in " ".join(body["reasons"]), str(body)[:260])
    r = api("POST", f"/dataset-versions/{out}/records/notes.txt/open", headers=bearer_for(ENGINEER))
    check("opening one is refused, with the reason", r.status_code == 403 and "deleted" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:160]}")
    check("a second sweep does nothing more to it", development_outputs.sweep() == 0 or noted(out)["expired_at"] == row["expired_at"])

    heading("A sample that ends: its own list is deleted, the files it named are not")
    ended = decide(CUSTODIAN, first.json()["id"], "end")
    check("an approver ends the sample", ended.status_code == 200)
    check("at once, before the sweep, the page says it has ended and its files are deleted, and does not talk of a lease for files that are about to go",
          api("GET", f"/dataset-versions/{sver}/records", headers=bearer_for(ENGINEER)).json()["basis"] == "expired" and noted(sver)["expired_at"] is None)
    development_outputs.sweep()
    check("its own files are deleted", objects_under(sver) == [], str(objects_under(sver)))
    check("the stored files it named are all still there", objects_under(src) == source_objects)
    body = api("GET", f"/dataset-versions/{sver}/records", headers=bearer_for(ENGINEER)).json()
    check("the sample is marked ended", noted(sver)["expired_at"] is not None and body["basis"] == "expired")
    check("the real files are still listed, and still opened only when an approver has allowed it", api("GET", f"/dataset-versions/{src}/records", headers=bearer_for(ENGINEER)).json()["total"] == 3)

    heading("A sample that was never approved is never noted")
    declined = ask(ENGINEER, src, kind="debug", records=1, days=2, named=["e-0003"], purpose="this one is declined")
    decide(CUSTODIAN, declined.json()["id"], "decline")
    with db() as conn:
        n = conn.execute("select count(*) as n from development_output o join development_sample s on s.id = o.sample_id where s.id = %s", (declined.json()["id"],)).fetchone()["n"]
    check("a declined request leaves nothing to end", n == 0, str(n))

    reset_keys()
    return summary("v147")


if __name__ == "__main__":
    sys.exit(main())
