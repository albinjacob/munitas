"""Uploads one derivation's result to storage, from inside a container.

Platform-authored and bind-mounted read-only by the worker, the counterpart of
sandbox_stage_fetch.py. It exists so the worker that runs the query needs no
storage client of its own: the key it was handed reaches this container and
nowhere else.

Environment: MUNITAS_S3_ENDPOINT, MUNITAS_S3_ACCESS_KEY, MUNITAS_S3_SECRET_KEY,
MUNITAS_S3_BUCKET, MUNITAS_PUT_KEY (the object to write). Reads /out/records.json
and prints {"uploaded": <bytes>}.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> int:
    import boto3
    from botocore.config import Config

    body = Path("/out/records.json").read_bytes()
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["MUNITAS_S3_ENDPOINT"],
        aws_access_key_id=os.environ["MUNITAS_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MUNITAS_S3_SECRET_KEY"],
        config=Config(signature_version="s3v4", retries={"max_attempts": 2}),
        region_name="us-east-1",
    )
    s3.put_object(Bucket=os.environ["MUNITAS_S3_BUCKET"], Key=os.environ["MUNITAS_PUT_KEY"], Body=body)
    print(json.dumps({"uploaded": len(body)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
