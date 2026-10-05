"""U94: a query over existing datasets is answered with a draft, and confirmed on terms.

Nothing here runs a query. It proves the part that decides whether one may run,
and what the result would be:

  * every input must be readable by the person, and the refusal says why
  * only a single SELECT over the declared inputs is accepted
  * each output field's sensitivity is traced to the fields it was computed
    from, and cannot be lowered; the most cautious answer is taken when a query
    cannot be traced
  * the result is registered at the strictest class of any input
  * the same query over the same versions is recognised, not run twice

Per assertion, never in aggregate.
"""

from __future__ import annotations

import sys
import time
import uuid

from common import (CANARY, RESEARCHER, api, bearer_for, check, db, fixture_department,
                    fixture_tabular_version, fixture_tenant, heading, require_api,
                    summary, tabular_rows)


def draft(inputs, sql, target=None, pk=("record_id",), who=RESEARCHER, purpose="u94 derivation"):
    return api("POST", "/derivations", json={
        "inputs": inputs, "sql": sql, "target_name": target or f"derived-{uuid.uuid4().hex[:8]}",
        "primary_key": list(pk), "purpose": purpose}, headers=bearer_for(who))


def fields(response) -> dict:
    return {f["name"]: f for f in response.json()["fields"]} if response.status_code == 201 else {}


