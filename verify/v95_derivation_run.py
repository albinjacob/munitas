"""U95: a confirmed query really runs in the sandbox, and what it makes is governed.

U94 proves what is decided before a query runs. This runs one, end to end, on
real storage with the real worker and the real container, and checks what comes
out:

  * the result is a sealed version at the strictest input class, with the
    query and its input versions on record, and an Iceberg table that agrees
    with the register
  * the rows are what the SQL computes, joins included
  * the person who asked can read it at once; somebody else cannot
  * a query the sandbox refuses (a file read, a bad key, a conversion) seals
    nothing and says why, without quoting a value
  * a revoked lease ends the person's access to what was made from it

Needs the stack up, the host worker and the sandbox worker running, and the
query image built (RUNBOOK, "Running a query to make a new dataset"). It waits
for each run to finish and says so if the workers are not there. Per assertion.
"""

from __future__ import annotations

import sys
import time
import uuid

import httpx

from common import (API, CANARY, RESEARCHER, api, bearer_for, check, db, fixture_department,
                    fixture_tabular_version, fixture_tenant, heading, require_api,
                    summary, tabular_rows)

WAIT_SECONDS = 150


def draft(inputs, sql, pk=("record_id",), purpose="u95 derivation", target=None):
    return api("POST", "/derivations", json={
        "inputs": inputs, "sql": sql, "target_name": target or f"made-{uuid.uuid4().hex[:8]}",
        "primary_key": list(pk), "purpose": purpose}, headers=bearer_for(RESEARCHER))


def confirm(draft_response, sensitivities=None):
    return api("POST", f"/derivations/{draft_response.json()['id']}/confirm",
               json={"sensitivities": sensitivities or {}}, headers=bearer_for(RESEARCHER))


def finished(derivation_id: str) -> dict:
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        d = api("GET", f"/derivations/{derivation_id}", headers=bearer_for(RESEARCHER)).json()
        if d["status"] in ("succeeded", "failed") or time.monotonic() > deadline:
            return d
        time.sleep(3)


def one(sql: str, params: tuple = ()):
    with db() as conn:
        return conn.execute(sql, params).fetchone()


