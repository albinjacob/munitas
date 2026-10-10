"""U161: a version says what it holds: its kinds, formats, structure, files and size, or its rows when it is a table, and who may ask.

The same facts the datasets list shows for a dataset, for one version, worked out the same way. Proved on real files in storage and on versions sealed in every
way a version can be: files whose first bytes were read, a table, a table that could not be written, a version whose files could not be read, one that is
part readable, and one that is empty. The answers are compared with figures the check works out for itself (the sizes of the bytes it uploaded), and with the
datasets list. A file name is never part of the answer, and another organisation's version is not found.

    docker compose exec -T munitas-api python /verify/v155_version_holds.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import uuid

sys.path.insert(0, "/app")

from common import (ADMIN, CANARY, CUSTODIAN, api, bearer_for, bucket_for, check, db, fixture_contract, fixture_tabular_contract,
                    fixture_tabular_version, fixture_version, heading, require_api, s3_client, summary)
from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, finish_org, make_org

WAV = b"RIFF\x24\x08\x00\x00WAVEfmt " + b"\x00" * 32
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 32
JSON = b'{"transcript": "hello", "turns": [1, 2, 3]}'
CSV = b"id,name,count\n1,a,2\n2,b,3\n"


def run(uploaded: list[str]) -> int:
    require_api()
    mark = uuid.uuid4().hex[:8]
    schema = fixture_contract(CANARY)
    bucket, client = bucket_for(CANARY), s3_client(*ADMIN)
    as_custodian = bearer_for(CUSTODIAN)

    def seal(name: str, files: dict[str, bytes], *, unreadable: dict[str, int] | None = None) -> tuple[str, str]:
        """A dataset with one version. `files` are uploaded first so the platform can read them; `unreadable` names files that are listed but not in storage."""
        made = api("POST", "/datasets", json={"tenant_id": CANARY, "name": f"holds-{mark}-{name}"})
        made.raise_for_status()
        for key, body in files.items():
            client.put_object(Bucket=bucket, Key=f"holds-{mark}/{name}/{key}", Body=body)
            uploaded.append(f"holds-{mark}/{name}/{key}")
        manifest = [{"key": f"holds-{mark}/{name}/{k}", "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()} for k, b in files.items()]
        manifest += [{"key": f"holds-{mark}/{name}/{k}", "bytes": n, "sha256": "0" * 64} for k, n in (unreadable or {}).items()]
        r = api("POST", "/dataset-versions", json={"tenant_id": CANARY, "dataset_id": made.json()["id"], "schema_id": schema, "visibility_class": "RAW",
                                                   "object_manifest": manifest, "record_count": len(manifest)})
        r.raise_for_status()
        return r.json()["id"], made.json()["id"]

    def holds(version_id: str, headers=None) -> dict:
        r = api("GET", f"/dataset-versions/{version_id}/holds", headers=headers or as_custodian)
        r.raise_for_status()
        return r.json()

    heading("Files whose first bytes were read")
    voice, _ = seal("voice", {"a.wav": WAV, "b.mp3": MP3})
    both, _ = seal("both", {"a.wav": WAV, "a.json": JSON})
    sheet, _ = seal("sheet", {"a.csv": CSV})
    disguised, _ = seal("disguised", {"note.txt": WAV})
    h = holds(voice)
    check("two audio files: audio, wav and mp3, unstructured", h["kind"] == "audio" and h["kinds"] == ["audio"] and h["formats"] == ["mp3", "wav"]
          and h["structure"] == "unstructured", str({k: h[k] for k in ("kind", "formats", "structure")}))
    check("it counts the files and adds up their size from what was uploaded", h["files"] == 2 and h["bytes"] == len(WAV) + len(MP3), f"{h['files']} files, {h['bytes']} bytes")
    check("each format has its own row, with its own count and size",
          {(r["format"], r["files"], r["bytes"]) for r in h["by_format"]} == {("wav", 1, len(WAV)), ("mp3", 1, len(MP3))}, str(h["by_format"]))
    check("every file was read, and it says so", h["read_from"] == "content" and h["table"] is None, str((h["read_from"], h["table"])))
    h = holds(both)
    check("audio and json together: multimodal, both kinds, structure mixed with both named",
          h["kind"] == "multimodal" and h["kinds"] == ["audio", "text"] and h["structure"] == "mixed" and h["structures"] == ["semi-structured", "unstructured"],
          str({k: h[k] for k in ("kind", "kinds", "structure", "structures")}))
    h = holds(sheet)
    check("a csv file is tabular and structured", h["kind"] == "tabular" and h["formats"] == ["csv"] and h["structure"] == "structured", str(h["formats"]))
    h = holds(disguised)
    check("a sound file named .txt is audio and wav: the bytes decide", h["kind"] == "audio" and h["formats"] == ["wav"], str(h["formats"]))

    heading("Tables, and a table that could not be written")
    tabular = fixture_tabular_contract(CANARY)
    table = fixture_tabular_version(CANARY, dataset_name=f"holds-{mark}-table", schema_id=tabular)
    h = holds(table["id"])
    check("a version stored as a table is one table: tabular, structured, format table, its rows counted, no files",
          h["kind"] == "tabular" and h["formats"] == ["table"] and h["structure"] == "structured" and h["files"] == 0 and h["table"] == {"present": True, "rows": 3},
          str({k: h[k] for k in ("kind", "formats", "files", "table")}))
    check("it is not described by the files its rows came from", h["read_from"] == "table" and h["bytes"] == 0)
    made = api("POST", "/datasets", json={"tenant_id": CANARY, "name": f"holds-{mark}-broken"})
    made.raise_for_status()
    broken = api("POST", "/dataset-versions", json={"tenant_id": CANARY, "dataset_id": made.json()["id"], "schema_id": tabular, "visibility_class": "RAW",
                                                    "object_manifest": [], "record_count": 1, "records_key": f"{CANARY}/no/such/records.json", "table_required": False})
    broken.raise_for_status()
    h = holds(broken.json()["id"])
    check("a table that could not be written is still tabular, and says no table was written", h["kind"] == "tabular" and h["table"] == {"present": False, "rows": None}, str(h["table"]))

    heading("Files that could not be read, and an empty version")
    unreadable = fixture_version(CANARY, schema, "RAW", f"holds-{mark}-unreadable")
    h = holds(unreadable["id"])
    check("files that are not in storage are told by their names, and it says so",
          h["read_from"] == "name" and h["kind"] == "text" and h["formats"] == ["json"], str((h["read_from"], h["kind"], h["formats"])))
    part, _ = seal("part", {"a.wav": WAV}, unreadable={"missing.json": 99})
    h = holds(part)
    check("part read and part not: mixed, with both kinds", h["read_from"] == "mixed" and h["kinds"] == ["audio", "text"] and h["files"] == 2 and h["bytes"] == len(WAV) + 99,
          str((h["read_from"], h["kinds"], h["files"], h["bytes"])))
    empty, _ = seal("empty", {})
    h = holds(empty)
    check("a version with nothing in it holds nothing, and no kind is made up",
          h["kind"] is None and h["kinds"] == [] and h["structure"] is None and h["files"] == 0 and h["bytes"] == 0 and h["by_format"] == [] and h["read_from"] is None, str(h))

    # A version sealed before kinds were written against files has entries with a key and a size only. A sealed version cannot be edited, so one is inserted
    # as the old way would have left it, and the database's own lists of extensions decide.
    _, old_dataset = seal("old", {})
    old_id = str(uuid.uuid4())
    old_manifest = [{"key": f"holds-{mark}/old/x.wav", "bytes": 10}, {"key": f"holds-{mark}/old/y.json", "bytes": 20}, {"key": f"holds-{mark}/old/z.csv", "bytes": 30}]
    with db() as conn:
        conn.execute("insert into dataset_version (id, tenant_id, dataset_id, version, visibility_class, storage_prefix, object_manifest, schema_id, record_count, content_hash) "
                     "values (%s, %s, %s, 2, 'RAW', %s, %s::jsonb, %s, 3, %s)",
                     (old_id, CANARY, old_dataset, f"holds-{mark}/old/", json.dumps(old_manifest), schema, hashlib.sha256(old_id.encode()).hexdigest()))
    h = holds(old_id)
    check("a version sealed before files were read is described from its file names by the database's lists, and says so",
          h["read_from"] == "name" and h["kinds"] == ["audio", "tabular", "text"] and h["kind"] == "multimodal" and h["formats"] == ["csv", "json", "wav"]
          and h["structures"] == ["semi-structured", "structured", "unstructured"] and h["structure"] == "mixed" and h["files"] == 3 and h["bytes"] == 60,
          str({k: h[k] for k in ("read_from", "kinds", "formats", "structures", "files", "bytes")}))

    heading("It agrees with the datasets list, and tells nothing it should not")
    listing = {d["name"]: d for d in api("GET", "/datasets", headers=as_custodian, params={"tenant_id": CANARY, "q": f"holds-{mark}-", "limit": 50}).json()["datasets"]}
    for name, version in (("voice", voice), ("both", both), ("sheet", sheet), ("disguised", disguised)):
        row, h = listing[f"holds-{mark}-{name}"], holds(version)
        check(f"{name}: kind, formats, structure, files and size are the same on the page and in the list",
              (row["kind"], row["formats"], row["structure"], row["files"], int(row["bytes"])) == (h["kind"], h["formats"], h["structure"], h["files"], h["bytes"]),
              f"{(row['kind'], row['formats'], row['structure'], row['files'], row['bytes'])} vs {(h['kind'], h['formats'], h['structure'], h['files'], h['bytes'])}")
    raw = api("GET", f"/dataset-versions/{voice}/holds", headers=as_custodian).text
    check("no file name is in the answer", "a.wav" not in raw and "b.mp3" not in raw and f"holds-{mark}" not in raw)
    detail = api("GET", f"/dataset-versions/{voice}", headers=as_custodian, params={"tenant_id": CANARY}).json()
    check("the version itself says when it was sealed, which the page shows", bool(detail.get("created_at")), str(detail.get("created_at")))

    heading("Who may ask")
    org = make_org()
    try:
        check("another organisation's member is told there is no such version", api("GET", f"/dataset-versions/{voice}/holds", headers=org.bearer("member")).status_code == 404)
        check("a request with no session is refused", api("GET", f"/dataset-versions/{voice}/holds").status_code == 401)
        check("a version that does not exist is not found", api("GET", f"/dataset-versions/{uuid.uuid4()}/holds", headers=as_custodian).status_code == 404)
    finally:
        finish_org(org, bearer_for(ADMIN_A), bearer_for(ADMIN_B))
        drop_org(org)

    heading("When a role was approved")
    held = api("GET", "/people/roles", headers=as_custodian)
    if held.status_code == 200 and held.json().get("held"):
        check("every role held says when it was approved, as a time the screen can show", all(g.get("created_at") for g in held.json()["held"]), str(held.json()["held"][0].keys()))
    else:
        check("the roles held can be read", held.status_code == 200, str(held.status_code))
    return summary("U161")


def main() -> int:
    """The files this check uploads belong to no version's own folder, so the storage audit would report them: they are removed however the run ends."""
    uploaded: list[str] = []
    try:
        return run(uploaded)
    finally:
        client, bucket = s3_client(*ADMIN), bucket_for(CANARY)
        for key in uploaded:
            client.delete_object(Bucket=bucket, Key=key)


if __name__ == "__main__":
    sys.exit(main())
