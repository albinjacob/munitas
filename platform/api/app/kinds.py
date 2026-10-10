"""What a file holds, worked out by the platform from the file, not declared by whoever uploaded it.

Three things are known about every file, and each is written into the version's own list of files when the version is sealed, which nothing can change
afterwards, so they are facts that travel with the version:

  kind       audio, image, text, tabular or other: what the content is.
  format     wav, mp3, json, csv, parquet and so on: how it is written. A version the platform stored as a table has the format "table".
  structure  structured (rows and columns), semi-structured (json, xml: fields without a fixed table) or unstructured (sound, pictures, prose).
             It follows from the kind and the format, and is worked out where it is shown, never stored apart from them.

How a file is told apart:
  * Its first bytes are read. Sound, image and column-store formats have recognisable first bytes, and those decide the kind and the format whatever the
    file is called.
  * A file whose bytes are readable text is text, and its format is its file extension; a comma or tab separated file is tabular, because text alone cannot
    say that rows and columns are what it holds.
  * A file whose bytes cannot be read, such as one locked to a person's key, is told by its name alone. The basis then says "name" rather than "content",
    so nobody mistakes the one for the other.

A version stored as a table is tabular, in the format "table", because the platform made the table: that is not detected, it is known.

A dataset's kind is the one kind all its files share, or "composite" when it has more than one. Its formats are all the formats it holds.
"""

from __future__ import annotations

import os

KINDS = ("audio", "image", "text", "tabular", "other")
STRUCTURES = ("structured", "semi-structured", "unstructured")

HEAD = 4096          # how many bytes of a file are read to tell what it is

_AUDIO = ("wav", "mp3", "flac", "ogg", "oga", "opus", "m4a", "aac")
_IMAGE = ("png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff")
_TABULAR = ("csv", "tsv", "parquet", "feather", "arrow", "xlsx")
_TEXT = ("txt", "md", "json", "jsonl", "ndjson", "xml", "html", "yaml", "yml", "log")
_SEMI = ("json", "jsonl", "ndjson", "xml", "html", "yaml", "yml")
_TABLE_SHAPED_TEXT = ("csv", "tsv")

_BY_NAME = {**dict.fromkeys(_AUDIO, "audio"), **dict.fromkeys(_IMAGE, "image"), **dict.fromkeys(_TABULAR, "tabular"), **dict.fromkeys(_TEXT, "text")}


def extension(name: str) -> str:
    return os.path.splitext(name.lower())[1].lstrip(".")


def structure_of(kind: str, fmt: str) -> str:
    """Structured, semi-structured or unstructured, from what a file is and how it is written."""
    if kind == "tabular" or fmt == "table":
        return "structured"
    return "semi-structured" if fmt in _SEMI else "unstructured"


def _from_bytes(head: bytes, name: str) -> tuple[str, str] | None:
    """(kind, format) that the first bytes of a file prove, or None when they prove nothing."""
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio", "wav"
    if head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "audio", "mp3"
    if head[:4] == b"fLaC":
        return "audio", "flac"
    if head[:4] == b"OggS":
        return "audio", "ogg"
    if head[4:8] == b"ftyp":
        return ("audio", "m4a") if head[8:12] in (b"M4A ", b"M4B ") else ("other", "mp4")       # any other brand is video
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image", "png"
    if head[:3] == b"\xff\xd8\xff":
        return "image", "jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "webp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image", "tiff"
    if head[:2] == b"BM" and extension(name) == "bmp":
        return "image", "bmp"
    if head[:4] == b"PAR1":
        return "tabular", "parquet"
    if head[:6] == b"ARROW1":
        return "tabular", "arrow"
    if head[:4] == b"%PDF":
        return "other", "pdf"
    if head[:4] == b"PK\x03\x04":
        # A spreadsheet is a zip file that says what it is in its name, which is all there is to go on.
        return ("tabular", "xlsx") if extension(name) == "xlsx" else ("other", "zip")
    if head[:2] == b"\x1f\x8b":
        return "other", "gzip"
    return None


def _is_text(head: bytes) -> bool:
    if not head or b"\x00" in head:
        return False
    for cut in range(0, 4):                  # the first bytes may end half way through a character
        try:
            head[:len(head) - cut].decode("utf-8")
            return True
        except UnicodeDecodeError:
            continue
    return False


def describe(name: str, head: bytes | None) -> tuple[str, str, str]:
    """(kind, format, basis) of one file: basis is "content" when its bytes decided and "name" when only its name could."""
    if head:
        proven = _from_bytes(head, name)
        if proven:
            return proven[0], proven[1], "content"
        if _is_text(head):
            ext = extension(name)
            return ("tabular" if ext in _TABLE_SHAPED_TEXT else "text"), (ext if ext in _TEXT + _TABLE_SHAPED_TEXT else "txt"), "content"
    ext = extension(name)
    return _BY_NAME.get(ext, "other"), (ext or "unknown"), "name"


def tag(manifest: list[dict], read_head, skip: set[str] = frozenset()) -> None:
    """Write each file's kind, format and basis into its entry in the manifest, in place.

    `read_head(key)` returns the first bytes of the file, or None when they cannot be read. Files in `skip` are not files the person gave: they are the
    table the platform wrote, which is tabular in the format "table" by being the table and is not counted again as files.
    """
    for entry in manifest:
        if entry.get("key") in skip:
            continue
        try:
            head = read_head(entry["key"])
        except Exception:  # noqa: BLE001 - what cannot be read is told by its name, and says so
            head = None
        entry["kind"], entry["format"], entry["kind_basis"] = describe(entry["key"], head)
