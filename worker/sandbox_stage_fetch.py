"""Fetches a run's authorized dataset objects into /out, sandboxed.

Platform-authored, not user-uploaded: this is the one piece of code that
runs inside the stage_data container (worker/sandbox_run.py's
stage_data), bind-mounted in read-only by the worker rather than fetched
from an agent's own uploaded code the way build_deps/run_sandboxed's
contract works.

Reads /stage/manifest.json (written by the worker: which real object keys
to fetch and where under /out to write them), fetches each one with the
scoped credential handed to it via environment variables, and writes
/out/manifest.json only once every file has actually landed, so a partial
failure never leaves a manifest claiming more than what is really there.

Environment contract:
  MUNITAS_S3_ENDPOINT, MUNITAS_S3_ACCESS_KEY, MUNITAS_S3_SECRET_KEY,
  MUNITAS_S3_BUCKET: the scoped credential the worker obtained on this
  run's behalf via POST /credentials. Never a platform admin key.
  MUNITAS_S3_SESSION_TOKEN: present only when the credential came from a
  backend whose grants are STS-style and expire on their own (Cloudflare
  R2 today); absent for a SeaweedFS-backed version, whose grant never
  carried one.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    import boto3
    from botocore.config import Config

    manifest = json.loads(Path("/stage/manifest.json").read_text())
    entries = manifest["objects"]
    out_dir = Path("/out")
    out_dir.mkdir(parents=True, exist_ok=True)

    # s3v4 and a region are not optional against SeaweedFS: without them
    # the request is signed in a way it rejects, reported as AccessDenied,
    # which reads like a missing grant rather than a malformed signature.
    # The same settings agent/tools.py's list_review_queue already uses.
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["MUNITAS_S3_ENDPOINT"],
        aws_access_key_id=os.environ["MUNITAS_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MUNITAS_S3_SECRET_KEY"],
        aws_session_token=os.environ.get("MUNITAS_S3_SESSION_TOKEN"),
        config=Config(signature_version="s3v4", retries={"max_attempts": 2}),
        region_name="us-east-1",
    )
    bucket = os.environ["MUNITAS_S3_BUCKET"]

    for entry in entries:
        dest = out_dir / entry["relative_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        body = s3.get_object(Bucket=bucket, Key=entry["key"])["Body"].read()
        dest.write_bytes(body)

    # Written only after every object above landed without raising, so a
    # partial failure surfaces as a nonzero exit and a short stdout tail
    # (sandbox_run.py's stage_data reads that as the failure reason),
    # never as a manifest describing files that are not actually there.
    (out_dir / "manifest.json").write_text(json.dumps({"objects": entries}))
    print(f"staged {len(entries)} object(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
