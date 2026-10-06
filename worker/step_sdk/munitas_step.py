"""munitas_step: how a custom pipeline step reads its recordings and returns its result.

A script step that says `recordings: true` in its pipeline file runs in a container with no internet, no storage and no keys. The platform hands it a
short-lived pass for this one step and a way to reach the platform's step routes, and nothing else. This library is the way to use them:

    import munitas_step as step

    for item in step.list_recordings():              # [{"record_id": "visit-7", "bytes": 4349894, "erased": False}, ...]
        if item["erased"]:
            continue                                 # the patient was erased: it cannot be opened, and is left out
        audio = step.open_recording(item["record_id"])   # a file you can read, seek in and pass to audio libraries
        ...
    step.result({"recommendation": "not_measured", "reason": "..."})   # the step's small JSON result, printed as its last line

A step whose pipeline file also says `writes_files: true` can write files through the platform:

    with step.write_output("visit-7-silenced.wav") as out:   # a file to write to, sent to the platform in parts of 8 MiB as it is written
        out.write(data)
    step.save_output("notes.txt", b"...")                    # or a whole file from bytes
    step.copy_to_output("clip.wav", "/tmp/clip.wav")         # or a file from this container's own scratch space

Nothing written is a version until the step ends. If it succeeds, the platform seals everything it finished as one version of the dataset
`<the run's dataset>-<the step's name>`, "under review", so nobody can use it until a person has looked. If it fails, or leaves a file unfinished (a
`with` block that raised), everything it wrote is thrown away. A step writes at most 500 files, 512 MiB each and 1 GiB together.

Three ways to read one recording, whichever the script needs:

    f = step.open_recording(id)           # a seekable file: asks the platform for 8 MiB blocks as they are needed, holds two, never the whole file
    data = step.read_recording(id)        # all of it, as bytes in memory
    step.save_recording(id, "/tmp/x.wav") # to a path inside this container (/tmp is memory-backed and size-limited, and goes with the container)

The platform decides every time, not only the first: whether the pass is real, whether the run is still going, whether the version of the pipeline was
approved to be given the recordings, and whether the patient was erased. Erasing a patient while their recording is being read makes the next block
raise RecordingErased. Opening a recording is written to the platform's key log, naming this step.

Standard library only. It is the one implementation of the reader: the platform's own workers import the same class.
"""

from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from typing import Callable

BLOCK = 8 * 1024 * 1024
RETRY_WAITS = (0.5, 1.5)


class StepError(Exception):
    """The platform refused, or could not be reached."""


class RecordingErased(StepError):
    """The patient this recording is about was erased, so it cannot be opened."""


class RangeFile(io.RawIOBase):
    """A recording, read as if it were an ordinary file, without ever being a file.

    It can be read and jumped around in, so audio libraries take it where they would take a path. Each read that is not already held becomes a request
    for one block of the recording (8 MiB), and the last two blocks are kept for the reads that follow. Only what is being read is held in memory,
    however long the recording is. `fetch(offset, length)` returns exactly that many bytes of the opened recording.
    """

    def __init__(self, size: int, fetch: Callable[[int, int], bytes], *, block: int = BLOCK, on_close: Callable[[], None] | None = None) -> None:
        super().__init__()
        self._size, self._fetch, self._block, self._on_close = size, fetch, block, on_close
        self._pos = 0
        self._held: OrderedDict[int, bytes] = OrderedDict()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        target = {io.SEEK_SET: offset, io.SEEK_CUR: self._pos + offset, io.SEEK_END: self._size + offset}[whence]
        if target < 0:
            raise ValueError("a recording has no bytes before its start")
        self._pos = target
        return self._pos

    def _block_at(self, index: int) -> bytes:
        if index in self._held:
            self._held.move_to_end(index)
            return self._held[index]
        start = index * self._block
        want = min(self._block, self._size - start)
        data = self._fetch(start, want)
        if len(data) != want:
            raise StepError(f"the platform returned {len(data)} bytes of the {want} asked for at {start}")
        self._held[index] = data
        while len(self._held) > 2:
            self._held.popitem(last=False)
        return data

    def readinto(self, buffer) -> int:
        wanted = min(len(buffer), max(0, self._size - self._pos))
        done = 0
        while done < wanted:
            index = self._pos // self._block
            data = self._block_at(index)
            inside = self._pos - index * self._block
            take = min(wanted - done, len(data) - inside)
            buffer[done:done + take] = data[inside:inside + take]
            done += take
            self._pos += take
        return done

    def close(self) -> None:
        if not self.closed:
            self._held.clear()
            if self._on_close:
                self._on_close()
        super().close()


def _settings() -> tuple[str, str]:
    try:
        return os.environ["MUNITAS_STEP_API"].rstrip("/"), os.environ["MUNITAS_STEP_PASS"]
    except KeyError as exc:
        raise StepError(f"{exc.args[0]} is not set: this step was not started with a pass, which a step gets only when its pipeline file says "
                        "`recordings: true`") from None