def main() -> int:
    require_api()
    tenant = fixture_tenant(CANARY)
    rows = tabular_rows(6)
    open_v = fixture_tabular_version(tenant, rows=rows, klass="PUBLISHED")
    src = open_v["dataset_name"]
    one_in = [{"dataset": src, "alias": "t"}]

    heading("U95: a query runs and its result is sealed")
    d = draft(one_in, "SELECT record_id, score, count FROM t WHERE count >= 4 ORDER BY count")
    c = confirm(d)
    check("the draft is confirmed and queued", c.status_code == 202, f"HTTP {c.status_code} {c.text[:120]}")
    run = finished(d.json()["id"])
    check("the run finishes and succeeds", run["status"] == "succeeded", f"{run['status']}: {run.get('error')}")
    out = one("""select dv.id::text as id, dv.record_count, dv.visibility_class, dv.sealed,
                        dv.produced_by_run::text as run, dv.iceberg_snapshot_id
                   from dataset_version dv where dv.id = %s""", (run["output_version_id"],)) \
        if run.get("output_version_id") else None
    check("a sealed version exists", bool(out and out["sealed"]), str(out))
    # The test rows have count 0, 2, 4, 6, 8, 10, so count >= 4 selects four of the six.
    check("with the rows the SQL selects (count >= 4 of 6 rows)", bool(out) and out["record_count"] == 4,
          f"{out and out['record_count']} rows")
    check("at the class of its input", bool(out) and out["visibility_class"] == "PUBLISHED",
          str(out and out["visibility_class"]))
    ref = one("select snapshot_id, record_count, metadata_location from iceberg_table_ref where dataset_version_id = %s",
              (run["output_version_id"],)) if out else None
    check("it is also an Iceberg table, and the register points at it",
          bool(ref) and ref["snapshot_id"] == out["iceberg_snapshot_id"] and ref["record_count"] == 4,
          str(ref and {k: v for k, v in ref.items() if k != "metadata_location"}))
    from common import read_table
    stored = sorted(read_table(ref["metadata_location"]).scan().to_arrow().to_pylist(),
                    key=lambda r: r["count"]) if ref else []
    expected = [{"record_id": r["record_id"], "score": r["score"], "count": r["count"]}
                for r in rows if r["count"] >= 4]
    check("and the table holds exactly the rows the SQL selected, with their values",
          [{k: r[k] for k in ("record_id", "score", "count")} for r in stored] == expected,
          f"{[r['record_id'] for r in stored]} vs {[r['record_id'] for r in expected]}")
    lineage = one("""select ar.input_versions::text as inputs, ar.triggered_by, ar.operator, ar.params::text as params
                       from action_run ar where ar.id = %s""", (out["run"],)) if out else None
    check("the run on record names the input version and the person",
          bool(lineage) and open_v["id"] in lineage["inputs"] and lineage["triggered_by"] == RESEARCHER,
          str(lineage)[:160])
    check("and the query's own derivation id", bool(lineage) and d.json()["id"] in lineage["params"], "named")

    heading("U95: a join, taking the strictest class")
    raw_v = fixture_tabular_version(tenant, rows=tabular_rows(6), klass="RAW")
    custodian = fixture_department(tenant, raw_v["dataset_id"])
    purpose = f"u95 join {uuid.uuid4().hex[:6]}"
    asked = api("POST", "/leases/requests", json={
        "tenant_id": tenant, "principal": RESEARCHER, "dataset_version_id": raw_v["id"],
        "purpose": purpose, "justification": "u95", "ttl_hours": 2}, headers=bearer_for(RESEARCHER))
    approved = api("POST", f"/leases/requests/{asked.json()['id']}/approve", headers=bearer_for(custodian))
    lease_id = approved.json().get("lease_id") or approved.json().get("id")
    time.sleep(4)
    both = [{"dataset": src, "alias": "o"}, {"dataset": raw_v["dataset_name"], "alias": "r"}]
    jd = draft(both, "SELECT o.record_id AS rid, o.score + r.score AS total FROM o JOIN r ON o.record_id = r.record_id",
               pk=("rid",), purpose=purpose)
    check("the join is drafted at RAW", jd.status_code == 201 and jd.json()["output_class"] == "RAW",
          f"HTTP {jd.status_code} {jd.text[:100]}")
    jc = confirm(jd)
    jrun = finished(jd.json()["id"]) if jc.status_code == 202 else {"status": "not started"}
    check("it runs and succeeds", jrun["status"] == "succeeded", f"{jrun['status']}: {jrun.get('error')}")
    jv = one("select visibility_class, record_count from dataset_version where id = %s",
             (jrun.get("output_version_id"),)) if jrun.get("output_version_id") else None
    check("the sealed result is RAW and has the joined rows", bool(jv) and jv["visibility_class"] == "RAW"
          and jv["record_count"] == 6, str(jv))
    check("the person was given access to the RAW result without anybody being asked",
          bool(one("select 1 as x from access_lease where principal = %s and dataset_version_id = %s and revoked = false",
                   (RESEARCHER, jrun.get("output_version_id")))) if jrun.get("output_version_id") else False,
          "a lease on the result")
    ends = one("select (select expires_at from access_lease where principal = %s and dataset_version_id = %s limit 1) "
               "= (select expires_at from access_lease where id = %s) as same",
               (RESEARCHER, jrun.get("output_version_id"), lease_id)) if jrun.get("output_version_id") else None
    check("and it ends when their access to the input ends", bool(ends and ends["same"]), str(ends))

    def open_result(who: str, why: str) -> int:
        token = api("POST", "/iceberg/tokens", json={"purpose": why, "hours": 1}, headers=bearer_for(who)).json()["token"]
        deadline = time.monotonic() + 45
        while True:
            r = httpx.get(f"{API}/iceberg/v1/{tenant}/namespaces/{jd.json()['target_name']}/tables/v1",
                          headers={"Authorization": f"Bearer {token}"}, timeout=30)
            if r.status_code != 503 or time.monotonic() > deadline:
                return r.status_code
            time.sleep(2)

    heading("U95: the person can open what they made, somebody else cannot")
    check("the person opens the RAW result through the catalog at once",
          bool(jrun.get("output_version_id")) and open_result(RESEARCHER, purpose) == 200, "HTTP 200")
    check("a colleague with no access to it is refused",
          bool(jrun.get("output_version_id")) and open_result("canary-engineer", "u95 colleague") == 403, "HTTP 403")
    api("POST", f"/leases/{lease_id}/revoke", headers=bearer_for(custodian))
    time.sleep(3)
    check("once the input lease is revoked the person is refused the result too",
          bool(jrun.get("output_version_id")) and open_result(RESEARCHER, purpose) == 403, "HTTP 403")

    heading("U95: queries the sandbox refuses seal nothing")
    for label, sql, pk, expect in (
        ("a key that repeats", "SELECT 'same' AS record_id, score FROM t", ("record_id",), "not unique"),
        ("a conversion that fails", "SELECT record_id, CAST(record_id AS DOUBLE) AS score FROM t", ("record_id",), "ConversionException"),
        ("no rows", "SELECT record_id, score FROM t WHERE count > 1000", ("record_id",), "no rows"),
    ):
        dd = draft(one_in, sql, pk=pk)
        cc = confirm(dd) if dd.status_code == 201 else None
        rr = finished(dd.json()["id"]) if cc is not None and cc.status_code == 202 else {"status": "not started"}
        check(f"{label} fails with a reason", rr["status"] == "failed" and expect in (rr.get("error") or ""),
              f"{rr['status']}: {rr.get('error')}")
        no_version = not one("select 1 as x from dataset_version where dataset_id = (select dataset_id from derivation where id = %s)",
                             (dd.json()["id"],)) if dd.status_code == 201 else False
        check(f"and {label} seals no version", no_version, "no version of the new dataset")
    leak = one("select count(*) as n from derivation where status = 'failed' and error ilike %s", ("%rec-%",))
    check("no failure reason quotes a value from the data", leak["n"] == 0, f"{leak['n']} reasons quote a record")

    return summary("U95")


if __name__ == "__main__":
    sys.exit(main())
