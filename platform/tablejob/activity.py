"""The activity that writes one job's table.

What this process is allowed to hold: a signed credential for the one job (it arrives in the workflow's input and names the
job and its organisation), and the storage key the control plane makes for that job when asked, which reads and writes the one
folder the version will have and lists object names in its organisation's bucket. Nothing else, so a worker that is compromised,
or that has a bug, can reach one folder of one organisation, and only while that job is active.

The platform decides what the table means: this reads what it is told to read, writes what it is told to write, and reports.
It does not seal a version and it does not decide whether a failed table refuses a seal. The control plane checks what it is told
(it lists and hashes the files itself, and opens the table) before anything is sealed.
"""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

import tablewriter

from . import config
from .workflow import MAX_ATTEMPTS

# How long to wait for the job's key to take effect in storage, which is a matter of seconds after the permissions are
# printed, before giving up and letting the workflow try again.
KEY_WAIT_SECONDS = 180


class Api:
    """The control plane, as one job sees it: every call carries this job's credential and nothing else."""

    def __init__(self, base: str, job_id: str, credential: str):
        self.base, self.job_id = base, job_id
        self.http = httpx.Client(timeout=120.0, headers={"X-Task-Credential": credential})

    def _url(self, tail: str) -> str:
        return f"{self.base}/table-jobs/{self.job_id}/{tail}"

    def work(self) -> dict:
        r = self.http.get(self._url("work"))
        if r.status_code == 409:
            raise ApplicationError("this job has already ended", type="JobEnded", non_retryable=True)
        r.raise_for_status()
        return r.json()

    def credentials(self) -> dict | None:
        r = self.http.post(self._url("credentials"))
        if r.status_code == 202:
            return None
        if r.status_code == 409:
            raise ApplicationError("this job has already ended", type="JobEnded", non_retryable=True)
        r.raise_for_status()
        return r.json()

    def finish(self, **body) -> dict:
        # The control plane lists and hashes every file of the table before it answers, which takes minutes for a table of
        # gigabytes, so this one call is given an hour where the others are given two minutes.
        r = self.http.post(self._url("finish"), json=body, timeout=3600.0)
        r.raise_for_status()
        return r.json()


def _opens(creds: dict, records_key: str) -> bool:
    """Whether storage honours the key yet, tried on the one object the job exists to read. Storage reads its permissions a
    moment after they are printed. (Not a list: a key limited to one folder cannot list.)"""
    props = tablewriter.s3_properties(creds["endpoint"], creds["access_key"], creds["secret_key"])
    try:
        tablewriter.s3_client(props).head_object(Bucket=creds["bucket"], Key=records_key)
        return True
    except Exception:  # noqa: BLE001
        return False


async def _storage_key(api: Api, records_key: str) -> dict:
    """The job's own key, once the control plane says it is in the permissions document, and once storage honours it.
    The waiting is done here, on the event loop, because a heartbeat may only be sent from there."""
    deadline = time.monotonic() + KEY_WAIT_SECONDS
    while True:
        creds = await asyncio.to_thread(api.credentials)
        if creds is not None and await asyncio.to_thread(_opens, creds, records_key):
            return creds
        if time.monotonic() > deadline:
            raise RuntimeError("the job's storage key did not take effect in time")
        activity.heartbeat()
        await asyncio.sleep(3)


def _write(work: dict, creds: dict, cancel: threading.Event) -> tablewriter.Projection:
    props = tablewriter.s3_properties(creds["endpoint"], creds["access_key"], creds["secret_key"])
    return tablewriter.write_table(
        props=props, client=tablewriter.s3_client(props), bucket=work["bucket"], prefix=work["storage_prefix"],
        location=work["location"], namespace=work["dataset_name"], table_name=work["table_name"], contract=work["contract"],
        records_key=work["records_key"], summary_base=work["summary_base"], cfg=tablewriter.Settings(**work["settings"]), cancel=cancel,
        # The key opens one folder and cannot list it. The control plane lists and hashes the files itself, and clears the folder
        # when a write does not finish, so this neither builds a manifest nor tries to remove what a failed attempt wrote.
        manifest=False, cleanup=False)


@activity.defn(name="write_table_job")
async def write_table_job(params: dict) -> dict:
    cfg = config.load()
    # A worker that serves one organisation refuses another's job before it does anything at all. The control plane would
    # not have routed it here, so this is the check of last resort: a mistake in routing must never become a table written
    # by the wrong worker.
    if cfg.tenant and params["tenant_id"] != cfg.tenant:
        raise ApplicationError("this worker serves one organisation and this job belongs to another", type="WrongOrganisation",
                               non_retryable=True)

    api = Api(cfg.api, params["job_id"], params["credential"])
    work = await asyncio.to_thread(api.work)
    creds = await _storage_key(api, work["records_key"])

    cancel = threading.Event()
    writing = asyncio.create_task(asyncio.to_thread(_write, work, creds, cancel))
    try:
        while not writing.done():
            activity.heartbeat()
            await asyncio.wait({writing}, timeout=15)
        projection = writing.result()
    except asyncio.CancelledError:
        # The activity was cancelled: tell the writer to stop, and give it time to remove what it wrote.
        cancel.set()
        await asyncio.wait({writing}, timeout=60)
        raise
    except tablewriter.Skipped as why:
        return await asyncio.to_thread(api.finish, outcome="skipped", reason=f"The table was not written: {why}.", blocking=why.blocking)
    except Exception as exc:  # noqa: BLE001 - what the library raised is reported by kind, never by message
        if activity.info().attempt < MAX_ATTEMPTS:
            raise  # the control plane clears what the attempt wrote before it hands out the work again
        return await asyncio.to_thread(api.finish, outcome="failed", reason=f"Writing the table failed ({type(exc).__name__}).", blocking=True)

    return await asyncio.to_thread(
        api.finish, outcome="written", metadata_location=projection.metadata_location, snapshot_id=projection.snapshot_id,
        record_count=projection.record_count, records_sha256=projection.records_sha256)


@activity.defn(name="report_table_job_failure")
async def report_table_job_failure(params: dict) -> dict:
    """The workflow gave up: tell the control plane, so the job ends and its version number and key are given back instead of
    being held until the job's time runs out."""
    cfg = config.load()
    api = Api(cfg.api, params["job_id"], params["credential"])
    return await asyncio.to_thread(api.finish, outcome="failed", reason=params["reason"], blocking=True)
