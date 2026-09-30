"""Fetching a HuggingFace dataset repo, as Temporal activities.

Everything here used to run inline inside one API request
(`platform/api/app/ingest.py`'s old `fetch_huggingface()`). Moved out
whole, not just relocated: the API only starts this job and returns, so a
slow or oversized fetch cannot hold a browser tab or take the API process
down with it (which is exactly what happened before this existed).

A HuggingFace token is never returned from an activity or passed as a
workflow parameter. Both land in Temporal's durable, replicated history,
the same reason `workflows.py`'s own docstring gives for keeping
transcripts out of activity return values. Each activity that needs one
resolves it itself, for the duration of the one request that needs it, and
lets it go.
"""

from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx
import psycopg
from temporalio import activity
from temporalio.exceptions import ApplicationError

from . import config, platform_client as cp
from .hf_licenses import lookup as lookup_license

HUGGINGFACE = "https://huggingface.co"
MAX_HUGGINGFACE_FILES = 200
MAX_UPLOAD_BYTES = 512 * 1024 * 1024


@contextmanager
def _db():
    with psycopg.connect(config.PG_DSN, autocommit=True) as conn:
        yield conn


def _token_for(fetched_by: str) -> str | None:
    response = httpx.get(f"{config.API}/directory/{fetched_by}/huggingface-token/secret",
                         headers={"x-worker-token": config.WORKER_TOKEN},
                         timeout=10.0, verify=config.api_verify())
    response.raise_for_status()
    return response.json().get("token")


def _token_still_works(token: str) -> bool:
    """Whether HuggingFace still honours this token, checked right now.

    The platform only ever validates a token once, at the moment somebody
    connects it (`external_accounts.py`'s `connect_huggingface`); nothing
    re-checks it afterwards, so a token revoked or expired on HuggingFace's
    own side looks identical to a perfectly good one that simply lacks
    access to one particular gated repo -- both come back as a 401 or 403
    from the listing or download call that found it. Reusing the same
    `whoami-v2` check here, at the point a fetch actually fails, is what
    tells the two apart: a dead token fails this too, a merely-unauthorised
    one still passes it.
    """
    try:
        who = httpx.get(
            f"{HUGGINGFACE}/api/whoami-v2",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15.0,
        )
    except httpx.HTTPError:
        # Can't tell either way; assume it still works rather than sending
        # someone to reconnect an account that a network blip, not a dead
        # token, is actually at fault for.
        return True
    return who.status_code == 200


def _headers(fetched_by: str) -> dict:
    token = _token_for(fetched_by)
    return {"Authorization": f"Bearer {token}"} if token else {}


def _terminal(message: str) -> ApplicationError:
    """A failure that retrying will not fix: a repo that does not exist stays
    that way. Marked non-retryable so Temporal does not spend its retry
    budget on something no attempt will change."""
    return ApplicationError(message, non_retryable=True)


