r"""U190: who can reach the policy store, and what they can do there.

The policy store holds the signed package that decides every access question. The engine refuses a package whose signature does not verify, so reaching the
store cannot change policy, but the store should still be as closed as it can be. It sits on a network of its own, shared only with the engine, and the engine
holds a key that may read one bucket. This tries both halves, which need to run in different places:

    verify/policy_store_check.sh

  outside  run in the API container, on the platform's own networks: the store cannot be reached at all, by name or by connecting;
  inside   run in a throwaway container on the store's network, with the engine's key: that key may read the package and may not write, delete or list, may not
           read any other bucket, and nobody without it, or with a wrong one, gets anything.

Needs the stack in bundle mode with a package published. It writes nothing.
"""

from __future__ import annotations

import os
import socket
import sys

import httpx  # noqa: E402

# Self-contained, in the same shape as verify/common.py's output, because the half that runs on the store's network has only this file and the API image's libraries.
_results: list[bool] = []
COUNTS_MARKER = "##MUNITAS-CHECK-COUNTS##"


def heading(text: str) -> None:
    print()
    print(text)
    print("-" * len(text))


def check(label: str, ok: bool, detail: str = "") -> bool:
    _results.append(bool(ok))
    shown = detail if (not ok or len(detail) <= 60) else ""
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({shown})" if shown else ""))
    return ok


def summary(name: str) -> int:
    passed, failed = sum(_results), sum(1 for r in _results if not r)
    print()
    print(f"{name}: {passed} passed, {failed} failed, 0 skipped")
    print(f'{COUNTS_MARKER} {{"passed":{passed},"failed":{failed},"skipped":0}}')
    _results.clear()
    return 1 if failed else 0


STORE = "http://policy-store:8333"
BUCKET = "policy-bundles"
OBJECT = "policy.tar.gz"


def client(key: str, secret: str):
    import boto3
    from botocore.config import Config

    return boto3.client("s3", endpoint_url=STORE, aws_access_key_id=key, aws_secret_access_key=secret,
                        config=Config(signature_version="s3v4", retries={"max_attempts": 1}, connect_timeout=5, read_timeout=10), region_name="us-east-1")


def status_of(call) -> int:
    from botocore.exceptions import ClientError

    try:
        call()
        return 200
    except ClientError as exc:
        return int(exc.response["ResponseMetadata"]["HTTPStatusCode"])


def outside() -> int:
    heading("U190: from the platform's own networks")
    try:
        socket.gethostbyname("policy-store")
        named = True
    except OSError:
        named = False
    check("the store's name does not resolve", not named)
    reached = True
    try:
        httpx.get(f"{STORE}/{BUCKET}/{OBJECT}", timeout=5.0)
    except httpx.HTTPError:
        reached = False
    check("a request to the store does not connect", not reached)
    return summary("U190 (outside)")


def inside() -> int:
    key = os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    if not key or not secret:
        print("The engine's key is not in the environment (AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY).")
        return 2
    reader = client(key, secret)
    wrong = client(key, "not-the-secret")
    other = client("somebody-else", "another-secret")

    heading("U190: from the store's network")
    got = reader.get_object(Bucket=BUCKET, Key=OBJECT)["Body"].read() if status_of(lambda: reader.get_object(Bucket=BUCKET, Key=OBJECT)) == 200 else b""
    check("the engine's key reads the package", len(got) > 1000, f"{len(got)} bytes")
    check("and it is a gzip file", got[:2] == b"\x1f\x8b")
    check("the engine's key cannot overwrite it", status_of(lambda: reader.put_object(Bucket=BUCKET, Key=OBJECT, Body=b"changed")) == 403)
    check("the engine's key cannot delete it", status_of(lambda: reader.delete_object(Bucket=BUCKET, Key=OBJECT)) == 403)
    check("the engine's key cannot write anything else into the bucket", status_of(lambda: reader.put_object(Bucket=BUCKET, Key="other", Body=b"x")) == 403)
    check("the engine's key cannot list the bucket", status_of(lambda: reader.list_objects_v2(Bucket=BUCKET)) == 403)
    try:
        names = [b["Name"] for b in reader.list_buckets().get("Buckets", [])]
    except Exception:  # a refusal is as good as an empty answer
        names = []
    check("the engine's key is shown no bucket names", names == [], str(names))
    check("the engine's key cannot read another bucket", status_of(lambda: reader.get_object(Bucket="munitas-health", Key=OBJECT)) == 403)
    check("the engine's key cannot make a bucket", status_of(lambda: reader.create_bucket(Bucket="made-by-the-engine")) == 403)
    check("a wrong secret reads nothing", status_of(lambda: wrong.get_object(Bucket=BUCKET, Key=OBJECT)) == 403)
    check("an unknown key reads nothing", status_of(lambda: other.get_object(Bucket=BUCKET, Key=OBJECT)) == 403)
    check("a request with no key reads nothing", httpx.get(f"{STORE}/{BUCKET}/{OBJECT}", timeout=10.0).status_code == 403)
    check("a request with no key cannot write", httpx.put(f"{STORE}/{BUCKET}/{OBJECT}", content=b"x", timeout=10.0).status_code == 403)
    check("the package is unchanged after all of that", reader.get_object(Bucket=BUCKET, Key=OBJECT)["Body"].read() == got)
    return summary("U190 (inside)")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("outside", "inside"):
        print("usage: v179_policy_store_access.py outside | inside   (verify/policy_store_check.sh runs both)")
        sys.exit(2)
    sys.exit(outside() if sys.argv[1] == "outside" else inside())
