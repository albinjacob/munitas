"""U43: a dataset fetched from HuggingFace earns its own trust, rather than
being told to assume it.

`source_kind: "huggingface"` on `/datasets/register` used to grant
`verified_source` immediately, from the label alone. That trusted a string
the caller supplied instead of checking anything: the same shape of bug this
project has already fixed three times over (an agent's identity read from
graph state, an approver taken from any string, a tenant taken from the
request body rather than the directory). `v22_ingest.py`'s U23 checks now
assert the honest version of that: naming HuggingFace is still an assertion
until a fetch actually happens.

This script is what proves the other half: `POST
/datasets/{id}/fetch-huggingface` makes a real request to a real, small,
public HuggingFace dataset repository, and only that upgrades the claim.

The fetch itself now runs as a background job (U46 covers that machinery
directly); this script starts one and waits for it, since what it is
actually proving here is the trust upgrade and the manifest, not the async
plumbing.

Depends on reaching huggingface.co, which nothing else in this suite does.
Skipped by name, not failed, if it is not reachable from wherever this runs.

    docker compose exec -T munitas-api python /verify/v43_huggingface_fetch.py
"""

from __future__ import annotations

import sys
import time
import uuid

import httpx

from common import (CANARY, ENGINEER, api, bearer_for, check, db, heading,
                    require_api, skip, summary)

TENANT = CANARY

# Small and stable: a handful of CSVs, unlikely to be renamed or taken down.
REPO = "xhluca/publichealth-qa"
PATH = "data"
EXPECTED_FILES = 8


def department(name: str) -> tuple[str, str]:
    with db() as conn:
        row = conn.execute(
            "select id, custodian from department where tenant_id = %s and name = %s",
            (TENANT, name),
        ).fetchone()
    if not row:
        raise RuntimeError(
            f"department {name!r} is missing from tenant {TENANT!r}. Apply "
            "infra/postgres/seed-canary.sql."
        )
    return str(row["id"]), row["custodian"]


def register(**over) -> "object":
    body = {
        "tenant_id": TENANT,
        "name": f"hf-fetch-{uuid.uuid4().hex[:8]}",
        "department_id": over.pop("department_id", None),
        "registered_by": ENGINEER,
        "provenance": "external_public",
        "declared_class": "RAW",
    }
    body.update(over)
    return api("POST", "/datasets/register", json=body)


