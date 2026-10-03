"""U108: a large table is read in batches and written a file at a time, from rows (one per line) or from a Parquet file.

A records file used to be one JSON list, read whole. That cannot be a large table: measured here, a JSON list takes about six
times its size in memory, and the process is allowed one gigabyte. Two further shapes are now accepted, chosen by the name:

  * newline-delimited JSON (.ndjson, .jsonl), one row per line, read as a stream;
  * Parquet (.parquet), read one batch at a time and rewritten through the platform's own writer, so the table has the
    contract's types, its column identifiers and its own file layout, and no value goes in unchecked.

This checks each from the side it protects: that the rows come out as they went in, that every kind of bad input is refused
with a reason that names a place and never a value, that a failure halfway leaves nothing behind (earlier files of the same
write included), that hostile Parquet is refused, and, with real files and a real measurement, that memory does not grow with
the file. The memory check has a control: the same rows as a JSON list, which must take clearly more, so the check can fail.

    docker compose exec -T munitas-api python /verify/v108_large_tables.py
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import time
from unittest import mock

sys.path.insert(0, "/app")

from common import ADMIN, api, bearer_for, bucket_for, check, db, fixture_tabular_contract, fixture_tabular_version, heading, require_api, s3_client, summary  # noqa: E402
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, finish_org, make_org  # noqa: E402

CHILD = r"""
import json, resource, sys
sys.path.insert(0, "/app")
from app import db, iceberg
db.pool.open()
t = json.loads(sys.argv[1])
iceberg.project(tenant_id=t["tenant"], backend="seaweedfs", dataset_id=t["dataset"], dataset_name="probe", version_id="x",
                version=1, prefix=t["prefix"], schema_id=t["schema"], records_key=t["key"], produced_by_run=None)
