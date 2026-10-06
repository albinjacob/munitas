"""Locking a whole file under a person's key, in chunks, so a file of any size is locked and opened without being held in memory.

A recording is far too large to lock in one piece and is uploaded in parts that can each be sent again, so it is locked a chunk at a time. Each
chunk is its own AES-GCM message. The organisation, the person, the file's name, the file's total size, the chunk's number and whether it is the
last chunk are bound in as additional authenticated data, which has three consequences that the checks in verify/v135 prove one by one:

  * a chunk cannot be moved to another place in the file, to another file or to another person and still open;
  * a file cut short cannot be opened as a whole, because the last chunk is the only one that says it is last;
  * a part can be locked on its own, from its number alone, so a part that is sent again is simply locked again.

The size of every chunk and every part follows from the file's total size, which the uploader states before sending anything and which is
bound into every chunk. Nothing about the layout is stored in the file itself.
"""

from __future__ import annotations

import os
from typing import BinaryIO, Callable, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .envelope import NONCE_BYTES

FORMAT = "stream:v1"
CHUNK = 1024 * 1024
CHUNKS_PER_PART = 8
PART = CHUNK * CHUNKS_PER_PART
TAG_BYTES = 16
OVERHEAD = NONCE_BYTES + TAG_BYTES


class NotOpenable(Exception):
    """The stored bytes are not what was locked: cut short, added to, reordered, or locked for another file or person."""


def chunk_count(total: int) -> int:
    return -(-total // CHUNK)


def part_count(total: int) -> int:
    return -(-total // PART)


def part_plain_size(total: int, part_number: int) -> int:
    """How many bytes of the file part `part_number` (counted from 1) holds. Every part but the last is full."""
    if not 1 <= part_number <= part_count(total):
        raise ValueError(f"part {part_number} is outside this file's {part_count(total)} parts")
    return min(PART, total - (part_number - 1) * PART)


def part_stored_size(total: int, part_number: int) -> int:
    plain = part_plain_size(total, part_number)
    return plain + OVERHEAD * chunk_count(plain)


def stored_size(total: int) -> int:
    return total + OVERHEAD * chunk_count(total)


def _aad(tenant_id: str, person_code: str, name: str, total: int, index: int, final: bool) -> bytes:
    return f"munitas|file|{FORMAT}|{tenant_id}|{person_code}|{name}|{total}|{index}|{int(final)}".encode("utf-8")


def lock_part(key: bytes, tenant_id: str, person_code: str, name: str, total: int, part_number: int, data: bytes) -> bytes:
    """One part of the file, locked. `data` must be exactly the bytes that part holds, so the last chunk is known to be last."""
    expected = part_plain_size(total, part_number)
    if len(data) != expected:
        raise ValueError(f"part {part_number} holds {expected} bytes, not {len(data)}")
    first = (part_number - 1) * CHUNKS_PER_PART
    last_chunk = chunk_count(total) - 1
    cipher, out = AESGCM(key), []
    for i in range(chunk_count(expected)):
        piece, index = data[i * CHUNK:(i + 1) * CHUNK], first + i
        nonce = os.urandom(NONCE_BYTES)
        out.append(nonce + cipher.encrypt(nonce, piece, _aad(tenant_id, person_code, name, total, index, index == last_chunk)))
    return b"".join(out)


def _read_exact(source: BinaryIO, n: int) -> bytes:
    got = b""
    while len(got) < n:
        piece = source.read(n - len(got))
        if not piece:
            break
        got += piece
    return got


def stored_chunk_size(total: int, index: int) -> int:
    """How many bytes chunk `index` takes in the locked file: its plain size and the overhead. Every chunk but the last is full."""
    last_chunk = chunk_count(total) - 1
    return (CHUNK if index < last_chunk else total - last_chunk * CHUNK) + OVERHEAD


def read_range(key: bytes, tenant_id: str, person_code: str, name: str, total: int, fetch: Callable[[int, int], bytes], start: int, length: int) -> bytes:
    """`length` bytes of the file from byte `start`, opened from its locked form, touching only the chunks that hold them.

    `fetch(offset, size)` returns that many stored bytes from the locked file. Each chunk is its own message, bound to its place in the file, so a
    range opens exactly as the same bytes would in a whole read, and a chunk that was changed, moved or cut off does not open. A range that runs
    past the end of the file is cut at the end.
    """
    if start < 0 or length < 0:
        raise ValueError("a range starts at 0 or later and has no negative length")
    end = min(total, start + length)
    if start >= end:
        return b""
    first, last = start // CHUNK, (end - 1) // CHUNK
    last_chunk = chunk_count(total) - 1
    offset = first * (CHUNK + OVERHEAD)
    wanted = sum(stored_chunk_size(total, i) for i in range(first, last + 1))
    blob = fetch(offset, wanted)
    if len(blob) != wanted:
        raise NotOpenable(f"the stored file ends before chunk {last + 1} of {last_chunk + 1}")
    cipher, out, at = AESGCM(key), [], 0
    for index in range(first, last + 1):
        size = stored_chunk_size(total, index)
        piece = blob[at:at + size]
        at += size
        try:
            out.append(cipher.decrypt(piece[:NONCE_BYTES], piece[NONCE_BYTES:], _aad(tenant_id, person_code, name, total, index, index == last_chunk)))
        except InvalidTag as exc:
            raise NotOpenable(f"chunk {index + 1} of {last_chunk + 1} does not open for this person and this file") from exc
    joined = b"".join(out)
    return joined[start - first * CHUNK:start - first * CHUNK + (end - start)]


def open_stream(key: bytes, tenant_id: str, person_code: str, name: str, total: int, source: BinaryIO) -> Iterator[bytes]:
    """The file's bytes, a chunk at a time, from its locked form. Raises NotOpenable at the first chunk that does not open, or at the end if
    the stored bytes are shorter or longer than they should be."""
    cipher, last_chunk = AESGCM(key), chunk_count(total) - 1
    for index in range(last_chunk + 1):
        plain = CHUNK if index < last_chunk else total - last_chunk * CHUNK
        blob = _read_exact(source, plain + OVERHEAD)
        if len(blob) != plain + OVERHEAD:
            raise NotOpenable(f"the stored file ends inside chunk {index + 1} of {last_chunk + 1}")
        try:
            yield cipher.decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], _aad(tenant_id, person_code, name, total, index, index == last_chunk))
        except InvalidTag as exc:
            raise NotOpenable(f"chunk {index + 1} of {last_chunk + 1} does not open for this person and this file") from exc
    if source.read(1):
        raise NotOpenable("the stored file has more bytes after its last chunk")
