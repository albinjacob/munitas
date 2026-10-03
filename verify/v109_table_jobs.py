"""U109: a large table is written by a worker, in a job, and the seal is finished by the platform when the worker reports.

A table of many gigabytes cannot be written while a request waits. So a seal that names a records file too large for that (or
whose caller asks for it) is answered at once, 202, with a job, and a table worker writes the table. This runs real jobs through
the real stack: the control plane, Temporal, the table worker in its own container, and storage. It checks each side of the
promise:

  * the version is sealed with its table inside it or not at all, and the platform checked what the worker said (it listed and
    hashed the files itself and opened the table);
  * a data problem refuses the seal and leaves nothing behind, and a caller who does not need a table gets the files without one;
  * the version number is reserved while the job runs, and free again if it does not finish;
  * the one storage key a worker is given opens one folder of one organisation and nothing else, and stops working when the job ends;
  * the job's credential opens that job and nothing else, and is not a way to read records.

Needs the stack up with the table worker running (docker compose up -d table-worker).

    docker compose exec -T munitas-api python /verify/v109_table_jobs.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import uuid

sys.path.insert(0, "/app")

import httpx  # noqa: E402

from common import ADMIN, API, api, bearer_for, bucket_for, check, db, fixture_tabular_contract, fixture_tabular_version, heading, require_api, s3_client, summary  # noqa: E402
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, finish_org, make_org  # noqa: E402

WAIT_SECONDS = 240


def row(i: int) -> dict:
    return {"record_id": f"rec-{i}", "transcript": f"synthetic transcript number {i} " + "x" * 120, "score": 0.5 + i, "count": i * 2,
            "ok": i % 2 == 0, "tags": ["a", f"b{i}"], "detail": {"i": i, "nested": {"k": "v"}}}


def ndjson(rows: int) -> bytes:
    return ("\n".join(json.dumps(row(i)) for i in range(rows)) + "\n").encode()


def main() -> int:
    require_api()
    from app import grants, table_jobs, task_credential
    from app import db as app_db

    if app_db.pool.closed:
        app_db.pool.open()
    org, other = make_org(), make_org()
    try:
        admin = bearer_for(ADMIN_A)
        member = org.bearer("member")
        schema = fixture_tabular_contract(org.id)
        first = fixture_tabular_version(org.id, dataset_name="jobs", schema_id=schema)
        dataset_id, bucket = first["dataset_id"], bucket_for(org.id)
        client = s3_client(*ADMIN)
        # Another organisation, with a record of its own in its own bucket, to try the job's key against.
        theirs = fixture_tabular_version(other.id, dataset_name="theirs", schema_id=fixture_tabular_contract(other.id))
        their_bucket, their_key = bucket_for(other.id), theirs["records_key"]

        def next_prefix() -> str:
            return api("GET", f"/datasets/{dataset_id}/next-version", params={"tenant_id": org.id}).json()["storage_prefix"]

        def put(body: bytes, suffix: str = "ndjson") -> tuple[str, list[dict]]:
            key = f"{next_prefix()}/records.{suffix}"
            client.put_object(Bucket=bucket, Key=key, Body=body)
            return key, [{"key": key, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}]

        def seal(key: str, manifest: list[dict], rows: int, **extra):
            return api("POST", "/dataset-versions", json={
                "tenant_id": org.id, "dataset_id": dataset_id, "schema_id": schema, "visibility_class": "RAW",
                "object_manifest": manifest, "record_count": rows, "records_key": key, **extra})

        def job_of(job_id: str) -> dict:
            return api("GET", f"/table-jobs/{job_id}", headers=member).json()

        def wait(job_id: str, until=("sealed", "refused", "expired")) -> dict:
            started = time.monotonic()
            while time.monotonic() - started < WAIT_SECONDS:
                state = job_of(job_id)
                if state["status"] in until:
                    return state
                time.sleep(2)
            return job_of(job_id)

        def under(prefix: str) -> list[str]:
            listing = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
            return [o["Key"] for o in listing.get("Contents", [])]

        def versions_of() -> int:
            with db() as conn:
                return conn.execute("select count(*) as n from dataset_version where dataset_id = %s", (dataset_id,)).fetchone()["n"]

        # --------------------------------------------------------------------------------------------------------------
        heading("A seal is handed to a worker, and the version is sealed when the worker reports")
        rows = 3000
        body = ndjson(rows)
        prefix = next_prefix()
        key, manifest = put(body)
        r = seal(key, manifest, rows, table_mode="background")
        started = r.json()
        check("the answer is 202 at once, with the job, the version number and the folder reserved",
              r.status_code == 202 and started["status"] == "pending" and started["version"] == 2 and started["storage_prefix"] == prefix
              and r.headers.get("location") == f"/table-jobs/{started['job_id']}", f"{r.status_code} {str(started)[:140]}")
        check("the version does not exist yet, and the next version number is already the one after", versions_of() == 1 and next_prefix().endswith("/v3"), next_prefix())
        done = wait(started["job_id"])
        check("the worker writes the table and the job is sealed", done["status"] == "sealed" and done["outcome"] == "written" and done["version_id"], str(done)[:200])
        version_id = done["version_id"]
        table = api("GET", f"/dataset-versions/{version_id}/table", headers=member).json()
        check("the version has its table, with every row", table["projected"] and table["rows"] == rows and table["filterable"], str(table)[:140])

        with db() as conn:
            v = conn.execute("select object_manifest, content_hash, record_count, storage_prefix from dataset_version where id = %s", (version_id,)).fetchone()
            ref = conn.execute("select metadata_location, records_sha256, record_count from iceberg_table_ref where dataset_version_id = %s", (version_id,)).fetchone()
        keys = {o["key"] for o in v["object_manifest"]}
        stored = {k for k in under(f"{prefix}/")}
        check("the manifest holds the records file and every file of the table, and nothing is in storage that it does not hold", keys == stored,
              f"{len(keys)} in the manifest, {len(stored)} in storage")
        wrong = [o["key"] for o in v["object_manifest"] if hashlib.sha256(client.get_object(Bucket=bucket, Key=o["key"])["Body"].read()).hexdigest() != o["sha256"]]
        check("every file's hash in the manifest is the hash of what is in storage", not wrong, str(wrong[:2]))
        from app import versions as app_versions
        check("the version's content hash is the hash of that manifest", v["content_hash"] == app_versions.content_hash(
            {"manifest": v["object_manifest"], "count": rows, "prefix": prefix}))
        check("the register holds the hash of the records file exactly as sent", ref["records_sha256"] == hashlib.sha256(body).hexdigest())

        from pyiceberg.table import StaticTable
        from app import iceberg
        snapshot = StaticTable.from_metadata(ref["metadata_location"], properties=iceberg._file_io(org.id, "seaweedfs")).current_snapshot()
        props = dict(snapshot.summary.additional_properties)
        check("the snapshot names the version it became, which was decided when the job was made",
              props["munitas.dataset-version-id"] == version_id and props["munitas.records-format"] == "ndjson" and props["munitas.record-count"] == str(rows),
              str({k: v for k, v in props.items() if "version" in k or "records" in k}))
        listed = {x["version"] for x in api("GET", f"/datasets/{dataset_id}/versions", headers=member).json()}
        check("the version is in the dataset's list", 2 in listed, str(listed))
        check("another organisation cannot see the job", api("GET", f"/table-jobs/{started['job_id']}", headers=other.bearer("member")).status_code == 404)

        # --------------------------------------------------------------------------------------------------------------
        heading("The size of the records file decides, when the caller does not")
        big = ndjson(115_000)
        key, manifest = put(big)
        r = seal(key, manifest, 115_000)
        check("a records file over the limit for a request becomes a job without being asked", r.status_code == 202, f"{len(big) // 1_000_000} MB: {r.status_code}")
        small_key, small_manifest = put(ndjson(50))
        r2 = seal(small_key, small_manifest, 50, table_mode="inline")
        check("a caller can ask for the table to be written while the request waits, and gets the version at once", r2.status_code == 201 and r2.json().get("sealed"),
              f"{r2.status_code}")
        sized = wait(r.json()["job_id"])
        check("the large table is written and sealed by the worker", sized["status"] == "sealed" and sized["outcome"] == "written", str(sized)[:160])

        # --------------------------------------------------------------------------------------------------------------
        heading("A data problem refuses the seal and leaves nothing behind")
        lines = [json.dumps(row(i)) for i in range(200)]
        lines[6] = "{this is not json"
        prefix, count = next_prefix(), versions_of()
        key, manifest = put(("\n".join(lines) + "\n").encode())
        r = seal(key, manifest, 200, table_mode="background")
        refused = wait(r.json()["job_id"])
        check("the job is refused, with the line", refused["status"] == "refused" and "line 7 is not JSON" in (refused["reason"] or ""), str(refused)[:200])
        check("no version was made", versions_of() == count and refused["version_id"] is None)
        check("nothing of a table is left under the folder", [k for k in under(f"{prefix}/") if "/iceberg/" in k] == [], str(under(f"{prefix}/")[:3]))
        check("and the folder is the next one again", next_prefix() == prefix, next_prefix())

        key, manifest = put(("\n".join(lines) + "\n").encode())
        r = seal(key, manifest, 200, table_mode="background", table_required=False)
        kept = wait(r.json()["job_id"])
        with db() as conn:
            note = conn.execute("select outcome, reason from iceberg_projection_note where dataset_version_id = %s", (kept["version_id"],)).fetchone()
        check("a caller who does not need a table gets the files sealed without one", kept["status"] == "sealed" and kept["outcome"] == "skipped", str(kept)[:160])
        check("and the note names the line", note and "line 7 is not JSON" in note["reason"], str(note))
        check("and no table file is in the sealed folder", [k for k in under(f"{kept['storage_prefix']}/") if "/iceberg/" in k] == [])

        outside = {"tenant_id": org.id, "dataset_id": dataset_id, "schema_id": schema, "visibility_class": "RAW", "object_manifest": [],
                   "record_count": 1, "records_key": f"{org.id}/somewhere/else/records.ndjson", "table_mode": "background"}
        r = api("POST", "/dataset-versions", json=outside)
        check("a records file outside the version's folder is refused when the request is made, and no job is made",
              r.status_code == 422 and "own folder" in json.dumps(r.json()), f"{r.status_code} {str(r.json())[:120]}")

        # --------------------------------------------------------------------------------------------------------------
        heading("The version number is reserved while a job waits")
        check("an organisation can be given a worker of its own, and only by a platform administrator",
              api("PUT", f"/tenants/{org.id}/table-worker", json={"dedicated": True}, headers=member).status_code == 403
              and api("PUT", f"/tenants/{org.id}/table-worker", json={"dedicated": True}, headers=admin).status_code == 200)
        prefix_a = next_prefix()
        key_a, manifest_a = put(ndjson(20))
        job_a = seal(key_a, manifest_a, 20, table_mode="background").json()
        check("the job goes to the organisation's own line, where nobody is working", job_a["dedicated_worker"] is True and job_a["status"] == "pending")
        prefix_b = next_prefix()
        key_b, manifest_b = put(ndjson(20))
        job_b = seal(key_b, manifest_b, 20, table_mode="background").json()
        check("a second seal of the dataset is given the next number, not the one the first job holds", prefix_b != prefix_a and job_b["version"] == job_a["version"] + 1,
              f"{job_a['version']} and {job_b['version']}")
        with db() as conn:
            duplicate = None
            try:
                with conn.transaction():
                    conn.execute("insert into table_job (id, tenant_id, dataset_id, version, storage_prefix, storage_backend, request, queue, status, expires_at) "
                                 "select %s, tenant_id, dataset_id, version, storage_prefix, storage_backend, request, queue, 'pending', expires_at "
                                 "from table_job where id = %s", (str(uuid.uuid4()), job_a["job_id"]))
            except Exception as exc:  # noqa: BLE001
                duplicate = type(exc).__name__
        check("storage of the register itself refuses two active jobs for one version number", duplicate == "UniqueViolation", str(duplicate))

        # --------------------------------------------------------------------------------------------------------------
        heading("The one key a worker is given opens one folder and nothing else")
        token = task_credential.mint(principal="x", task_kind="table_write_job", task_id=job_a["job_id"], tenant_id=org.id, ttl_seconds=600)
        r = httpx.post(f"{API}/table-jobs/{job_a['job_id']}/credentials", headers={"X-Task-Credential": token}, timeout=120)
        creds = r.json()
        check("the platform makes the key, once it is in the live permissions", r.status_code == 200 and creds.get("active") and creds["prefix"] == prefix_a, f"{r.status_code}")
        time.sleep(4)  # storage reads its permissions a moment after they are printed
        import tablewriter
        scoped = tablewriter.s3_client(tablewriter.s3_properties(creds["endpoint"], creds["access_key"], creds["secret_key"]))

        def attempt(call) -> str:
            try:
                call()
                return "allowed"
            except Exception as exc:  # noqa: BLE001
                return getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)

        check("it can read the records the job was made for", attempt(lambda: scoped.get_object(Bucket=bucket, Key=key_a)["Body"].read(5)) == "allowed")
        check("it can write and remove under the folder",
              attempt(lambda: scoped.put_object(Bucket=bucket, Key=f"{prefix_a}/iceberg/probe.txt", Body=b"x")) == "allowed"
              and attempt(lambda: scoped.delete_object(Bucket=bucket, Key=f"{prefix_a}/iceberg/probe.txt")) == "allowed")
        check("it can list object names in its own organisation's bucket, which is the one place it is wider than its folder, and a name is all it gets",
              attempt(lambda: scoped.list_objects_v2(Bucket=bucket)) == "allowed"
              and attempt(lambda: scoped.get_object(Bucket=bucket, Key=first["records_key"])) == "AccessDenied")
        check("it cannot read another version of the same organisation", attempt(lambda: scoped.get_object(Bucket=bucket, Key=first["records_key"])) == "AccessDenied")
        check("it cannot write outside its folder in the same bucket", attempt(lambda: scoped.put_object(Bucket=bucket, Key=f"{prefix_b}/stray.txt", Body=b"x")) == "AccessDenied")
        check("it cannot read another organisation's bucket", attempt(lambda: scoped.get_object(Bucket=their_bucket, Key=their_key)) == "AccessDenied")
        check("it cannot list another organisation's bucket", attempt(lambda: scoped.list_objects_v2(Bucket=their_bucket)) == "AccessDenied")
        check("it cannot write to another organisation's bucket", attempt(lambda: scoped.put_object(Bucket=their_bucket, Key="stray.txt", Body=b"x")) == "AccessDenied")
        check("it cannot make or remove a bucket", attempt(lambda: scoped.create_bucket(Bucket=f"stray-{uuid.uuid4().hex[:8]}")) != "allowed"
              and attempt(lambda: scoped.delete_bucket(Bucket=their_bucket)) != "allowed")
        # A control: the same attempt, with a key that is allowed to read there, is allowed. So a refusal above is the key's, and
        # not something the test does to every call.
        check("control: the same attempt with the platform's administrator key is allowed, so the refusals above are the job key's",
              attempt(lambda: s3_client(*ADMIN).get_object(Bucket=their_bucket, Key=their_key)["Body"].read(5)) == "allowed")

        heading("The job's credential opens that job and nothing else")
        url = f"{API}/table-jobs"

        def call(method: str, path: str, credential: str | None, **kw):
            headers = {"X-Task-Credential": credential} if credential else {}
            return httpx.request(method, f"{url}{path}", headers=headers, timeout=60, **kw)

        check("no credential is refused", call("GET", f"/{job_a['job_id']}/work", None).status_code == 403)
        check("a credential for another job is refused", call("GET", f"/{job_a['job_id']}/work", task_credential.mint(
            principal="x", task_kind="table_write_job", task_id=job_b["job_id"], tenant_id=org.id)).status_code == 403)
        check("a credential for the same job in another organisation's name is refused", call("GET", f"/{job_a['job_id']}/work", task_credential.mint(
            principal="x", task_kind="table_write_job", task_id=job_a["job_id"], tenant_id=other.id)).status_code == 403)
        check("an expired credential is refused", call("GET", f"/{job_a['job_id']}/work", task_credential.mint(
            principal="x", task_kind="table_write_job", task_id=job_a["job_id"], tenant_id=org.id, ttl_seconds=-5)).status_code == 403)
        check("a credential of another kind is refused", call("GET", f"/{job_a['job_id']}/work", task_credential.mint(
            principal="x", task_kind="agent_run", task_id=job_a["job_id"], tenant_id=org.id)).status_code == 403)
        check("a made-up credential is refused", call("GET", f"/{job_a['job_id']}/work", "not.a.credential").status_code == 403)
        r = httpx.get(f"{API}/dataset-versions/{version_id}", headers={"X-Task-Credential": token}, timeout=30)
        check("the job's credential is not a way to read a version's records or manifest", r.status_code == 401, f"{r.status_code}")
        r = call("POST", f"/{job_a['job_id']}/finish", token, json={"outcome": "written", "metadata_location": f"s3://{their_bucket}/x/iceberg/metadata/00000.json",
                                                                    "snapshot_id": 1, "record_count": 1, "records_sha256": "0" * 64})
        check("a report that points outside the version's folder does not seal anything", r.status_code == 200 and r.json()["status"] != "sealed", str(r.json())[:200])

        # --------------------------------------------------------------------------------------------------------------
        heading("The platform checks what a worker reports against storage")
        import tablewriter as tw

        def by_hand(rows: int = 40, **extra):
            """Do what a worker does, with the job's own key and the same library, so a report can be made wrong in a chosen way.
            The organisation is still on its own line of work, where nobody is working, so the real worker does not take the job."""
            key, manifest = put(ndjson(rows))
            job = seal(key, manifest, rows, table_mode="background", **extra).json()
            headers = {"X-Task-Credential": task_credential.mint(principal="x", task_kind="table_write_job", task_id=job["job_id"],
                                                                 tenant_id=org.id, ttl_seconds=600)}
            work = httpx.get(f"{API}/table-jobs/{job['job_id']}/work", headers=headers, timeout=60).json()
            granted = httpx.post(f"{API}/table-jobs/{job['job_id']}/credentials", headers=headers, timeout=120).json()
            time.sleep(5)  # storage reads its permissions a moment after they are printed
            props = tw.s3_properties(granted["endpoint"], granted["access_key"], granted["secret_key"])
            projection = tw.write_table(
                props=props, client=tw.s3_client(props), bucket=work["bucket"], prefix=work["storage_prefix"], location=work["location"],
                namespace=work["dataset_name"], table_name=work["table_name"], contract=work["contract"], records_key=work["records_key"],
                summary_base=work["summary_base"], cfg=tw.Settings(**work["settings"]), manifest=False, cleanup=False)
            return job, headers, projection, work

        def report(job, headers, projection, **override):
            body = {"outcome": "written", "metadata_location": projection.metadata_location, "snapshot_id": projection.snapshot_id,
                    "record_count": projection.record_count, "records_sha256": projection.records_sha256, **override}
            return httpx.post(f"{API}/table-jobs/{job['job_id']}/finish", headers=headers, json=body, timeout=300).json()

        job, headers, projection, work = by_hand()
        check("the work the platform hands out names the table and the contract, and a worker with the job's own key can write it",
              work["table_name"] == f"v{job['version']}" and projection.record_count == 40 and projection.objects == [], f"{projection.record_count} rows")
        honest = report(job, headers, projection)
        check("an honest report is checked and sealed", honest["status"] == "sealed" and honest["outcome"] == "written" and honest["version_id"], str(honest)[:160])
        again = report(job, headers, projection)
        check("a report made twice is answered with the same result and seals nothing more", again["status"] == "sealed" and again["version_id"] == honest["version_id"])
        listed = {x["version"] for x in api("GET", f"/datasets/{dataset_id}/versions", headers=member).json()}
        check("one version was made, not two", list(sorted(listed)).count(job["version"]) == 1, str(sorted(listed)))

        job, headers, projection, work = by_hand()
        client.put_object(Bucket=bucket, Key=f"{work['storage_prefix']}/iceberg/data/stray-0000.parquet", Body=b"left by another attempt")
        r = report(job, headers, projection)
        check("a data file in the folder that the table does not use refuses the seal", r["status"] == "refused" and "does not use" in (r["reason"] or ""), str(r)[:200])
        check("and the folder is cleared, the stray file included", [k for k in under(f"{work['storage_prefix']}/") if "/iceberg/" in k] == [])

        job, headers, projection, work = by_hand()
        r = report(job, headers, projection, record_count=projection.record_count + 1)
        check("a row count that is not the table's refuses the seal", r["status"] == "refused" and "did not check out" in (r["reason"] or ""), str(r)[:200])

        job, headers, projection, work = by_hand()
        r = report(job, headers, projection, records_sha256="0" * 64)
        check("a records hash that is not the one the producer declared refuses the seal", r["status"] == "refused" and "hash differs" in (r["reason"] or ""), str(r)[:200])

        job, headers, projection, work = by_hand()
        r = report(job, headers, projection, metadata_location=f"s3://{their_bucket}/x/iceberg/metadata/00000.metadata.json")
        check("a table reported at a location outside the version's folder refuses the seal", r["status"] == "refused", str(r)[:200])

        # --------------------------------------------------------------------------------------------------------------
        heading("A job nobody finished expires, and its key stops working")
        with db() as conn:
            conn.execute("update table_job set status = 'running', expires_at = now() - interval '1 minute' where id = %s", (job_b["job_id"],))
        expired = table_jobs.expire_due()
        check("a job past its time is expired and has no version", expired >= 1 and job_of(job_b["job_id"])["status"] == "expired")
        with db() as conn:
            conn.execute("update table_job set status = 'expired', finished_at = now() where id = %s and status in ('pending','running')", (job_a["job_id"],))
        grants.reconcile(trigger="manual")
        time.sleep(5)
        check("the key of an ended job no longer opens its folder", attempt(lambda: scoped.get_object(Bucket=bucket, Key=key_a)) != "allowed")
        with db() as conn:
            newest = conn.execute("select max(version) as v from dataset_version where dataset_id = %s", (dataset_id,)).fetchone()["v"]
        check("and with no job holding a number, the next seal takes the one after the newest version", next_prefix().endswith(f"/v{newest + 1}"),
              f"newest {newest}, next {next_prefix()}")
    finally:
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        for o in (org, other):
            finish_org(o, priya, ravi)
            drop_org(o)
    return summary("U109")


if __name__ == "__main__":
    sys.exit(main())
