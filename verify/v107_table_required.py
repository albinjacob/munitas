"""U107: a version that must be a table is refused when its table cannot be written, and nothing is left behind.

A caller who names a records file is asking for a table. Sealing the version without one when something went wrong used to
be silent: the version looked fine and only its table was missing, and a failed attempt could leave half-written files under
a sealed version's prefix. This checks the tighter rules, each from the side it protects:

  * by default the seal is refused, with the reason, and the version number stays unused;
  * a producer can say, for one seal, that it would rather keep the files without a table;
  * a write that fails halfway leaves nothing behind (a real partial write from the real library, then the failure);
  * a write that takes too long is given up on, and the straggler writes nothing afterwards;
  * a file over the size limit, projection switched off and a platform-wide fail-open switch never refuse.

The refusals over HTTP are real. The faults inside a write are made by replacing one step of the writer for the length of one
seal, so they show what the platform does with the fault, not what the real library would raise in the field (U105 holds that).

    docker compose exec -T munitas-api python /verify/v107_table_required.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from unittest import mock

sys.path.insert(0, "/app")

from common import ADMIN, api, bearer_for, bucket_for, check, db, fixture_tabular_contract, fixture_tabular_version, heading, require_api, s3_client, summary, tabular_rows  # noqa: E402
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, finish_org, make_org  # noqa: E402


def reasons(r) -> str:
    try:
        body = r.json()["detail"]
        return " ".join(body["reasons"]) if isinstance(body, dict) else str(body)
    except Exception:
        return r.text[:200]


def versions_of(dataset_id: str) -> int:
    with db() as conn:
        return conn.execute("select count(*) as n from dataset_version where dataset_id = %s", (dataset_id,)).fetchone()["n"]


def under(bucket: str, prefix: str) -> list[str]:
    listing = s3_client(*ADMIN).list_objects_v2(Bucket=bucket, Prefix=prefix)
    return [o["Key"] for o in listing.get("Contents", [])]


def main() -> int:
    require_api()
    from app import config, iceberg, versions
    from app import db as app_db

    if app_db.pool.closed:
        app_db.pool.open()
    org = make_org()
    try:
        schema = fixture_tabular_contract(org.id)
        first = fixture_tabular_version(org.id, dataset_name="a-table", schema_id=schema)
        dataset_id = first["dataset_id"]
        bucket = bucket_for(org.id)

        def next_prefix() -> str:
            return api("GET", f"/datasets/{dataset_id}/next-version", params={"tenant_id": org.id}).json()["storage_prefix"]

        def put_records(rows: list[dict]) -> tuple[str, list[dict]]:
            body = json.dumps(rows).encode()
            key = f"{next_prefix()}/records.json"
            s3_client(*ADMIN).put_object(Bucket=bucket, Key=key, Body=body)
            return key, [{"key": key, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}]

        def seal_request(key: str, manifest: list[dict], rows: int, **extra):
            return api("POST", "/dataset-versions", json={
                "tenant_id": org.id, "dataset_id": dataset_id, "schema_id": schema, "visibility_class": "RAW",
                "object_manifest": manifest, "record_count": rows, "records_key": key, **extra})

        heading("By default, a table that cannot be written refuses the seal")
        before_prefix, before_count = next_prefix(), versions_of(dataset_id)
        r = seal_request(f"{org.id}/no/such/records.json", [], 1)
        check("an unreadable records file is refused, with the reason and what to do",
              r.status_code == 422 and "could not be read" in reasons(r) and "number is unused" in reasons(r), f"{r.status_code} {reasons(r)[:110]}")
        check("no version was created and the next version number is the same", versions_of(dataset_id) == before_count and next_prefix() == before_prefix)
        bad_key, bad_manifest = put_records([{**row, "count": "not a number"} for row in tabular_rows(3)])
        r = seal_request(bad_key, bad_manifest, 3)
        check("rows that do not fit the contract are refused, naming the kind of error and quoting no value",
              r.status_code == 422 and "ArrowInvalid" in reasons(r) and "not a number" not in reasons(r), f"{r.status_code} {reasons(r)[:120]}")
        check("and that attempt left no table file behind", under(bucket, f"{before_prefix}/iceberg/") == [])
        check("a producer that does not need a table says so, and the seal goes through with a note",
              seal_request(bad_key, bad_manifest, 3, table_required=False).status_code == 201)

        heading("A write that fails halfway leaves nothing behind")
        good_key, good_manifest = put_records(tabular_rows(4))
        prefix = next_prefix()
        count = versions_of(dataset_id)
        import pyiceberg.io.pyarrow as pyiceberg_writer

        real_write = pyiceberg_writer._dataframe_to_data_files
        wrote: list[str] = []

        def writes_then_fails(*args, **kwargs):
            # The real library writes the data files for the rows it was given, and then the storage fails.
            wrote.extend(f.file_path for f in real_write(*args, **kwargs))
            raise RuntimeError("storage fault during the data write")

        with mock.patch.object(pyiceberg_writer, "_dataframe_to_data_files", side_effect=writes_then_fails):
            refused = None
            try:
                versions.seal(tenant_id=org.id, dataset_id=dataset_id, schema_id=schema, visibility_class="RAW", storage_backend="seaweedfs",
                              object_manifest=good_manifest, record_count=4, records_key=good_key)
            except iceberg.TableRequired as exc:
                refused = exc
        check("the seal is refused as a failure, not a skip", refused is not None and refused.outcome == "failed" and "RuntimeError" in refused.reason,
              refused.reason if refused else "sealed")
        check("the message of the fault is not repeated", refused is not None and "storage fault" not in refused.reason)
        check("the real library had written the table's data files and its metadata before the fault, and all of it was removed",
              len(wrote) > 0 and under(bucket, f"{prefix}/iceberg/") == [], f"{len(wrote)} data files written, then {under(bucket, f'{prefix}/iceberg/')}")
        check("and no version was created", versions_of(dataset_id) == count)
        again = versions.seal(tenant_id=org.id, dataset_id=dataset_id, schema_id=schema, visibility_class="RAW", storage_backend="seaweedfs",
                              object_manifest=good_manifest, record_count=4, records_key=good_key)
        check("the same records then seal as a table at the same version number, which was never used",
              again["storage_prefix"] == prefix and bool(db_ref(again["id"])), again["storage_prefix"])

        heading("A write that takes too long is given up on")
        slow_key, slow_manifest = put_records(tabular_rows(2))
        slow_prefix, count = next_prefix(), versions_of(dataset_id)
        original = iceberg.build

        def slow(*a, **k):
            time.sleep(3)
            return original(*a, **k)

        started = time.monotonic()
        with mock.patch.object(config, "ICEBERG_TIMEOUT_SECONDS", 1), mock.patch.object(iceberg, "build", side_effect=slow):
            refused = None
            try:
                versions.seal(tenant_id=org.id, dataset_id=dataset_id, schema_id=schema, visibility_class="RAW", storage_backend="seaweedfs",
                              object_manifest=slow_manifest, record_count=2, records_key=slow_key)
            except iceberg.TableRequired as exc:
                refused = exc
        waited = time.monotonic() - started
        check("the seal is refused after about the deadline, not after the whole write",
              refused is not None and refused.outcome == "failed" and "did not finish within 1 seconds" in refused.reason and waited < 2.9,
              f"{waited:.1f} s: {refused.reason[:90] if refused else 'sealed'}")
        time.sleep(4)
        check("and the abandoned writer, when it wakes, writes nothing", under(bucket, f"{slow_prefix}/iceberg/") == [], str(under(bucket, f"{slow_prefix}/iceberg/")))
        check("no version was created", versions_of(dataset_id) == count)

        heading("Reasons the caller cannot fix never refuse")
        key, manifest = put_records(tabular_rows(3))
        with mock.patch.object(config, "ICEBERG_MAX_BYTES", 10):
            big = versions.seal(tenant_id=org.id, dataset_id=dataset_id, schema_id=schema, visibility_class="RAW", storage_backend="seaweedfs",
                                object_manifest=manifest, record_count=3, records_key=key)
        check("a records file over the size limit is sealed, with a note saying so", bool(big["id"]) and "over the" in table_reason(big["id"]), table_reason(big["id"])[:100])
        key, manifest = put_records(tabular_rows(3))
        with mock.patch.object(config, "ICEBERG_PROJECTION", False):
            off = versions.seal(tenant_id=org.id, dataset_id=dataset_id, schema_id=schema, visibility_class="RAW", storage_backend="seaweedfs",
                                object_manifest=manifest, record_count=3, records_key=key)
        check("with projection switched off a version is sealed, with a note saying so", bool(off["id"]) and "switched off" in table_reason(off["id"]))
        key, manifest = put_records([{**row, "count": "not a number"} for row in tabular_rows(2)])
        with mock.patch.object(config, "ICEBERG_FAIL_CLOSED", False):
            lax = versions.seal(tenant_id=org.id, dataset_id=dataset_id, schema_id=schema, visibility_class="RAW", storage_backend="seaweedfs",
                                object_manifest=manifest, record_count=2, records_key=key)
        check("with the platform-wide switch off, even rows that do not fit are sealed without a table", bool(lax["id"]) and "ArrowInvalid" in table_reason(lax["id"]))
    finally:
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        finish_org(org, priya, ravi)
        drop_org(org)
    return summary("U107")


def db_ref(version_id: str):
    with db() as conn:
        return conn.execute("select 1 from iceberg_table_ref where dataset_version_id = %s", (version_id,)).fetchone()


def table_reason(version_id: str) -> str:
    with db() as conn:
        row = conn.execute("select reason from iceberg_projection_note where dataset_version_id = %s", (version_id,)).fetchone()
    return row["reason"] if row else ""


if __name__ == "__main__":
    sys.exit(main())