@activity.defn
def prepare_fetch(params: dict) -> dict:
    """List the repo, look up its licence, and say what is left to fetch.

    Everything here is one or two lightweight metadata calls, not a file
    transfer, so it stays a single activity rather than one per HTTP call.
    """
    repo_id, revision, path = params["repo_id"], params["revision"], params["path"]
    fetched_by, dataset_id = params["fetched_by"], params["dataset_id"]
    headers = _headers(fetched_by)

    listing_url = f"{HUGGINGFACE}/api/datasets/{repo_id}/tree/{revision}/{path}".rstrip("/")
    try:
        listing = httpx.get(listing_url, params={"recursive": "true"}, headers=headers, timeout=15.0)
    except httpx.HTTPError as exc:
        raise ApplicationError(f"could not reach HuggingFace: {exc}") from exc

    if listing.status_code in (401, 403, 404):
        token = _token_for(fetched_by)
        if token and _token_still_works(token):
            raise _terminal(
                f"{repo_id!r} is gated, and the connected HuggingFace account has "
                "not been granted access to it; request access on huggingface.co, "
                "or use an account that already has it"
            )
        if token:
            raise _terminal(
                "the connected HuggingFace account's token no longer works "
                "(revoked or expired); reconnect it, or use a public repository"
            )
        raise _terminal(
            f"{repo_id!r} requires authentication this platform does not have; "
            "connect a HuggingFace account, or use a public repository"
        )
    if listing.status_code >= 400:
        raise ApplicationError(f"HuggingFace returned {listing.status_code} listing that repo")

    entries = [e for e in listing.json() if e.get("type") == "file"]
    if not entries:
        raise _terminal("that path holds no files")
    if len(entries) > MAX_HUGGINGFACE_FILES:
        raise _terminal(
            f"{len(entries)} files is over the {MAX_HUGGINGFACE_FILES} file "
            "limit; narrow the path to fetch fewer at a time"
        )
    oversized = [e["path"] for e in entries if e.get("size", 0) > MAX_UPLOAD_BYTES]
    if oversized:
        raise _terminal(
            f"{oversized[0]!r} is over the {MAX_UPLOAD_BYTES} byte limit"
        )

    tag = None
    try:
        info = httpx.get(f"{HUGGINGFACE}/api/datasets/{repo_id}", headers=headers, timeout=15.0)
        if info.status_code == 200:
            tag = info.json().get("cardData", {}).get("license")
    except httpx.HTTPError:
        pass
    terms = lookup_license(tag)

    with _db() as conn:
        already = {
            row[0] for row in conn.execute(
                "select locator from dataset_source where dataset_id = %s and kind = 'huggingface'",
                (dataset_id,),
            ).fetchall()
        }

    remaining = [
        {"path": e["path"], "size": e.get("size", 0)}
        for e in entries
        if f"{repo_id}/{e['path']} @ {revision}" not in already
    ]

    with _db() as conn:
        conn.execute(
            "update huggingface_fetch_job set files_total = %s, bytes_total = %s where id = %s",
            (len(entries), sum(e.get("size", 0) for e in entries), params["job_id"]),
        )

    # Asked with the tenant this specific dataset actually belongs to, because
    # a dataset fetched from the console can belong to any of them.
    #
    # The bucket is taken from the same answer. This used to upload to a
    # constant in the worker's own config while asking for a tenant-specific
    # prefix, which was correct only for tenants on the shared bucket and put
    # everybody else's files in the wrong one with nothing reporting it. That
    # constant no longer exists.
    resp = httpx.get(
        f"{config.API}/datasets/{dataset_id}/next-version",
        params={"tenant_id": params["tenant_id"]}, timeout=15.0,
        verify=config.api_verify(),
    )
    resp.raise_for_status()
    where = resp.json()
    prefix, bucket = where["storage_prefix"], where["bucket"]

    return {
        "entries": remaining,
        "files_total": len(entries),
        "already_had": len(entries) - len(remaining),
        "license_tag": tag,
        "license_export_unmodified": terms.export_unmodified,
        "license_export_modified": terms.export_modified,
        "storage_prefix": prefix,
        "bucket": bucket,
    }


@activity.defn
def fetch_one_file(params: dict) -> dict:
    """Download one file, verify it, and record it.

    `hf_hub_download` does its own resumable, retried transfer to local
    disk; nothing here holds the file in memory at any point, the same
    reasoning that already fixed the API's own buffering bug. Verifies the
    downloaded size against what the listing reported before trusting it
    is complete.
    """
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import HfHubHTTPError

    repo_id, revision, entry_path = params["repo_id"], params["revision"], params["entry_path"]
    dataset_id, fetched_by = params["dataset_id"], params["fetched_by"]
    job_id, storage_prefix = params["job_id"], params["storage_prefix"]
    expected_size = params.get("expected_size", 0)
    token = _token_for(fetched_by)

    activity.heartbeat(f"downloading {entry_path}")
    try:
        local_path = hf_hub_download(
            repo_id=repo_id,
            filename=entry_path,
            revision=revision,
            repo_type="dataset",
            token=token,
            local_dir=str(config.WORK / "hf-ingest" / job_id),
        )
    except HfHubHTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in (401, 403):
            # The listing can be public while individual files are gated
            # file-by-file, which is a real shape a repo can take, not a
            # hypothetical: `raphaelmerx/openwho` lists its files openly but
            # refuses most of them without a granted account. Marked
            # non-retryable: retrying does not change who has access.
            if token and _token_still_works(token):
                raise _terminal(
                    f"{entry_path!r} in {repo_id!r} is gated, and the connected "
                    "HuggingFace account has not been granted access to it"
                ) from exc
            if token:
                raise _terminal(
                    "the connected HuggingFace account's token no longer works "
                    "(revoked or expired); reconnect it"
                ) from exc
            raise _terminal(
                f"{entry_path!r} in {repo_id!r} requires authentication this "
                "platform does not have; connect a HuggingFace account"
            ) from exc
        raise
    activity.heartbeat(f"downloaded {entry_path}")

    path = Path(local_path)
    actual_size = path.stat().st_size
    if expected_size and actual_size != expected_size:
        raise ApplicationError(
            f"{entry_path!r} arrived as {actual_size} bytes, HuggingFace listed "
            f"{expected_size}; the transfer did not complete cleanly"
        )

    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum = digest.hexdigest()

    filename = entry_path.rsplit("/", 1)[-1]
    key = f"{storage_prefix}/{filename}"
    # pipeline_action no longer holds standing Write (see
    # docs/internal/design/write-credential-rationale.md), so this proves the real
    # job asking, the same task_credential.py shape action_run/pipeline_run
    # already use, minted once by ingest.py's fetch_huggingface at the
    # moment this job began and threaded through unchanged since. Minted
    # fresh per file rather than once per job: nothing here can hold a
    # boto3 client across the separate activity invocations Temporal makes
    # for each file, and a write_grant row for the same job and prefix is
    # idempotent (schema.sql's own unique constraint), so re-asking costs a
    # cheap extra round trip, not a second grant.
    write_client = cp.s3_scoped_write(
        params["task_credential"], params["dataset_id"], params["tenant_id"],
        f"{params['tenant_id']}-pipeline",
        f"huggingface fetch job {job_id}: write {filename!r}",
    )
    write_client.upload_file(str(path), params["bucket"], key)
    path.unlink(missing_ok=True)

    locator = f"{repo_id}/{entry_path} @ {revision}"
    with _db() as conn:
        conn.execute(
            """insert into dataset_source
                 (id, dataset_id, kind, locator, licence, checksum, bytes, fetched_by)
               values (%s, %s, 'huggingface', %s, %s, %s, %s, %s)""",
            (str(uuid.uuid4()), dataset_id, locator, params.get("license_tag"),
             checksum, actual_size, fetched_by),
        )
        conn.execute(
            """update huggingface_fetch_job
                 set files_done = files_done + 1, bytes_done = bytes_done + %s
               where id = %s""",
            (actual_size, job_id),
        )

    return {"filename": filename, "bytes": actual_size, "checksum": checksum}


