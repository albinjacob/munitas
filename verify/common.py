"""Shared helpers for the verification scripts.

One rule governs everything in this directory: report per assertion, never in
aggregate. "The suite passed" and "every claim I meant to test passed" are
different statements, and only the second is worth anything. So each check
prints its own PASS or FAIL line with the value that decided it, and a script
that cannot run a check says SKIP with the reason rather than staying silent.
"""

from __future__ import annotations

import io
import json
import os
import sys
import uuid
import wave
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ports_config import PORTS  # noqa: E402

from markers import COUNTS_MARKER  # noqa: E402

# VERIFY_API is set explicitly, to the container-internal port, in
# docker-compose.yml's munitas-api service -- a script run inside that
# container (docker compose exec, which is how verify/run_all.py runs
# everything) always gets that, regardless of what host port config.json
# publishes it under. This default only matters for a script run directly
# from the host, where the host-published port is the right one to fall
# back to.
API = os.environ.get("MUNITAS_VERIFY_API", f"http://localhost:{PORTS['munitas_api_http']}")
PG_DSN = os.environ["PG_DSN"]
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://seaweedfs:8333")

_results: list[tuple[str, bool | None, str]] = []


def tiny_wav(seconds: float = 0.25, rate: int = 16000) -> bytes:
    """A real, minimal wav file, built rather than faked.

    Several scripts used to upload a name ending .wav whose contents were the
    words "pretend audio bytes". The upload endpoint now reads what it is given
    and refuses a file that contradicts its own name, so those uploads would be
    refused, correctly. Building one real file here means every script says
    "audio" the same way, and a caller that knows how many frames it wrote has
    an independent expectation to compare the platform's answer against.
    """
    frames = int(seconds * rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)
    return buffer.getvalue()


