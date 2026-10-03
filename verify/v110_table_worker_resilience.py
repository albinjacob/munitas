"""U110: a worker that dies halfway does not damage a table, and an organisation's own worker serves that organisation only.

U109 runs jobs through the stack and checks the promises about what is sealed and what a key can reach. This one does what
only a person with Docker can: it kills the worker while it is writing, and it runs a worker of an organisation's own. It runs
on the host, because it starts, kills and stops containers (Docker runs inside WSL2 on this machine, so through wsl.exe).

  * a worker killed in the middle of a large write: Temporal notices the missing heartbeat, the work is handed out again, the
    control plane clears what the dead attempt left, and the version is sealed with exactly the files of one complete table;
  * an organisation given its own worker: its job waits for that worker and for nobody else, however long it takes, and the
    housekeeping screen says so; the shared pool never takes it, and the organisation's worker never takes anybody else's;
  * a dedicated worker refuses a job of another organisation, and refuses to start without being told which it serves.

Takes about five minutes. Run it alone: a second job on the stack could make a log line mean something else.

    .venv\\Scripts\\python.exe verify\\v110_table_worker_resilience.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Run from the host, the checks' helpers need to be told where the stack is. In the container these are already set.
os.environ.setdefault("PG_DSN", "postgresql://munitas:munitas@localhost:5432/platform")
os.environ.setdefault("S3_ENDPOINT", "http://localhost:8333")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS", "http://localhost:4433")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS_ADMIN", "http://localhost:4434")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ADMIN, api, bearer_for, bucket_for, check, db, fixture_tabular_contract, fixture_tabular_version, heading, require_api, s3_client, summary  # noqa: E402
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, finish_org, make_org  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DISTRO = "Ubuntu-20.04"
SHARED = "munitas-table-worker-1"
WAIT = 420


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["wsl.exe", "-d", DISTRO, "--", "docker", *args], capture_output=True, text=True)


def logs(container: str) -> str:
    done = docker("logs", "--since", "30m", container)
    return done.stdout + done.stderr


def worker_script(action: str, tenant: str) -> None:
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "admin" / "table-worker.py"), action, tenant], capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(done.stdout + done.stderr)


def row(i: int) -> dict:
    return {"record_id": f"rec-{i}", "transcript": f"synthetic transcript number {i} " + "x" * 120, "score": 0.5 + i, "count": i * 2,
            "ok": i % 2 == 0, "tags": ["a", f"b{i}"], "detail": {"i": i, "nested": {"k": "v"}}}


def main() -> int:
    require_api()
    admin = bearer_for(ADMIN_A)
    client = s3_client(*ADMIN)
    orgs = []

    def make(prefix_name: str):
        org = make_org()
        orgs.append(org)
        schema = fixture_tabular_contract(org.id)
        first = fixture_tabular_version(org.id, dataset_name=prefix_name, schema_id=schema)
        return org, schema, first["dataset_id"], bucket_for(org.id), org.bearer("member")

    def put(org, dataset_id, bucket, lines: list[bytes] | None = None, path: str | None = None) -> tuple[str, list[dict], int]:
        prefix = api("GET", f"/datasets/{dataset_id}/next-version", params={"tenant_id": org.id}).json()["storage_prefix"]
        key = f"{prefix}/records.ndjson"
        digest, size = hashlib.sha256(), 0
        if path:
            with open(path, "rb") as handle:
                for piece in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                    digest.update(piece)
                    size += len(piece)
            client.upload_file(path, bucket, key)
        else:
            body = b"".join(lines)
            digest.update(body)
            size = len(body)
            client.put_object(Bucket=bucket, Key=key, Body=body)
        return key, [{"key": key, "bytes": size, "sha256": digest.hexdigest()}], size

    def seal(org, schema, dataset_id, key, manifest, rows):
        return api("POST", "/dataset-versions", json={
            "tenant_id": org.id, "dataset_id": dataset_id, "schema_id": schema, "visibility_class": "RAW",
            "object_manifest": manifest, "record_count": rows, "records_key": key, "table_mode": "background"})

    def state(member, job_id):
        return api("GET", f"/table-jobs/{job_id}", headers=member).json()

    def wait(member, job_id, until, timeout=WAIT):
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            now = state(member, job_id)
            if now["status"] in until:
                return now
            time.sleep(3)
        return state(member, job_id)

    try:
        # --------------------------------------------------------------------------------------------------------------
        heading("A worker killed halfway through a large write")
        org, schema, dataset_id, bucket, member = make("crash")
        rows = 1_200_000
        path = "/tmp/u110.ndjson" if os.name != "nt" else str(Path(os.environ.get("TEMP", ".")) / "u110.ndjson")
        with open(path, "w") as out:
            for i in range(rows):
                out.write(json.dumps(row(i)) + "\n")
        key, manifest, size = put(org, dataset_id, bucket, path=path)
        os.remove(path)
        job = seal(org, schema, dataset_id, key, manifest, rows).json()
        # Kill it when there is proof it is part way through: the job is running and the first data file of the table is already in
        # storage. A fixed wait is a guess, and a write of a few hundred megabytes finishes in seconds.
        data_prefix = f"{job['storage_prefix']}/iceberg/data/"
        mid_write, started = [], time.monotonic()
        while time.monotonic() - started < 180:
            if state(member, job["job_id"])["status"] == "running":
                mid_write = [o["Key"] for o in client.list_objects_v2(Bucket=bucket, Prefix=data_prefix).get("Contents", [])]
                if mid_write:
                    break
            time.sleep(0.5)
        check(f"the worker is part way through writing the table ({size // 1_000_000} MB, {len(mid_write)} data files already in storage)",
              bool(mid_write) and state(member, job["job_id"])["status"] == "running")
        killed = docker("kill", SHARED)
        check("the worker container was killed in the middle of the write", killed.returncode == 0, killed.stderr.strip()[:100])
        # Docker does not restart a container that was killed by hand (its restart policy is for a container that crashed), so
        # this does what an orchestrator does after a crash: starts it again, once the platform has had time to notice.
        time.sleep(5)
        docker("start", SHARED)
        back = False
        for _ in range(30):
            if docker("inspect", "-f", "{{.State.Running}}", SHARED).stdout.strip() == "true":
                back = True
                break
            time.sleep(2)
        check("the worker is started again", back)
        done = wait(member, job["job_id"], ("sealed", "refused", "expired"))
        check("the job is handed out again and sealed with its table", done["status"] == "sealed" and done["outcome"] == "written", str(done)[:200])
        with db() as conn:
            attempts = conn.execute("select attempts from table_job where id = %s", (job["job_id"],)).fetchone()["attempts"]
            v = conn.execute("select object_manifest, record_count from dataset_version where id = %s", (done["version_id"],)).fetchone()
            ref = conn.execute("select record_count from iceberg_table_ref where dataset_version_id = %s", (done["version_id"],)).fetchone()
        check("it took more than one attempt, which is the proof it was interrupted", attempts >= 2, f"{attempts} attempts")
        check("the table has every row", ref and ref["record_count"] == rows, str(ref))
        in_storage = {o["Key"] for o in client.list_objects_v2(Bucket=bucket, Prefix=f"{done['storage_prefix']}/").get("Contents", [])}
        in_manifest = {o["key"] for o in v["object_manifest"]}
        check("none of the files the dead attempt had written is still there", set(mid_write).isdisjoint(in_storage),
              f"{len(set(mid_write) & in_storage)} of {len(mid_write)} remain")
        check("what is in storage is exactly what the manifest holds, so the dead attempt left nothing behind", in_storage == in_manifest,
              f"{len(in_storage)} in storage, {len(in_manifest)} in the manifest, {len(in_storage - in_manifest)} not in it")
        wrong = [o["key"] for o in v["object_manifest"] if hashlib.sha256(client.get_object(Bucket=bucket, Key=o["key"])["Body"].read()).hexdigest() != o["sha256"]]
        check("and every file is the file the manifest says", not wrong, str(wrong[:2]))

        # --------------------------------------------------------------------------------------------------------------
        heading("An organisation's own worker serves that organisation only")
        own, own_schema, own_dataset, own_bucket, own_member = make("own")
        other, other_schema, other_dataset, other_bucket, other_member = make("other")
        check("a platform administrator gives the organisation its own line of work",
              api("PUT", f"/tenants/{own.id}/table-worker", json={"dedicated": True}, headers=admin).json().get("dedicated") is True)

        lines = [json.dumps(row(i)).encode() + b"\n" for i in range(3000)]
        key, manifest, _ = put(own, own_dataset, own_bucket, lines)
        mine = seal(own, own_schema, own_dataset, key, manifest, 3000).json()
        time.sleep(25)
        now = state(own_member, mine["job_id"])
        check("its job waits, although the shared pool is running and idle", now["status"] == "pending" and now["dedicated_worker"] is True, str(now)[:160])
        check("the shared pool's log has no trace of the job", mine["job_id"] not in logs(SHARED))

        with db() as conn:
            conn.execute("update table_job set created_at = now() - interval '20 minutes' where id = %s", (mine["job_id"],))
        wide = api("GET", "/housekeeping/storage", headers=admin).json()["table_jobs"]
        listed = [w for w in wide["waiting"] if w["job_id"] == mine["job_id"]]
        check("the housekeeping screen reports the job as stalled and raises the alert, naming the line it waits on",
              wide["alert"] is True and listed and listed[0]["stalled"] and listed[0]["dedicated_worker"] and own.id in listed[0]["queue"]
              and "dataset_name" not in listed[0], str(listed)[:200])
        its = api("GET", "/housekeeping/storage", params={"tenant_id": own.id}, headers=own.bearer("dpo")).json()["table_jobs"]
        check("the organisation's own view names the dataset, and shows no other organisation's job",
              any(w.get("dataset_name") == "own" for w in its["waiting"]) and all(w["tenant_id"] == own.id for w in its["waiting"]), str(its["waiting"])[:160])

        with db() as conn:
            conn.execute("update table_job set created_at = now() where id = %s", (mine["job_id"],))
        worker_script("start", own.id)
        dedicated = f"munitas-table-worker-{own.id}"
        sealed = wait(own_member, mine["job_id"], ("sealed", "refused", "expired"), 150)
        check("when its own worker starts, the job is written and sealed", sealed["status"] == "sealed" and sealed["outcome"] == "written", str(sealed)[:200])
        check("the organisation's worker carried it, and the shared pool did not", mine["job_id"] in logs(dedicated) and mine["job_id"] not in logs(SHARED),
              f"in its own worker's log: {mine['job_id'] in logs(dedicated)}, in the shared pool's: {mine['job_id'] in logs(SHARED)}")

        key, manifest, _ = put(other, other_dataset, other_bucket, lines)
        theirs = seal(other, other_schema, other_dataset, key, manifest, 3000).json()
        done_other = wait(other_member, theirs["job_id"], ("sealed", "refused", "expired"), 150)
        check("another organisation's job is written by the shared pool", done_other["status"] == "sealed" and done_other["dedicated_worker"] is False, str(done_other)[:160])
        check("and the organisation's own worker has no trace of it", theirs["job_id"] not in logs(dedicated))

        refused = docker("exec", dedicated, "python", "-c",
                         "import asyncio\nfrom tablejob.activity import write_table_job\n"
                         "try:\n    asyncio.run(write_table_job({'tenant_id': 'somebody-else', 'job_id': 'x', 'credential': 'y'}))\n"
                         "except Exception as exc:\n    print(type(exc).__name__, getattr(exc, 'type', ''), exc)\n")
        check("a job of another organisation, handed to it anyway, is refused before anything is done",
              "WrongOrganisation" in refused.stdout, (refused.stdout + refused.stderr).strip()[:160])
        nameless = docker("run", "--rm", "-e", "MUNITAS_TABLE_DEDICATED=1", "munitas-table-worker:latest", "python", "-c",
                          "from tablejob import config; config.load()")
        check("a dedicated worker will not start without being told which organisation it serves",
              nameless.returncode != 0 and "must be told which organisation" in nameless.stderr, nameless.stderr.strip()[-120:])

        worker_script("stop", own.id)
        check("the organisation can be taken back to the shared pool",
              api("PUT", f"/tenants/{own.id}/table-worker", json={"dedicated": False}, headers=admin).json().get("dedicated") is False)
        key, manifest, _ = put(own, own_dataset, own_bucket, lines)
        again = seal(own, own_schema, own_dataset, key, manifest, 3000).json()
        back_on_pool = wait(own_member, again["job_id"], ("sealed", "refused", "expired"), 150)
        check("its next job goes to the shared pool and is sealed", back_on_pool["status"] == "sealed" and back_on_pool["dedicated_worker"] is False, str(back_on_pool)[:160])
    finally:
        for org in orgs:
            try:
                worker_script("stop", org.id)
            except SystemExit:
                pass
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        for org in orgs:
            finish_org(org, priya, ravi)
            drop_org(org)
    return summary("U110")


if __name__ == "__main__":
    sys.exit(main())