def wait_for_job(dataset_id: str, timeout: float = 90.0) -> dict | None:
    """Poll until the latest fetch job leaves `running`, or give up.

    `None` means the worker (a separate process this suite cannot start
    for itself) did not answer in time, not that anything failed.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = api("GET", f"/datasets/{dataset_id}/huggingface-fetch-jobs",
                  headers=bearer_for(ENGINEER)).json()
        if jobs and jobs[0]["status"] != "running":
            return jobs[0]
        time.sleep(2.0)
    return None


def main() -> int:
    require_api()

    try:
        reachable = httpx.get(f"https://huggingface.co/api/datasets/{REPO}", timeout=10.0).status_code == 200
    except httpx.HTTPError:
        reachable = False
    if not reachable:
        skip("every check in this script", "huggingface.co is not reachable from here")
        return summary("U43")

    owning, custodian = department("Verification")

    heading("U43: fetching real files from a real, public repository")

    registered = register(department_id=owning)
    check("registering at the safe default needs no claim yet",
          registered.json().get("needs_confirmation") is False,
          str(registered.json()))

    # Declaring PUBLISHED is the claim; it is asserted, same as any other, until the
    # fetch below either backs it or does not.
    claimed = api(
        "POST", "/datasets/register",
        json={
            "tenant_id": TENANT, "name": f"hf-fetch-claim-{uuid.uuid4().hex[:8]}",
            "department_id": owning, "registered_by": ENGINEER,
            "provenance": "external_public", "declared_class": "PUBLISHED",
        },
    )
    claimed_id = claimed.json()["id"]
    check("a PUBLISHED claim before any fetch is asserted, not verified",
          claimed.json().get("declaration_basis") == "asserted",
          str(claimed.json().get("declaration_basis")))

    started = api(
        "POST", f"/datasets/{claimed_id}/fetch-huggingface",
        json={"fetched_by": ENGINEER, "repo_id": REPO, "revision": "main", "path": PATH},
    )
    check("starting the fetch returns 202 immediately, not the finished result",
          started.status_code == 202, f"HTTP {started.status_code}: {started.text[:200]}")

    job = wait_for_job(claimed_id) if started.status_code == 202 else None
    if job is None:
        skip("the fetch job completes",
             "no result within the timeout; is `python -m worker.main` running?")
    else:
        check("the job succeeded", job["status"] == "succeeded", str(job))
        check(f"all {EXPECTED_FILES} files in the repo's data/ folder came back",
              job.get("files_done") == EXPECTED_FILES, str(job.get("files_done")))

        with db() as conn:
            row = conn.execute(
                "select declaration_basis, classification_confirmed_by from dataset where id = %s",
                (claimed_id,),
            ).fetchone()
        check("the claim is now backed by the fetch the platform made itself",
              row["declaration_basis"] == "verified_source", str(row))
        check("and needed no custodian to confirm it",
              row["classification_confirmed_by"] is None, str(row))

    with db() as conn:
        rows = conn.execute(
            """select kind, checksum, bytes from dataset_source
               where dataset_id = %s and kind = 'huggingface'""",
            (claimed_id,),
        ).fetchall()
    check("each fetched file left its own dataset_source row, with a checksum",
          len(rows) == EXPECTED_FILES and all(r["checksum"] for r in rows),
          f"{len(rows)} rows")

    heading("U43: fetching again is safe")

    restarted = api(
        "POST", f"/datasets/{claimed_id}/fetch-huggingface",
        json={"fetched_by": ENGINEER, "repo_id": REPO, "revision": "main", "path": PATH},
    )
    check("a second fetch of the same repo and path starts fine",
          restarted.status_code == 202, f"HTTP {restarted.status_code}")

    rejob = wait_for_job(claimed_id) if restarted.status_code == 202 else None
    if rejob is None:
        skip("the repeat fetch job completes",
             "no result within the timeout; is `python -m worker.main` running?")
    else:
        check("it finds nothing new to add",
              rejob["status"] == "succeeded" and rejob.get("files_done") == 0,
              str(rejob))

    with db() as conn:
        n = conn.execute(
            "select count(*) as n from dataset_source where dataset_id = %s and kind = 'huggingface'",
            (claimed_id,),
        ).fetchone()["n"]
    check("the manifest did not double",
          n == EXPECTED_FILES, f"{n} rows after two fetches")

    heading("U43: it seals, and reads as PUBLISHED immediately")

    sealed = api("POST", f"/datasets/{claimed_id}/seal")
    check("the fetched files seal into a version",
          sealed.status_code == 201, f"HTTP {sealed.status_code}: {sealed.text[:200]}")
    if sealed.status_code == 201:
        check(f"the version holds all {EXPECTED_FILES} files",
              sealed.json().get("files") == EXPECTED_FILES, str(sealed.json()))

    waiting = api(
        "GET", "/datasets/awaiting-confirmation",
        params={"tenant_id": TENANT, "custodian": custodian},
        headers=bearer_for(ENGINEER),
    ).json()
    check("it never sat in the custodian's confirmation queue",
          all(d["id"] != claimed_id for d in waiting["items"]),
          f"{waiting['total']} datasets waiting")

    heading("U43: a repository that does not exist fails the job, not the start")

    other = register(department_id=owning)
    other_id = other.json()["id"]
    missing_started = api(
        "POST", f"/datasets/{other_id}/fetch-huggingface",
        json={
            "fetched_by": ENGINEER,
            "repo_id": "nobody/this-does-not-exist-on-huggingface-xyz",
            "revision": "main", "path": "",
        },
    )
    check("starting the fetch still succeeds; the repo's own existence is the job's to check",
          missing_started.status_code == 202, f"HTTP {missing_started.status_code}: {missing_started.text[:200]}")

    missing_job = wait_for_job(other_id, timeout=60.0) if missing_started.status_code == 202 else None
    if missing_job is None:
        skip("a nonexistent repo's job ends as failed, not stuck running",
             "no result within the timeout; is `python -m worker.main` running?")
    else:
        check("a nonexistent repo is a failed job, not a crash",
              missing_job["status"] == "failed", str(missing_job))

    heading("U43: only a registered human may trigger a fetch")

    unregistered = register(department_id=owning)
    bad_caller = api(
        "POST", f"/datasets/{unregistered.json()['id']}/fetch-huggingface",
        json={"fetched_by": "nobody-registered-under-this-name", "repo_id": REPO, "path": PATH},
        # Signed in as a real person, naming somebody who is not them: the platform acts as the signed-in person only.
        headers=bearer_for(ENGINEER),
    )
    check("an unregistered fetched_by is refused before anything starts",
          bad_caller.status_code == 403, f"HTTP {bad_caller.status_code}")

    return summary("U43")


if __name__ == "__main__":
    sys.exit(main())
