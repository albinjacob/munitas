r"""U180: a table that was brought in is read back through real tools and a real notebook, with its real types, and only by somebody who may read it.

Runs on the host against the live platform, with generated tables (scripts/testdata) and their truth files, because DuckDB and PyIceberg are the tools a
person actually uses and the API container does not carry them:

    $env:MUNITAS_PG_DSN = "postgresql://munitas:munitas@localhost:5432/platform"
    $env:MUNITAS_VERIFY_KRATOS = "http://localhost:4433"
    $env:MUNITAS_S3_ENDPOINT = "http://localhost:8333"
    .venv\Scripts\python.exe verify\v171_read_back.py

Two tables are brought in by an engineer through the person's route: a table with a composite key, decimals and millisecond timestamps (100,000 prices),
and a table with a composite key and hourly timestamps (100,000 ward readings). Checked one item at a time:

  * a researcher with no lease is not shown the table, cannot open it, and cannot see its first rows, and each refusal says why;
  * the researcher asks, the custodian approves, and then DuckDB and PyIceberg read the table, and each total, smallest, largest, count of different keys
    and sum of decimals is exactly the one the generator wrote down, with the column types a tool expects (a decimal, a timestamp, a date);
  * a notebook signs in as the researcher through the real client, takes a catalog token and reads the same table with the client's own DuckDB;
  * the first rows can be looked at by somebody who may read the table, are the first rows of the table, are never more than were asked for, and each
    look is recorded, allowed or refused;
  * when the custodian revokes the lease, the table goes from the catalog and the first rows are refused again.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ports_config import PORTS  # noqa: E402

os.environ.setdefault("MUNITAS_PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS", f"http://localhost:{PORTS['kratos_public']}")
os.environ.setdefault("MUNITAS_S3_ENDPOINT", f"http://localhost:{PORTS['seaweedfs_s3']}")
os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "client"))

import duckdb  # noqa: E402
import httpx  # noqa: E402
from pyiceberg.catalog.rest import RestCatalog  # noqa: E402

from common import API, CANARY, ENGINEER, api, bearer_for, check, db, heading, require_api, summary  # noqa: E402
from munitas_client import Munitas  # noqa: E402
from scripts.testdata import contracts, generate, tables, truth  # noqa: E402

RESEARCHER, CUSTODIAN = "canary-researcher", "canary-custodian"
WORK = Path(tempfile.mkdtemp(prefix="munitas-u180-"))
HYDRA = "http://localhost:4444"


def duck(token: str):
    con = duckdb.connect()
    con.execute("INSTALL iceberg; LOAD iceberg;")
    con.execute(f"ATTACH '{CANARY}' AS lake (TYPE ICEBERG, ENDPOINT '{API}/iceberg', TOKEN '{token}')")
    return con


def listed(con) -> set[tuple[str, str]]:
    return {(r[1], r[2]) for r in con.execute("SHOW ALL TABLES").fetchall()}


def approve_in_browser(person: str, shown: str) -> None:
    """The person's browser: open the address in the notebook's message, type the code, be signed in by the console session, approve."""
    address = re.search(r"open (\S+) and type the code (\S+)\.", shown)
    url, code = address.group(1), address.group(2)
    browser, headers = httpx.Client(follow_redirects=False, timeout=30.0), bearer_for(person)
    for _ in range(30):
        r = browser.get(url)
        if r.status_code not in (301, 302, 303, 307):
            raise RuntimeError(f"the sign-in stopped at {urlparse(url).path} with {r.status_code}")
        where = urljoin(url, r.headers["Location"])
        path, query = urlparse(where).path, {k: v[0] for k, v in parse_qs(urlparse(where).query).items()}
        if path == "/notebook-sign-in/code":
            step = api("POST", "/notebook-sign-in/code", json={"device_challenge": query["device_challenge"], "user_code": code}, headers=headers)
        elif path == "/notebook-sign-in/login":
            step = api("POST", "/notebook-sign-in/login", json={"login_challenge": query["login_challenge"]}, headers=headers)
        elif path == "/notebook-sign-in/consent":
            step = api("POST", "/notebook-sign-in/consent", json={"consent_challenge": query["consent_challenge"], "approve": True}, headers=headers)
        elif path == "/notebook-sign-in/done":
            return
        else:
            url = where
            continue
        if step.status_code != 200:
            raise RuntimeError(f"{path} answered {step.status_code}: {step.text[:160]}")
        url = step.json()["redirect_to"]


def main() -> int:
    require_api()
    try:
        run()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return summary("U180")


def run() -> None:
    engineer, researcher, custodian = bearer_for(ENGINEER), bearer_for(RESEARCHER), bearer_for(CUSTODIAN)
    department = next(d["id"] for d in api("GET", "/organisation", headers=engineer).json()["departments"] if d["name"] == "Verification")
    tag = uuid.uuid4().hex[:8]
    brought = {}

    heading("Two tables are brought in by an engineer")
    for name in ("fx_ticks", "ward_occupancy"):
        spec = tables.SPECS[name]
        generate.make(name, 100_000, ("csv",), WORK, quiet=True)
        folder = WORK / name / "100000"
        dataset = api("POST", "/datasets/register", headers=engineer, json={"tenant_id": CANARY, "name": f"readback-{name}-{tag}", "department_id": department,
                                                                                "registered_by": ENGINEER, "provenance": "internal_regulated"}).json()["id"]
        body = (folder / "csv" / f"{name}.csv").read_bytes()
        r = api("POST", f"/datasets/{dataset}/table-uploads", headers=engineer, json={"file_name": f"{name}.csv", "size_bytes": len(body),
                                                                                    "contract": contracts.to_yaml(spec), "options": {}})
        job = r.json()["job_id"]
        for n in range(1, -(-len(body) // (8 * 1024 * 1024)) + 1):
            api("PUT", f"/table-uploads/{job}/parts/{n}", headers=engineer, content=body[(n - 1) * 8 * 1024 * 1024:n * 8 * 1024 * 1024])
        api("POST", f"/table-uploads/{job}/finish", headers=engineer)
        for _ in range(120):
            state = api("GET", f"/table-uploads/{job}", headers=engineer).json()
            if state["status"] in ("sealed", "refused", "expired"):
                break
            time.sleep(2)
        brought[name] = {"dataset": dataset, "dataset_name": f"readback-{name}-{tag}", "version": state.get("version_id"), "folder": folder, "spec": spec,
                         "truth": json.loads((folder / "truth.json").read_text(encoding="utf-8"))}
        check(f"{name}: 100,000 rows are brought in and sealed", state["status"] == "sealed" and state.get("version_id"), str(state.get("reason")))

    purpose = f"u180 read back {tag}"
    token = api("POST", "/iceberg/tokens", json={"purpose": purpose, "hours": 1}, headers=researcher).json()["token"]
    fx, ward = brought["fx_ticks"], brought["ward_occupancy"]

    heading("With no lease the table is not shown, cannot be opened, and its rows are refused, with the reason")
    con = duck(token)
    check("DuckDB does not list either table", not ({(fx["dataset_name"], "v1"), (ward["dataset_name"], "v1")} & listed(con)))
    try:
        con.execute(f'SELECT count(*) FROM lake."{fx["dataset_name"]}".v1').fetchall()
        refused = False
    except Exception as exc:  # noqa: BLE001
        refused = "RAW" in str(exc) or "not" in str(exc).lower()
    check("querying it is refused", refused)
    r = api("GET", f"/dataset-versions/{fx['version']}/preview", headers=researcher)
    check("its first rows are refused, with the reason in words", r.status_code == 403 and "do not hold access" in r.text, f"{r.status_code} {r.text[:140]}")
    with db() as conn:
        seen = conn.execute("select allowed, purpose from access_decision where principal = %s and dataset_version_id = %s and purpose = %s limit 1",
                            (RESEARCHER, fx["version"], "look at the first rows of the table")).fetchone()
    check("the refusal is recorded as a decision", bool(seen) and seen["allowed"] is False, str(seen))

    heading("The researcher asks, the custodian approves, and both tools read the table back to its truth")
    leases = {}
    for name, info in brought.items():
        asked = api("POST", "/leases/requests", json={"tenant_id": CANARY, "principal": RESEARCHER, "dataset_version_id": info["version"], "purpose": purpose,
                                                     "justification": "u180 reads the table back", "ttl_hours": 2}, headers=researcher)
        approved = api("POST", f"/leases/requests/{asked.json()['id']}/approve", headers=custodian)
        leases[name] = approved.json().get("lease_id") or approved.json().get("id")
        check(f"{name}: the researcher asks and the custodian approves", asked.status_code == 201 and approved.status_code == 201, f"{asked.status_code}/{approved.status_code} {approved.text[:100]}")

    deadline, con = time.monotonic() + 90, None
    while time.monotonic() < deadline:
        try:
            con = duck(token)
            con.execute(f'SELECT count(*) FROM lake."{fx["dataset_name"]}".v1').fetchone()
            con.execute(f'SELECT count(*) FROM lake."{ward["dataset_name"]}".v1').fetchone()
            break
        except Exception:  # noqa: BLE001 - storage reads its permissions a moment after they are printed
            time.sleep(3)
    t = f'lake."{fx["dataset_name"]}".v1'
    row = con.execute(f"SELECT count(*), sum(bid), sum(ask), min(ticked_at), max(ticked_at), count(DISTINCT pair), count(DISTINCT venue) FROM {t}").fetchone()
    want = fx["truth"]["columns"]
    check("fx_ticks in DuckDB: the count, the exact sums of the decimals, the first and last tick and the pairs are the generator's",
          row[0] == 100_000 and str(row[1]) == want["bid"]["sum"] and str(row[2]) == want["ask"]["sum"] and row[5] == 12
          and row[3].strftime("%Y-%m-%d %H:%M:%S.%f")[:23] == want["ticked_at"]["min"].replace("T", " ")[:23]
          and row[4].strftime("%Y-%m-%d %H:%M:%S.%f")[:23] == want["ticked_at"]["max"].replace("T", " ")[:23], str(row)[:200])
    kinds = {r[0]: r[1] for r in con.execute(f"SELECT column_name, column_type FROM (DESCRIBE {t})").fetchall()}
    check("and the types are what a tool expects: decimals, a timestamp, text", kinds["bid"] == "DECIMAL(12,6)" and kinds["ask"] == "DECIMAL(12,6)"
          and kinds["ticked_at"].startswith("TIMESTAMP") and kinds["pair"] == "VARCHAR", str(kinds))
    minutes = con.execute(f"SELECT count(*) FROM (SELECT date_trunc('minute', ticked_at) FROM {t} GROUP BY 1)").fetchone()[0]
    check("a time function works on the timestamps (about 35 minutes of ticks, four a second for twelve pairs)", 30 <= minutes <= 40, str(minutes))
    w = f'lake."{ward["dataset_name"]}".v1'
    row = con.execute(f"SELECT count(*), sum(beds_open), sum(beds_occupied), min(hour_start), max(hour_start), count(DISTINCT ward_code) FROM {w}").fetchone()
    want = ward["truth"]["columns"]
    check("ward_occupancy in DuckDB: the count, the sums, the first and last hour and the wards are the generator's",
          row[0] == 100_000 and str(row[1]) == want["beds_open"]["sum"] and str(row[2]) == want["beds_occupied"]["sum"] and row[5] == 800
          and row[3].strftime("%Y-%m-%dT%H:%M:%S") == want["hour_start"]["min"].replace(" ", "T") and row[4].strftime("%Y-%m-%dT%H:%M:%S") == want["hour_start"]["max"].replace(" ", "T"), str(row)[:200])

    catalog = RestCatalog("munitas", uri=f"{API}/iceberg", token=token, warehouse=CANARY)
    for name, info in brought.items():
        arrow = catalog.load_table((info["dataset_name"], "v1")).scan().to_arrow()
        problems = truth.compare(info["truth"], truth.summarize(info["spec"], arrow.select([c.name for c in info["spec"].cols]).cast(info["spec"].schema())))
        check(f"{name} in PyIceberg: every column's totals, smallest, largest, the keys and their fingerprint agree with the truth file", not problems, "; ".join(problems[:2]))
        if name == "fx_ticks":
            check("and the two tools agree on the number of different minutes", duck(token).execute(
                f"SELECT count(*) FROM (SELECT date_trunc('minute', ticked_at) FROM lake.\"{info['dataset_name']}\".v1 GROUP BY 1)").fetchone()[0] == minutes,
                  str(minutes))

    heading("A notebook signs in as the researcher through the real client and reads the same table")
    shown: list[str] = []
    result: dict = {}

    def sign_in():
        try:
            result["me"] = Munitas.sign_in(API, "u180 notebook", show=shown.append, wait=90, allow_http=True)
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=sign_in)
    thread.start()
    for _ in range(40):
        if shown:
            break
        time.sleep(0.5)
    check("the notebook shows a code and an address", bool(shown) and "type the code" in shown[0], shown[0][:100] if shown else "nothing shown")
    approve_in_browser(RESEARCHER, shown[0])
    thread.join(timeout=100)
    me = result.get("me")
    check("it is signed in once the person approves", me is not None, str(result.get("error")))
    notebook_token = me.catalog_token(purpose)
    nb = me.attach_duckdb(notebook_token, CANARY)
    seen = nb.execute(f'SELECT count(*), sum(beds_open) FROM lake."{ward["dataset_name"]}".v1').fetchone()
    check("the notebook, with its own pass, reads the table through DuckDB and gets the generator's totals", seen[0] == 100_000 and str(seen[1]) == want["beds_open"]["sum"], str(seen))

    heading("The first rows can be looked at by somebody who may read the table, and each look is recorded")
    r = api("GET", f"/dataset-versions/{fx['version']}/preview", headers=researcher)
    body = r.json() if r.status_code == 200 else {}
    columns = [c["name"] for c in body.get("columns", [])]
    check("the researcher is shown 20 rows with the table's columns in their order", r.status_code == 200 and len(body["rows"]) == 20 and body["shown"] == 20
          and columns == [c.name for c in fx["spec"].cols] and body["rows_total"] == 100_000, f"{r.status_code} {str(body)[:140]}")
    first = con.execute(f"SELECT pair, ticked_at FROM {t} ORDER BY ticked_at, pair LIMIT 1").fetchone()
    check("they are rows of the table, and decimals and timestamps keep every digit as text", re.fullmatch(r"\d+\.\d{6}", body["rows"][0]["bid"]) is not None
          and re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d+)?", body["rows"][0]["ticked_at"]) is not None and body["rows"][0]["pair"] in {p for p in tables.PAIRS}, str(body["rows"][0]))
    r = api("GET", f"/dataset-versions/{fx['version']}/preview", headers=researcher, params={"limit": 5000})
    check("it never shows more than 100 rows, whatever is asked for", r.status_code == 200 and r.json()["shown"] == 100 and r.json()["limit"] == 100)
    with db() as conn:
        n = conn.execute("select count(*) as n from access_decision where principal = %s and dataset_version_id = %s and allowed and purpose = %s",
                         (RESEARCHER, fx["version"], "look at the first rows of the table")).fetchone()["n"]
    check("every look was recorded as a decision, with who and why", n == 2, f"{n} recorded")
    r = api("GET", f"/dataset-versions/{fx['version']}/preview", headers=bearer_for("neighbour-researcher"))
    check("a person of another organisation is told there is no such version", r.status_code == 404, str(r.status_code))

    heading("When the lease is revoked the table goes, and the rows are refused again")
    for name, lease in leases.items():
        revoked = api("POST", f"/leases/{lease}/revoke", headers=custodian)
        check(f"{name}: the custodian revokes the lease", revoked.status_code == 200, f"HTTP {revoked.status_code}")
    gone = duck(token)
    check("DuckDB no longer lists the tables", not ({(fx["dataset_name"], "v1"), (ward["dataset_name"], "v1")} & listed(gone)))
    r = api("GET", f"/dataset-versions/{fx['version']}/preview", headers=researcher)
    check("and the first rows are refused again", r.status_code == 403, f"{r.status_code}")


if __name__ == "__main__":
    sys.exit(main())
