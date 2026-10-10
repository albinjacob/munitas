"""Asking the platform to open a file, from any worker.

A locked file is stored as ciphertext, so nothing outside the platform can read it from storage: the platform checks that the task is real and
was launched with the version as an input, unwraps the patient's key (the opening is logged with the task named), and returns each range that is
read. The key never leaves the platform.

Kept to httpx alone on purpose. The sandbox worker runs in the WSL distro with a slim set of packages and cannot import the pipeline's storage
client, and it needs exactly two things from here: to ask for the pass a custom step is started with, and (through the pipeline's own steps) to open
a file as a file.

The file is the library's own `RangeFile`, the one implementation of the reader. A custom step uses it from inside its container; the pipeline's
transcribe and redact steps use it from here.
"""

from __future__ import annotations

import time

import httpx

from . import config
from .step_sdk.munitas_step import BLOCK, RangeFile  # noqa: F401  (re-exported: steps and checks read files through it)

RETRY_WAITS = (0.5, 1.5)


class ControlPlaneError(Exception):
    pass


class FileErased(Exception):
    """The patient this file is about was erased, so it cannot be opened and the run leaves it out."""


def _headers() -> dict:
    return {"x-worker-token": config.WORKER_TOKEN}


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


def open_remote(*, principal: str, tenant_id: str, task_credential: str, version_id: str, record_id: str, purpose: str, kind: str = "audio") -> RangeFile:
    """A file the platform opens for this task, as a file that is read a range at a time.

    Opening it is the one write to the key log, naming the task. A patient who was erased raises FileErased, at the opening or at any later
    read, because the platform checks the patient's key on every range. The key never leaves the platform.
    """
    identity = {"principal": principal, "tenant_id": tenant_id, "task_credential": task_credential, "dataset_version_id": version_id,
                "record_id": record_id, "kind": kind}
    client = httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), verify=config.api_verify())

    def refusal(response: httpx.Response, what: str) -> Exception:
        if response.status_code == 410:
            return FileErased(record_id)
        return ControlPlaneError(f"{what} file {record_id} returned {response.status_code}: {response.text[:300]}")

    try:
        begun = _post(client, "/internal/files/begin", {**identity, "purpose": purpose})
        if begun.status_code >= 400:
            raise refusal(begun, "opening")
        size = int(begun.json()["bytes"])
    except Exception:
        client.close()
        raise

    def fetch(offset: int, length: int) -> bytes:
        response = _post(client, "/internal/files/range", {**identity, "purpose": "read", "offset": offset, "length": length})
        if response.status_code >= 400:
            raise refusal(response, "reading")
        return response.content

    return RangeFile(size, fetch, on_close=client.close)


def finish_step(*, principal: str, tenant_id: str, task_credential: str, step: str, succeeded: bool) -> dict:
    """Tell the platform a writing step's container has exited. If it succeeded the platform seals what it wrote as one version under review;
    if it did not, or the platform will not, everything it wrote is thrown away. Answers {"status": "sealed" | "empty" | "discarded" | "refused"}."""
    response = httpx.post(f"{config.API}/internal/dag-step/finish", timeout=300.0, headers=_headers(), verify=config.api_verify(),
                          json={"principal": principal, "tenant_id": tenant_id, "task_credential": task_credential, "step": step, "succeeded": succeeded})
    if response.status_code >= 400:
        raise ControlPlaneError(f"finishing step {step} returned {response.status_code}: {response.text[:300]}")
    return response.json()


def step_pass(*, principal: str, tenant_id: str, task_credential: str, step: str) -> str:
    """The pass a custom step's container is started with. The platform makes one only for a step that its pipeline version declares as given the
    files, whose version is approved, in a run that is still going; the pass names this step and expires with its time limit."""
    response = httpx.post(f"{config.API}/internal/dag-step/pass", timeout=60.0, headers=_headers(), verify=config.api_verify(),
                          json={"principal": principal, "tenant_id": tenant_id, "task_credential": task_credential, "step": step})
    if response.status_code >= 400:
        raise ControlPlaneError(f"asking for the pass of step {step} returned {response.status_code}: {response.text[:300]}")
    return response.json()["pass"]
