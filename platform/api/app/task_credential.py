"""Spawn-time proof of identity for Type A workloads.

A Type A workload is one with a clear "this task begins now" moment the
platform itself controls: `agent_run` (an agent's own execution), `action_run`
(a pipeline step that seals its own output), `pipeline_run` (a whole pipeline
execution, several steps and possibly several `action_run`s), and whenever
`training_job`/`model_eval` become real running services. This does not
cover Type B -- a standing service like `annotation_tool` that mediates many
human sessions over time needs a delegation mechanism, proving which human's
session a read is for, not a spawn-time secret -- and nothing here is meant
to.

`huggingface_fetch_job` is a fifth: a HuggingFace dataset fetch also has a
clear begin moment (the job row `platform/api/app/ingest.py`'s
`fetch_huggingface` inserts before starting the Temporal workflow), and
writes into object storage as `pipeline_action` the same way an `action_run`
does, through `POST /write-credentials`
(docs/internal/design/write-credential-rationale.md). It is not an `action_run`
itself: nothing about a HuggingFace fetch transforms one dataset version into
another the way a pipeline step does, so it gets its own task kind rather
than a borrowed one that would claim a relationship that does not exist.

`pipeline_run` exists alongside `action_run`, not instead of it, because not
every step has its own natural close event to bind a per-step token to.
`action_run` closes when a step seals a new dataset version; a step like
`adopt_version` (worker/activities.py) reads an existing one and produces no
sealed output of its own, so it never opens an `action_run` at all (nothing
would ever close it). `pipeline_run` already opens before that step runs and
closes once, at the end of the whole pipeline, so a token bound to it covers
exactly that gap -- the same reasoning GitHub Actions' OIDC token is scoped
to the whole workflow run rather than each job step, or a Kubernetes
ServiceAccount token to the whole pod rather than each container in it: the
credential binds to whichever unit actually has a real, enforced lifetime,
not to whichever unit happens to be doing the asking.

Modelled on how OCI resource principals actually work, not on a shared
secret: identity proof is delivered through a channel only the real task
could receive (the process that spawns it -- the API itself for `action_run`,
the worker for a sandboxed `agent_run`), at the moment the platform creates
the task, never something the caller types in afterward. `agent_run` used to
rely on a plain random secret (`agent_run.run_secret`) stored in the database
and compared by equality; this replaces that comparison with a signed,
self-describing token verified by signature and expiry alone, so nothing
here is looked up or compared against a stored value at verification time.

    from task_credential import mint, verify, InvalidTaskCredential
    token = mint(principal="agent-triage-1-runtime", task_kind="agent_run",
                 task_id=run_id, tenant_id="health")
    claim = verify(token)  # raises InvalidTaskCredential if bad or expired

The signing key is a single HMAC key derived from `MUNITAS_MASTER_KEY` via
HKDF, with its own `info` label so it cannot collide with a key
`platform/crypto/envelope.py` derives for a different purpose from the same
root. One symmetric key, not a certificate pair: the API is the only party
that will ever verify one of these tokens, unlike OCI's shape where many
independent services must each check a resource's identity independently.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# A run's own natural lifetime bounds how long it actually needs this, so
# this is a generous backstop against a task that never ends, not the
# expected clock a well-behaved run runs out.
DEFAULT_TTL_SECONDS = 6 * 3600

TASK_KINDS = ("agent_run", "action_run", "pipeline_run", "huggingface_fetch_job", "table_write_job")


class InvalidTaskCredential(Exception):
    """The token is malformed, wrongly signed, or expired."""


@dataclass(frozen=True)
class TaskClaim:
    principal: str
    task_kind: str
    task_id: str
    tenant_id: str
    expires_at: float


def _signing_key() -> bytes:
    raw = os.environ.get("MUNITAS_MASTER_KEY")
    if not raw:
        raise RuntimeError("MUNITAS_MASTER_KEY is not set")
    kdf = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=b"munitas/task-credential",
    )
    return kdf.derive(raw.encode("utf-8"))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def mint(*, principal: str, task_kind: str, task_id: str, tenant_id: str,
          ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """A signed token proving `task_id` really is what spawned this call.

    Called once, by the platform, at the moment the task row is created --
    never by the workload itself, which only ever receives the result.
    """
    if task_kind not in TASK_KINDS:
        raise ValueError(f"{task_kind!r} is not a Type A task kind: {TASK_KINDS}")
    payload = {
        "principal": principal,
        "task_kind": task_kind,
        "task_id": task_id,
        "tenant_id": tenant_id,
        "expires_at": time.time() + ttl_seconds,
    }
    encoded = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_signing_key(), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_b64(signature)}"


def verify(token: str) -> TaskClaim:
    """The claim a token makes, if its signature is valid and it has not expired.

    Raises `InvalidTaskCredential` rather than returning `None` on any
    failure, so a caller cannot forget to check a falsy result: every path
    through this function either returns a trustworthy claim or raises.
    """
    try:
        encoded, signature_b64 = token.split(".")
        signature = _unb64(signature_b64)
        payload = json.loads(_unb64(encoded))
    except Exception as exc:
        raise InvalidTaskCredential("malformed task credential") from exc

    expected = hmac.new(_signing_key(), encoded.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise InvalidTaskCredential("task credential signature does not match")

    try:
        claim = TaskClaim(
            principal=payload["principal"], task_kind=payload["task_kind"],
            task_id=payload["task_id"], tenant_id=payload["tenant_id"],
            expires_at=payload["expires_at"],
        )
    except KeyError as exc:
        raise InvalidTaskCredential(f"task credential is missing {exc}") from exc

    if claim.expires_at < time.time():
        raise InvalidTaskCredential("task credential has expired")
    return claim
