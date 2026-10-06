"""U135: a whole file is locked under a person's key in chunks, and a chunk that is moved, repeated, cut off or added to is refused.

A recording is far too large to lock in one piece, and it is uploaded in parts that can each be sent again. So each part is locked on its own, a
chunk at a time, from its number and the file's stated size alone. This checks the module that does that, one item at a time, with no database and
no storage (the bytes are made up here):

  * the layout follows from the size alone: parts, chunks and stored sizes agree with what is actually produced, for sizes either side of every
    boundary, and a file of one byte or exactly one part works;
  * a file locked part by part, in any order, opens to the same bytes, and a part locked twice (a retry) opens as well as the first;
  * a part of the wrong length is refused before anything is locked;
  * chunks moved to another position, swapped, left out, repeated, or cut off at the end, and bytes added at the end, are each refused;
  * a file does not open for another person, another organisation, another name or another stated size;
  * a flipped bit anywhere is refused, and nothing opens with the wrong key;
  * opening reads one chunk at a time: a file of several parts is opened while holding no more than one chunk, which is measured.

    docker compose exec -T munitas-api python /verify/v135_locked_file_stream.py
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform"))

from common import check, heading, summary  # noqa: E402
from crypto import stream  # noqa: E402
from crypto.stream import CHUNK, OVERHEAD, PART, NotOpenable  # noqa: E402

KEY, TENANT, PERSON, NAME = os.urandom(32), "health", "T-0123456789abcdef", "visit-0007.wav"


def lock_all(data: bytes, order=None, **who) -> list[bytes]:
    total = len(data)
    who = {"key": KEY, "tenant": TENANT, "person": PERSON, "name": NAME, **who}
    parts = {}
    for n in order or range(1, stream.part_count(total) + 1):
        a = (n - 1) * PART
        parts[n] = stream.lock_part(who["key"], who["tenant"], who["person"], who["name"], total, n, data[a:a + stream.part_plain_size(total, n)])
    return [parts[n] for n in sorted(parts)]


def open_all(stored: bytes, total: int, **who) -> bytes:
    who = {"key": KEY, "tenant": TENANT, "person": PERSON, "name": NAME, **who}
    return b"".join(stream.open_stream(who["key"], who["tenant"], who["person"], who["name"], total, io.BytesIO(stored)))


def refused(fn) -> bool:
    try:
        fn()
    except NotOpenable:
        return True
    return False


def chunks_of(stored: bytes, total: int) -> list[bytes]:
    out, at = [], 0
    for i in range(stream.chunk_count(total)):
        size = (CHUNK if i < stream.chunk_count(total) - 1 else total - i * CHUNK) + OVERHEAD
        out.append(stored[at:at + size])
        at += size
    return out


def main() -> int:
    heading("The layout follows from the size")
    for total in (1, 2, CHUNK - 1, CHUNK, CHUNK + 1, PART - 1, PART, PART + 1, 2 * PART, 2 * PART + 5):
        data = os.urandom(total)
        parts = lock_all(data)
        sizes_ok = [len(p) for p in parts] == [stream.part_stored_size(total, n) for n in range(1, len(parts) + 1)]
        check(f"{total} bytes: {len(parts)} part(s), every size as the layout says, total {stream.stored_size(total)}",
              len(parts) == stream.part_count(total) and sizes_ok and sum(map(len, parts)) == stream.stored_size(total))
        check(f"{total} bytes: opens to the same bytes", open_all(b"".join(parts), total) == data)

    heading("Parts locked in any order, and sent again")
    data = os.urandom(3 * PART + 12345)
    total = len(data)
    shuffled = lock_all(data, order=[4, 2, 1, 3])
    check("parts locked out of order, joined in order, open to the same bytes", open_all(b"".join(shuffled), total) == data)
    again = stream.lock_part(KEY, TENANT, PERSON, NAME, total, 2, data[PART:2 * PART])
    check("a part locked a second time is different bytes (fresh nonces) and still opens in the first one's place",
          again != shuffled[1] and open_all(b"".join([shuffled[0], again, shuffled[2], shuffled[3]]), total) == data)
    check("a part of the wrong length is refused before anything is locked",
          all(_raises_value_error(lambda n=n, b=b: stream.lock_part(KEY, TENANT, PERSON, NAME, total, n, b))
              for n, b in ((1, b"x" * (PART - 1)), (4, data[3 * PART:] + b"x"), (2, b""))))
    check("a part number outside the file is refused",
          all(_raises_value_error(lambda n=n: stream.lock_part(KEY, TENANT, PERSON, NAME, total, n, b"x")) for n in (0, 5, -1)))

    heading("A file that was tampered with")
    small = os.urandom(3 * CHUNK + 17)
    st = len(small)
    stored = b"".join(lock_all(small))
    cs = chunks_of(stored, st)
    check("the file as locked opens", open_all(stored, st) == small)
    check("two chunks swapped is refused", refused(lambda: open_all(cs[1] + cs[0] + cs[2] + cs[3], st)))
    check("a chunk left out is refused", refused(lambda: open_all(cs[0] + cs[2] + cs[3], st)))
    check("a chunk repeated in place of another is refused", refused(lambda: open_all(cs[0] + cs[1] + cs[1] + cs[3], st)))
    check("the last chunk cut off is refused", refused(lambda: open_all(b"".join(cs[:3]), st)))
    check("the last chunk cut short is refused", refused(lambda: open_all(stored[:-5], st)))
    check("every chunk but the last, cut after a whole chunk, is refused", refused(lambda: open_all(cs[0], st)))
    check("bytes added at the end are refused", refused(lambda: open_all(stored + b"x", st)))
    check("a whole extra chunk at the end is refused", refused(lambda: open_all(stored + cs[1], st)))
    check("an empty stored file is refused", refused(lambda: open_all(b"", st)))
    flipped = []
    for at in (0, 11, 12, CHUNK, len(stored) // 2, len(stored) - 1):
        bad = bytearray(stored)
        bad[at] ^= 1
        flipped.append(refused(lambda b=bytes(bad): open_all(b, st)))
    check("a bit flipped in a nonce, in the body, in the middle, in the final tag: each refused", all(flipped), str(flipped))

    heading("A file for someone or something else")
    check("another person's code does not open it", refused(lambda: open_all(stored, st, person="T-ffffffffffffffff")))
    check("another organisation does not open it", refused(lambda: open_all(stored, st, tenant="finance")))
    check("another file name does not open it (a recording cannot be given another recording's name)", refused(lambda: open_all(stored, st, name="visit-0008.wav")))
    check("another stated size does not open it", refused(lambda: open_all(stored, st + 1)) and refused(lambda: open_all(stored, st - 1)))
    check("another key does not open it", refused(lambda: open_all(stored, st, key=os.urandom(32))))

    heading("Opening holds one chunk at a time")
    big = os.urandom(2 * PART + CHUNK + 3)
    bigstored = b"".join(lock_all(big))
    seen, largest = [], [0]

    class Counting(io.BytesIO):
        def read(self, n=-1):
            piece = super().read(n)
            largest[0] = max(largest[0], len(piece))
            return piece

    out = 0
    for piece in stream.open_stream(KEY, TENANT, PERSON, NAME, len(big), Counting(bigstored)):
        seen.append(len(piece))
        out += len(piece)
    check("every read asked storage for at most one chunk and its overhead", largest[0] <= CHUNK + OVERHEAD, f"the largest read was {largest[0]} bytes")
    check("and no piece handed on was larger than one chunk", max(seen) <= CHUNK and out == len(big), f"{len(seen)} pieces, {out} bytes")
    return summary("v135")


def _raises_value_error(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
