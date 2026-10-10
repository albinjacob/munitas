"""A small client for working with Munitas from a notebook or a script.

    from munitas_client import Munitas
    me = Munitas.sign_in("https://munitas.example")

`sign_in` shows a short code and an address. Open the address in the browser you are signed in to the console with, type the code, and approve. The notebook
then holds a pass that lasts a few minutes and renews itself, for up to eight hours, as you. It can find datasets, ask for access, open the few files you were
allowed in a notebook and make a dataset from a query. It cannot decide anybody's request or change roles or settings: those are done in the console. You can
see and end it under "Your notebooks" in the console, or here with `me.sign_out()`. The pass is kept in memory and never written to a file.

`Munitas(api, session_token)` still works with the token the console signs a person in with, which has all the powers the person has. Prefer `sign_in`.

Everything here calls the platform's own API as that person, so the platform decides each time what they may do. Nothing here holds a credential for storage.

What it covers:

  catalog_token / attach_duckdb   open governed tables in DuckDB
  request_access / approve        ask for access below your role, and decide it
  derive                          make a new dataset from a query
  lineage / derivation            see where a version came from
  list_files / open_file          read the few real files an approver allowed you to open in a notebook
  read_file / save_file

Needs httpx. attach_duckdb also needs duckdb. The file methods also need `munitas_step.py` beside this file: it holds the one reader that
custom pipeline steps use too, which reads a file as if it were an ordinary file without ever holding more than two blocks of it.

Real files are locked, one key for each person they are about, and the platform opens them for you:

    me = Munitas("http://localhost:8000", session_token)
    for item in me.list_files("calls"):                 # the files your permission names: [{"record_id": ..., "bytes": ..., "erased": False}, ...]
        if item["erased"]:
            continue                                      # the person was erased: it cannot be opened
        audio = me.open_file(item["record_id"], "calls")  # a file you can read, seek in and pass to audio libraries
        ...

Asking for the permission is done on the version's page, choosing that you will open the files in a notebook. An approver of the department that owns the
data decides, and may allow fewer files or fewer hours. The files are fixed when it is allowed, and every opening is written down. What you read is what you
hold in memory or save yourself: the platform no longer sees a copy you save.
"""

from __future__ import annotations

import time

import httpx


class MunitasError(Exception):
    """The platform said no, with its reasons."""


class FileErased(MunitasError):
    """The person this file is about was erased, so it cannot be opened."""


def _reader():
    """The one reader of a file, shared with custom pipeline steps. Imported when a file is first opened, so the rest of this library needs nothing else."""
    try:
        import munitas_step
    except ImportError as exc:
        raise MunitasError("reading files needs munitas_step.py beside munitas_client.py: it is the reader that custom pipeline steps use as well") from exc
    return munitas_step


def _require_https(api: str, allow_http: bool) -> None:
    """A pass or a session sent over plain http can be read by anybody on the way. Local addresses are fine: they never leave the machine."""
    from urllib.parse import urlparse

    host = (urlparse(api).hostname or "").lower()
    if urlparse(api).scheme == "https" or allow_http or host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost"):
        return
    raise MunitasError(f"{api} is not https, so what is sent could be read on the way. Use an https address (or pass allow_http=True if you are sure of the network)")