def main() -> int:
    require_api()
    tenant = fixture_tenant(CANARY)
    rows = tabular_rows(5)
    open_v = fixture_tabular_version(tenant, rows=rows, klass="PUBLISHED")
    raw_v = fixture_tabular_version(tenant, rows=tabular_rows(4), klass="RAW")
    custodian = fixture_department(tenant, raw_v["dataset_id"])
    src = open_v["dataset_name"]
    one = [{"dataset": src, "alias": "t"}]

    heading("U94: the person must be able to read every input")
    r = draft([{"dataset": raw_v["dataset_name"], "alias": "r"}], "SELECT record_id FROM r")
    check("a raw dataset with no lease is refused", r.status_code == 403, f"HTTP {r.status_code}")
    check("and the refusal names the dataset and the class", raw_v["dataset_name"] in r.text and "RAW" in r.text,
          r.text[:160])
    check("a dataset that does not exist is refused", draft([{"dataset": "no-such-dataset-xyz"}], "SELECT 1").status_code == 404, "")
    check("a version that does not exist is refused",
          draft([{"dataset": src, "version": 99}], "SELECT 1").status_code == 404, "")

    heading("U94: only a single SELECT over the declared inputs is accepted")
    r = draft(one, "SELECT content AS record_id FROM read_text('/etc/passwd')")
    check("a query that reads a file is refused, and says it reaches outside its inputs",
          r.status_code == 422 and "outside the datasets it declared" in r.text, f"HTTP {r.status_code} {r.text[:130]}")
    for label, sql in (("a DROP", "DROP TABLE t"),
                       ("two statements", "SELECT record_id FROM t; SELECT 1"),
                       ("a table that was not declared", "SELECT record_id FROM somewhere_else"),
                       ("a COPY out", "COPY (SELECT * FROM t) TO '/tmp/x.csv'")):
        r = draft(one, sql)
        check(f"{label} is refused", r.status_code == 422, f"HTTP {r.status_code} {r.text[:90]}")

    heading("U94: the draft describes the result without running it")
    r = draft(one, "SELECT record_id, score, length(transcript) AS n, count FROM t")
    f = fields(r)
    check("a draft is made", r.status_code == 201, f"HTTP {r.status_code} {r.text[:120]}")
    check("with the columns the query produces, in order",
          list(f) == ["record_id", "score", "n", "count"], str(list(f)))
    check("and their types as the contract names them",
          [f[k]["type"] for k in f] == ["string", "float", "int", "int"], str([f[k]["type"] for k in f]))
    check("a copied field keeps its own sensitivity, a computed one takes its inputs'",
          (f.get("record_id", {}).get("sensitivity"), f.get("n", {}).get("sensitivity"),
           f.get("score", {}).get("sensitivity")) == ("none", "phi", "none"),
          str({k: v["sensitivity"] for k, v in f.items()}))
    check("the draft is not yet a dataset",
          not db_one("select 1 as x from dataset where name = %s", (r.json()["target_name"],)), "no dataset yet")

    r = draft(one, "SELECT * FROM t")
    f = fields(r)
    check("SELECT * carries every column's own sensitivity",
          {k: v["sensitivity"] for k, v in f.items()} ==
          {"record_id": "none", "transcript": "phi", "score": "none", "count": "none",
           "ok": "none", "tags": "quasi", "detail": "none"}, str({k: v["sensitivity"] for k, v in f.items()}))
    r = draft(one, "SELECT count(*) AS n FROM t", pk=("n",))
    check("a count of rows draws on no field", fields(r).get("n", {}).get("sensitivity") == "none",
          str(fields(r).get("n")))
    r = draft(one, "WITH c AS (SELECT record_id, transcript FROM t) SELECT record_id FROM c")
    check("a query that cannot be traced takes the most cautious answer",
          fields(r).get("record_id", {}).get("sensitivity") == "phi", str(fields(r).get("record_id")))

    heading("U94: the shape of the result is checked")
    check("a column with no name of its own is refused",
          draft(one, "SELECT record_id, length(transcript) FROM t").status_code == 422, "needs an alias")
    check("a primary key that is not a column is refused",
          draft(one, "SELECT record_id FROM t", pk=("nope",)).status_code == 422, "")
    check("a name already taken is refused",
          draft(one, "SELECT record_id FROM t", target=src).status_code == 409, "")

    heading("U94: confirming registers the result, on terms")
    r = draft(one, "SELECT record_id, length(transcript) AS n FROM t", target=f"derived-{uuid.uuid4().hex[:8]}")
    d = r.json()
    # A second draft of the same name, made before the first is confirmed: the
    # only way two requests for one name can exist, since a confirmed one takes it.
    same = draft(one, "SELECT record_id, length(transcript) AS n FROM t", target=d["target_name"])
    lowered = api("POST", f"/derivations/{d['id']}/confirm", json={"sensitivities": {"n": "none"}},
                  headers=bearer_for(RESEARCHER))
    check("lowering a computed field's sensitivity is refused", lowered.status_code == 422,
          f"HTTP {lowered.status_code} {lowered.text[:110]}")
    ok = api("POST", f"/derivations/{d['id']}/confirm", json={"sensitivities": {"record_id": "quasi"}},
             headers=bearer_for(RESEARCHER))
    check("raising one is accepted and the run is queued", ok.status_code == 202 and ok.json()["status"] == "queued",
          f"HTTP {ok.status_code} {ok.text[:120]}")
    ds = db_one("select declared_class, declaration_basis, registered_by, provenance from dataset "
                "where tenant_id = %s and name = %s", (tenant, d["target_name"]))
    check("the dataset is registered at the strictest input class",
          ds and ds["declared_class"] == "PUBLISHED", str(ds))
    contract = db_one("select fields from schema_contract where id = (select schema_id from derivation where id = %s)",
                      (d["id"],))
    check("the contract carries the chosen sensitivities, and who added the fields",
          {x["name"]: x["sensitivity"] for x in contract["fields"]} == {"record_id": "quasi", "n": "phi"}
          and all(x["added_by"] == RESEARCHER for x in contract["fields"]), str(contract))
    # Same person, same name, same query, same version, same shape.
    again = api("POST", f"/derivations/{same.json()['id']}/confirm", json={"sensitivities": {"record_id": "quasi"}},
                headers=bearer_for(RESEARCHER))
    check("the same request by the same person is recognised, not run twice",
          again.status_code == 202 and again.json().get("reused") is True and again.json()["id"] == d["id"],
          f"HTTP {again.status_code} reused={again.json().get('reused')}")
    count = db_one("select count(*) as n from dataset where tenant_id = %s and name = %s", (tenant, d["target_name"]))["n"]
    check("and registers nothing new", count == 1, f"{count} dataset called {d['target_name']}")
    other = draft(one, "SELECT record_id, length(transcript) AS n FROM t", target=d["target_name"] + "-b")
    check("a different name for the same query is a different request, with its own dataset",
          other.status_code == 201 and api("POST", f"/derivations/{other.json()['id']}/confirm",
                                           json={"sensitivities": {"record_id": "quasi"}},
                                           headers=bearer_for(RESEARCHER)).json().get("reused") is False, "not reused")
    # A colleague with the same read access asking for the same query under a name
    # of their own gets their own request and dataset, never the first person's.
    colleague = draft(one, "SELECT record_id, length(transcript) AS n FROM t",
                      target=d["target_name"] + "-colleague", who="canary-engineer")
    theirs = api("POST", f"/derivations/{colleague.json()['id']}/confirm", json={"sensitivities": {"record_id": "quasi"}},
                 headers=bearer_for("canary-engineer"))
    owner = db_one("select registered_by from dataset where tenant_id = %s and name = %s",
                   (tenant, d["target_name"] + "-colleague"))
    check("the same query by a colleague is their own request, not the first person's",
          theirs.status_code == 202 and theirs.json().get("reused") is False
          and theirs.json()["id"] != d["id"] and owner and owner["registered_by"] == "canary-engineer",
          f"HTTP {theirs.status_code} reused={theirs.json().get('reused')} owner={owner}")
    check("and nothing of the first person's request is shown to the colleague",
          d["purpose"] not in theirs.text or d["purpose"] == colleague.json()["purpose"], "own purpose only")
    check("somebody else cannot see or confirm it",
          api("GET", f"/derivations/{d['id']}", headers=bearer_for("canary-engineer")).status_code == 404, "")

    heading("U94: a join takes the strictest class of its inputs")
    purpose = f"u94 join {uuid.uuid4().hex[:6]}"
    asked = api("POST", "/leases/requests", json={
        "tenant_id": tenant, "principal": RESEARCHER, "dataset_version_id": raw_v["id"],
        "purpose": purpose, "justification": "u94", "ttl_hours": 2}, headers=bearer_for(RESEARCHER))
    approved = api("POST", f"/leases/requests/{asked.json()['id']}/approve", headers=bearer_for(custodian))
    lease_id = approved.json().get("lease_id") or approved.json().get("id")
    time.sleep(3)
    both = [{"dataset": src, "alias": "o"}, {"dataset": raw_v["dataset_name"], "alias": "r"}]
    r = draft(both, "SELECT o.record_id AS oid, r.score AS rs FROM o JOIN r ON o.record_id = r.record_id",
              pk=("oid",), purpose=purpose)
    check("with access to both, a join is drafted", r.status_code == 201, f"HTTP {r.status_code} {r.text[:150]}")
    check("the result is RAW, the strictest of PUBLISHED and RAW", r.json().get("output_class") == "RAW",
          str(r.json().get("output_class")))
    check("its fields draw on both inputs",
          {k: v["sensitivity"] for k, v in fields(r).items()} == {"oid": "none", "rs": "none"}, str(fields(r)))
    api("POST", f"/leases/{lease_id}/revoke", headers=bearer_for(custodian))
    time.sleep(2)
    late = api("POST", f"/derivations/{r.json()['id']}/confirm", json={}, headers=bearer_for(RESEARCHER))
    check("confirming after the lease was revoked is refused", late.status_code == 403,
          f"HTTP {late.status_code} {late.text[:120]}")

    return summary("U94")


def db_one(sql: str, params: tuple = ()):
    with db() as conn:
        return conn.execute(sql, params).fetchone()


if __name__ == "__main__":
    sys.exit(main())