@activity.defn
def finalize_job(params: dict) -> None:
    """Fold the licence into the dataset, and close out the job.

    The same update `ingest.py`'s old inline version ran once per whole
    fetch, unchanged: `external_licensed` when the licence grants either
    export aspect, `internal_regulated` when it grants neither, only while
    nobody has confirmed a sensitivity claim yet.
    """
    dataset_id = params["dataset_id"]
    tag = params.get("license_tag")
    export_unmodified = bool(params.get("license_export_unmodified"))
    export_modified = bool(params.get("license_export_modified"))
    derived_provenance = "external_licensed" if (export_unmodified or export_modified) else "internal_regulated"

    with _db() as conn:
        conn.execute(
            """update dataset set
                 license_tag = %s,
                 license_export_unmodified = %s,
                 license_export_modified = %s,
                 provenance_registered_as = case
                   when provenance <> %s then provenance else provenance_registered_as end,
                 provenance_overridden_at = case
                   when provenance <> %s then now() else provenance_overridden_at end,
                 provenance = %s
               where id = %s and classification_confirmed_by is null""",
            (tag, export_unmodified, export_modified,
             derived_provenance, derived_provenance, derived_provenance, dataset_id),
        )
        conn.execute(
            """update dataset set declaration_basis = 'verified_source'
               where id = %s and declaration_basis = 'asserted'
                 and classification_confirmed_by is null""",
            (dataset_id,),
        )
        conn.execute(
            """update huggingface_fetch_job
                 set status = 'succeeded', ended_at = now(),
                     files_total = %s, bytes_total = bytes_done
               where id = %s""",
            (params.get("files_total"), params["job_id"]),
        )


@activity.defn
def fail_job(params: dict) -> None:
    """Mark a job failed, so a crash anywhere in the workflow still leaves a
    row the console can show, not a `running` job that never moves again."""
    with _db() as conn:
        conn.execute(
            """update huggingface_fetch_job
                 set status = 'failed', error = %s, ended_at = now()
               where id = %s and status = 'running'""",
            (params["error"][:2000], params["job_id"]),
        )


@activity.defn
def cancel_job(params: dict) -> None:
    """Mark a job cancelled, distinct from failed: nothing went wrong, the
    console asked for it to stop. Files already recorded in `dataset_source`
    before the cancellation reached the workflow are left as they are, so a
    later fetch of the same repo still skips them rather than re-fetching
    what already landed."""
    with _db() as conn:
        conn.execute(
            """update huggingface_fetch_job
                 set status = 'cancelled', ended_at = now()
               where id = %s and status = 'running'""",
            (params["job_id"],),
        )
