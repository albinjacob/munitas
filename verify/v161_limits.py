"""U168: an organisation's limits on what people may ask for are settings, with today's behaviour as their defaults, and every screen and decision uses the one value in force.

Who may ask to read rows of locked columns, the most rows and days, and the most files and hours to open locked files in the page and in a notebook were constants in the policy file.
They are now limits an organisation can set, held in `limit_version` (never edited, with who set it and why) and worked out by `limits.py`. Checked here one item at a time,
against the live platform and the live policy engine:

  * with nothing set, everything is as it always was: the defaults equal the policy's own constants, and a request is accepted or refused with the same words as before;
  * a value an operator sets is the one in force: the limits page says so, with who set it, when and why, and a real request is accepted or refused by it, with the new limit in the words;
  * who may ask can be changed, can be emptied, and the refusal lists the roles that may;
  * the most the platform allows holds: the script refuses a value above it, and a row put in the table by hand that exceeds it is held to it all the same;
  * a limit is never edited: setting it again adds a row, the latest is in force, the database refuses an edit, and resetting puts the default back;
  * another organisation is not affected, and nobody who is not signed in reads or tests the limits;
  * the "what would happen" answer is the same decision a real request gets.

    docker compose exec -T munitas-api python /verify/v161_limits.py
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

import httpx  # noqa: E402

from common import API, CANARY, CUSTODIAN, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402
from v128_person_keys_erasure import ENGINEER, RESEARCHER, good_contract, register_dataset, reset_keys  # noqa: E402
from v140_custom_steps_and_files import reasons_of  # noqa: E402
from v137_locked_files import make_wav, upload_whole  # noqa: E402

DPO = "canary-dpo"
OTHER_ORGANISATION = "neighbour-custodian"  # a custodian of another test organisation
BY = "U168 operator"


def script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "/scripts-admin/set-limit.py", "--tenant", CANARY, "--by", BY, *args], capture_output=True, text=True, timeout=60)


def limits_of(who: str = RESEARCHER) -> dict:
    return {r["key"]: r for r in api("GET", "/limits", headers=bearer_for(who)).json()["limits"]}


def would(who: str, capability: str, role: str, size: int, length: int) -> httpx.Response:
    return api("POST", "/limits/what-would-happen", json={"capability": capability, "role": role, "size": size, "length": length}, headers=bearer_for(who))


MADE: list[str] = []


def ask_rows(version: str, who: str, rows: int, days: int = 3) -> httpx.Response:
    r = api("POST", "/locked-column-leases", json={"dataset_version_id": version, "purpose": "U168 check the limit", "days": days, "rows": rows}, headers=bearer_for(who))
    if r.status_code == 201:
        MADE.append(r.json()["id"])
    return r


def tidy() -> None:
    """The requests this check made are declined by the approver, so none is left waiting in the queue of the people who decide."""
    for lease in MADE:
        api("POST", f"/locked-column-leases/{lease}/decline", headers=bearer_for(CUSTODIAN), json={"note": "U168 finished with this request"})
    MADE.clear()


def clean() -> None:
    with db() as conn:
        conn.execute("delete from limit_version where tenant_id = %s", (CANARY,))
        conn.commit()


def run() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    from app import opa
    from app import db as app_db
    if app_db.pool.closed:
        app_db.pool.open()
    tag = uuid.uuid4().hex[:6]
    clean()

    heading("Nothing set: everything is as it always was")
    r = limits_of()
    check("there are twelve limits, each with its default and the most the platform allows", len(r) == 12 and all(v["source"] == "default" and v["value"] == v["default"] for v in r.values()), str(sorted(r))[:200])
    check("the defaults are the policy's own constants (a drift between the two would show here)",
          r["locked_rows.most_days"]["default"] == opa.read_document("munitas/access/locked_lease_max_days") and r["locked_rows.most_rows"]["default"] == opa.read_document("munitas/access/locked_lease_max_rows")
          and sorted(r["locked_rows.who_may_ask"]["default"]) == sorted(opa.read_document("munitas/access/locked_lease_requester_roles"))
          and {"records": r["files_in_page.most_files"]["default"], "hours": r["files_in_page.most_hours"]["default"]} == opa.read_document("munitas/access/file_lease_default_caps")["console"]
          and {"records": r["files_in_notebook.most_files"]["default"], "hours": r["files_in_notebook.most_hours"]["default"]} == opa.read_document("munitas/access/file_lease_default_caps")["notebook"])
    refused = would(RESEARCHER, "locked_rows", "analyst", 30, 9)
    check("a refusal reads as it always did: the roles, the days and the rows", refused.status_code == 200 and refused.json()["allowed"] is False
          and "this person holds no role that may ask for a lease on locked columns (a data engineer, a de-identification reviewer, a privacy auditor or the data protection officer)" in refused.json()["reasons"]
          and "a lease lasts between 1 and 7 days" in refused.json()["reasons"] and "a lease opens between 1 and 20 rows" in refused.json()["reasons"], str(refused.json())[:300])
    check("20 rows for 7 days by a data engineer is accepted, 21 is not", would(RESEARCHER, "locked_rows", "pipeline_operator", 20, 7).json()["allowed"] is True and would(RESEARCHER, "locked_rows", "pipeline_operator", 21, 7).json()["allowed"] is False)
    check("the files: 10 in the page for an hour, 200 in a notebook for 8", would(RESEARCHER, "files_in_page", "analyst", 10, 1).json()["allowed"] is True and would(RESEARCHER, "files_in_page", "analyst", 11, 1).json()["allowed"] is False
          and would(RESEARCHER, "files_in_notebook", "analyst", 200, 8).json()["allowed"] is True and would(RESEARCHER, "files_in_notebook", "analyst", 201, 8).json()["allowed"] is False)
    check("nobody who is not signed in reads or tests the limits", httpx.get(f"{API}/limits", timeout=30.0).status_code == 401
          and httpx.post(f"{API}/limits/what-would-happen", json={"capability": "locked_rows", "role": "analyst", "size": 1, "length": 1}, timeout=30.0).status_code == 401)
    check("a capability that does not exist is refused in words", would(RESEARCHER, "everything", "analyst", 1, 1).status_code == 400)

    heading("A locked table to ask about")
    rows = [{"visit_id": f"v{i:03d}", "clinic_number": f"M{i:03d}-{tag}", "patient_name": f"Person{i:03d} Zzexample{tag}", "diagnosis": f"dx-{i:03d}", "clinic": "north" if i % 2 else "south", "age": 20 + i} for i in range(1, 41)]
    sealed = api("POST", f"/datasets/{register_dataset(ENGINEER)}/seal-table", headers=bearer_for(ENGINEER), json={"contract": good_contract(), "rows": rows})
    check("a table of forty people with locked columns is sealed", sealed.status_code == 201, f"{sealed.status_code} {sealed.text[:140]}")
    table = sealed.json()["id"]
    screen = api("GET", f"/dataset-versions/{table}/locking", headers=bearer_for(ENGINEER)).json()
    check("the table's page says the limits in force: 20 rows, 7 days", screen["limits"] == {"days": 7, "rows": 20}, str(screen["limits"]))
    check("21 rows is refused by the real request, in the words it always used", ask_rows(table, ENGINEER, 21).status_code == 403 and "a lease opens between 1 and 20 rows" in reasons_of(ask_rows(table, ENGINEER, 21)))

    heading("An operator sets a limit")
    listed = script("--list")
    check("the script lists every limit with its default", listed.returncode == 0 and "locked_rows.most_rows" in listed.stdout and "the default" in listed.stdout, (listed.stdout + listed.stderr)[:200])
    dry = script("--limit", "locked_rows.most_rows", "--value", "30", "--reason", "U168 dry run", "--dry-run")
    check("a dry run says what would be written and writes nothing", dry.returncode == 0 and "Dry run" in dry.stdout and limits_of()["locked_rows.most_rows"]["source"] == "default", (dry.stdout + dry.stderr)[:200])
    done = script("--limit", "locked_rows.most_rows", "--value", "30", "--reason", "U168 the audit asked for thirty")
    check("setting the most rows to 30 is accepted", done.returncode == 0 and "Written" in done.stdout, (done.stdout + done.stderr)[:200])
    now = limits_of()["locked_rows.most_rows"]
    check("the limits page says 30, that the organisation set it, who, and why", now["value"] == 30 and now["source"] == "organisation" and now["set_by"] == BY and now["reason"] == "U168 the audit asked for thirty" and now["set_at"], str(now)[:240])
    check("other limits are untouched", limits_of()["locked_rows.most_days"]["source"] == "default")
    check("the table's page now says 30 rows", api("GET", f"/dataset-versions/{table}/locking", headers=bearer_for(ENGINEER)).json()["limits"]["rows"] == 30)
    check("the real request for 21 rows, refused before, is now accepted", ask_rows(table, ENGINEER, 21).status_code == 201)
    too_many = ask_rows(table, RESEARCHER, 31)
    check("and 31 is refused, with the new limit in the words", too_many.status_code == 403 and "a lease opens between 1 and 30 rows" in reasons_of(too_many), f"{too_many.status_code} {reasons_of(too_many)[:160]}")
    check("what-would-happen says the same as the real request", would(RESEARCHER, "locked_rows", "pipeline_operator", 30, 3).json()["allowed"] is True and would(RESEARCHER, "locked_rows", "pipeline_operator", 31, 3).json()["allowed"] is False)

    heading("The most the platform allows")
    over = script("--limit", "locked_rows.most_rows", "--value", "101", "--reason", "U168 too much")
    check("the script refuses a value above the most the platform allows, and says what that is", over.returncode == 1 and "most the platform allows" in over.stderr and "100" in over.stderr, (over.stdout + over.stderr)[:200])
    check("and nothing was written", limits_of()["locked_rows.most_rows"]["value"] == 30)
    with db() as conn:
        conn.execute("insert into limit_version (tenant_id, limit_key, value, reason, set_by) values (%s, 'locked_rows.most_days', '500', 'U168 put in by hand', %s)", (CANARY, BY))
        conn.execute("insert into limit_version (tenant_id, limit_key, value, reason, set_by) values (%s, 'locked_rows.who_may_ask', '[\"platform_admin\", \"dpo\"]', 'U168 put in by hand', %s)", (CANARY, BY))
        conn.commit()
    held = limits_of()
    check("a row put in the table by hand above the maximum is held to it all the same: 30 days, not 500", held["locked_rows.most_days"]["value"] == 30, str(held["locked_rows.most_days"]["value"]))
    check("and a role the platform never allows to ask is left out", held["locked_rows.who_may_ask"]["value"] == ["dpo"], str(held["locked_rows.who_may_ask"]["value"]))

    heading("Who may ask")
    roles = script("--limit", "locked_rows.who_may_ask", "--value", "pipeline_operator,notebook_explore", "--reason", "U168 researchers may ask")
    check("an operator sets who may ask: a data engineer and a researcher", roles.returncode == 0, (roles.stdout + roles.stderr)[:200])
    check("the page lists them in the platform's order", limits_of()["locked_rows.who_may_ask"]["value"] == ["pipeline_operator", "notebook_explore"], str(limits_of()["locked_rows.who_may_ask"]["value"]))
    ok = ask_rows(table, RESEARCHER, 5)
    check("a researcher can now ask, in a real request", ok.status_code == 201, f"{ok.status_code} {reasons_of(ok)[:140]}")
    no = would(DPO, "locked_rows", "dpo", 5, 3).json()
    check("the data protection officer, left out, is refused, and the refusal names who may ask", no["allowed"] is False
          and "this person holds no role that may ask for a lease on locked columns (a data engineer or a researcher)" in no["reasons"], str(no)[:300])
    bad = script("--limit", "locked_rows.who_may_ask", "--value", "pipeline_operator,platform_admin", "--reason", "U168 not allowed")
    check("the script refuses a role the platform never allows to ask", bad.returncode == 1 and "platform_admin" in bad.stderr, (bad.stdout + bad.stderr)[:200])
    nobody = script("--limit", "locked_rows.who_may_ask", "--value", "", "--reason", "U168 nobody")
    check("an organisation may say nobody may ask", nobody.returncode == 0 and limits_of()["locked_rows.who_may_ask"]["value"] == [], (nobody.stdout + nobody.stderr)[:200])
    emptied = would(RESEARCHER, "locked_rows", "pipeline_operator", 5, 3).json()
    check("and then everybody is refused, in words", emptied["allowed"] is False and "no role may ask for a lease on locked columns in this organisation" in emptied["reasons"], str(emptied)[:200])

    heading("The files")
    script("--limit", "files_in_page.most_files", "--value", "25", "--reason", "U168 more files in the page")
    script("--limit", "files_in_notebook.most_hours", "--value", "24", "--reason", "U168 a longer day in a notebook")
    check("25 files in the page is accepted, 26 is not", would(RESEARCHER, "files_in_page", "analyst", 25, 1).json()["allowed"] is True and would(RESEARCHER, "files_in_page", "analyst", 26, 1).json()["allowed"] is False)
    check("24 hours in a notebook is accepted, 25 is not", would(RESEARCHER, "files_in_notebook", "analyst", 10, 24).json()["allowed"] is True and would(RESEARCHER, "files_in_notebook", "analyst", 10, 25).json()["allowed"] is False)
    dataset = register_dataset(ENGINEER)
    upload_whole(dataset, "r-1.wav", make_wav(40_000), f"U168-{tag}")
    files = api("POST", f"/datasets/{dataset}/seal-files", headers=bearer_for(ENGINEER)).json()["id"]
    caps = api("GET", f"/dataset-versions/{files}/file-lease", headers=bearer_for(RESEARCHER)).json()["caps"]
    check("the files page is told the limits in force", caps == {"console": {"records": 25, "hours": 1}, "notebook": {"records": 200, "hours": 24}}, str(caps))
    too_long = api("POST", f"/dataset-versions/{files}/file-leases", json={"kind": "notebook", "purpose": "U168", "records": 1, "hours": 25}, headers=bearer_for(RESEARCHER))
    check("a real request for 25 hours in a notebook is refused with the limit in force in the words", too_long.status_code == 403 and "a notebook lease lasts at most 24 hours" in reasons_of(too_long), f"{too_long.status_code} {reasons_of(too_long)[:140]}")

    heading("Never edited, and put back")
    with db() as conn:
        try:
            conn.execute("update limit_version set value = '999' where tenant_id = %s", (CANARY,))
            conn.commit()
            edited = True
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            edited = "never edited" not in str(exc)
    check("the database refuses to edit a limit that was set", edited is False)
    with db() as conn:
        history = conn.execute("select count(*) as n from limit_version where tenant_id = %s and limit_key = 'locked_rows.who_may_ask'", (CANARY,)).fetchone()["n"]
    check("setting a limit again added a row each time: the three changes to who may ask are all kept", history == 3, str(history))
    back = script("--limit", "locked_rows.most_rows", "--reset", "--reason", "U168 back to the default")
    check("resetting puts the default back as the newest row", back.returncode == 0 and limits_of()["locked_rows.most_rows"]["value"] == 20, (back.stdout + back.stderr)[:200])
    script("--limit", "locked_rows.who_may_ask", "--reset", "--reason", "U168 back to the default")
    check("and with who may ask reset too, 21 rows is refused again and 20 is accepted", would(RESEARCHER, "locked_rows", "pipeline_operator", 21, 3).json()["allowed"] is False
          and would(RESEARCHER, "locked_rows", "pipeline_operator", 20, 3).json()["allowed"] is True)

    heading("Another organisation")
    other = limits_of(OTHER_ORGANISATION)
    check("another organisation's person sees the defaults and none of this organisation's changes", all(v["source"] == "default" for v in other.values()), str({k: v["source"] for k, v in other.items()}))
    check("their test uses their own limits: 20 rows by a data engineer is accepted there", would(OTHER_ORGANISATION, "locked_rows", "pipeline_operator", 20, 3).json()["allowed"] is True)

    clean()
    return summary("U168")


def main() -> int:
    try:
        return run()
    finally:
        tidy()
        clean()


if __name__ == "__main__":
    sys.exit(main())
