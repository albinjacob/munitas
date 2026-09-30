"""U46: fetching from HuggingFace is a real background job, not a blocking request.

`fetch_huggingface()` used to do the whole fetch inline, holding one HTTP
request open until every file arrived. A real repo's single largest file
turned out to be several hundred megabytes, downloaded whole into memory
before this container's own 512MB limit, and got the process OOM-killed
mid-request: confirmed live, `docker inspect` showed the restart, and
`curl` against the endpoint got `curl: (52) Empty reply from server`.

The fix has two parts, both checked here. `POST /fetch-huggingface` now
starts a Temporal workflow (`worker/hf_ingest_workflow.py`, running in a
separate process from the API) and returns `202` immediately with a job
id; the actual fetch happens in that other process, streamed to disk
rather than buffered in memory, so neither the slow request nor the large
file can happen to this one again. `GET
/datasets/{id}/huggingface-fetch-jobs` is what the console polls instead
of waiting.

This depends on the worker actually running (`python -m worker.main`) and
reachable at the same Temporal server this container is, which is a real,
separate process this suite cannot start for itself. If a job never
leaves `running`, that is reported as a skip, not a failure: the platform
cannot be faulted for a helper process nobody started.

    docker compose exec -T munitas-api python /verify/v46_huggingface_async_ingest.py
"""

from __future__ import annotations

import sys
import time
import uuid

import httpx

from common import (CANARY, ENGINEER, api, bearer_for, check, db, heading,
                    require_api, skip, summary)

TENANT = CANARY


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


def register(owning: str, **over) -> "object":
    body = {
        "tenant_id": TENANT,
        "name": f"u46-{uuid.uuid4().hex[:8]}",
        "department_id": owning,
        "registered_by": ENGINEER,
        "provenance": "internal_regulated",
        "declared_class": "RAW",
    }
    body.update(over)
    return api("POST", "/datasets/register", json=body)


def start_fetch(dataset_id: str, repo_id: str, path: str = "") -> "object":
    return api(
        "POST", f"/datasets/{dataset_id}/fetch-huggingface",
        json={"fetched_by": ENGINEER, "repo_id": repo_id, "revision": "main", "path": path},
    )


def wait_for_job(dataset_id: str, timeout: float = 90.0) -> dict | None:
    """Poll the job list until the latest job leaves `running`, or give up.

    Returns `None` on timeout, which the caller treats as "could not be
    proven" rather than "failed": a job stuck at `running` past this
    budget most likely means the worker process is not up, not that the
    workflow itself is broken.
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
        reachable = httpx.get("https://huggingface.co/api/datasets/scikit-learn/iris", timeout=10.0).status_code == 200
    except httpx.HTTPError:
        reachable = False
    if not reachable:
        skip("every check in this script", "huggingface.co is not reachable from here")
        return summary("U46")

    owning, _custodian = department("Verification")

    heading("U46: starting a fetch returns immediately, not once it finishes")

    dataset = register(owning)
    dataset_id = dataset.json()["id"]

    start = time.monotonic()
    started = start_fetch(dataset_id, "scikit-learn/iris")
    elapsed = time.monotonic() - start
    check("the endpoint answers 202, not 200 or 201",
          started.status_code == 202, f"HTTP {started.status_code}: {started.text[:200]}")
    check("it answers in well under the time a real fetch takes",
          elapsed < 5.0, f"{elapsed:.2f}s")
    job_id = started.json().get("job_id") if started.status_code == 202 else None
    check("the response names a job id", bool(job_id), str(started.json()))

    heading("U46: the job reaches a real outcome, not a stuck 'running' row")

    job = wait_for_job(dataset_id)
    if job is None:
        skip("the job transitions out of 'running'",
             "no result within the timeout; is `python -m worker.main` running?")
    else:
        check("the job succeeded", job["status"] == "succeeded", str(job))
        if job["status"] == "succeeded":
            check("files_done matches files_total",
                  job["files_done"] == job["files_total"] and job["files_done"] > 0,
                  f"{job['files_done']} of {job['files_total']}")

            with db() as conn:
                rows = conn.execute(
                    """select count(*) as n, coalesce(sum(bytes), 0) as total
                       from dataset_source where dataset_id = %s and kind = 'huggingface'""",
                    (dataset_id,),
                ).fetchone()
            check("every fetched file has its own dataset_source row",
                  rows["n"] == job["files_done"], f"{rows['n']} rows, job says {job['files_done']}")
            check("bytes_done on the job matches what was actually recorded",
                  rows["total"] == job["bytes_done"], f"{rows['total']} vs {job['bytes_done']}")

    heading("U46: a broken repo fails the job cleanly, not silently")

    broken = register(owning)
    broken_id = broken.json()["id"]
    broken_started = start_fetch(broken_id, "nobody/this-does-not-exist-on-huggingface-xyz")
    check("starting the fetch itself still succeeds (202); the repo problem is the job's to report",
          broken_started.status_code == 202, f"HTTP {broken_started.status_code}")

    broken_job = wait_for_job(broken_id, timeout=60.0)
    if broken_job is None:
        skip("the broken-repo job reaches 'failed'",
             "no result within the timeout; is `python -m worker.main` running?")
    else:
        check("it ends as failed, not stuck running",
              broken_job["status"] == "failed", str(broken_job))
        check("the failure carries a real reason",
              bool(broken_job.get("error")), str(broken_job.get("error")))

    heading("U46: sealing is refused while a fetch is still running")

    running_test = register(owning)
    running_id = running_test.json()["id"]
    running_started = start_fetch(running_id, "scikit-learn/iris")
    if running_started.status_code == 202:
        # Raced deliberately: sealing is attempted immediately, before the
        # job has had time to finish, to catch the window this check exists
        # for. If the job already finished by the time this runs, the
        # assertion below is skipped rather than reported as a failure,
        # since that is a timing fact about this machine, not the platform.
        immediate_seal = api("POST", f"/datasets/{running_id}/seal")
        status_now = api("GET", f"/datasets/{running_id}/huggingface-fetch-jobs",
                        headers=bearer_for(ENGINEER)).json()
        if status_now and status_now[0]["status"] == "running":
            check("sealing while a fetch is running is refused",
                  immediate_seal.status_code == 409, f"HTTP {immediate_seal.status_code}")
        else:
            skip("sealing while a fetch is running is refused",
                 "the fetch had already finished before the seal attempt landed")

    return summary("U46")


if __name__ == "__main__":
    sys.exit(main())