def _call(method: str, path: str, headers: dict | None = None, data: bytes | None = None) -> tuple[int, dict, bytes]:
    """One request to the platform's step routes, sent again a couple of times when the connection fails or the platform is briefly unavailable.
    Every request a step makes can safely be sent again: sending a part again replaces it, and opening a file again starts it again."""
    api, token = _settings()
    last: Exception | None = None
    for wait in (*RETRY_WAITS, None):
        request = urllib.request.Request(f"{api}{path}", method=method, data=data, headers={"Authorization": f"Bearer {token}", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if exc.code < 500:
                return exc.code, dict(exc.headers), body
            last = StepError(f"{path} returned {exc.code}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        if wait is not None:
            time.sleep(wait)
    raise StepError(f"{path} could not be reached: {last}")


def _refusal(status: int, body: bytes, record_id: str, what: str) -> Exception:
    if status == 410:
        return RecordingErased(record_id)
    return StepError(f"{what} {record_id} returned {status}: {body[:300].decode('utf-8', 'replace')}")


def list_recordings() -> list[dict]:
    """The recordings this step may be given: each one's `record_id`, its `bytes` once opened, and whether its patient was `erased`."""
    status, _, body = _call("GET", "/step/v1/recordings")
    if status != 200:
        raise StepError(f"listing the recordings returned {status}: {body[:300].decode('utf-8', 'replace')}")
    return json.loads(body)["recordings"]


def open_recording(record_id: str) -> RangeFile:
    """A recording as a file to read. Opening it is written to the platform's key log. Raises RecordingErased when its patient was erased."""
    status, _, body = _call("POST", f"/step/v1/recordings/{record_id}/open")
    if status != 200:
        raise _refusal(status, body, record_id, "opening")
    opened = json.loads(body)

    def fetch(offset: int, length: int) -> bytes:
        status, _, data = _call("GET", f"/step/v1/recordings/{record_id}/range?offset={offset}&length={length}", {"x-handle": opened["handle"]})
        if status != 200:
            raise _refusal(status, data, record_id, "reading")
        return data

    return RangeFile(int(opened["bytes"]), fetch)


def read_recording(record_id: str) -> bytes:
    """The whole recording, as bytes in memory."""
    with open_recording(record_id) as source:
        return source.read()


def save_recording(record_id: str, path: str) -> int:
    """The whole recording, written to `path` in pieces. Returns its size. Use a path under /tmp: it is the only place a step may write."""
    total = 0
    with open_recording(record_id) as source, open(path, "wb") as out:
        while True:
            piece = source.read(BLOCK)
            if not piece:
                return total
            out.write(piece)
            total += len(piece)


class OutputFile(io.RawIOBase):
    """A file being written through the platform. Writing sends it in parts of 8 MiB, so a file of any size is written while holding at most one
    part. Closing it finishes the file. Leaving a `with` block because of an error does not: the file is left unfinished, and the platform throws it
    away when the step ends."""

    def __init__(self, name: str) -> None:
        super().__init__()
        status, _, body = _call("POST", f"/step/v1/outputs/{name}")
        if status != 201:
            raise StepError(f"opening output {name} returned {status}: {body[:300].decode('utf-8', 'replace')}")
        self._name, self._part_bytes = name, int(json.loads(body)["part_bytes"])
        self._buffer, self._next, self._finished = bytearray(), 1, False

    def writable(self) -> bool:
        return True

    def _send(self, data: bytes) -> None:
        status, _, body = _call("PUT", f"/step/v1/outputs/{self._name}/parts/{self._next}", {"Content-Type": "application/octet-stream"}, data)
        if status != 200:
            raise StepError(f"sending part {self._next} of {self._name} returned {status}: {body[:300].decode('utf-8', 'replace')}")
        self._next += 1

    def write(self, data) -> int:
        if self._finished:
            raise StepError(f"{self._name} was already finished")
        self._buffer += data
        while len(self._buffer) >= self._part_bytes:
            self._send(bytes(self._buffer[:self._part_bytes]))
            del self._buffer[:self._part_bytes]
        return len(data)

    def finish(self) -> dict:
        """Send what is left as the last part (an empty file sends one empty part) and finish the file. Returns its size and fingerprint."""
        if self._finished:
            raise StepError(f"{self._name} was already finished")
        if self._buffer or self._next == 1:
            self._send(bytes(self._buffer))
            self._buffer.clear()
        status, _, body = _call("POST", f"/step/v1/outputs/{self._name}/complete")
        if status != 200:
            raise StepError(f"finishing {self._name} returned {status}: {body[:300].decode('utf-8', 'replace')}")
        self._finished = True
        return json.loads(body)

    def close(self) -> None:
        if not self.closed and not self._finished and not getattr(self, "_abandoned", False):
            self.finish()
        super().close()

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self._abandoned = True  # an error inside the block: the file is not finished, and the platform throws it away
        super().__exit__(exc_type, exc, tb)


def write_output(name: str) -> OutputFile:
    """A file to write to, sent to the platform as it is written. Use it as `with write_output("name.wav") as out:`."""
    return OutputFile(name)


def save_output(name: str, data: bytes) -> dict:
    """A whole file from bytes. Returns its size and fingerprint."""
    out = OutputFile(name)
    out.write(data)
    return out.finish()


def copy_to_output(name: str, path: str) -> dict:
    """A file from this container's own scratch space, sent in pieces. Returns its size and fingerprint."""
    out = OutputFile(name)
    with open(path, "rb") as source:
        for piece in iter(lambda: source.read(BLOCK), b""):
            out.write(piece)
    return out.finish()


def result(value: dict) -> None:
    """The step's result, a small JSON object, printed as the last line of output, which is where the platform reads it from."""
    print(json.dumps(value), flush=True)
