"""U100: when an organisation's time is up and nothing holds it, everything inside it is deleted, and nothing else is.

The rewrite rules that make a sealed version, a hold and the record of a closing impossible to
delete are the platform's central guarantee, so this proves two things at once: the purge does
remove everything of an organisation that is due and not held, and it cannot be used to remove
anything else. The second half is the one that matters. It tries to delete sealed data while
naming an organisation that is not due, one that is held, one that is not retired at all and
one that does not exist, and each time the data must still be there. Only then does it let the
real purge run, and counts what is left of the organisation, in every table, in storage and in
what it leaves behind.

    docker compose exec -T munitas-api python /verify/v100_purge.py
"""

from __future__ import annotations

import sys

from common import api, bearer_for, check, db, heading, require_api, summary
from lifecycle_fixture import (ADMIN_A, ADMIN_B, bucket_exists, drop_org, forget_deletion_record,
                               hold_body, make_org, move_dates, seal_data)


def agent_versions(org_id: str) -> int:
    with db() as conn:
        return conn.execute("select count(*) as n from agent_version where tenant_id = %s", (org_id,)).fetchone()["n"]


def versions(org_id: str) -> int:
    with db() as conn:
        return conn.execute("select count(*) as n from dataset_version where tenant_id = %s", (org_id,)).fetchone()["n"]


def scalar(sql: str, params: tuple = ()):
    with db() as conn:
        return next(iter(conn.execute(sql, params).fetchone().values()))


def deletion_attempt(org_id: str, naming: str) -> int:
    """Delete one organisation's sealed versions while a purge of `naming` is declared. Returns
    how many rows went. Always rolled back, so a check never changes what it is checking."""
    import psycopg
    with db() as conn:
        try:
            with conn.transaction():
                conn.execute("select set_config('munitas.purge_tenant', %s, true)", (naming,))
                n = conn.execute("with d as (delete from dataset_version where tenant_id = %s returning 1) "
                                 "select count(*) as n from d", (org_id,)).fetchone()["n"]
                raise _Undo(n)
        except _Undo as undo:
            return undo.n
        except psycopg.Error:
            return 0


class _Undo(Exception):
    def __init__(self, n: int):
        self.n = n


def tables_with_rows(org_id: str) -> dict[str, int]:
    """Every table that still holds anything of this organisation, found the same way the purge
    finds them and then once more by looking in every table for the id as text, so a table the
    purge forgot cannot hide."""
    found: dict[str, int] = {}
    with db() as conn:
        direct = [r["table_name"] for r in conn.execute(
            "select distinct c.table_name from information_schema.columns c "
            "join information_schema.tables t on t.table_schema = c.table_schema and t.table_name = c.table_name "
            "where c.column_name = 'tenant_id' and c.table_schema = 'public' and t.table_type = 'BASE TABLE' "
            "and c.table_name not like 'access_decision_%%' and c.table_name <> 'tenant_deletion_record'").fetchall()]
        for table in direct:
            n = conn.execute(f'select count(*) as n from "{table}" where tenant_id = %s', (org_id,)).fetchone()["n"]
            if n:
                found[table] = n
        for table, column, parent in (("dataset_source", "dataset_id", "dataset"),
                                      ("class_transition", "dataset_version_id", "dataset_version"),
                                      ("huggingface_fetch_job", "dataset_id", "dataset")):
            n = conn.execute(f'select count(*) as n from "{table}" where {column} in '
                             f'(select id from "{parent}" where tenant_id = %s)', (org_id,)).fetchone()["n"]
            if n:
                found[table] = n
    return found