def check(label: str, ok: bool, detail: str = "") -> bool:
    _results.append((label, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def skip(label: str, why: str) -> None:
    """Record a check that could not run.

    Deliberately not counted as a pass. An untested claim reported as green is
    the failure this whole directory exists to prevent.
    """
    _results.append((label, None, why))
    print(f"  [SKIP] {label}  ({why})")


def heading(text: str) -> None:
    print(f"\n{text}\n" + "-" * len(text))


def summary(name: str) -> int:
    passed = sum(1 for _, ok, _ in _results if ok is True)
    failed = sum(1 for _, ok, _ in _results if ok is False)
    skipped = sum(1 for _, ok, _ in _results if ok is None)
    print(f"\n{name}: {passed} passed, {failed} failed, {skipped} skipped")
    if skipped:
        print("  Skipped checks are not passes. See the reasons above.")
    # The same counts as one exact line, for run_all.py to read. A script's exit
    # code cannot say "everything here was skipped", because skipping is not a
    # failure; this can, and it is not scraped out of the sentence above, which
    # someone will reword.
    print(f"{COUNTS_MARKER} " + json.dumps({"passed": passed, "failed": failed, "skipped": skipped},
                                           separators=(",", ":")))
    return 1 if failed else 0


def db():
    # Bounded, so a database that stops answering fails the check rather than
    # hanging it: 10 s to connect, 2 minutes for any one statement.
    return psycopg.connect(PG_DSN, row_factory=dict_row, autocommit=True,
                           connect_timeout=10, options="-c statement_timeout=120000")


def bucket_for(tenant_id: str, backend: str = "seaweedfs") -> str:
    """The bucket this tenant's objects actually live in.

    Reads the row the platform reads. Sealing a version writes no object, so
    on a fresh install a test organisation can hold sealed versions and still
    have no bucket: the platform creates one on an organisation's first
    write. Fixtures here write their objects straight into storage, so when
    the row is missing the platform is asked to create the bucket through
    its own first-write code (seaweed.bucket), the same step
    scripts/seed/reseed-tenant.ps1 takes for the worked examples. Never
    created here directly: a fixture that makes its own bucket proves its
    own setup rather than the platform's behaviour.

    That needs the platform's code, which only the API container has. On the
    host a missing bucket is refused with what to do instead.
    """
    with db() as conn:
        row = conn.execute(
            "select bucket from tenant_storage_provision "
            "where tenant_id = %s and backend = %s",
            (tenant_id, backend),
        ).fetchone()
    if row:
        return row["bucket"]
    if backend == "seaweedfs" and os.path.isdir("/app/app"):
        if "/app" not in sys.path:
            sys.path.insert(0, "/app")
        from app import db as app_db
        from app import seaweed as app_seaweed
        if app_db.pool.closed:
            app_db.pool.open()
        return app_seaweed.bucket(tenant_id)
    raise RuntimeError(
        f"no {backend} bucket recorded for tenant {tenant_id!r}, and nothing "
        "has written for it yet. Run the in-container suite first "
        "(run-verification.ps1), whose first write creates it."
    )


# What the platform's own workers send where they have no login. A few reads of one record answer
# a signed-in person (limited to their own organisation) or a worker presenting this.
WORKER_HEADERS = {"x-worker-token": os.environ.get("MUNITAS_WORKER_TOKEN", "dev-worker-token-not-for-production")}


# How a script authenticates, by route. The platform closed these routes to anonymous callers: the ones the platform's own workers
# use take the worker token, and the ones a person uses take that person's session, and the person is the one the request names
# (registered_by, confirmed_by, fetched_by), because the platform acts as the signed-in person and refuses a name that is not theirs.
# A script that wants to prove a refusal passes its own headers (even an empty set) and is left alone.
WORKER_ROUTES = (
    ("POST", r"/schema-contracts"), ("POST", r"/datasets"), ("POST", r"/action-runs"), ("POST", r"/pipeline-runs"),
    ("POST", r"/pipeline-runs/[^/]+/end"), ("POST", r"/write-credentials"), ("POST", r"/credentials"),
    ("POST", r"/dataset-versions"), ("POST", r"/dataset-versions/[^/]+/promote"), ("POST", r"/records"),
    ("POST", r"/records/[^/]+/open"), ("DELETE", r"/records/[^/]+"), ("GET", r"/policy/roles"),
    ("GET", r"/pipeline/(kinds|served-backends)"), ("GET", r"/datasets/[^/]+/next-version"),
    ("GET", r"/agent-versions/[^/]+/egress-status"),
)
PERSON_ROUTES = (
    ("POST", r"/datasets/register", "registered_by"), ("POST", r"/agents/register", "registered_by"),
    ("POST", r"/agents/[^/]+/versions", "registered_by"), ("POST", r"/agents/[^/]+/versions/upload", "registered_by"),
    ("POST", r"/pipelines/register", "registered_by"), ("POST", r"/pipelines/[^/]+/versions/upload", "registered_by"),
    ("POST", r"/datasets/[^/]+/confirm-classification", "confirmed_by"),
    ("POST", r"/datasets/[^/]+/fetch-huggingface", "fetched_by"),
)

_sessions: dict[str, dict[str, str]] = {}


def _session_for(person: str) -> dict[str, str]:
    """A signed-in session for a seeded person, kept for the run: a check that acts as somebody many times logs in once."""
    if person not in _sessions:
        _sessions[person] = bearer_for(person)
    return _sessions[person]


def acting_headers(method: str, path: str, kwargs: dict) -> dict[str, str] | None:
    import re

    for verb, pattern in WORKER_ROUTES:
        if method == verb and re.fullmatch(pattern, path):
            return WORKER_HEADERS
    for verb, pattern, field in PERSON_ROUTES:
        if method == verb and re.fullmatch(pattern, path):
            body = kwargs.get("json") if isinstance(kwargs.get("json"), dict) else kwargs.get("data")
            if isinstance(body, dict) and body.get(field):
                return _session_for(body[field])
            return None
    # An upload, a seal or a cancel on a dataset is done by the person who registered it.
    m = re.fullmatch(r"/datasets/([^/]+)/(files|seal|seal-audio|huggingface-fetch-jobs/[^/]+/cancel)", path)
    if method == "POST" and m:
        with db() as conn:
            row = conn.execute("select registered_by from dataset where id = %s", (m.group(1),)).fetchone()
        if row and row["registered_by"]:
            return _session_for(row["registered_by"])
    return None


def api(method: str, path: str, **kwargs) -> httpx.Response:
    if "headers" not in kwargs:
        found = acting_headers(method, path, kwargs)
        if found:
            kwargs["headers"] = found
    return httpx.request(method, f"{API}{path}", timeout=15.0, **kwargs)


def s3_client(access_key: str, secret_key: str, session_token: str | None = None,
              endpoint: str | None = None):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint or S3_ENDPOINT,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        aws_session_token=session_token,
        config=Config(signature_version="s3v4", retries={"max_attempts": 1}),
        region_name="us-east-1",
    )


ADMIN = ("munitas-admin", "munitas-admin-secret")

# Where verification writes.
#
# Everything used to go into `t1`, the tenant the demonstration organisation
# lives in, so the console listed seventy fixtures beside two real datasets and
# none of them could be removed: sealed versions cannot be deleted, which is the
# guarantee V1 proves. The fix is not cleanup, it is a separate tenant.
#
# Overridable so a script can name another tenant on purpose. U28 needs exactly
# that, because proving isolation takes two.
CANARY = os.environ.get("MUNITAS_VERIFY_TENANT", "canary")

# The principals seeded by infra/postgres/seed-canary.sql.
#
# Kept here rather than typed into each script because `directory.id` is one
# primary key across every tenant, so these cannot be the demonstration
# organisation's names. A script that hardcoded `svc-trainer` would now be
# naming a principal registered in a different tenant, which is a confusing way
# to fail.
CUSTODIAN = "canary-custodian"
ENGINEER = "canary-engineer"
RESEARCHER = "canary-researcher"
OVERSIGHT = "canary-dpo"
REVIEWER = "canary-reviewer"
PIPELINE = "canary-pipeline"
TRAINER = "canary-trainer"
AGENT = "canary-agent"
ANNOTATION = "canary-annotation"

DEPARTMENT = "Verification"

KRATOS = os.environ.get("MUNITAS_VERIFY_KRATOS", "http://kratos:4433")

# Duplicated from infra/kratos/seed-identities.py, the same convention
# verify/v53_real_authentication.py already uses: these scripts do not
# import the seed script as a library, so known seeded values are typed
# here directly. Centralized in this module (rather than kept private to
# one script, the way v53 did) because roughly a dozen scripts now need a
# real session, not just one.
_EMAIL_BY_DIRECTORY_ID = {
    "cust-hartley": "hartley@health.example",
    "cust-okonjo": "okonjo@health.example",
    "dpo-nakamura": "nakamura@health.example",
    "sam-researcher": "sam@health.example",
    "eng-devi": "devi@health.example",
    "ops-priya": "priya@health.example",
    "ops-ravi": "ravi@health.example",
    "cust-dunmore": "dunmore@harbour.example",
    "dpo-adeyemi": "adeyemi@harbour.example",
    "ana-quinn": "quinn@harbour.example",
    "canary-custodian": "custodian@canary.example",
    "canary-elsewhere": "elsewhere@canary.example",
    "canary-engineer": "engineer@canary.example",
    "canary-researcher": "researcher@canary.example",
    "canary-dpo": "dpo@canary.example",
    "canary-reviewer": "reviewer@canary.example",
    "rev-imani": "imani@health.example",
}
_PASSWORD = "dev-password-not-for-production"


def bearer_for(directory_id: str) -> dict[str, str]:
    """A real Kratos session's bearer header for a seeded identity.

    Uses the API-native login flow (/self-service/login/api), the same one
    v53_real_authentication.py already proved works: it returns a
    session_token usable directly as a bearer token, simpler for a script
    than driving the browser cookie flow. Raises rather than returning a
    header verify scripts should not silently proceed with, since a check
    that runs unauthenticated is a check of the wrong thing.
    """
    email = _EMAIL_BY_DIRECTORY_ID.get(directory_id)
    if not email:
        raise RuntimeError(
            f"{directory_id!r} has no known seeded email in "
            "verify/common.py's _EMAIL_BY_DIRECTORY_ID; add it if this is "
            "a real seeded identity"
        )
    flow = httpx.get(f"{KRATOS}/self-service/login/api", timeout=10.0).json()
    r = httpx.post(
        f"{KRATOS}/self-service/login",
        params={"flow": flow["id"]},
        json={"method": "password", "identifier": email, "password": _PASSWORD},
        headers={"Accept": "application/json"},
        timeout=10.0,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"Kratos login failed for {directory_id!r} ({email}): "
            f"HTTP {r.status_code} {r.text[:200]}"
        )
    token = r.json()["session_token"]
    return {"Authorization": f"Bearer {token}"}


# Names the verification suite mints fresh per run. These are declared
# disposable at creation (purpose `scratch`), which is what lets scripts/admin/tidy-probes.py
# remove them without disabling the immutability rules. Anything else the suite
# creates stays `canary`: writable, reclaimable, and fully immutable, which is
# what v1_immutability.py needs its own fixture to be.
_DISPOSABLE_PREFIXES = (
    "storage-probe-", "ingest-probe-", "legacy-probe-", "scratch-probe-",
    "scratch-empty-", "verify-retired-",
)


def fixture_tenant(tenant_id: str = CANARY) -> str:
    purpose = "scratch" if tenant_id.startswith(_DISPOSABLE_PREFIXES) else "canary"
    with db() as conn:
        conn.execute(
            """insert into tenant (id, isolation_level, key_ref, purpose)
               values (%s, 'shared', %s, %s) on conflict (id) do nothing""",
            (tenant_id, f"key/{tenant_id}", purpose),
        )
    return tenant_id


def fixture_contract(tenant_id: str = CANARY) -> str:
    body = {
        "tenant_id": tenant_id,
        "name": "encounter",
        "fields": [
            {"name": "record_id", "type": "string", "sensitivity": "none", "added_by": "verify"},
            {"name": "transcript", "type": "string", "sensitivity": "phi", "added_by": "verify"},
        ],
        "primary_key": ["record_id"],
    }
    r = api("POST", "/schema-contracts", json=body)
    r.raise_for_status()
    return r.json()["id"]


def fixture_version(tenant_id: str, schema_id: str, klass: str = "RAW",
                    dataset_name: str | None = None) -> dict:
    """Create a fresh dataset and one sealed version in it."""
    name = dataset_name or f"verify-{uuid.uuid4().hex[:8]}"
    r = api("POST", "/datasets", json={"tenant_id": tenant_id, "name": name})
    r.raise_for_status()
    dataset_id = r.json()["id"]

    r = api("POST", "/dataset-versions", json={
        "tenant_id": tenant_id,
        "dataset_id": dataset_id,
        "schema_id": schema_id,
        "visibility_class": klass,
        "object_manifest": [{"key": "part-0.json", "bytes": 128}],
        "record_count": 1,
    })
    r.raise_for_status()
    out = r.json()
    out["dataset_id"] = dataset_id
    out["dataset_name"] = name
    return out


def fixture_department(tenant_id: str, dataset_id: str,
                       name: str = DEPARTMENT) -> str:
    """Attach a dataset to a seeded department, and return its custodian.

    Uses the organisation from `infra/postgres/seed-canary.sql` rather than
    inventing one. An earlier version created a fresh custodian and department on
    every run, which accumulated invented people in the directory and put them in
    front of anyone opening the console.

    Reusing the seed also makes the test more like the thing it stands in for:
    real approvals are made by a custodian who already existed, not by one
    conjured for the occasion.
    """
    with db() as conn:
        row = conn.execute(
            "select id, custodian from department where tenant_id = %s and name = %s",
            (tenant_id, name),
        ).fetchone()

        if not row:
            raise RuntimeError(
                f"department {name!r} is missing from tenant {tenant_id!r}. Apply "
                "infra/postgres/seed-canary.sql, which the schema does not create "
                "because who is accountable is not the platform's to invent."
            )

        conn.execute(
            "update dataset set department_id = %s where id = %s",
            (row["id"], dataset_id),
        )
    return row["custodian"]


# --------------------------------------------------------------- tabular --
#
# A version made of rows, not files: the shape the Iceberg projection writes as a
# table. One field of every kind a contract can name, so a check that passes here
# has seen a string, a float, an integer, a boolean, a list and a dictionary.

TABULAR_FIELDS = [
    {"name": "record_id", "type": "string", "sensitivity": "none", "added_by": "verify"},
    {"name": "transcript", "type": "string", "sensitivity": "phi", "added_by": "verify"},
    {"name": "score", "type": "float", "sensitivity": "none", "added_by": "verify"},
    {"name": "count", "type": "int", "sensitivity": "none", "added_by": "verify"},
    {"name": "ok", "type": "bool", "sensitivity": "none", "added_by": "verify"},
    {"name": "tags", "type": "list", "sensitivity": "quasi", "added_by": "verify"},
    {"name": "detail", "type": "dict", "sensitivity": "none", "added_by": "verify"},
]


def tabular_rows(n: int = 3) -> list[dict]:
    return [
        {"record_id": f"rec-{i}", "transcript": f"synthetic transcript number {i}",
         "score": 0.5 + i, "count": i * 2, "ok": i % 2 == 0,
         "tags": ["a", f"b{i}"], "detail": {"i": i, "nested": {"k": "v"}}}
        for i in range(n)
    ]


def fixture_tabular_contract(tenant_id: str = CANARY) -> str:
    r = api("POST", "/schema-contracts", json={
        "tenant_id": tenant_id, "name": "iceberg_probe",
        "fields": TABULAR_FIELDS, "primary_key": ["record_id"],
    })
    r.raise_for_status()
    return r.json()["id"]


def fixture_tabular_version(tenant_id: str = CANARY, rows: list[dict] | None = None,
                            klass: str = "RAW", dataset_name: str | None = None,
                            produced_by_run: str | None = None,
                            schema_id: str | None = None, with_records_key: bool = True,
                            dataset_id: str | None = None, table_required: bool | None = None) -> dict:
    """A dataset and one sealed version whose records are really in storage.

    The records object is written the way a producer writes it: at the prefix
    the platform reserved, before sealing, and the seal names it. Returns the
    seal response plus the rows, the dataset, the bucket and the records key.
    """
    import hashlib as _hashlib

    rows = rows if rows is not None else tabular_rows()
    schema_id = schema_id or fixture_tabular_contract(tenant_id)
    name = dataset_name or f"iceberg-{uuid.uuid4().hex[:8]}"
    if dataset_id is None:
        r = api("POST", "/datasets", json={"tenant_id": tenant_id, "name": name})
        r.raise_for_status()
        dataset_id = r.json()["id"]
    # else: a further version of a dataset that already exists, named `dataset_name`.

    where = api("GET", f"/datasets/{dataset_id}/next-version", params={"tenant_id": tenant_id})
    where.raise_for_status()
    prefix = where.json()["storage_prefix"]
    bucket = bucket_for(tenant_id)
    body = json.dumps(rows).encode("utf-8")
    key = f"{prefix}/records.json"
    s3_client(*ADMIN).put_object(Bucket=bucket, Key=key, Body=body)

    payload = {
        "tenant_id": tenant_id, "dataset_id": dataset_id, "schema_id": schema_id,
        "visibility_class": klass,
        "object_manifest": [{"key": key, "bytes": len(body),
                             "sha256": _hashlib.sha256(body).hexdigest()}],
        "record_count": len(rows),
    }
    if produced_by_run:
        payload["produced_by_run"] = produced_by_run
    if with_records_key:
        payload["records_key"] = key
    if table_required is not None:
        payload["table_required"] = table_required
    sealed = api("POST", "/dataset-versions", json=payload)
    sealed.raise_for_status()
    out = sealed.json()
    out.update({"dataset_id": dataset_id, "dataset_name": name, "bucket": bucket,
                "records_key": key, "rows": rows, "prefix": prefix,
                "records_sha256": _hashlib.sha256(body).hexdigest(), "schema_id": schema_id})
    return out


def read_table(metadata_location: str):
    """The Iceberg table at this metadata file, read with the platform's own
    super-key. A reader's view of it is a different check (the catalog's)."""
    from pyiceberg.table import StaticTable

    return StaticTable.from_metadata(metadata_location, properties={
        "s3.endpoint": S3_ENDPOINT, "s3.access-key-id": ADMIN[0],
        "s3.secret-access-key": ADMIN[1], "s3.region": "us-east-1",
        "s3.path-style-access": "true",
    })


def require_api() -> None:
    try:
        r = api("GET", "/health")
        r.raise_for_status()
    except Exception as exc:
        print(f"control plane unreachable at {API}: {exc}")
        sys.exit(2)
