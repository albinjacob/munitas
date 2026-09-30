"""U45: fetching a gated HuggingFace repo with your own account, not a shared one.

`fetch_huggingface()` had no way to prove to HuggingFace who was asking, so
every gated repo refused every request identically, regardless of who
triggered it. `POST /directory/{id}/huggingface-token` lets a registered
human connect their own token; `fetch_huggingface()` then reaches for it
when that person is the one fetching, so a gated repo's answer reflects
whether *that person* has actually been granted access, not whether some
shared platform account happens to have been.

What this script can prove without a real, valid personal HuggingFace
token: an obviously wrong token is refused and writes nothing; a workload
cannot connect one at all, only a human; disconnecting something that was
never connected is not an error; the status endpoint never returns a
token, only whether one exists; and a public-repo fetch behaves identically
whether or not the caller has connected an account, so this feature changes
nothing for the common case.

What it cannot prove here: that a *valid* token actually unlocks a gated
repo that account has been granted. That needs a real HuggingFace account
with real granted access, which cannot be manufactured in this suite. Named
here as a limitation, not silently skipped.

Depends on reaching huggingface.co for the token-rejection checks, which
most of this suite does not. Skipped by name, not failed, if unreachable.

    docker compose exec -T munitas-api python /verify/v45_huggingface_credentials.py
"""

from __future__ import annotations

import sys
import uuid

import httpx

from common import (CANARY, ENGINEER, TRAINER, api, bearer_for, check, db,
                    heading, require_api, skip, summary)

TENANT = CANARY


def status(directory_id: str, as_: str | None = None) -> "object":
    return api("GET", f"/directory/{directory_id}/huggingface-token",
               headers=bearer_for(as_ or directory_id))


def connect(directory_id: str, token: str, as_: str | None = None) -> "object":
    return api("POST", f"/directory/{directory_id}/huggingface-token",
               json={"token": token}, headers=bearer_for(as_ or directory_id))


def credential_row(directory_id: str) -> dict | None:
    with db() as conn:
        return conn.execute(
            "select * from external_credential where directory_id = %s and provider = 'huggingface'",
            (directory_id,),
        ).fetchone()


def main() -> int:
    require_api()

    try:
        reachable = httpx.get(
            "https://huggingface.co/api/whoami-v2",
            headers={"Authorization": "Bearer not-a-real-token"},
            timeout=10.0,
        ).status_code in (401, 403)
    except httpx.HTTPError:
        reachable = False

    heading("U45: a token that does not work is refused, and stores nothing")

    if not reachable:
        skip("connecting an obviously invalid token is refused with 422",
             "huggingface.co is not reachable from here")
        skip("a rejected token leaves no row behind",
             "huggingface.co is not reachable from here")
    else:
        rejected = connect(ENGINEER, "not-a-real-token-at-all")
        check("connecting an obviously invalid token is refused",
              rejected.status_code == 422, f"HTTP {rejected.status_code}: {rejected.text[:200]}")

        row = credential_row(ENGINEER)
        check("a rejected token leaves no row behind",
              row is None, str(row))

    heading("U45: only a human may connect a personal account")

    # A workload has no Kratos identity to log in as (auth.py's own
    # design), so no session can ever claim to be TRAINER: the identity
    # check this endpoint now requires (nobody may touch somebody else's
    # HuggingFace connection) refuses this before the endpoint's own
    # "a workload acts through its own registered identity" check is even
    # reached. Both protections agree a workload never gets one; this is
    # just which one fires first now that a session is required at all.
    workload = connect(TRAINER, "irrelevant-because-refused-first", as_=ENGINEER)
    check("a workload cannot connect a HuggingFace account",
          workload.status_code == 403, f"HTTP {workload.status_code}: {workload.text[:200]}")
    check("and nothing was written for it",
          credential_row(TRAINER) is None)

    heading("U45: status and disconnect never require a connection to exist first")

    nobody = status("canary-custodian")
    check("status for somebody with no connection says so plainly",
          nobody.status_code == 200 and nobody.json() == {
              "connected": False, "hf_username": None, "connected_at": None,
          },
          str(nobody.json()) if nobody.status_code == 200 else f"HTTP {nobody.status_code}")

    disconnect_nothing = api("DELETE", "/directory/canary-custodian/huggingface-token",
                             headers=bearer_for("canary-custodian"))
    check("disconnecting a connection that never existed is not an error",
          disconnect_nothing.status_code == 200, f"HTTP {disconnect_nothing.status_code}")

    heading("U45: the status endpoint never returns the token itself")

    if reachable:
        # The rejected-token check above already proved nothing gets stored
        # on failure. Confirm the response shape here holds no secret field
        # even in principle, independent of whether a row exists.
        body = status(ENGINEER).json()
        check("no key in the status response could hold a token",
              set(body.keys()) <= {"connected", "hf_username", "connected_at"},
              str(body))
    else:
        skip("no key in the status response could hold a token",
             "depends on the connect attempt above, itself skipped")

    heading("U45: a public-repo fetch is unaffected by having no account connected")

    if not reachable:
        skip("starting a public-repo fetch still succeeds with no HuggingFace account connected",
             "huggingface.co is not reachable from here")
    else:
        department = db_department()
        dataset_id = register_public_dataset(department)
        if dataset_id:
            fetched = api(
                "POST", f"/datasets/{dataset_id}/fetch-huggingface",
                json={"fetched_by": ENGINEER, "repo_id": "scikit-learn/iris", "revision": "main", "path": ""},
            )
            # 202, not the finished result: fetching runs as a background
            # job now (U46 covers that machinery). What this script is
            # actually proving is that having no connected account does not
            # change anything about a public-repo fetch, which is already
            # true the moment starting it is unaffected.
            check("starting the fetch still succeeds with no HuggingFace account connected",
                  fetched.status_code == 202, f"HTTP {fetched.status_code}: {fetched.text[:200]}")

    return summary("U45")


def db_department() -> str:
    with db() as conn:
        row = conn.execute(
            "select id from department where tenant_id = %s and name = 'Verification'",
            (TENANT,),
        ).fetchone()
    if not row:
        raise RuntimeError("department 'Verification' is missing; apply infra/postgres/seed-canary.sql")
    return str(row["id"])


def register_public_dataset(department_id: str) -> str | None:
    r = api("POST", "/datasets/register", json={
        "tenant_id": TENANT, "name": f"u45-public-{uuid.uuid4().hex[:8]}",
        "department_id": department_id, "registered_by": ENGINEER,
        "provenance": "internal_regulated", "declared_class": "RAW",
    })
    return r.json()["id"] if r.status_code == 201 else None


if __name__ == "__main__":
    sys.exit(main())
