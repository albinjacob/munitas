r"""U202: the first upload for a new organisation waits for storage to recognise its key, instead of failing.

Run in the API container, which holds the API's code:

    docker compose exec -T munitas-api python /verify/v188_new_key_wait.py

When an organisation's storage is set up, its upload key is written to SeaweedFS and the client is handed back at once. SeaweedFS reads a new key a moment after it is written, so a write in that moment
was told the key did not exist, and the upload failed with a server error (seen once in a full run: a first upload into a brand-new organisation). This proves the client now waits, and only in that case:
it runs the platform's own client against a stand-in for storage that refuses the key a chosen number of times and then accepts it.

  * a client given a key that was written a moment ago retries the refusal "the key does not exist" and succeeds once storage knows the key;
  * it retries nothing else: a different refusal is final on the first answer;
  * a client for a key that was active already does not retry at all, so a wrong key still fails at once;
  * a key storage never recognises is given up on after a bounded number of tries, and the refusal is passed on as it was.
"""

from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/verify")
sys.path.insert(0, "/app")

from botocore.exceptions import ClientError  # noqa: E402
from common import check, heading, summary  # noqa: E402

from app import seaweed  # noqa: E402

BODY = (b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>%s</Code><Message>%s</Message><RequestId>r</RequestId></Error>')


class Storage(BaseHTTPRequestHandler):
    """Stands in for storage: refuses the first `refusals` writes with `code`, then accepts them."""

    refusals = 0
    code = b"InvalidAccessKeyId"
    seen = 0

    def do_PUT(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        type(self).seen += 1
        if type(self).seen <= type(self).refusals:
            body = BODY % (type(self).code, b"refused")
            self.send_response(403)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("ETag", '"abc"')
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def log_message(self, *args):  # silence
        pass


def serve(refusals: int, code: bytes = b"InvalidAccessKeyId") -> tuple[HTTPServer, str, type]:
    handler = type("Handler", (Storage,), {"refusals": refusals, "code": code, "seen": 0})
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}", handler


def put(endpoint: str, just_activated: bool):
    client = seaweed._ingest_client("key", "secret", just_activated=just_activated, endpoint=endpoint)
    return client.put_object(Bucket="b", Key="k", Body=b"hello")


def main() -> int:
    seaweed.NEW_KEY_PAUSE = 0.02  # the real pause is a quarter of a second; the check does not need to wait that long

    heading("U202: the retry hook")
    check("a refusal that says the key does not exist is retried after a pause", seaweed._wait_for_new_key(response=(None, {"Error": {"Code": "InvalidAccessKeyId"}}), attempts=1) == seaweed.NEW_KEY_PAUSE)
    check("any other refusal is left as it is", seaweed._wait_for_new_key(response=(None, {"Error": {"Code": "AccessDenied"}}), attempts=1) is None)
    check("a success is left as it is", seaweed._wait_for_new_key(response=(None, {}), attempts=1) is None)
    check("no response at all is left as it is", seaweed._wait_for_new_key(response=None, attempts=1) is None)
    check("it gives up after a bounded number of tries", seaweed._wait_for_new_key(response=(None, {"Error": {"Code": "InvalidAccessKeyId"}}), attempts=seaweed.NEW_KEY_ATTEMPTS + 1) is None)
    check("the longest wait is about five seconds with the real pause", 4 <= seaweed.NEW_KEY_ATTEMPTS * 0.25 <= 6, str(seaweed.NEW_KEY_ATTEMPTS))

    heading("U202: a key written a moment ago")
    server, url, handler = serve(refusals=2)
    try:
        answer = put(url, just_activated=True)
        check("a write refused twice for an unknown key succeeds once storage knows it", answer["ResponseMetadata"]["HTTPStatusCode"] == 200, str(answer)[:100])
        check("it took three requests: two refusals and the write", handler.seen == 3, str(handler.seen))
    finally:
        server.shutdown()

    heading("U202: a key that was active already")
    server, url, handler = serve(refusals=1)
    try:
        try:
            put(url, just_activated=False)
            refused = None
        except ClientError as exc:
            refused = exc.response["Error"]["Code"]
        check("a wrong key fails at once, on the first answer", refused == "InvalidAccessKeyId" and handler.seen == 1, f"{refused} after {handler.seen}")
    finally:
        server.shutdown()

    heading("U202: only that refusal is waited for")
    server, url, handler = serve(refusals=5, code=b"AccessDenied")
    try:
        try:
            put(url, just_activated=True)
            refused = None
        except ClientError as exc:
            refused = exc.response["Error"]["Code"]
        check("another refusal is final on the first answer, even for a new key", refused == "AccessDenied" and handler.seen == 1, f"{refused} after {handler.seen}")
    finally:
        server.shutdown()

    heading("U202: a key storage never recognises")
    seaweed.NEW_KEY_ATTEMPTS = 4
    server, url, handler = serve(refusals=1000)
    try:
        try:
            put(url, just_activated=True)
            refused = None
        except ClientError as exc:
            refused = exc.response["Error"]["Code"]
        check("it gives up after the bounded number of tries and passes the refusal on as it was", refused == "InvalidAccessKeyId" and handler.seen == seaweed.NEW_KEY_ATTEMPTS + 1, f"{refused} after {handler.seen}")
    finally:
        server.shutdown()
    return summary("U202")


if __name__ == "__main__":
    sys.exit(main())
