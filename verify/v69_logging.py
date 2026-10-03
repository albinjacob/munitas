"""U69: the control plane's log carries identifiers, never contents.

This platform holds unredacted clinical audio and transcripts, and anything
written to a log leaves the governance boundary at once: stdout goes to
Docker's JSON file, which goes wherever collection ships it. So the rule is
"log identifiers, never contents", and a rule with nothing checking it is a
comment.

Two halves, and the second is the one that matters. The first proves the
formatter scrubs what it is given, which protects against a mistake at one
call site. The second reads every call site in the control plane and fails
on any that passes something outside the allowed fields, which is what stops
the mistake being made.

    docker compose exec -T munitas-api python /verify/v69_logging.py
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).parent))

from app import logs  # noqa: E402

from common import check, heading, summary  # noqa: E402

APP = Path("/app/app")

# What a log line may name. Identifiers, outcomes, and reasons the platform
# itself produced. Anything else is either contents or a new kind of fact
# that should be argued for here before it appears in a stream that leaves
# the boundary.
ALLOWED_FIELDS = {
    "tenant_id", "dataset_id", "dataset_version_id", "agent_id", "run_id",
    "bucket", "prefix", "backend", "role", "principal", "purpose",
    "outcome", "state", "status", "reason", "error", "error_type",
    "count", "seconds", "volumes_wanted", "volumes_reserved",
    "request_id", "lease_id", "identity",
    # The id of a derivation (a query that makes a new dataset). An identifier
    # of the same kind as dataset_id and run_id, so it says which one a line is
    # about without saying anything about what the query read or produced.
    "derivation_id",
    # The id of a table job (a large table written by a worker), the line of work it is on (the name of a queue, which holds
    # an organisation id and nothing else), and the version number it reserved. Identifiers of the same kind as derivation_id and
    # dataset_version_id: they say which job a line is about without saying anything about the rows it is writing.
    "job_id", "queue", "version",
}

EXTRA_CALL = re.compile(r"extra=\{(.*?)\}", re.S)
FIELD_KEY = re.compile(r'"([a-z_]+)"\s*:')


def formatted(record_fields: dict, message: str = "probe") -> dict:
    """One record through the real formatter, back as a dict."""
    record = logging.LogRecord(
        "munitas.api.probe", logging.INFO, __file__, 1, message, None, None
    )
    for key, value in record_fields.items():
        setattr(record, key, value)
    return json.loads(logs.JsonFormatter().format(record))


def main() -> int:
    heading("U69: a secret-shaped field never reaches the stream")

    for field in ("secret_ciphertext", "access_key", "session_token",
                  "password", "api_key"):
        out = formatted({field: "the-actual-value"})
        check(f"{field} is redacted", out.get(field) == logs.REDACTED, out.get(field))

    heading("U69: contents cannot ride along in a field")

    transcript = "patient said " + ("x" * 500)
    out = formatted({"reason": transcript})
    check("a long string is truncated rather than emitted whole",
          len(out["reason"]) < len(transcript) and "truncated" in out["reason"],
          f"{len(out['reason'])} chars")

    out = formatted({"record": {"transcript": "words", "name": "a person"}})
    check("a structure is reported by shape, not by content",
          "not logged" in str(out["record"]) and "person" not in str(out["record"]),
          out["record"])

    heading("U69: every line is machine-readable and says who and when")

    out = formatted({"tenant_id": "canary"}, message="something happened")
    check("the event carries a timestamp", "ts" in out, out.get("ts"))
    check("and a level", out.get("level") == "info", out.get("level"))
    check("and the logger it came from", out.get("logger") == "munitas.api.probe",
          out.get("logger"))
    check("and the event itself", out.get("event") == "something happened",
          out.get("event"))
    check("and the identifier it was given", out.get("tenant_id") == "canary",
          out.get("tenant_id"))

    heading("U69: a request's lines can be tied together")

    token = logs.request_id.set("abc123")
    try:
        out = formatted({})
        check("a line carries the request it belongs to",
              out.get("request_id") == "abc123", out.get("request_id"))
    finally:
        logs.request_id.reset(token)

    out = formatted({})
    check("and carries none when there is no request", "request_id" not in out,
          out.get("request_id"))

    heading("U69: no call site names a field outside the allowed set")

    # The half that stops the mistake rather than surviving it. Read from
    # the source rather than from a running process: a call site that is
    # only reached on a rare failure path still has to obey the rule, and
    # exercising every one of them is not a thing this can do.
    offenders: list[str] = []
    scanned = 0
    for path in sorted(APP.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in EXTRA_CALL.finditer(source):
            scanned += 1
            for field in FIELD_KEY.findall(match.group(1)):
                if field not in ALLOWED_FIELDS:
                    offenders.append(f"{path.name}: {field}")

    check(f"every extra= field is one of the {len(ALLOWED_FIELDS)} allowed",
          not offenders, ", ".join(offenders) or f"{scanned} call site(s) scanned")

    heading("U69: the control plane logs at all")

    # It did not, for its whole life, while the worker and the agent both
    # did. This fails if somebody removes the wiring rather than the rule.
    main_source = (APP / "main.py").read_text(encoding="utf-8")
    check("start-up configures logging",
          "logs.configure()" in main_source)
    check("and a request id is assigned per request",
          "logs.request_id.set" in main_source)
    check("and the storage failure that used to vanish is reported",
          "storage permissions could not be printed at start-up" in main_source)

    return summary("U69")


if __name__ == "__main__":
    sys.exit(main())
