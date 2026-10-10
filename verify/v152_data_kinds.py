"""U157: what a dataset holds (kind, format, structure) is worked out by the platform from the files, and says so on the list.

Three things are checked, each against something independent of the code that does the work:
  1. The detector, on the real first bytes of real formats (a wav header, an mp3 tag, a png, a parquet file, readable text, random bytes) and on
     the cases that decide wrongly if they are handled lazily: a video that has the same marker as an audio file, a text file cut in the middle
     of a character, a file named one thing and being another.
  2. The database's own lists of extensions against the detector's. They are written twice (the detector for new versions, the database function for
     versions sealed before the platform wrote kinds), so a check compares them for every extension.
  3. Real files in storage, sealed through the API: the kind, format and basis written against each file, what the list says about the dataset, and
     that each filter finds it and nothing else.

    docker compose exec -T munitas-api python /verify/v152_data_kinds.py
"""

from __future__ import annotations

import json
import sys
import uuid

from common import ADMIN, CANARY, CUSTODIAN, api, bearer_for, bucket_for, check, db, fixture_contract, heading, require_api, s3_client, summary

WAV = b"RIFF\x24\x08\x00\x00WAVEfmt " + b"\x00" * 32
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 32
FLAC = b"fLaC\x00\x00\x00\x22" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32
PARQUET = b"PAR1" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"\x00" * 8
M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00"
MP4_VIDEO = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00"
JSON = b'{"transcript": "hello", "turns": [1, 2, 3]}'
CSV = b"id,name,count\n1,a,2\n2,b,3\n"
RANDOM = bytes(range(256)) * 4


