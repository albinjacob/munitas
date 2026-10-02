"""U93: a key from the catalog expires, and a long read carries on or stops on cue.

U91 and U92 prove who may open a table. This proves what happens to the storage
key it hands over, which is the part that matters when a key leaks or a read
runs long:

  * the key says when it stops working, and it does, in about that time
  * a client can ask for the next one, and is refused once access has ended
  * revoking a lease ends its keys at once, whatever time they had left
  * DuckDB, asked for one long query that outlasts several keys, finishes it,
    and the same query is stopped by a revoke, which shows it was reading
    storage throughout and not from memory
  * PyIceberg, through scripts/client/iceberg_reader.py, does the same

It needs the API started with short keys and small files, because a one-hour
key would make this a three-hour test:

    $env:MUNITAS_CATALOG_KEY_SECONDS = "60"
    $env:MUNITAS_ICEBERG_ROW_GROUP_ROWS = "100"
    $env:MUNITAS_ICEBERG_FILE_BYTES = "6000"
    (restart the API with those set, as RUNBOOK describes)

    .venv\\Scripts\\python.exe -m pip install duckdb numpy "pyiceberg[pyarrow]" boto3
    .venv\\Scripts\\python.exe verify\\v93_catalog_key_expiry.py

About six minutes. With any other settings every check reports a skip and says
which setting to change, because passing against a one-hour key would prove
nothing. Per assertion, never in aggregate.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ports_config import PORTS  # noqa: E402

os.environ.setdefault("PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS", f"http://localhost:{PORTS['kratos_public']}")
os.environ.setdefault("S3_ENDPOINT", f"http://localhost:{PORTS['seaweedfs_s3']}")
os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "client"))

import boto3  # noqa: E402
import duckdb  # noqa: E402
import httpx  # noqa: E402
from botocore.client import Config  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402
from iceberg_reader import read_batches  # noqa: E402
from pyiceberg.catalog.rest import RestCatalog  # noqa: E402

from common import (API, CANARY, RESEARCHER, api, bearer_for, check, db, fixture_department,  # noqa: E402
                    fixture_tabular_version, fixture_tenant, heading, require_api, skip,
                    summary, tabular_rows)

KEY_SECONDS = 60       # what the API must have been started with
ROWS = 1500
SCAN_SECONDS = 170     # longer than two whole key lifetimes
EXPIRY = "s3.session-token-expires-at-ms"


def token_for(purpose: str) -> str:
    r = api("POST", "/iceberg/tokens", json={"purpose": purpose, "hours": 1}, headers=bearer_for(RESEARCHER))
    r.raise_for_status()
    return r.json()["token"]


def load(tenant: str, token: str, ns: str, table: str = "v1") -> httpx.Response:
    return httpx.get(f"{API}/iceberg/v1/{tenant}/namespaces/{ns}/tables/{table}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)


def refresh(tenant: str, token: str, ns: str, table: str = "v1") -> httpx.Response:
    return httpx.get(f"{API}/iceberg/v1/{tenant}/namespaces/{ns}/tables/{table}/credentials",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)


def s3_for(config: dict):
    return boto3.client(
        "s3", endpoint_url=config["s3.endpoint"], aws_access_key_id=config["s3.access-key-id"],
        aws_secret_access_key=config["s3.secret-access-key"], region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))


def can_read(s3, bucket: str, key: str) -> bool:
    try:
        s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return True
    except ClientError:
        return False


def lease_for(tenant: str, version: dict, purpose: str) -> tuple[str, str]:
    """An approved lease on a raw version: (lease id, custodian)."""
    custodian = fixture_department(tenant, version["dataset_id"])
    asked = api("POST", "/leases/requests", json={
        "tenant_id": tenant, "principal": RESEARCHER, "dataset_version_id": version["id"],
        "purpose": purpose, "justification": "u93 key expiry", "ttl_hours": 2,
    }, headers=bearer_for(RESEARCHER))
    approved = api("POST", f"/leases/requests/{asked.json()['id']}/approve", headers=bearer_for(custodian))
    return approved.json().get("lease_id") or approved.json().get("id"), custodian


def open_when_active(tenant: str, token: str, ns: str) -> httpx.Response:
    """Opening a raw table the moment its lease is approved can answer 503 until
    storage permissions are written, which is the platform saying 'a moment'."""
    deadline = time.monotonic() + 45
    while True:
        r = load(tenant, token, ns)
        if r.status_code != 503 or time.monotonic() > deadline:
            return r
        time.sleep(2)


def main() -> int:
    require_api()
    tenant = fixture_tenant(CANARY)
    rows = tabular_rows(ROWS)

    heading("U93: the API is set up for this test")
    probe = fixture_tabular_version(tenant, rows=rows, klass="PUBLISHED")
    ptoken = token_for(f"u93 probe {uuid.uuid4().hex[:6]}")
    first = load(tenant, ptoken, probe["dataset_name"])
    cfg = first.json().get("config", {}) if first.status_code == 200 else {}
    left = (int(cfg[EXPIRY]) / 1000 - time.time()) if EXPIRY in cfg else None
    configured = left is not None and KEY_SECONDS / 2 - 5 <= left <= KEY_SECONDS + 15
    check("the catalog says when the key stops working",
          first.status_code == 200 and EXPIRY in cfg, f"HTTP {first.status_code}")
    check("and where to ask for the next one",
          "client.refresh-credentials-endpoint" in cfg, str(sorted(cfg))[:120])
    if not configured:
        why = (f"the key has {None if left is None else round(left)} s left, so the API was not "
               f"started with MUNITAS_CATALOG_KEY_SECONDS={KEY_SECONDS}")
        for label in ("a key stops working when it says", "the next key works and is a different key",
                      "a revoked lease ends its keys at once, and a refresh is refused with the reason",
                      "a long DuckDB query outlasts several keys", "a revoke stops that same query",
                      "a long PyIceberg read outlasts several keys", "a revoke stops that read"):
            skip(label, why)
        return summary("U93")
    check("a key lasts between half and all of the setting",
          KEY_SECONDS / 2 - 5 <= left <= KEY_SECONDS + 5, f"{left:.0f} s left")

    # ---------------------------------------------------------------- the key --
    heading("U93: a key stops working when it says, and the next one works")
    ref_bucket = first.json()["metadata-location"].removeprefix("s3://").split("/", 1)
    bucket, meta_key = ref_bucket
    s3 = s3_for(cfg)
    check("the key reads the table's files now", can_read(s3, bucket, meta_key), "read the metadata file")
    wait = int(cfg[EXPIRY]) / 1000 - time.time() + 20
    print(f"  ... waiting {wait:.0f} s for that key to run out")
    time.sleep(max(wait, 0))
    check("a key stops working when it says", not can_read(s3, bucket, meta_key),
          "refused after its stated time" if not can_read(s3, bucket, meta_key) else "still reads")
    nxt = refresh(tenant, ptoken, probe["dataset_name"])
    new_cfg = nxt.json()["storage-credentials"][0]["config"] if nxt.status_code == 200 else {}
    check("the next key works and is a different key",
          nxt.status_code == 200 and new_cfg.get("s3.access-key-id") != cfg["s3.access-key-id"]
          and can_read(s3_for(new_cfg), bucket, meta_key), f"HTTP {nxt.status_code}")

    # --------------------------------------------------------- revoke is at once --
    heading("U93: revoking a lease ends its keys at once")
    raw = fixture_tabular_version(tenant, rows=rows, klass="RAW")
    purpose = f"u93 revoke {uuid.uuid4().hex[:6]}"
    rtoken = token_for(purpose)
    lease, custodian = lease_for(tenant, raw, purpose)
    opened = open_when_active(tenant, rtoken, raw["dataset_name"])
    rcfg = opened.json().get("config", {}) if opened.status_code == 200 else {}
    rb, rk = opened.json()["metadata-location"].removeprefix("s3://").split("/", 1) if rcfg else ("", "")
    rs3 = s3_for(rcfg) if rcfg else None
    check("a leased raw table opens and its key reads", bool(rcfg) and can_read(rs3, rb, rk),
          f"HTTP {opened.status_code}")
    api("POST", f"/leases/{lease}/revoke", headers=bearer_for(custodian))
    time.sleep(3)
    check("the key it was handed stops at once, with most of its time unspent",
          bool(rcfg) and not can_read(rs3, rb, rk), "refused 3 s after the revoke")
    again = refresh(tenant, rtoken, raw["dataset_name"])
    check("asking for the next key is refused, with the reason",
          again.status_code == 403 and "RAW" in again.text, f"HTTP {again.status_code} {again.text[:100]}")

    # ----------------------------------------------------- long reads, four at once --
    heading("U93: long reads, each longer than two whole key lifetimes")
    scans: dict[str, dict] = {}
    plan = []
    version_ids: dict[str, str] = {}
    for tool in ("duckdb", "pyiceberg"):
        for ending in ("finishes", "revoked"):
            klass = "RAW" if ending == "revoked" else "PUBLISHED"
            version = fixture_tabular_version(tenant, rows=rows, klass=klass)
            why = f"u93 {tool} {ending} {uuid.uuid4().hex[:6]}"
            token = token_for(why)
            lease = custodian = None
            if klass == "RAW":
                lease, custodian = lease_for(tenant, version, why)
                if open_when_active(tenant, token, version["dataset_name"]).status_code != 200:
                    skip(f"{tool} {ending}", "the lease did not become active in time")
                    continue
            plan.append((f"{tool} {ending}", tool, version["dataset_name"], token, lease, custodian))
            version_ids[f"{tool} {ending}"] = version["id"]

    files = len(list(RestCatalog("munitas", uri=f"{API}/iceberg", token=ptoken, warehouse=tenant)
                     .load_table((probe["dataset_name"], "v1")).scan().plan_files()))
    check("the table is many files, so a read opens storage again and again", files >= 10, f"{files} data files")

    per_row = SCAN_SECONDS / ROWS

    def duck_scan(name: str, ns: str, token: str) -> None:
        try:
            con = duckdb.connect()
            con.execute("INSTALL iceberg; LOAD iceberg;")
            con.execute("SET enable_external_file_cache=false; SET threads=1")
            con.execute(f"ATTACH '{tenant}' AS lake (TYPE ICEBERG, ENDPOINT '{API}/iceberg', TOKEN '{token}')")
            con.create_function("slow", lambda x: (time.sleep(per_row), x)[1], ["VARCHAR"], "VARCHAR")
            n = con.execute(f'select count(*), sum(length(slow(record_id))) from lake."{ns}".v1').fetchone()[0]
            scans[name] = {"ok": True, "rows": n}
        except Exception as exc:
            scans[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def py_scan(name: str, ns: str, token: str) -> None:
        try:
            cat = RestCatalog("munitas", uri=f"{API}/iceberg", token=token, warehouse=tenant)
            n = 0
            for batch in read_batches(cat, (ns, "v1")):
                n += batch.num_rows
                time.sleep(per_row * batch.num_rows)
            scans[name] = {"ok": True, "rows": n}
        except Exception as exc:
            scans[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    started = time.monotonic()
    threads = []
    for name, tool, ns, token, lease, custodian in plan:
        t = threading.Thread(target=duck_scan if tool == "duckdb" else py_scan, args=(name, ns, token))
        t.start()
        threads.append(t)
    revoked_at = {}
    time.sleep(SCAN_SECONDS / 3)
    for name, tool, ns, token, lease, custodian in plan:
        if lease:
            api("POST", f"/leases/{lease}/revoke", headers=bearer_for(custodian))
            revoked_at[name] = time.monotonic() - started
    for t in threads:
        t.join()
    took = time.monotonic() - started

    done = {n: scans.get(n, {}) for n, *_ in plan}
    for name, label_ok, label_stop in (
            ("duckdb finishes", "a long DuckDB query outlasts several keys", None),
            ("pyiceberg finishes", "a long PyIceberg read outlasts several keys", None)):
        r = done.get(name, {})
        check(label_ok, r.get("ok") and r.get("rows") == ROWS and took > 2 * KEY_SECONDS,
              f"{r.get('rows')} of {ROWS} rows in {took:.0f} s" if r.get("ok") else r.get("error", "did not run")[:200])
    for name, label in (("duckdb revoked", "a revoke stops that same query"),
                        ("pyiceberg revoked", "a revoke stops that read")):
        r = done.get(name, {})
        check(label, name in done and not r.get("ok", True),
              (r.get("error", "") or "it finished")[:200])

    # What the catalog itself recorded, which is the evidence that the tools
    # asked again on their own: every table open is one decision in the audit
    # log, and nothing in this script opens a table while a scan runs.
    def decisions(name: str) -> list[dict]:
        with db() as conn:
            return conn.execute(
                """select allowed, at from access_decision
                    where dataset_version_id = %s and principal = %s and phase = 'policy'
                    order by at""", (version_ids[name], RESEARCHER)).fetchall()

    heading("U93: the catalog's own record shows the tools asking again")
    for tool in ("duckdb", "pyiceberg"):
        finishing = decisions(f"{tool} finishes") if f"{tool} finishes" in version_ids else []
        check(f"{tool} opened the same table again, more than twice, during one read",
              len(finishing) >= 3, f"{len(finishing)} opens recorded over {took:.0f} s")
    # DuckDB asks again, is refused, and stops. PyIceberg's reader is stopped by
    # storage itself before it asks, so only DuckDB has a refused ask to find.
    stopped = decisions("duckdb revoked") if "duckdb revoked" in version_ids else []
    check("DuckDB's last ask after the revoke was refused, and the refusal is on record",
          bool(stopped) and not stopped[-1]["allowed"],
          f"{len(stopped)} asks, last allowed={stopped[-1]['allowed'] if stopped else None}")

    return summary("U93")


if __name__ == "__main__":
    sys.exit(main())
