"""U134: a screen can ask what this person may do with a locked table, and what the lists say names people.

The console should never guess who may read a table's locked columns in full, who may ask for a lease and under which limits: the platform
knows, and says. This checks, one item at a time, with real sign-ins:

  * a table sealed under a privacy contract answers with its columns, how each is classified and treated, and how many rows it holds, and
    never a value;
  * each person is told what they can do: the owning department's approver reads in full, an engineer may ask, a researcher may not ask, and a
    person with a request waiting or a lease in force is told so;
  * the limits are the policy's own (7 days, 20 rows), read from it and not copied;
  * a table that was not sealed under a privacy contract, and another organisation's table, are not found;
  * the lease lists name the people, so a screen need not show an identifier.

    docker compose exec -T munitas-api python /verify/v134_locking_summary.py
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

from common import CANARY, api, bearer_for, check, fixture_contract, fixture_tenant, fixture_version, heading, require_api, summary  # noqa: E402
from lifecycle_fixture import drop_org, make_org  # noqa: E402
from v128_person_keys_erasure import ENGINEER, CUSTODIAN, RESEARCHER, good_contract, register_dataset, reasons_of, reset_keys, rows_for  # noqa: E402

AUDITOR = "canary-auditor"


def locking(who: str, version: str):
    return api("GET", f"/dataset-versions/{version}/locking", headers=bearer_for(who))


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    tag = uuid.uuid4().hex[:6]
    dataset = register_dataset(ENGINEER)
    sealed = api("POST", f"/datasets/{dataset}/seal-table", headers=bearer_for(ENGINEER), json={"contract": good_contract(), "rows": rows_for(f"A-{tag}", f"B-{tag}")})
    version = sealed.json()["id"]

    heading("What the table says about itself")
    r = locking(ENGINEER, version)
    s = r.json() if r.status_code == 200 else {}
    check("a table sealed under a privacy contract answers", r.status_code == 200 and s.get("version_id") == version, f"{r.status_code} {reasons_of(r)[:80]}")
    cols = {c["name"]: c for c in s.get("columns", [])}
    check("it lists the columns with how each is classified and treated",
          cols.get("patient_name") == {"name": "patient_name", "classification": "direct", "treatment": "lock"}
          and cols.get("clinic", {}).get("treatment") == "readable" and len(cols) == 6, str(cols)[:200])
    check("it says how many rows it holds", s.get("records") == 3)
    text = json.dumps(s)
    check("and holds no value from the table", not any(w in text for w in ("Ada Example", "Ben Example", "asthma", f"A-{tag}")), text[:120])

    heading("What each person may do")
    cust = locking(CUSTODIAN, version).json()
    check("the owning department's approver reads in full and need not ask", cust["mode"] == "full" and cust["may_ask"] is False, str(cust)[:200])
    eng = s
    check("an engineer reads nothing yet and may ask", eng["mode"] == "none" and eng["may_ask"] is True and eng["lease"] is None and eng["waiting"] is None, str(eng)[:200])
    check("a researcher may not ask", locking(RESEARCHER, version).json()["may_ask"] is False)
    check("the auditor may ask, and reads nothing yet", locking(AUDITOR, version).json()["may_ask"] is True and locking(AUDITOR, version).json()["mode"] == "none")
    check("the limits are the policy's own", eng["limits"] == {"days": 7, "rows": 20}, str(eng.get("limits")))

    heading("A request, then a lease")
    ask = api("POST", "/locked-column-leases", headers=bearer_for(ENGINEER), json={"dataset_version_id": version, "purpose": "devise the postcode rule", "days": 2, "rows": 3})
    waiting = locking(ENGINEER, version).json()
    check("a request that waits is shown to the person who asked", ask.status_code == 201 and waiting["waiting"]["id"] == ask.json()["id"] and waiting["mode"] == "none", str(waiting)[:200])
    api("POST", f"/locked-column-leases/{ask.json()['id']}/approve", headers=bearer_for(CUSTODIAN), json={"note": "ok"})
    held = locking(ENGINEER, version).json()
    check("an approved lease is shown, with its rows and its end, and the person is told they read a sample",
          held["mode"] == "sample" and held["lease"]["row_limit"] == 3 and held["lease"]["expires_at"] and held["waiting"] is None, str(held)[:240])
    check("a person holding a lease cannot be told to ask again", held["may_ask"] is False)

    heading("The lists name people")
    lists = api("GET", "/locked-column-leases", headers=bearer_for(ENGINEER)).json()
    mine = next(x for x in lists["mine"] if x["id"] == ask.json()["id"])
    check("a lease names who asked and who decided, as people and not as identifiers",
          mine["principal_label"] == "Canary engineer" and mine["decided_by_label"] == "Canary custodian", str(mine)[:240])

    granted = api("GET", "/locked-column-leases", headers=bearer_for(CUSTODIAN)).json()["granted"]
    check("the owning department's approver sees the lease in force, to end it if they must",
          any(x["id"] == ask.json()["id"] and x["principal_label"] == "Canary engineer" for x in granted), str(granted)[:160])
    check("another department's custodian does not", not any(x["id"] == ask.json()["id"] for x in api("GET", "/locked-column-leases", headers=bearer_for("canary-elsewhere")).json()["granted"]))
    check("and the person who holds it has no such list", api("GET", "/locked-column-leases", headers=bearer_for(ENGINEER)).json()["granted"] == [])

    api("GET", f"/dataset-versions/{version}/person-rows", headers=bearer_for(ENGINEER))
    evidence = api("GET", "/audit/locked-columns", headers=bearer_for(AUDITOR)).json()
    check("the audit evidence names who read, as people", any(x["principal_label"] == "Canary engineer" and x["allowed"] for x in evidence["reads"]), str(evidence["reads"][:1])[:200])
    check("and who made and opened keys", any(x["actor_label"] == "Canary engineer" and x["action"] == "unwrap" for x in evidence["key_uses"]))

    heading("Not found")
    plain = fixture_version(CANARY, fixture_contract(CANARY))["id"]
    check("a version that was not sealed under a privacy contract is not found", locking(ENGINEER, plain).status_code == 404)
    check("nor is one that does not exist", locking(ENGINEER, str(uuid.uuid4())).status_code == 404)
    org = make_org()
    try:
        check("another organisation's person finds nothing", api("GET", f"/dataset-versions/{version}/locking", headers=org.bearer("custodian")).status_code == 404)
    finally:
        drop_org(org)
    reset_keys()
    return summary("v134")


if __name__ == "__main__":
    sys.exit(main())