def run(uploaded: list[str]) -> int:
    require_api()
    sys.path.insert(0, "/app")
    from app import kinds

    heading("The detector, on real first bytes")
    cases = [
        ("a wav header", "call.wav", WAV, ("audio", "wav", "content")),
        ("an mp3 tag", "call.mp3", MP3, ("audio", "mp3", "content")),
        ("a flac header", "call.flac", FLAC, ("audio", "flac", "content")),
        ("an m4a brand", "call.m4a", M4A, ("audio", "m4a", "content")),
        ("a png", "scan.png", PNG, ("image", "png", "content")),
        ("a jpeg", "scan.jpg", JPEG, ("image", "jpeg", "content")),
        ("a parquet file", "rows.parquet", PARQUET, ("tabular", "parquet", "content")),
        ("a pdf", "form.pdf", PDF, ("other", "pdf", "content")),
        ("readable json", "turns.json", JSON, ("text", "json", "content")),
        ("a comma separated file is tabular, not text", "rows.csv", CSV, ("tabular", "csv", "content")),
        ("readable text with no known extension is text", "notes", b"just words\n", ("text", "txt", "content")),
    ]
    for label, name, head, want in cases:
        got = kinds.describe(name, head)
        check(f"{label}: {want[0]}, {want[1]}, by {want[2]}", got == want, str(got))

    check("an mp4 video has the same marker as an m4a and is not audio",
          kinds.describe("clip.mp4", MP4_VIDEO) == ("other", "mp4", "content"), str(kinds.describe("clip.mp4", MP4_VIDEO)))
    check("a wav that is named .txt is still audio: the bytes decide, not the name",
          kinds.describe("call.txt", WAV) == ("audio", "wav", "content"), str(kinds.describe("call.txt", WAV)))
    check("a text file cut in the middle of a character is still text",
          kinds.describe("a.txt", ("é" * 3).encode()[:-1])[0] == "text", str(kinds.describe("a.txt", ("é" * 3).encode()[:-1])))
    check("random bytes with a .json name are not called text: they are told by the name, and say so",
          kinds.describe("locked.json", RANDOM) == ("text", "json", "name"), str(kinds.describe("locked.json", RANDOM)))
    check("a file whose bytes could not be read is told by its name, and says so",
          kinds.describe("call.wav", None) == ("audio", "wav", "name"), str(kinds.describe("call.wav", None)))
    check("an unknown extension with unreadable bytes is other, with the extension as its format",
          kinds.describe("blob.xyz", None) == ("other", "xyz", "name"), str(kinds.describe("blob.xyz", None)))

    heading("Structure follows from kind and format")
    for kind, fmt, want in (("tabular", "csv", "structured"), ("tabular", "table", "structured"), ("text", "json", "semi-structured"),
                            ("text", "xml", "semi-structured"), ("text", "txt", "unstructured"), ("audio", "wav", "unstructured"),
                            ("image", "png", "unstructured"), ("other", "pdf", "unstructured")):
        check(f"{kind} {fmt} is {want}", kinds.structure_of(kind, fmt) == want, kinds.structure_of(kind, fmt))

    heading("The database's lists agree with the detector's")
    exts = sorted(set(kinds._BY_NAME) | {"xyz", "pdf", "zip"})
    disagree = []
    with db() as conn:
        for ext in exts:
            name = f"a/b/file.{ext}"
            k, f, _ = kinds.describe(name, None)
            row = conn.execute("select manifest_kind(%s::jsonb) as k, manifest_format(%s::jsonb) as f, format_structure(%s, %s) as s",
                               (json.dumps({"key": name}), json.dumps({"key": name}), k, f)).fetchone()
            if (row["k"], row["f"], row["s"]) != (k, f, kinds.structure_of(k, f)):
                disagree.append((ext, (k, f, kinds.structure_of(k, f)), tuple(row.values())))
    check(f"all {len(exts)} extensions give the same kind, format and structure in the detector and in the database", not disagree, str(disagree[:3]))
    with db() as conn:
        row = conn.execute("select manifest_kind(%s::jsonb) as k, manifest_format(%s::jsonb) as f",
                           (json.dumps({"key": "x.csv", "kind": "audio", "format": "wav"}),) * 2).fetchone()
    check("a kind written against a file wins over what its name says", (row["k"], row["f"]) == ("audio", "wav"), str(dict(row)))

    heading("Real files, sealed through the platform")
    # The long-standing test organisation, because a brand new one has no storage identity switched on yet and its files cannot be read: the platform would
    # then tell every file by its name and say so, which is a different case from the one checked here. Names carry this run's mark, so the list is
    # narrowed to them and nothing else in the organisation is looked at. Sealed versions cannot be removed; the tidy of test data clears them.
    mark = uuid.uuid4().hex[:8]
    tenant = CANARY
    schema = fixture_contract(tenant)
    bucket, client = bucket_for(tenant), s3_client(*ADMIN)

    def seal(name: str, files: dict[str, bytes]) -> str:
        made = api("POST", "/datasets", json={"tenant_id": tenant, "name": f"kinds-{mark}-{name}"})
        made.raise_for_status()
        for key, body in files.items():
            client.put_object(Bucket=bucket, Key=f"kinds-{mark}/{name}/{key}", Body=body)
            uploaded.append(f"kinds-{mark}/{name}/{key}")
        manifest = [{"key": f"kinds-{mark}/{name}/{k}", "bytes": len(b), "sha256": "0" * 64} for k, b in files.items()]
        r = api("POST", "/dataset-versions", json={"tenant_id": tenant, "dataset_id": made.json()["id"], "schema_id": schema,
                                                   "visibility_class": "RAW", "object_manifest": manifest, "record_count": len(manifest)})
        r.raise_for_status()
        return r.json()["id"]

    voice = seal("voice", {"a.wav": WAV, "b.mp3": MP3})
    seal("words", {"a.json": JSON})
    seal("both", {"a.wav": WAV, "a.json": JSON})
    seal("sheet", {"a.csv": CSV})
    seal("disguised", {"note.txt": WAV})
    api("POST", "/datasets", json={"tenant_id": tenant, "name": f"kinds-{mark}-empty"}).raise_for_status()

    with db() as conn:
        manifest = conn.execute("select object_manifest from dataset_version where id = %s", (voice,)).fetchone()["object_manifest"]
    check("each file of a sealed version has its kind, format and basis written against it, read from the file",
          sorted((e["kind"], e["format"], e["kind_basis"]) for e in manifest) == [("audio", "mp3", "content"), ("audio", "wav", "content")], str(manifest))

    def listing(**params) -> dict:
        r = api("GET", "/datasets", headers=bearer_for(CUSTODIAN), params={"tenant_id": tenant, "q": f"kinds-{mark}-", **params})
        r.raise_for_status()
        return {d["name"].split(f"kinds-{mark}-")[1]: d for d in r.json()["datasets"]}

    row = listing()
    check("the six datasets are listed", sorted(row) == ["both", "disguised", "empty", "sheet", "voice", "words"], str(sorted(row)))
    check("two audio files: audio, unimodal, wav and mp3, 2 files, unstructured",
          row["voice"]["kind"] == "audio" and row["voice"]["kinds"] == ["audio"] and row["voice"]["formats"] == ["mp3", "wav"]
          and row["voice"]["files"] == 2 and row["voice"]["structure"] == "unstructured", str({k: row["voice"].get(k) for k in ("kind", "formats", "files", "structure")}))
    check("json: text, semi-structured", row["words"]["kind"] == "text" and row["words"]["structure"] == "semi-structured", str(row["words"]["structure"]))
    check("audio and json together: multimodal, both kinds listed, structure mixed",
          row["both"]["kind"] == "multimodal" and row["both"]["kinds"] == ["audio", "text"] and row["both"]["structure"] == "mixed"
          and row["both"]["structures"] == ["semi-structured", "unstructured"], str({k: row["both"].get(k) for k in ("kind", "kinds", "structure", "structures")}))
    check("csv: tabular, structured", row["sheet"]["kind"] == "tabular" and row["sheet"]["structure"] == "structured", str(row["sheet"]["kind"]))
    check("a sound file named .txt is audio, found from its bytes", row["disguised"]["kind"] == "audio" and row["disguised"]["formats"] == ["wav"], str(row["disguised"]["formats"]))
    check("a dataset with nothing in it holds nothing, and no kind is made up for it",
          row["empty"]["kind"] is None and row["empty"]["kinds"] == [] and row["empty"]["structure"] is None and row["empty"]["files"] == 0,
          str({k: row["empty"].get(k) for k in ("kind", "kinds", "structure", "files")}))

    check("filtering by audio finds the three that hold audio", sorted(listing(kind="audio")) == ["both", "disguised", "voice"], str(sorted(listing(kind="audio"))))
    check("filtering by multimodal finds only the one with more than one kind", sorted(listing(kind="multimodal")) == ["both"], str(sorted(listing(kind="multimodal"))))
    check("filtering by tabular finds only the csv", sorted(listing(kind="tabular")) == ["sheet"], str(sorted(listing(kind="tabular"))))
    check("filtering by the format json finds the two that hold it", sorted(listing(format="json")) == ["both", "words"], str(sorted(listing(format="json"))))
    check("filtering by structure semi-structured finds the two that hold it", sorted(listing(structure="semi-structured")) == ["both", "words"], str(sorted(listing(structure="semi-structured"))))
    check("filtering by structure mixed finds only the one that holds two", sorted(listing(structure="mixed")) == ["both"], str(sorted(listing(structure="mixed"))))
    check("a kind and a format together narrow, not widen", sorted(listing(kind="audio", format="json")) == ["both"], str(sorted(listing(kind="audio", format="json"))))
    def order(**params) -> list[str]:
        return list(listing(**params))

    heading("Ordering the list")
    check("by name, first to last", order(sort="name", direction="asc") == ["both", "disguised", "empty", "sheet", "voice", "words"], str(order(sort="name", direction="asc")))
    check("by name, last to first", order(sort="name", direction="desc") == ["words", "voice", "sheet", "empty", "disguised", "both"], str(order(sort="name", direction="desc")))
    check("by when it was registered, newest first, which is what the list does when nothing is asked",
          order(sort="registered", direction="desc") == ["empty", "disguised", "sheet", "both", "words", "voice"] == order(), str(order(sort="registered", direction="desc")))
    check("by when it was registered, oldest first", order(sort="registered", direction="asc") == ["voice", "words", "both", "sheet", "disguised", "empty"], str(order(sort="registered", direction="asc")))
    check("by versions, the empty one comes first when ascending and last when descending",
          order(sort="versions", direction="asc")[0] == "empty" and order(sort="versions", direction="desc")[-1] == "empty", str(order(sort="versions", direction="desc")))
    check("by kind, datasets of one kind come by name, and one with nothing in it comes last",
          order(sort="kind", direction="asc") == ["disguised", "voice", "both", "sheet", "words", "empty"], str(order(sort="kind", direction="asc")))
    check("by kind, last to first, the one with nothing in it is still last",
          order(sort="kind", direction="desc") == ["words", "sheet", "both", "disguised", "voice", "empty"], str(order(sort="kind", direction="desc")))
    page = api("GET", "/datasets", headers=bearer_for(CUSTODIAN), params={"tenant_id": tenant, "q": f"kinds-{mark}-", "sort": "kind", "direction": "asc", "limit": 3, "offset": 0}).json()
    rest = api("GET", "/datasets", headers=bearer_for(CUSTODIAN), params={"tenant_id": tenant, "q": f"kinds-{mark}-", "sort": "kind", "direction": "asc", "limit": 3, "offset": 3}).json()
    paged = [d["name"] for d in page["datasets"] + rest["datasets"]]
    check("two pages of three, in the same order, hold all six once each", len(paged) == 6 and len(set(paged)) == 6 and paged == [d["name"] for d in api("GET", "/datasets", headers=bearer_for(CUSTODIAN), params={"tenant_id": tenant, "q": f"kinds-{mark}-", "sort": "kind", "direction": "asc"}).json()["datasets"]], str(paged))
    bad = api("GET", "/datasets", headers=bearer_for(CUSTODIAN), params={"tenant_id": tenant, "sort": "name; drop table dataset"})
    check("a way of ordering that is not on the list is refused, and nothing is run", bad.status_code == 422, str(bad.status_code))
    check("a direction other than asc or desc is refused", api("GET", "/datasets", headers=bearer_for(CUSTODIAN), params={"tenant_id": tenant, "direction": "sideways"}).status_code == 422)

    check("a kind nothing holds finds nothing", listing(kind="image") == {}, str(listing(kind="image")))
    return summary("U157")


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
