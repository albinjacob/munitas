"""Alerts: something a person should look at, written down where they can find it, without stopping anything.

An alert is not a refusal. When the platform notices something odd it carries on and says so, because stopping work to report a worry
turns a worry into an outage. Each alert is one line of JSON appended to one file, with a `category` that says which part of the
platform raised it (`storage-permissions` today; a new part adds a category, not a file). The same line goes to the control plane's
operational log, so a log collector sees it too, and a later metrics exporter can count the lines by category and severity without any
change to what is written.

The file is for people and tools. It is not evidence: a file that rotates is not an audit trail (see `logs.py`), so anything that
must survive is also recorded in the database by whoever raised the alert (the storage permission update records its alerts on its
own row). Like every log line, an alert names identifiers and numbers and never contents: no record, no transcript, no key.

Where the file lives is the setting `MUNITAS_ALERTS_FILE` (`config.ALERTS_FILE`). In the stack it is `/logs/alerts.jsonl`, and `/logs` is
the `logs` folder inside the data folder (`MUNITAS_DATA` in `.env`), next to the worker's logs. Setting it to an empty string switches
the file off; the alert still goes to the log and to whoever raised it.

Raising an alert never raises: a full disk or a missing folder is reported in the log and the platform carries on.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import threading
from datetime import datetime, timezone

from . import config, logs

log = logging.getLogger("munitas.api.alerts")

# Rotated by size so that a noisy day cannot fill the disk: 10 MB a file and five old files kept.
MAX_BYTES = 10 * 1024 * 1024
KEEP_FILES = 5

SEVERITIES = ("notice", "warning", "critical")

_writer: logging.Logger | None = None
_writer_path: str | None = None
_lock = threading.Lock()


def _file_logger(path: str) -> logging.Logger:
    """One rotating file handler per path, made on first use and made again if the path changes."""
    global _writer, _writer_path
    with _lock:
        if _writer is not None and _writer_path == path:
            return _writer
        if _writer is not None:
            for handler in list(_writer.handlers):
                _writer.removeHandler(handler)
                handler.close()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        writer = logging.getLogger("munitas.alerts.file")
        writer.setLevel(logging.INFO)
        writer.propagate = False
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=KEEP_FILES, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        writer.addHandler(handler)
        _writer, _writer_path = writer, path
        return writer


def raise_alert(category: str, event: str, message: str, *, severity: str = "warning", **details) -> dict:
    """Write one alert and return it.

    `category` is the part of the platform raising it, `event` a short stable name for what happened (what a dashboard would count),
    `message` a full sentence a person can read, and `details` the identifiers and numbers behind it.
    """
    if severity not in SEVERITIES:
        severity = "warning"
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "category": category,
        "event": event,
        "severity": severity,
        "message": message,
        "details": _safe(details),
        "request_id": logs.request_id.get(),
    }
    line = json.dumps(record, sort_keys=True, default=str)
    path = config.ALERTS_FILE
    if path:
        try:
            _file_logger(path).info(line)
        except Exception as exc:  # noqa: BLE001 - an alert that cannot be written must never stop the work that raised it
            log.warning("an alert could not be written to its file", extra={"reason": type(exc).__name__})
    log.warning("alert raised", extra={"category": category, "event": event, "severity": severity})
    return record



def _safe(details: dict) -> dict:
    """Keep what is safe to write: values that are numbers, text of reasonable length, or short lists of those. Anything whose name
    looks like a secret is dropped, as in `logs.py`: a mistake in a call site should not become a leak."""
    out: dict = {}
    for name, value in details.items():
        if any(word in name.lower() for word in logs.SECRET_HINTS):
            continue
        out[name] = _clip(value)
    return out


def _clip(value, depth: int = 0):
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, str):
        return value if len(value) <= 300 else value[:300] + "..."
    if isinstance(value, (list, tuple, set)) and depth < 2:
        items = sorted(value, key=str) if isinstance(value, set) else list(value)
        return [_clip(v, depth + 1) for v in items[:20]] + ([f"and {len(items) - 20} more"] if len(items) > 20 else [])
    if isinstance(value, dict) and depth < 2:
        return {str(k): _clip(v, depth + 1) for k, v in list(value.items())[:20]}
    return str(value)[:300]
