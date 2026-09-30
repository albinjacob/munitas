"""The control plane's operational log: what the process did, never what it holds.

The worker has had loggers since it was written (`munitas.worker`,
`munitas.worker.lite`) and the agent emits OpenTelemetry spans. The control
plane had neither, which is the wrong way round: it is the component that
decides every grant and refusal, and it was the one with no operational
record. Failures here were either raised at the caller or swallowed into
`app.state.storage_error`, where nothing reads them.

WHAT BELONGS HERE, AND WHAT DOES NOT

This platform already has an audit trail, and it is not this. `access_decision`
records every grant and refusal in Postgres, per tenant, as evidence that
survives. Logs are the other thing: an operational record of what the process
attempted and how it went, kept to diagnose behaviour, rotated away without
ceremony.

So a governance fact never lives only in a log. A log that rotates is not an
audit trail, and citing one as evidence of who was allowed to read what is a
category error. Conversely a diagnostic detail does not belong in
`access_decision`, which exists to answer a different question.

WHY THE REDACTION BELOW IS NOT OPTIONAL

This platform holds unredacted clinical audio and transcripts. Anything written
here leaves the governance boundary immediately: stdout goes to Docker's JSON
file, which goes wherever log collection ships it, and none of that is inside
the boundary the policy engine guards.

The rule is **log identifiers, never contents**. A tenant id, a dataset version
id, a bucket name, a storage prefix, a role, an outcome, a duration: all safe
and all useful. A transcript, a detected identifier, a request body, an access
key: never, under any circumstance, at any level.

Two mechanical guards back the rule up, because a rule nobody can break by
accident is worth more than one everybody remembers:

  * fields whose name looks like a secret are replaced before formatting,
    so passing one is harmless rather than catastrophic; and
  * long string values are truncated, because the realistic way PHI reaches a
    log is somebody passing a whole record or response body, and those are
    long. A truncated transcript is still a leak, so this is a backstop and
    not permission: the field allowlist in verify/v69_logging.py is what
    actually checks call sites.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

# Set per request by main.py's middleware, read by the formatter, so every
# line emitted while serving one request carries the same id without any
# call site having to pass it down. A trace id would join here the same way
# if tracing ever reaches the control plane.
request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

# Field names that must never reach an output stream, matched on substring so
# `secret`, `client_secret` and `secret_ciphertext` are all caught. Redaction
# happens whatever the level, including debug: "it was only a debug line" is
# how a credential ends up in a log aggregator.
SECRET_HINTS = (
    "secret", "password", "token", "access_key", "accesskey",
    "credential", "authorization", "cookie", "session", "wrapped_key",
    "private", "api_key", "apikey",
)

# Long enough for any identifier, prefix, bucket name or reason string this
# platform produces; far too short for a transcript or a response body.
MAX_VALUE_CHARS = 300

REDACTED = "[redacted]"

# Keys the logging module puts on every record. Everything else on a record
# was passed by a call site as an extra, which is what gets emitted.
_BUILTIN = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)

# Uvicorn attaches its own pre-colourised copy of the message to every
# record. Emitting it would put ANSI escape codes inside a JSON string and
# repeat the message twice per line, so it is dropped rather than scrubbed.
_NOISE = frozenset({"color_message", "taskName"})


def _scrub(key: str, value: object) -> object:
    lowered = key.lower()
    if any(hint in lowered for hint in SECRET_HINTS):
        return REDACTED
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[:MAX_VALUE_CHARS] + f"... [{len(value)} chars, truncated]"
    if isinstance(value, (dict, list, tuple, set)):
        # Structures are how a whole record reaches a log line. The shape is
        # reported so the line is still useful for diagnosis; the contents
        # are not, because this is exactly the path PHI would take.
        return f"[{type(value).__name__} of {len(value)}, not logged]"
    return value


class JsonFormatter(logging.Formatter):
    """One event per line, machine-readable, with the scrubbing applied."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        rid = request_id.get()
        if rid:
            payload["request_id"] = rid
        for key, value in record.__dict__.items():
            if key in _BUILTIN or key in _NOISE or key.startswith("_"):
                continue
            payload[key] = _scrub(key, value)
        if record.exc_info:
            # The type and message, never the full traceback: a traceback can
            # carry local variables' values into the line.
            exc_type, exc_value, _ = record.exc_info
            payload["error_type"] = getattr(exc_type, "__name__", str(exc_type))
            payload["error"] = _scrub("error", str(exc_value))
        return json.dumps(payload, default=str)


def configure() -> None:
    """Point the control plane's own loggers, and uvicorn's, at one handler.

    Called once from main.py's lifespan. Uvicorn is included deliberately:
    two formats in one stream means whatever reads them has to understand
    both, and the one that gets parsed is the one that gets read.

    MUNITAS_LOG_LEVEL sets the level, defaulting to info. Debug is left off
    rather than merely quiet, because a debug level that can be switched on
    in production is a standing invitation to log more than belongs here.
    """
    level = os.environ.get("MUNITAS_LOG_LEVEL", "INFO").upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger("munitas")
    root.handlers = [handler]
    root.setLevel(level)
    root.propagate = False

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False


def get_logger(module: str) -> logging.Logger:
    """A logger named for the module, under the convention the worker set.

    `logs.get_logger("storage")` gives `munitas.api.storage`, which sits
    beside the worker's `munitas.worker` rather than inventing a second
    naming scheme for the same platform.
    """
    return logging.getLogger(f"munitas.api.{module}")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]
