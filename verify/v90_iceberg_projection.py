"""U90: a sealed tabular version is also an Iceberg table, and the table agrees
with the register.

The claim is not "a table exists". It is that the table is the same data the
version holds, that the version still means what it meant, and that writing the
table cost the version nothing:

  * the table's rows, types, key and per-field sensitivity are the contract's
  * its snapshot names the version, the records object it came from (by hash),
    and the run that produced it, none of it supplied by a caller
  * every file written is in the version's manifest, with the right hash, so the
    content hash covers the table and storage agrees with the register
  * everything lives under the version's own prefix, so the grant that covers the
    version covers the table
  * a version that cannot honestly be a table is still sealed, without one

Per assertion, never in aggregate.

    docker compose exec -T munitas-api python /verify/v90_iceberg_projection.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import uuid

from common import (ADMIN, CANARY, ENGINEER, TABULAR_FIELDS, api, check, db,
                    fixture_contract, fixture_tabular_version, fixture_tenant, fixture_version,
                    heading, read_table, require_api, s3_client, summary)


def content_hash(manifest: list, count: int, prefix: str) -> str:
    canonical = json.dumps({"manifest": manifest, "count": count, "prefix": prefix},
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> int:
    require_api()
    tenant = fixture_tenant(CANARY)
    s3 = s3_client(*ADMIN)

    # An action run to stand as the producer, so provenance has something real
    # to read back. Built the way U2's lineage check builds one.
    contract = fixture_contract(tenant)
    action_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """insert into dataset_action
                 (id, tenant_id, name, source_schema_id, target_schema_id, output_class)
               values (%s, %s, %s, %s, %s, 'UNDER_REVIEW')""",
            (action_id, tenant, f"project-{action_id[:8]}", contract, contract),
        )
    run = api("POST", "/action-runs", json={
        "tenant_id": tenant, "action_id": action_id,
        "code_hash": "sha256:codehash-iceberg", "image_digest": "sha256:image-iceberg",
        "operator": "verify-suite", "idempotency_key": f"u90-{uuid.uuid4().hex}",
        "input_versions": [], "trigger_kind": "manual", "triggered_by": ENGINEER,
    })
    run.raise_for_status()
    run_id = run.json()["id"]

    version = fixture_tabular_version(tenant, produced_by_run=run_id)
    vid, prefix = version["id"], version["prefix"]
    rows = version["rows"]

    heading("U90: the version is sealed, and also has a table")
    with db() as conn:
        v = conn.execute("select * from dataset_version where id = %s", (vid,)).fetchone()
        ref = conn.execute("select * from iceberg_table_ref where dataset_version_id = %s",
                           (vid,)).fetchone()
    check("the version is sealed", bool(v and v["sealed"]), str(v and v["sealed"]))
    check("a table is recorded for it", ref is not None, "recorded" if ref else "no iceberg_table_ref row")
    if not ref:
        return summary("U90")
    check("the table is named for the dataset and the version",
          (ref["namespace"], ref["table_name"]) == (version["dataset_name"], "v1"),
          f"{ref['namespace']}.{ref['table_name']}")
    check("the version row carries the snapshot id, from the moment it was sealed",
          v["iceberg_snapshot_id"] == ref["snapshot_id"],
          f"{v['iceberg_snapshot_id']} vs {ref['snapshot_id']}")
    check("the format is Iceberg version 2", ref["format_version"] == 2, str(ref["format_version"]))

    heading("U90: every file lives under the version's own prefix, and the register lists it")
    listed = []
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=version["bucket"], Prefix=f"{prefix}/iceberg/"):
        listed += [o["Key"] for o in page.get("Contents", [])]
    check("the table has files", len(listed) >= 3, f"{len(listed)} objects")
    check("its location is inside the version's prefix",
          ref["location"] == f"s3://{version['bucket']}/{prefix}/iceberg", ref["location"])
    check("its metadata file is inside the version's prefix",
          ref["metadata_location"].startswith(f"s3://{version['bucket']}/{prefix}/iceberg/metadata/"),
          ref["metadata_location"])

    manifest = v["object_manifest"]
    in_manifest = {m["key"]: m for m in manifest}
    missing_from_manifest = [k for k in listed if k not in in_manifest]
    check("every table file is in the version's object manifest",
          not missing_from_manifest, f"{len(listed)} files, {len(missing_from_manifest)} missing")
    phantom = [k for k in in_manifest if "/iceberg/" in k and k not in set(listed)]
    check("the manifest lists no table file that is not in storage",
          not phantom, f"{len(phantom)} manifest entries name a missing object")
    bad = []
    for key in listed:
        body = s3.get_object(Bucket=version["bucket"], Key=key)["Body"].read()
        entry = in_manifest.get(key, {})
        if entry.get("sha256") != hashlib.sha256(body).hexdigest() or entry.get("bytes") != len(body):
            bad.append(key)
    check("each manifest entry's hash and size match the stored object", not bad,
          f"{len(bad)} differ, first: {bad[:1]}")
    recomputed = content_hash(manifest, v["record_count"], prefix)
    check("the content hash covers the table's files",
          v["content_hash"] == recomputed, v["content_hash"][:16])

    heading("U90: the table is the contract's shape and the version's rows")
    table = read_table(ref["metadata_location"])
    fields = table.schema().fields
    check("one column per contract field, in order",
          [f.name for f in fields] == [f["name"] for f in TABULAR_FIELDS],
          str([f.name for f in fields]))
    kinds = {"string": "string", "float": "double", "int": "long", "bool": "boolean",
             "list": "string", "dict": "string"}
    check("each column has the type its contract field names",
          all(str(f.field_type) == kinds[spec["type"]] for f, spec in zip(fields, TABULAR_FIELDS)),
          str([str(f.field_type) for f in fields]))
    check("the key field is required and no other is",
          [f.required for f in fields] == [spec["name"] == "record_id" for spec in TABULAR_FIELDS],
          str([(f.name, f.required) for f in fields]))
    check("each column carries its contract sensitivity",
          all((f.doc or "").startswith(f"sensitivity={spec['sensitivity']}")
              for f, spec in zip(fields, TABULAR_FIELDS)),
          str([f.doc for f in fields]))
    back = {r["record_id"]: r for r in table.scan().to_arrow().to_pylist()}
    check("the table holds exactly the version's records", set(back) == {r["record_id"] for r in rows},
          str(sorted(back)))
    scalars_ok = all(back[r["record_id"]][k] == r[k] for r in rows
                     for k in ("transcript", "score", "count", "ok"))
    check("plain values come back unchanged", scalars_ok, f"{len(rows)} rows compared")
    json_ok = all(json.loads(back[r["record_id"]]["tags"]) == r["tags"]
                  and json.loads(back[r["record_id"]]["detail"]) == r["detail"] for r in rows)
    check("lists and dictionaries come back as the same JSON", json_ok, f"{len(rows)} rows compared")

    heading("U90: the snapshot says what it came from, and Munitas wrote that")
    snapshot = table.current_snapshot()
    props = dict(snapshot.summary.additional_properties)
    check("the snapshot is the one the register recorded",
          snapshot.snapshot_id == ref["snapshot_id"], f"{snapshot.snapshot_id} vs {ref['snapshot_id']}")
    check("it names the version", props.get("munitas.dataset-version-id") == vid,
          props.get("munitas.dataset-version-id"))
    check("it names the records object by its hash, and the hash is right",
          props.get("munitas.records-sha256") == version["records_sha256"],
          props.get("munitas.records-sha256"))
    check("its record count is the register's",
          props.get("munitas.record-count") == str(v["record_count"]), props.get("munitas.record-count"))
    check("it names the producing run, read from the register",
          props.get("munitas.produced-by-run") == run_id, props.get("munitas.produced-by-run"))
    check("it carries the code hash and image digest that run recorded",
          (props.get("munitas.code-hash"), props.get("munitas.image-digest"))
          == ("sha256:codehash-iceberg", "sha256:image-iceberg"),
          str((props.get("munitas.code-hash"), props.get("munitas.image-digest"))))
    refs = {k: (r.snapshot_ref_type.value, r.snapshot_id) for k, r in table.metadata.refs.items()}
    check("a permanent tag names this snapshot",
          refs.get("v1") == ("tag", snapshot.snapshot_id), str(refs))
    check("the main branch is this snapshot too",
          refs.get("main") == ("branch", snapshot.snapshot_id), str(refs))

    heading("U90: the register's pointer is written once")
    with db() as conn:
        try:
            conn.execute("update iceberg_table_ref set snapshot_id = 0 where dataset_version_id = %s", (vid,))
            changed = True
        except Exception:
            changed = False
    check("an attempt to change it is refused", not changed,
          "refused" if not changed else "the update went through")

    heading("U90: a version that cannot be a table is still sealed, without one")
    plain = fixture_version(tenant, contract, "RAW")
    with db() as conn:
        none = conn.execute("select 1 from iceberg_table_ref where dataset_version_id = %s",
                            (plain["id"],)).fetchone()
        flagless = conn.execute("select iceberg_snapshot_id from dataset_version where id = %s",
                                (plain["id"],)).fetchone()
    check("a version made of files, with no records object, has no table", none is None,
          "no table" if none is None else "a table was recorded for a version with no rows")
    check("and its snapshot id is empty", flagless["iceberg_snapshot_id"] is None,
          str(flagless["iceberg_snapshot_id"]))

    broken = fixture_tabular_version(
        tenant, rows=[{"record_id": None, "transcript": "no key"}, {"record_id": "x", "transcript": "ok"}])
    with db() as conn:
        b = conn.execute("select sealed, iceberg_snapshot_id from dataset_version where id = %s",
                         (broken["id"],)).fetchone()
        bref = conn.execute("select 1 from iceberg_table_ref where dataset_version_id = %s",
                            (broken["id"],)).fetchone()
    check("a row with no key value still seals the version", bool(b and b["sealed"]), str(b))
    check("but no table is recorded for it", bref is None and b["iceberg_snapshot_id"] is None,
          "no table" if bref is None else "a table was written from rows that break their contract")

    missing = api("POST", "/dataset-versions", json={
        "tenant_id": tenant, "dataset_id": plain["dataset_id"], "schema_id": version["schema_id"],
        "visibility_class": "RAW", "object_manifest": [], "record_count": 1,
        "records_key": f"{prefix}/does-not-exist.json",
    })
    check("a records key that names no object still seals", missing.status_code == 201,
          f"HTTP {missing.status_code}")
    if missing.status_code == 201:
        with db() as conn:
            mref = conn.execute("select 1 from iceberg_table_ref where dataset_version_id = %s",
                                (missing.json()["id"],)).fetchone()
        check("and records no table", mref is None,
              "no table" if mref is None else "a table was recorded for a missing object")

    return summary("U90")


if __name__ == "__main__":
    sys.exit(main())