class Munitas:
    def __init__(self, api: str, session_token: str | None = None, *, allow_http: bool = False):
        self.api = api.rstrip("/")
        _require_https(self.api, allow_http)
        self._pass = session_token
        # Set only for a notebook that signed in: the renewal token, and when the pass next needs renewing. Held in memory and nowhere else.
        self._refresh: str | None = None
        self._pass_until = 0.0
        self._me: dict | None = None

    @classmethod
    def sign_in(cls, api: str, label: str | None = None, *, show=print, wait: float | None = None, allow_http: bool = False) -> "Munitas":
        """Sign this notebook in as you. Shows a code and an address, waits while you approve it in the console, and returns a ready `Munitas`.

        `label` is what the console shows you when you approve, so you can tell this notebook from another (it defaults to this machine's name). `show` is
        where the instructions go (print, or a function that displays them). Raises MunitasError if you refuse, if it is not approved in time, or if the
        platform cannot be reached."""
        import socket

        me = cls(api, allow_http=allow_http)
        name = label or f"{socket.gethostname()} (Python notebook)"
        r = httpx.post(f"{me.api}/notebook/v1/sign-in", json={"label": name}, timeout=60.0)
        me._raise_for(r)
        began = r.json()
        show(f"To sign this notebook in as you, open {began['verification_uri_complete']} and type the code {began['user_code']}. "
             f"Check that the notebook it names is {name!r}. It waits for you up to {began['expires_in'] // 60} minutes.")
        interval = max(5, int(began.get("interval", 5)))
        give_up = time.monotonic() + (wait if wait is not None else began["expires_in"])
        while time.monotonic() < give_up:
            time.sleep(interval)
            got = httpx.post(f"{me.api}/notebook/v1/sign-in/token", json={"device_code": began["device_code"]}, timeout=60.0)
            if got.status_code == 200:
                me._take(got.json())
                return me
            error = got.json().get("error") if got.headers.get("content-type", "").startswith("application/json") else None
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error == "access_denied":
                raise MunitasError("the person refused this sign-in")
            if error == "expired_token":
                raise MunitasError("nobody approved this sign-in in time. Run sign_in again")
            raise MunitasError(f"the sign-in failed: HTTP {got.status_code} {got.text[:200]}")
        raise MunitasError("nobody approved this sign-in in time. Run sign_in again")

    def _take(self, got: dict) -> None:
        self._pass = got["access_token"]
        self._refresh = got.get("refresh_token", self._refresh)
        self._pass_until = time.monotonic() + int(got.get("expires_in", 900))

    def _auth(self) -> dict:
        """The header for the next request. A notebook's pass is renewed first when it is about to run out."""
        if self._refresh is not None and time.monotonic() >= self._pass_until - 60:
            r = httpx.post(f"{self.api}/notebook/v1/sign-in/refresh", json={"refresh_token": self._refresh}, timeout=60.0)
            if r.status_code != 200:
                self._pass = self._refresh = None
                raise MunitasError("this notebook's sign-in has ended (it ran out, or the person ended it). Run Munitas.sign_in again")
            self._take(r.json())
        if not self._pass:
            raise MunitasError("this notebook is not signed in. Run Munitas.sign_in first")
        return {"Authorization": f"Bearer {self._pass}"}

    def sign_out(self) -> None:
        """End this notebook's sign-in now. The pass and the renewal token stop at once."""
        self._call("POST", f"/notebook-sign-ins/{self.me['session_id']}/revoke")
        self._pass = self._refresh = None
        self._me = None

    @property
    def tenant(self) -> str:
        """The organisation this person belongs to, as the platform records it."""
        return self.me["tenant_id"]

    @property
    def me(self) -> dict:
        if self._me is None:
            self._me = self._call("GET", "/auth/whoami")
        return self._me

    # ------------------------------------------------------------- plumbing --

    def _call(self, method: str, path: str, **kwargs) -> dict:
        r = httpx.request(method, f"{self.api}{path}", headers=self._auth(), timeout=60.0, **kwargs)
        self._raise_for(r)
        return r.json() if r.content else {}

    @staticmethod
    def _raise_for(r) -> None:
        if r.status_code >= 400:
            try:
                body = r.json()
                detail = body.get("detail", body)
                reasons = detail.get("reasons") if isinstance(detail, dict) else None
                text = "; ".join(reasons) if reasons else str(detail)
            except ValueError:
                text = r.text[:300]
            raise MunitasError(f"HTTP {r.status_code}: {text}")

    # ------------------------------------------------------------- datasets --

    def dataset(self, name: str) -> dict:
        found = self._call("GET", "/datasets", params={"q": name})["datasets"]
        for row in found:
            if row["name"] == name:
                return row
        raise MunitasError(f"no dataset called {name!r}")

    def latest_version(self, name: str) -> dict:
        d = self.dataset(name)
        versions = self._call("GET", f"/datasets/{d['id']}/versions")
        newest = max(versions, key=lambda v: v["version"])
        return {**newest, "id": newest["dataset_version_id"]}

    # --------------------------------------------------------------- access --

    def request_access(self, dataset: str, purpose: str, justification: str, hours: int = 2) -> str:
        """Ask for access to the newest version of a dataset. Returns the request id."""
        v = self.latest_version(dataset)
        body = {"tenant_id": self.tenant, "principal": self._who(), "dataset_version_id": v["id"],
                "purpose": purpose, "justification": justification, "ttl_hours": hours}
        return self._call("POST", "/leases/requests", json=body)["id"]

    def approve(self, request_id: str) -> str:
        """Approve a request as the custodian of the data. Returns the lease id."""
        r = self._call("POST", f"/leases/requests/{request_id}/approve")
        return r.get("lease_id") or r.get("id")

    def revoke(self, lease_id: str) -> None:
        self._call("POST", f"/leases/{lease_id}/revoke")

    def _who(self) -> str:
        return self.me["id"]

    # ---------------------------------------------------------------- files --

    def _files_version(self, dataset: str | None, version_id: str | None) -> str:
        if version_id:
            return version_id
        if not dataset:
            raise MunitasError("name the dataset, or pass version_id=")
        return self.latest_version(dataset)["id"]

    def files_token(self, dataset: str | None = None, *, version_id: str | None = None) -> str:
        """A token that opens only the files your notebook permission names, on one version, for at most an hour. Asked for again when it runs out.

        Refused when you hold no permission to open these files in a notebook, and the reason says how to ask for one."""
        vid = self._files_version(dataset, version_id)
        return self._call("POST", f"/dataset-versions/{vid}/files-token")["token"]

    def _files_call(self, vid: str, method: str, path: str) -> dict:
        """One request to the notebook routes with this version's files token, renewed once when it has run out."""
        tokens = self.__dict__.setdefault("_files_tokens", {})
        for attempt in (0, 1):
            if vid not in tokens:
                tokens[vid] = self.files_token(version_id=vid)
            r = httpx.request(method, f"{self.api}{path}", headers={"Authorization": f"Bearer {tokens[vid]}"}, timeout=60.0)
            if r.status_code == 401 and attempt == 0:
                del tokens[vid]
                continue
            return self._answer(r)
        raise MunitasError("the files token could not be renewed")

    @staticmethod
    def _answer(r) -> dict:
        if r.status_code >= 400:
            try:
                body = r.json()
                detail = body.get("detail", body)
                reasons = detail.get("reasons") if isinstance(detail, dict) else None
                text = "; ".join(reasons) if reasons else str(detail)
            except ValueError:
                text = r.text[:300]
            if r.status_code == 410:
                raise FileErased(text)
            raise MunitasError(f"HTTP {r.status_code}: {text}")
        return r.json() if r.content else {}

    def list_files(self, dataset: str | None = None, *, version_id: str | None = None) -> list[dict]:
        """The files your notebook permission names on the newest version of `dataset` (or on `version_id`): each one's `record_id`, its `bytes`, its
        `content_type` and whether the person it is about was `erased`. Opens nothing, so nothing is written down."""
        vid = self._files_version(dataset, version_id)
        return self._files_call(vid, "GET", "/notebook/v1/files")["files"]

    def open_file(self, record_id: str, dataset: str | None = None, *, version_id: str | None = None):
        """A file as a file to read: seekable, read by blocks as it is needed. Opening it is written down, naming you and this notebook.

        Raises FileErased when the person it is about was erased, and MunitasError when your permission does not name this file or has ended."""
        vid = self._files_version(dataset, version_id)
        reader = _reader()

        def open_once() -> dict:
            return self._files_call(vid, "POST", f"/notebook/v1/files/{record_id}/open")

        opened = open_once()
        held = {"handle": opened["handle"]}

        def fetch(offset: int, length: int) -> bytes:
            for attempt in (0, 1):
                r = httpx.get(f"{self.api}/record-stream/{held['handle']}", headers={"Range": f"bytes={offset}-{offset + length - 1}"}, timeout=120.0)
                if r.status_code == 401 and attempt == 0:           # the handle lasts minutes: open the file again, which is written down again
                    held["handle"] = open_once()["handle"]
                    continue
                if r.status_code in (200, 206):
                    return r.content
                self._answer(r)
            raise MunitasError(f"{record_id} could not be read")

        return reader.RangeFile(int(opened["bytes"]), fetch)

    def read_file(self, record_id: str, dataset: str | None = None, *, version_id: str | None = None) -> bytes:
        """The whole file, as bytes in memory."""
        with self.open_file(record_id, dataset, version_id=version_id) as source:
            return source.read()

    def save_file(self, record_id: str, path: str, dataset: str | None = None, *, version_id: str | None = None) -> int:
        """The whole file, written to `path` in pieces. Returns its size. What you save is yours to look after: the platform no longer sees it."""
        total = 0
        block = _reader().BLOCK
        with self.open_file(record_id, dataset, version_id=version_id) as source, open(path, "wb") as out:
            while True:
                piece = source.read(block)
                if not piece:
                    return total
                out.write(piece)
                total += len(piece)

    # -------------------------------------------------------------- catalog --

    def catalog_token(self, purpose: str, hours: float = 8) -> str:
        """A token for DuckDB, PyIceberg or any Iceberg client. Shown once.
        The purpose is the sentence your access was approved for."""
        return self._call("POST", "/iceberg/tokens", json={"purpose": purpose, "hours": hours})["token"]

    def attach_duckdb(self, token: str, tenant: str | None = None):
        """A DuckDB connection with this organisation's catalog attached as `lake`."""
        import duckdb

        con = duckdb.connect()
        con.execute("INSTALL iceberg; LOAD iceberg;")
        con.execute(f"ATTACH '{tenant or self.tenant}' AS lake "
                    f"(TYPE ICEBERG, ENDPOINT '{self.api}/iceberg', TOKEN '{token}')")
        return con

    # ------------------------------------------------------------ derivation --

    def draft(self, *, inputs: list[dict], sql: str, name: str, primary_key: list[str], purpose: str) -> dict:
        """Ask what a query would produce. Runs nothing and registers nothing."""
        return self._call("POST", "/derivations", json={
            "inputs": inputs, "sql": sql, "target_name": name, "primary_key": primary_key, "purpose": purpose})

    def confirm(self, draft: dict, sensitivities: dict | None = None) -> dict:
        """Register the result and start the run. Sensitivities may be raised, never lowered."""
        return self._call("POST", f"/derivations/{draft['id']}/confirm",
                          json={"sensitivities": sensitivities or {}})

    def derive(self, *, inputs: list[dict], sql: str, name: str, primary_key: list[str], purpose: str,
               sensitivities: dict | None = None, wait: bool = True, timeout: float = 300,
               on_draft=None) -> dict:
        """Make a new dataset from a query: draft, show it to `on_draft`, confirm, wait.

        The query runs on the platform, in a sandbox, never here.
        """
        draft = self.draft(inputs=inputs, sql=sql, name=name, primary_key=primary_key, purpose=purpose)
        if on_draft:
            on_draft(draft)
        confirmed = self.confirm(draft, sensitivities)
        # The platform may answer with an earlier request of the same person for
        # the same result, so follow the id it returns, not the draft's.
        return self.wait(confirmed["id"], timeout) if wait else confirmed

    def derivation(self, derivation_id: str) -> dict:
        return self._call("GET", f"/derivations/{derivation_id}")

    def wait(self, derivation_id: str, timeout: float = 300) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            d = self.derivation(derivation_id)
            if d["status"] in ("succeeded", "failed"):
                return d
            if time.monotonic() > deadline:
                raise MunitasError(f"the derivation is still {d['status']} after {timeout:.0f} seconds")
            time.sleep(2)

    def lineage(self, version_id: str) -> dict:
        return self._call("GET", f"/lineage/{version_id}", params={"tenant_id": self.tenant})
