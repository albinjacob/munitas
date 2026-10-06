"""Asking the platform to open a recording, from any worker.

A locked recording is stored as ciphertext, so nothing outside the platform can read it from storage: the platform checks that the task is real and
was launched with the version as an input, unwraps the patient's key (the opening is logged with the task named), and streams the audio back. The
key never leaves the platform.

Kept to httpx alone on purpose. The sandbox worker runs in the WSL distro with a slim set of packages and cannot import the pipeline's storage
client, and a custom pipeline step needs exactly this: its recordings, opened by the platform, written into a folder the step can read.
"""

from __future__ import annotations

import io
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable

import httpx

from . import config

BLOCK = 8 * 1024 * 1024
RETRY_WAITS = (0.5, 1.5)


class ControlPlaneError(Exception):
    pass


class RecordingErased(Exception):
    """The patient this recording is about was erased, so it cannot be opened and the run leaves it out."""


def _headers() -> dict:
    return {"x-worker-token": config.WORKER_TOKEN}


def stream_to_file(local: Path, *, principal: str, tenant_id: str, task_credential: str, version_id: str, record_id: str, purpose: str,
                   kind: str = "audio", step: str | None = None) -> None:
    """The recording (or its answer key) opened by the platform and written to `local`. A body shorter than the platform said it would be is
    refused, because the platform ends a body short when a chunk does not open. A patient who was erased raises RecordingErased."""
    payload = {"principal": principal, "tenant_id": tenant_id, "task_credential": task_credential, "dataset_version_id": version_id,
               "record_id": record_id, "purpose": purpose, "kind": kind}
    if step is not None:
        payload["step"] = step
    written = 0
    with httpx.stream("POST", f"{config.API}/internal/recordings/open", timeout=httpx.Timeout(30.0, read=120.0),
                      headers=_headers(), verify=config.api_verify(), json=payload) as response:
        if response.status_code == 410:
            raise RecordingErased(record_id)
        if response.status_code >= 400:
            response.read()
            raise ControlPlaneError(f"opening recording {record_id} returned {response.status_code}: {response.text[:300]}")
        expected = int(response.headers["x-plain-bytes"])
        try:
            with open(local, "wb") as out:
                for piece in response.iter_bytes():
                    out.write(piece)
                    written += len(piece)
        except httpx.HTTPError as exc:
            local.unlink(missing_ok=True)
            raise ControlPlaneError(f"recording {record_id} was cut off after {written} of {expected} bytes: {exc}") from exc
    if written != expected:
        local.unlink(missing_ok=True)
        raise ControlPlaneError(f"recording {record_id} came back as {written} bytes, not the {expected} the platform said")


def list_for_step(*, principal: str, tenant_id: str, task_credential: str, version_id: str, step: str) -> list[dict]:
    """The recordings a custom step is to be given, as the platform lists them: id, size, and whether the patient was erased. The platform refuses
    when the step's version was not approved to be given them."""
    response = httpx.post(f"{config.API}/internal/dag-step/recordings", timeout=60.0, headers=_headers(), verify=config.api_verify(),
                          json={"principal": principal, "tenant_id": tenant_id, "task_credential": task_credential,
                                "dataset_version_id": version_id, "step": step})
    if response.status_code >= 400:
        raise ControlPlaneError(f"listing the recordings for step {step} returned {response.status_code}: {response.text[:300]}")
    return response.json()["recordings"]


class RangeFile(io.RawIOBase):
    """A recording, read as if it were an ordinary file, without ever being a file.

    It can be read and jumped around in, so the speech model and the audio reader take it where they would take a path. Behind it, each read that
    is not already held becomes a request for one block of the recording (8 MiB), and the last two blocks are kept for the reads that follow. The
    recording is never written to disk, and only what is being read is held in memory, however long the recording is.

    `fetch(offset, length)` returns exactly that many bytes of the opened recording.
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
            raise ControlPlaneError(f"the platform returned {len(data)} bytes of the {want} asked for at {start}")
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


def _post(client: httpx.Client, path: str, payload: dict) -> httpx.Response:
    """One request to the platform, sent again a couple of times when the connection fails or the platform is briefly unavailable."""
    last: Exception | None = None
    for wait in (*RETRY_WAITS, None):
        try:
            response = client.post(f"{config.API}{path}", json=payload, headers=_headers())
            if response.status_code < 500:
                return response
            last = ControlPlaneError(f"{path} returned {response.status_code}")
        except httpx.TransportError as exc:
            last = exc
        if wait is not None:
            time.sleep(wait)
    raise ControlPlaneError(f"{path} could not be reached: {last}")


def open_remote(*, principal: str, tenant_id: str, task_credential: str, version_id: str, record_id: str, purpose: str, kind: str = "audio",
                step: str | None = None) -> RangeFile:
    """A recording the platform opens for this task, as a file that is read a range at a time.

    Opening it is the one write to the key log, naming the task. A patient who was erased raises RecordingErased, at the opening or at any later
    read, because the platform checks the patient's key on every range. The key never leaves the platform.
    """
    identity = {"principal": principal, "tenant_id": tenant_id, "task_credential": task_credential, "dataset_version_id": version_id,
                "record_id": record_id, "kind": kind}
    if step is not None:
        identity["step"] = step
    client = httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), verify=config.api_verify())

    def refusal(response: httpx.Response, what: str) -> Exception:
        if response.status_code == 410:
            return RecordingErased(record_id)
        return ControlPlaneError(f"{what} recording {record_id} returned {response.status_code}: {response.text[:300]}")

    try:
        begun = _post(client, "/internal/recordings/begin", {**identity, "purpose": purpose})
        if begun.status_code >= 400:
            raise refusal(begun, "opening")
        size = int(begun.json()["bytes"])
    except Exception:
        client.close()
        raise

    def fetch(offset: int, length: int) -> bytes:
        response = _post(client, "/internal/recordings/range", {**identity, "purpose": "read", "offset": offset, "length": length})
        if response.status_code >= 400:
            raise refusal(response, "reading")
        return response.content

    return RangeFile(size, fetch, on_close=client.close)
