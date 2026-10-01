"""U92: real tools, DuckDB and PyIceberg, read governed tables through the catalog.

U91 proves the catalog's behaviour with plain HTTP calls. This proves what a
person would actually do: connect a tool they already own, with a token, and
query. Two independent engines, one set of rows, and the same answers from both:

  * each tool connects with the token alone and sees only what the person may read
  * a table the person may not read is refused by the catalog, whichever tool asks
  * the rows come back exactly as sealed, and both tools agree with each other
    and with the source
  * a dataset's history is its versions, each a table of its own
  * a lease makes a raw table appear, and revoking it makes it go

Runs on the host, because it uses tools the API container does not carry:

    .venv\\Scripts\\python.exe -m pip install duckdb "pyiceberg[pyarrow]"
    .venv\\Scripts\\python.exe verify\\v92_iceberg_real_clients.py

The host's PG_DSN, VERIFY_KRATOS and S3_ENDPOINT are set to the published
localhost ports when they are not already set. Per assertion, never in aggregate.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ports_config import PORTS  # noqa: E402

# Before verify/common.py is imported, because it reads these at import. Newer S3
# clients send uploads SeaweedFS stores wrongly unless told to send plain ones.
os.environ.setdefault("PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS", f"http://localhost:{PORTS['kratos_public']}")
os.environ.setdefault("S3_ENDPOINT", f"http://localhost:{PORTS['seaweedfs_s3']}")
os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402
from pyiceberg.catalog.rest import RestCatalog  # noqa: E402
from pyiceberg.exceptions import ForbiddenError  # noqa: E402

from common import (API, CANARY, RESEARCHER, TABULAR_FIELDS, api, bearer_for, check, db,  # noqa: E402
                    fixture_department, fixture_tabular_version, fixture_tenant,
                    heading, require_api, summary, tabular_rows)


def token_for(purpose: str) -> str:
    r = api("POST", "/iceberg/tokens", json={"purpose": purpose, "hours": 1}, headers=bearer_for(RESEARCHER))
    r.raise_for_status()
    return r.json()["token"]


def duck(tenant: str, token: str):
    """A fresh DuckDB connection with the catalog attached, as a person would."""
    con = duckdb.connect()
    con.execute("INSTALL iceberg; LOAD iceberg;")
    con.execute(f"ATTACH '{tenant}' AS lake (TYPE ICEBERG, ENDPOINT '{API}/iceberg', TOKEN '{token}')")
    return con


def pyice(tenant: str, token: str) -> RestCatalog:
    return RestCatalog("munitas", uri=f"{API}/iceberg", token=token, warehouse=tenant)


def duck_tables(con) -> set[tuple[str, str]]:
    return {(r[1], r[2]) for r in con.execute("SHOW ALL TABLES").fetchall()}


def aggregates(rows: list[dict]) -> tuple:
    return (len(rows), sum(r["count"] for r in rows), round(sum(r["score"] for r in rows), 6),
            sum(1 for r in rows if r["ok"]))


def main() -> int:
    require_api()
    tenant = fixture_tenant(CANARY)
    rows = tabular_rows(5)
    open_v = fixture_tabular_version(tenant, rows=rows, klass="PUBLISHED")
    ns = open_v["dataset_name"]
    raw_rows = tabular_rows(4)
    raw_v = fixture_tabular_version(tenant, rows=raw_rows, klass="RAW")
    custodian = fixture_department(tenant, raw_v["dataset_id"])
    purpose = f"u92 real clients {uuid.uuid4().hex[:6]}"
    token = token_for(purpose)

    heading("U92: PyIceberg connects with the token alone")
    cat = pyice(tenant, token)
    spaces = [n[0] for n in cat.list_namespaces()]
    check("it lists the published dataset", ns in spaces, f"{len(spaces)} namespaces")
    check("and not the raw one it may not read", raw_v["dataset_name"] not in spaces, f"{len(spaces)} namespaces")
    check("it lists the version as a table", [t[1] for t in cat.list_tables(ns)] == ["v1"],
          str(cat.list_tables(ns)))
    table = cat.load_table((ns, "v1"))
    back = sorted(table.scan().to_arrow().to_pylist(), key=lambda r: r["record_id"])
    check("it reads the rows back, through a key the catalog handed it",
          [r["record_id"] for r in back] == [r["record_id"] for r in rows], f"{len(back)} rows")
    check("with the values exactly as sealed",
          all(b[k] == r[k] for b, r in zip(back, rows) for k in ("transcript", "score", "count", "ok")),
          f"{len(rows)} rows compared")
    check("the schema carries each field's sensitivity",
          [(f.name, (f.doc or "").split(" ")[0]) for f in table.schema().fields]
          == [(f["name"], f"sensitivity={f['sensitivity']}") for f in TABULAR_FIELDS],
          str([(f.name, f.doc) for f in table.schema().fields][:2]))
    tag = table.snapshot_by_name("v1")
    with db() as conn:
        ref = conn.execute("select snapshot_id from iceberg_table_ref where dataset_version_id = %s",
                           (open_v["id"],)).fetchone()
    check("the permanent tag resolves to the snapshot the register recorded",
          tag is not None and tag.snapshot_id == ref["snapshot_id"], str(tag and tag.snapshot_id))
    check("the snapshot names the version it came from",
          dict(tag.summary.additional_properties).get("munitas.dataset-version-id") == open_v["id"], "named")
    try:
        cat.load_table((raw_v["dataset_name"], "v1"))
        denied, why = False, ""
    except Exception as exc:
        denied, why = True, f"{type(exc).__name__}: {exc}"
    check("opening the raw table with no lease is refused, with the reason", denied and "RAW" in why, why[:120])

    heading("U92: DuckDB connects with the token alone")
    con = duck(tenant, token)
    listed = duck_tables(con)
    check("it lists the published table", (ns, "v1") in listed, f"{len(listed)} tables")
    check("and not the raw one", (raw_v["dataset_name"], "v1") not in listed, f"{len(listed)} tables")
    got = con.execute(
        f'SELECT count(*), sum("count"), round(sum(score), 6), sum(CASE WHEN ok THEN 1 ELSE 0 END) '
        f'FROM lake."{ns}".v1').fetchone()
    check("a SQL aggregate over the table works", tuple(got) == aggregates(rows), f"{tuple(got)}")
    first = con.execute(f'SELECT transcript, json_extract_string(detail, \'$.nested.k\') '
                        f'FROM lake."{ns}".v1 WHERE record_id = \'rec-0\'').fetchone()
    check("a filter and a JSON field read out of the same table",
          first == (rows[0]["transcript"], "v"), str(first))
    try:
        con.execute(f'SELECT count(*) FROM lake."{raw_v["dataset_name"]}".v1').fetchall()
        refused, msg = False, ""
    except Exception as exc:
        refused, msg = True, str(exc)
    check("querying the raw table with no lease is refused", refused,
          msg[:140] if refused else "it returned rows")

    heading("U92: the two tools and the source agree")
    check("PyIceberg and DuckDB return the same aggregate",
          aggregates(back) == tuple(got), f"{aggregates(back)} vs {tuple(got)}")
    check("and it is the aggregate of the rows that were sealed", aggregates(rows) == tuple(got),
          f"{aggregates(rows)} vs {tuple(got)}")

    heading("U92: a dataset's history is its versions, each a table")
    more = tabular_rows(8)
    second = fixture_tabular_version(tenant, rows=more, klass="PUBLISHED", dataset_name=ns,
                                     dataset_id=open_v["dataset_id"], schema_id=open_v["schema_id"])
    check("a second version is sealed into the same dataset", second["version"] == 2, str(second["version"]))
    names = sorted(t[1] for t in pyice(tenant, token).list_tables(ns))
    check("the catalog lists both versions as tables", names == ["v1", "v2"], str(names))
    con2 = duck(tenant, token)
    counts = (con2.execute(f'SELECT count(*) FROM lake."{ns}".v1').fetchone()[0],
              con2.execute(f'SELECT count(*) FROM lake."{ns}".v2').fetchone()[0])
    check("each version answers with its own rows, version 1 unchanged by version 2",
          counts == (len(rows), len(more)), str(counts))
    t2 = pyice(tenant, token).load_table((ns, "v2"))
    check("each carries its own permanent tag", "v2" in t2.metadata.refs and "v1" not in t2.metadata.refs,
          str(sorted(t2.metadata.refs)))

    heading("U92: a lease makes a raw table appear in both tools, and revoking it takes it away")
    asked = api("POST", "/leases/requests", json={
        "tenant_id": tenant, "principal": RESEARCHER, "dataset_version_id": raw_v["id"],
        "purpose": purpose, "justification": "u92 verifies real clients", "ttl_hours": 2,
    }, headers=bearer_for(RESEARCHER))
    approved = api("POST", f"/leases/requests/{asked.json()['id']}/approve", headers=bearer_for(custodian))
    check("the researcher asks and the custodian approves",
          asked.status_code == 201 and approved.status_code == 201, f"{asked.status_code}/{approved.status_code}")
    lease_id = approved.json().get("lease_id") or approved.json().get("id")

    import time
    deadline, ready = time.monotonic() + 45, None
    while time.monotonic() < deadline:
        try:
            con3 = duck(tenant, token)
            ready = tuple(con3.execute(
                f'SELECT count(*), sum("count"), round(sum(score), 6), sum(CASE WHEN ok THEN 1 ELSE 0 END) '
                f'FROM lake."{raw_v["dataset_name"]}".v1').fetchone())
            break
        except Exception:
            time.sleep(2)
    check("DuckDB now lists and reads the raw table", ready == aggregates(raw_rows), str(ready))
    pt = pyice(tenant, token).load_table((raw_v["dataset_name"], "v1"))
    again = sorted(pt.scan().to_arrow().to_pylist(), key=lambda r: r["record_id"])
    check("and PyIceberg reads the same rows", aggregates(again) == aggregates(raw_rows), str(aggregates(again)))

    revoked = api("POST", f"/leases/{lease_id}/revoke", headers=bearer_for(custodian))
    check("the custodian revokes the lease", revoked.status_code == 200, f"HTTP {revoked.status_code}")
    con4 = duck(tenant, token)
    still_listed = (raw_v["dataset_name"], "v1") in duck_tables(con4)
    check("DuckDB no longer lists the raw table", not still_listed,
          "listed" if still_listed else "not listed")
    try:
        con4.execute(f'SELECT count(*) FROM lake."{raw_v["dataset_name"]}".v1').fetchall()
        gone = False
    except Exception:
        gone = True
    check("or reads it", gone, "refused" if gone else "it still returned rows")
    try:
        pyice(tenant, token).load_table((raw_v["dataset_name"], "v1"))
        gone2 = False
    except ForbiddenError:
        gone2 = True
    except Exception:
        gone2 = True
    check("and PyIceberg is refused too", gone2, "refused" if gone2 else "it still opened")

    return summary("U92")


if __name__ == "__main__":
    sys.exit(main())
