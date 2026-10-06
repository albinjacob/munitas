"""Asking the platform to open a recording, from any worker.

A locked recording is stored as ciphertext, so nothing outside the platform can read it from storage: the platform checks that the task is real and
was launched with the version as an input, unwraps the patient's key (the opening is logged with the task named), and streams the audio back. The
key never leaves the platform.

Kept to httpx alone on purpose. The sandbox worker runs in the WSL distro with a slim set of packages and cannot import the pipeline's storage
client, and a custom pipeline step needs exactly this: its recordings, opened by the platform, written into a folder the step can read.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from . import config


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