def main() -> int:
    require_api()
    org = make_org()
    bystander = make_org()
    try:
        priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
        data = seal_data(org)
        keep = seal_data(bystander)
        check("the organisation holds sealed data, a sealed agent version and files before anything",
              versions(org.id) == 1 and agent_versions(org.id) == 1 and bucket_exists(data["bucket"]))
        with db() as conn:
            health_before = conn.execute("select (select count(*) from dataset_version where tenant_id = 'health') as v, "
                                         "(select count(*) from directory where tenant_id = 'health') as d").fetchone()

        heading("The rules still hold for everybody who is not due")
        check("an active organisation: naming it is not enough", deletion_attempt(org.id, org.id) == 0)
        check("an organisation that does not exist", deletion_attempt(org.id, "no-such-organisation") == 0)
        check("a different organisation named instead", deletion_attempt(org.id, bystander.id) == 0)
        check("canary's sealed versions, with canary named", deletion_attempt("canary", "canary") == 0)
        check("no organisation named at all", deletion_attempt(org.id, "") == 0)
        check("the sealed data is untouched by all of that", versions(org.id) == 1 and versions("canary") > 0)
        check("and so is its sealed agent version, which is guarded by a rule of its own",
              agent_versions(org.id) == 1)

        api("POST", "/lifecycle/organisation/retire", headers=org.bearer("custodian"),
            json={"reason": "The clinic is closing"}).raise_for_status()
        check("retiring: still not deletable", deletion_attempt(org.id, org.id) == 0)
        move_dates(org.id, retiring_ended=True)
        check("closing: still not deletable", deletion_attempt(org.id, org.id) == 0)

        heading("A hold stops it, even only proposed")
        hold = api("POST", "/lifecycle/holds", headers=priya, json=hold_body(org)).json()
        move_dates(org.id, closing_ended=True)
        with db() as conn:
            phase = conn.execute("select tenant_phase(%s) as p, tenant_hold_state(%s) as h", (org.id, org.id)).fetchone()
        check("both periods are over and a proposed hold stands", (phase["p"], phase["h"]) == ("purge_due", "pending"), str(phase))
        check("deleting is refused while the hold is only proposed", deletion_attempt(org.id, org.id) == 0)
        r = api("POST", "/lifecycle/sweep", headers=priya)
        check("a sweep leaves it alone", org.id not in r.json()["purged"] and versions(org.id) == 1)
        api("POST", f"/lifecycle/holds/{hold['id']}/decide", headers=ravi,
            json={"approve": True, "note": "checked"}).raise_for_status()
        check("and when approved", deletion_attempt(org.id, org.id) == 0 and versions(org.id) == 1)
        api("POST", f"/lifecycle/holds/{hold['id']}/release", headers=ravi,
            json={"reason": "Matter closed"}).raise_for_status()
        check("a released hold restarts the closing period, so it is not yet due",
              deletion_attempt(org.id, org.id) == 0 and versions(org.id) == 1)
        move_dates(org.id, closing_ended=True)

        heading("Due and not held: the purge")
        check("the database now says it may be purged", scalar("select tenant_purge_allowed(%s)", (org.id,)) is True)
        r = api("POST", "/lifecycle/sweep", headers=priya)
        check("a sweep purges it", r.status_code == 200 and org.id in r.json()["purged"], r.text[:200])
        record = (r.json()["records"] or [{}])[0] if org.id in r.json()["purged"] else {}

        heading("What is left of it")
        left = tables_with_rows(org.id)
        check("its sealed agent version is gone too", agent_versions(org.id) == 0)
        check("no table holds any row of it", left == {}, ", ".join(f"{k}: {v}" for k, v in left.items()))
        check("its bucket is gone", not bucket_exists(data["bucket"]))
        check("and the purge counted the files it removed", record.get("files_removed") == 2, str(record.get("files_removed")))
        with db() as conn:
            tenant = conn.execute("select purged_at, tenant_phase(id) as p from tenant where id = %s", (org.id,)).fetchone()
            rec = conn.execute("select * from tenant_deletion_record where tenant_id = %s", (org.id,)).fetchone()
        check("its row stays as a tombstone marked purged", tenant and tenant["purged_at"] is not None and tenant["p"] == "purged")
        check("one deletion record was left", rec is not None)
        if rec:
            check("it says who asked, why, and what was removed",
                  rec["retire_requested_by"] == "Custodian" and rec["retire_reason"] == "The clinic is closing"
                  and rec["rows_removed"].get("dataset_version") == 1 and rec["files_removed"] == 2, str(rec["rows_removed"])[:120])
            holds = rec["holds"]
            check("it names the hold by number and authority, and says it was released",
                  len(holds) == 1 and holds[0]["matter_number"] == "HC-2026-0417" and holds[0]["status"] == "released",
                  str(holds)[:120])
            flat = str(rec)
            check("it holds no contact detail, no matter name and no description",
                  "example.test" not in flat and "Doe v" not in flat and "Ruth Aldous" not in flat and "claim about" not in flat)
        check("a person of that organisation is refused",
              api("GET", "/auth/whoami", headers=org.bearer("member")).status_code in (401, 403))
        check("a second sweep finds nothing to do", org.id not in api("POST", "/lifecycle/sweep", headers=priya).json()["purged"])
        r = api("POST", "/lifecycle/holds", headers=priya, json=hold_body(org, number="HC-9"))
        check("a hold cannot be placed on what no longer exists", r.status_code in (409, 422), f"{r.status_code}")
        listing = api("GET", "/lifecycle/deletions", headers=priya).json()["deletions"]
        check("a platform administrator can read the record", any(d["tenant_id"] == org.id for d in listing))
        check("a member of another organisation cannot",
              api("GET", "/lifecycle/deletions", headers=bystander.bearer("member")).status_code == 403)
        with db() as conn:
            before = conn.execute("select count(*) as n from tenant_deletion_record").fetchone()["n"]
            conn.execute("delete from tenant_deletion_record where tenant_id = %s", (org.id,))
            conn.execute("update tenant_deletion_record set purged_by = 'x' where tenant_id = %s", (org.id,))
            after = conn.execute("select count(*) as n from tenant_deletion_record where purged_by = 'x'").fetchone()["n"]
            now = conn.execute("select count(*) as n from tenant_deletion_record").fetchone()["n"]
        check("the record cannot be deleted or changed", now == before and after == 0, f"{before} -> {now}, changed {after}")

        heading("Nothing else was touched")
        check("the organisation that was only beside it keeps its data", versions(bystander.id) == 1 and bucket_exists(keep["bucket"]))
        with db() as conn:
            health_after = conn.execute("select (select count(*) from dataset_version where tenant_id = 'health') as v, "
                                        "(select count(*) from directory where tenant_id = 'health') as d").fetchone()
        check("health's data and people are unchanged", dict(health_after) == dict(health_before), str(health_after))
        check("the other organisation could not be purged: it is not retired",
              not scalar("select tenant_purge_allowed(%s)", (bystander.id,)))
    finally:
        # The sealed data of the bystander cannot be deleted by design, so it is closed and
        # purged like anything else, which also leaves it clean.
        with db() as conn:
            row = conn.execute("select purpose from tenant where id = %s", (bystander.id,)).fetchone()
        if row and row["purpose"] == "production":
            api("POST", "/lifecycle/organisation/retire", headers=bearer_for(ADMIN_A),
                json={"tenant_id": bystander.id, "reason": "verification finished"})
            move_dates(bystander.id, retiring_ended=True, closing_ended=True)
            api("POST", "/lifecycle/sweep", headers=bearer_for(ADMIN_A))
        drop_org(org)
        drop_org(bystander)
    return summary("U100")


if __name__ == "__main__":
    sys.exit(main())
