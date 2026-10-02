"""A small client for working with Munitas from a notebook or a script.

    from munitas_client import Munitas
    me = Munitas("http://localhost:8000", session_token)

A session token is the one the console signs a person in with. Everything here
calls the platform's own API as that person, so the platform decides each time
what they may do. Nothing here holds a credential for storage.

What it covers:

  catalog_token / attach_duckdb   open governed tables in DuckDB
  request_access / approve        ask for access below your role, and decide it
  derive                          make a new dataset from a query
  lineage / derivation            see where a version came from

Needs httpx. attach_duckdb also needs duckdb.
"""

from __future__ import annotations

import time

import httpx


class MunitasError(Exception):
    """The platform said no, with its reasons."""


class Munitas:
    def __init__(self, api: str, session_token: str):
        self.api = api.rstrip("/")
        self._headers = {"Authorization": f"Bearer {session_token}"}
        self._me: dict | None = None

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
        r = httpx.request(method, f"{self.api}{path}", headers=self._headers, timeout=60.0, **kwargs)
        if r.status_code >= 400:
            try:
                body = r.json()
                detail = body.get("detail", body)
                reasons = detail.get("reasons") if isinstance(detail, dict) else None
                text = "; ".join(reasons) if reasons else str(detail)
            except ValueError:
                text = r.text[:300]
            raise MunitasError(f"HTTP {r.status_code}: {text}")
        return r.json() if r.content else {}

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