print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)
"""


def row(i: int) -> dict:
    return {"record_id": f"rec-{i}", "transcript": f"synthetic transcript number {i} " + "x" * 120, "score": 0.5 + i, "count": i * 2,
            "ok": i % 2 == 0, "tags": ["a", f"b{i}"], "detail": {"i": i, "nested": {"k": "v"}}}


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
    keys, token = [], None
    while True:
        listing = s3_client(*ADMIN).list_objects_v2(Bucket=bucket, Prefix=prefix, **({"ContinuationToken": token} if token else {}))
        keys += [o["Key"] for o in listing.get("Contents", [])]
        token = listing.get("NextContinuationToken")
        if not token:
            return keys


def parquet_of(rows: int, start: int = 0, count_type=None, row_group: int | None = None, **replace) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    ids = range(start, start + rows)
    columns = {
        "record_id": pa.array([f"rec-{i}" for i in ids], pa.string()), "transcript": pa.array([f"synthetic transcript number {i}" for i in ids], pa.string()),
        "score": pa.array([0.5 + i for i in ids], pa.float64()), "count": pa.array([i * 2 for i in ids], count_type or pa.int32()),
        "ok": pa.array([i % 2 == 0 for i in ids], pa.bool_()), "tags": pa.array([json.dumps(["a", f"b{i}"]) for i in ids], pa.string()),
        "detail": pa.array([json.dumps({"i": i, "nested": {"k": "v"}}) for i in ids], pa.string()),
    }
    columns.update(replace)
    out = io.BytesIO()
    pq.write_table(pa.table({k: v for k, v in columns.items() if v is not None}), out, compression="zstd", row_group_size=row_group)
    return out.getvalue()


def main() -> int:
    require_api()
    from app import config, iceberg, versions
    import pyarrow.compute as pc
    from app import db as app_db

    if app_db.pool.closed:
        app_db.pool.open()
    org = make_org()
    try:
        schema = fixture_tabular_contract(org.id)
        first = fixture_tabular_version(org.id, dataset_name="a-table", schema_id=schema)
        dataset_id, bucket = first["dataset_id"], bucket_for(org.id)
        client = s3_client(*ADMIN)

        def next_prefix() -> str:
            return api("GET", f"/datasets/{dataset_id}/next-version", params={"tenant_id": org.id}).json()["storage_prefix"]

        def put(body: bytes, suffix: str) -> tuple[str, list[dict]]:
            key = f"{next_prefix()}/records.{suffix}"
            client.put_object(Bucket=bucket, Key=key, Body=body)
            return key, [{"key": key, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}]

        def seal(key: str, manifest: list[dict], rows: int, **extra):
            return api("POST", "/dataset-versions", json={
                "tenant_id": org.id, "dataset_id": dataset_id, "schema_id": schema, "visibility_class": "RAW",
                "object_manifest": manifest, "record_count": rows, "records_key": key, **extra})

        def seal_here(key: str, manifest: list[dict], rows: int):
            return versions.seal(tenant_id=org.id, dataset_id=dataset_id, schema_id=schema, visibility_class="RAW", storage_backend="seaweedfs",
                                 object_manifest=manifest, record_count=rows, records_key=key)

        def refused(body: bytes, suffix: str, rows: int = 5):
            """Seal in this process, where the settings a check patches are the settings that apply, and expect refusal.
            Returns the refusal (or None), its reason, what was left under the version's table and whether the number is unused."""
            prefix, count = next_prefix(), versions_of(dataset_id)
            key, manifest = put(body, suffix)
            try:
                seal_here(key, manifest, rows)
                return None, "sealed", under(bucket, f"{prefix}/iceberg/"), versions_of(dataset_id) == count
            except iceberg.TableRequired as exc:
                return exc, exc.reason, under(bucket, f"{prefix}/iceberg/"), versions_of(dataset_id) == count

        def note_of(version_id: str) -> str:
            with db() as conn:
                found = conn.execute("select reason from iceberg_projection_note where dataset_version_id = %s", (version_id,)).fetchone()
            return found["reason"] if found else ""

        def table_of(version_id: str):
            from pyiceberg.table import StaticTable

            with db() as conn:
                ref = conn.execute("select metadata_location, record_count, records_sha256 from iceberg_table_ref where dataset_version_id = %s",
                                   (version_id,)).fetchone()
            return ref, StaticTable.from_metadata(ref["metadata_location"], properties=iceberg._file_io(org.id, "seaweedfs"))

        def summary_of(table) -> dict:
            return dict(table.current_snapshot().summary.additional_properties)

        heading("The shape is chosen by the name")
        names = {"x/records.json": "json", "x/records.NDJSON": "ndjson", "x/records.jsonl": "ndjson", "x/records.parquet": "parquet",
                 "x/records.PARQUET": "parquet", "x/records": "json", "x/records.csv": "json"}
        check("a name says which shape, and a name that says nothing is a JSON list as before",
              all(iceberg.records_format(k) == v for k, v in names.items()), str({k: iceberg.records_format(k) for k in names}))

        heading("Rows, one per line")
        rows = 25_000
        lines = [json.dumps(row(i)) for i in range(rows)]
        lines.insert(100, "")
        lines.insert(5000, "   ")
        body = ("\n".join(lines) + "\n").encode()
        key, manifest = put(body, "ndjson")
        prefix = next_prefix()
        with mock.patch.object(config, "ICEBERG_BATCH_ROWS", 2000), mock.patch.object(config, "ICEBERG_FILE_BYTES", 1024 * 1024):
            sealed = seal_here(key, manifest, rows)
        ref, table = table_of(sealed["id"])
        arrow = table.scan().to_arrow()
        check("every row is in the table, and the blank lines are not rows", ref["record_count"] == rows == arrow.num_rows, f"{arrow.num_rows}")
        picked = arrow.filter(pc.equal(arrow["record_id"], "rec-12345")).to_pylist()
        check("a row comes out as it went in, a list and a dictionary as JSON text",
              len(picked) == 1 and picked[0]["count"] == 24690 and picked[0]["ok"] is False and abs(picked[0]["score"] - 12345.5) < 1e-9
              and picked[0]["tags"] == json.dumps(["a", "b12345"]) and picked[0]["detail"] == json.dumps({"i": 12345, "nested": {"k": "v"}}, sort_keys=True),
              str(picked)[:160])
        check("the register holds the hash of the file exactly as sent", ref["records_sha256"] == hashlib.sha256(body).hexdigest())
        props = summary_of(table)
        check("the snapshot says the shape, the count and the hash, which were known before the first row was written",
              props["munitas.records-format"] == "ndjson" and props["munitas.record-count"] == str(rows)
              and props["munitas.records-sha256"] == ref["records_sha256"], str({k: v for k, v in props.items() if "records" in k or "count" in k}))
        files = [k for k in under(bucket, f"{prefix}/iceberg/") if k.endswith(".parquet")]
        check("it was written as several data files, a batch at a time", len(files) > 1, f"{len(files)} files")
        with db() as conn:
            manifest_keys = {o["key"] for o in conn.execute("select object_manifest from dataset_version where id = %s", (sealed["id"],)).fetchone()["object_manifest"]}
        check("and every data file is in the version's manifest, so the content hash covers it", bool(files) and set(under(bucket, f"{prefix}/iceberg/")) <= manifest_keys,
              f"{len(set(under(bucket, f'{prefix}/iceberg/')) - manifest_keys)} files not in the manifest")

        heading("Rows that cannot be a table are refused, naming a place and never a value")
        good = [json.dumps(row(i)) for i in range(6000)]
        text = "\n".join(good[:6] + ["{not json"] + good[7:]) + "\n"
        key, manifest = put(text.encode(), "ndjson")
        r = seal(key, manifest, 6000)
        check("over HTTP the answer is 422, with the line and what to do", r.status_code == 422 and "line 7 is not JSON" in reasons(r)
              and "table_required" in reasons(r), f"{r.status_code} {reasons(r)[:120]}")
        for what, mutate, expect in [
            ("a line that is not JSON", lambda l: l.__setitem__(6, "{this is not json"), "line 7 is not JSON"),
            ("a line that is JSON and not a row", lambda l: l.__setitem__(6, json.dumps([1, 2, 3])), "line 7 is not a row"),
            ("a row with no value for the key", lambda l: l.__setitem__(6, json.dumps({**row(6), "record_id": None})), "no value for the key field 'record_id'"),
            ("a value of the wrong type, deep in the file, after earlier files were written",
             lambda l: l.__setitem__(5100, json.dumps({**row(5100), "count": "SECRET-not-a-number"})), "ArrowInvalid"),
        ]:
            lines = list(good)
            mutate(lines)
            with mock.patch.object(config, "ICEBERG_BATCH_ROWS", 1000), mock.patch.object(config, "ICEBERG_FILE_BYTES", 200 * 1024):
                exc, why, left, same = refused(("\n".join(lines) + "\n").encode(), "ndjson")
            check(f"{what} is refused with 422 and the place", exc is not None and expect in why, why[:140])
            check("  and nothing is left behind and the version number is unused", left == [] and same, str(left[:3]))
        check("the place of a wrong value is the range of lines of its batch, and the reason carries none of it",
              "lines 5001 to 6000" in why and "SECRET" not in why, why[:160])
        exc, why, left, same = refused(b"\n   \n\n", "ndjson")
        check("a file of blank lines has no rows", exc is not None and "has no rows" in why, why[:100])
        with mock.patch.object(config, "ICEBERG_MAX_ROW_BYTES", 1000):
            exc, why, left, same = refused((json.dumps(row(1)) + "\n" + "x" * 5000 + "\n").encode(), "ndjson")
        check("a line longer than the limit is refused before it is held", exc is not None and "line 2 is longer than 1 KB" in why, why[:100])

        heading("Parquet")
        import pyarrow as pa
        import pyarrow.parquet as pq

        source = parquet_of(30_000, row_group=10_000)
        key, manifest = put(source, "parquet")
        prefix = next_prefix()
        with mock.patch.object(config, "ICEBERG_BATCH_ROWS", 4000), mock.patch.object(config, "ICEBERG_FILE_BYTES", 1024 * 1024):
            sealed = seal_here(key, manifest, 30_000)
        ref, table = table_of(sealed["id"])
        arrow = table.scan().to_arrow()
        picked = arrow.filter(pc.equal(arrow["record_id"], "rec-29999")).to_pylist()
        check("every row of a Parquet file is in the table, with the contract's types",
              ref["record_count"] == arrow.num_rows == 30_000 and len(picked) == 1 and picked[0]["count"] == 59998
              and isinstance(picked[0]["count"], int) and picked[0]["tags"] == json.dumps(["a", "b29999"]), str(picked)[:140])
        check("a 32-bit integer column is read as the contract's 64-bit integer", str(table.schema().find_field("count").field_type) == "long")
        check("the register holds the hash of the Parquet file exactly as sent", ref["records_sha256"] == hashlib.sha256(source).hexdigest())
        check("the snapshot says it came from Parquet", summary_of(table)["munitas.records-format"] == "parquet")
        data = [k for k in under(bucket, f"{prefix}/iceberg/") if k.endswith(".parquet")]
        written = pq.ParquetFile(io.BytesIO(client.get_object(Bucket=bucket, Key=data[0])["Body"].read())).schema_arrow
        sent = pq.ParquetFile(io.BytesIO(source)).schema_arrow
        check("the written files carry the table's column identifiers, which the producer's file did not",
              written.field("record_id").metadata and b"PARQUET:field_id" in written.field("record_id").metadata and not sent.field("record_id").metadata,
              str(written.field("record_id").metadata))
        check("and the table is several files, so the producer's row groups are not the table's layout", len(data) > 1, f"{len(data)} files")
        dictionary = parquet_of(500, record_id=pa.array([f"rec-{i}" for i in range(500)]).dictionary_encode())
        key, manifest = put(dictionary, "parquet")
        check("a column the producer dictionary-encoded is read as text", table_of(seal_here(key, manifest, 500)["id"])[0]["record_count"] == 500)

        heading("Parquet that does not fit the contract is refused, naming a column and never a value")
        wide = pa.table({"record_id": ["a"]})
        out = io.BytesIO()
        pq.write_table(wide, out)
        cases = [
            ("a file with a column missing", parquet_of(10, tags=None), "no column for 'tags'"),
            ("a file with a column the contract does not describe", parquet_of(10, nickname=["n"] * 10), "does not describe: 'nickname'"),
            ("a number column holding text", parquet_of(10, count=[f"SECRET-{i}" for i in range(10)]), "column types do not match the contract for 'count'"),
            ("a text column holding numbers", parquet_of(10, transcript=list(range(10))), "do not match the contract for 'transcript'"),
            ("a column of nested values where the contract says text", parquet_of(10, detail=[{"k": i} for i in range(10)]), "do not match the contract for 'detail'"),
            ("a key column with a missing value", parquet_of(10, record_id=["a", None] + ["b"] * 8), "no value for the key field 'record_id'"),
            ("a value too large for the contract's integer", parquet_of(10, count_type=pa.uint64(), count=pa.array([2 ** 63 + 5] * 10, pa.uint64())),
             "does not fit its contract type (ArrowInvalid)"),
            ("a file with no rows", parquet_of(0), "no rows"),
        ]
        for what, body, expect in cases:
            exc, why, left, same = refused(body, "parquet")
            check(f"{what} is refused with 422", exc is not None and expect in why and "SECRET" not in why and "9223372036854775" not in why,
                  why[:150])
            check("  and nothing is left behind and the version number is unused", left == [] and same, str(left[:3]))

        r = seal(*put(parquet_of(10, tags=None), "parquet"), 10)
        check("over HTTP a Parquet file with a column missing is answered 422 and names the column", r.status_code == 422 and "no column for 'tags'" in reasons(r),
              f"{r.status_code} {reasons(r)[:110]}")

        heading("Hostile Parquet is refused before it is read")
        exc, why, left, same = refused(os.urandom(5000), "parquet")
        check("bytes that are not Parquet are refused as not readable", exc is not None and "not a readable Parquet file" in why, why[:110])
        exc, why, left, same = refused(source[: len(source) // 2], "parquet")
        check("a Parquet file cut short is refused as not readable", exc is not None and "not a readable Parquet file" in why, why[:110])
        big_groups = parquet_of(20_000, row_group=20_000)
        with mock.patch.object(config, "ICEBERG_PARQUET_MAX_ROW_GROUP_BYTES", 100 * 1024):
            exc, why, left, same = refused(big_groups, "parquet")
        check("a row group larger than what is read at once is refused, with what to do", exc is not None and "row group" in why
              and "smaller row groups" in why, why[:160])
        with mock.patch.object(config, "ICEBERG_PARQUET_THRIFT_CONTAINER_ITEMS", 5):
            exc, why, left, same = refused(source, "parquet")
        check("a footer that asks for more than the limit allows is refused", exc is not None and "not a readable Parquet file" in why, why[:110])
        key, manifest = put(source, "parquet")
        with mock.patch.object(config, "ICEBERG_STREAM_MAX_BYTES", 1000):
            over = seal_here(key, manifest, 30_000)
        check("a file over the streaming size limit is sealed without a table, says so, and does not refuse the seal",
              bool(over["id"]) and "over the" in note_of(over["id"]), note_of(over["id"])[:140])

        heading("A write that takes too long is given up on, and the straggler writes nothing more")
        slow_body = ("\n".join(json.dumps(row(i)) for i in range(5000)) + "\n").encode()
        key, manifest = put(slow_body, "ndjson")
        slow_prefix, count = next_prefix(), versions_of(dataset_id)
        original = iceberg.build

        def slow(*a, **k):
            time.sleep(1.2)
            return original(*a, **k)

        started = time.monotonic()
        with mock.patch.object(config, "ICEBERG_TIMEOUT_SECONDS", 2), mock.patch.object(config, "ICEBERG_BATCH_ROWS", 500), \
                mock.patch.object(config, "ICEBERG_FILE_BYTES", 100 * 1024), mock.patch.object(iceberg, "build", side_effect=slow):
            gave_up = None
            try:
                seal_here(key, manifest, 5000)
            except iceberg.TableRequired as exc:
                gave_up = exc
        waited = time.monotonic() - started
        check("the seal is refused after about the deadline, not after the whole write",
              gave_up is not None and "did not finish within 2 seconds" in gave_up.reason and waited < 5, f"{waited:.1f} s")
        time.sleep(14)
        check("and when the abandoned writer wakes it stops and removes what it wrote",
              under(bucket, f"{slow_prefix}/iceberg/") == [] and versions_of(dataset_id) == count, str(under(bucket, f"{slow_prefix}/iceberg/")[:3]))

        heading("The same rules as before, for a producer that does not need a table")
        bad = ("\n".join(good[:6] + ["{not json"] + good[7:]) + "\n").encode()
        key, manifest = put(bad, "ndjson")
        r = seal(key, manifest, 6000, table_required=False)
        check("rows that cannot be a table are sealed as files when the producer says it does not need one", r.status_code == 201, str(r.status_code))
        with db() as conn:
            note = conn.execute("select outcome, reason from iceberg_projection_note where dataset_version_id = %s", (r.json()["id"],)).fetchone()
        check("and the note names the line", note and note["outcome"] == "skipped" and "line 7 is not JSON" in note["reason"], str(note))
        body = json.dumps([row(i) for i in range(10)]).encode()
        key, manifest = put(body, "json")
        with mock.patch.object(config, "ICEBERG_MAX_BYTES", 100):
            listed = seal_here(key, manifest, 10)
        check("a JSON list over its limit is sealed without a table, and the note says which shapes have no such limit",
              bool(listed["id"]) and "newline-delimited JSON or Parquet" in note_of(listed["id"]), note_of(listed["id"])[:200])

        heading("Memory does not grow with the file")
        peaks: dict[str, int] = {}

        def peak(label: str, kind: str, rows: int, env: dict | None = None) -> int:
            """Seal rows in a process of its own and report its highest memory. The file is made here, in a different process."""
            suffix = {"ndjson": "ndjson", "json": "json", "parquet": "parquet"}[kind]
            path = f"/tmp/u108.{suffix}"
            if kind == "parquet":
                with pq.ParquetWriter(path, pa.schema([("record_id", pa.string()), ("transcript", pa.string()), ("score", pa.float64()),
                                                       ("count", pa.int32()), ("ok", pa.bool_()), ("tags", pa.string()), ("detail", pa.string())]),
                                      compression="zstd") as writer:
                    for start in range(0, rows, 50_000):
                        n = min(50_000, rows - start)
                        writer.write_table(pa.Table.from_batches([pa.RecordBatch.from_pylist(
                            [{"record_id": f"rec-{start + i}", "transcript": f"synthetic transcript number {start + i} " + "x" * 120,
                              "score": 0.5 + i, "count": i, "ok": i % 2 == 0, "tags": "[]", "detail": "{}"} for i in range(n)])]).cast(writer.schema),
                                           row_group_size=100_000)
            else:
                with open(path, "w") as out:
                    out.write("[" if kind == "json" else "")
                    for i in range(rows):
                        out.write(("," if kind == "json" and i else "") + json.dumps(row(i)) + ("" if kind == "json" else "\n"))
                    out.write("]" if kind == "json" else "")
            where = f"mem/{label}"
            client.upload_file(path, bucket, f"{where}/records.{suffix}")
            size = os.path.getsize(path)
            os.remove(path)
            done = subprocess.run([sys.executable, "-c", CHILD, json.dumps({"tenant": org.id, "dataset": dataset_id, "prefix": where, "schema": schema,
                                                                           "key": f"{where}/records.{suffix}"})],
                                  capture_output=True, text=True, env={**os.environ, **(env or {})}, timeout=900)
            if done.returncode:
                print(done.stderr[-600:])
            peaks[label] = int(done.stdout.strip().splitlines()[-1]) if done.returncode == 0 else -1
            print(f"    {label}: {rows} rows, {size // 1_000_000} MB, highest memory {peaks[label]} MB")
            return peaks[label]

        peak("rows small", "ndjson", 150_000)
        peak("rows large", "ndjson", 600_000)
        check("four times the rows as lines adds little memory", peaks["rows large"] - peaks["rows small"] < 120 and peaks["rows large"] < 800,
              f"{peaks['rows small']} MB, then {peaks['rows large']} MB")
        peak("parquet small", "parquet", 500_000)
        peak("parquet large", "parquet", 2_000_000)
        check("four times the rows as Parquet adds little memory", peaks["parquet large"] - peaks["parquet small"] < 120 and peaks["parquet large"] < 800,
              f"{peaks['parquet small']} MB, then {peaks['parquet large']} MB")
        peak("list control", "json", 300_000, env={"MUNITAS_ICEBERG_MAX_BYTES": str(2 * 1024 ** 3)})
        check("control: the same kind of rows as one JSON list take clearly more memory than a larger file of lines",
              peaks["list control"] > peaks["rows large"] + 150, f"{peaks['list control']} MB for a smaller file, against {peaks['rows large']} MB")
    finally:
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        finish_org(org, priya, ravi)
        drop_org(org)
    return summary("U108")


if __name__ == "__main__":
    sys.exit(main())
