"""U33: a retired tenant is closed, not erased.

Retiring exists because a tenant cannot be deleted. Its sealed versions, the
leases approved inside it and the directory entries those approvals name are all
immutable on purpose, so a customer who leaves needs an honest state rather than
a row that quietly disappears.

That makes "closed" a claim with two halves, and both are tested here. Nothing
more may be written to it, and everything already written to it stays readable.
A tenant that failed the first half would be closed in name only. One that
failed the second would be deletion wearing a different word.

The check worth reading is the last one. The refusal is a trigger rather than a
line in each endpoint, and a trigger is only as good as the list of tables it was
attached to, so the list is compared against every table that carries a
`tenant_id` and the exemptions are named rather than assumed.

    docker compose exec -T munitas-api python /verify/v33_retired_tenant.py
"""

from __future__ import annotations

import sys
import uuid

import psycopg

from common import (CANARY, ENGINEER, api, bearer_for, check, db,
                     fixture_contract, fixture_tenant, fixture_version,
                     heading, require_api, summary)

# Tables that write to a retired tenant on purpose. Each one is a decision, so
# each one carries its reason.
EXEMPT = {
    # A refused write is exactly the kind of thing that must still be recorded.
    # A log that goes silent when a tenant closes is worse than no log.
    "access_decision",
    # Winding a closed tenant down is the one thing left to do to it.
    "storage_reclamation",
    # Closing and reopening a tenant has to remain possible.
    "tenant",
    # A closed organisation's records stay readable, and opening one through the
    # catalog records which key was asked for. The row holds no records and
    # grants nothing a lease or a role had not already decided.
    "catalog_key",
}


def make_retired_fixture() -> dict:
    """A tenant with real data in it, then closed, built fresh every run.

    Not a permanent fixture. This test used to lean on `t1`, the platform's
    original demonstration tenant, existing forever in every environment
    because sealed rows could not be deleted any other way. scripts/admin/nuke-tenant.py
    now exists precisely so nothing needs to make that trade again: this
    builds a small retired tenant and teardown() below removes it.
    """
    tenant_id = f"verify-retired-{uuid.uuid4().hex[:8]}"
    fixture_tenant(tenant_id)
    schema_id = fixture_contract(tenant_id)
    version = fixture_version(tenant_id, schema_id, "RAW")

    person_id = f"{tenant_id}-person"
    with db() as conn:
        conn.execute(
            "insert into directory (id, tenant_id, label, kind, roles) "
            "values (%s, %s, 'Verify retired-tenant person', 'human', '{}')",
            (person_id, tenant_id),
        )
        conn.execute(
            "insert into access_decision "
            "(at, principal, principal_kind, tenant_id, dataset_version_id, "
            " allowed, reasons) "
            "values (now(), %s, 'human', %s, %s, true, '{fixture}')",
            (person_id, tenant_id, version["id"]),
        )
        conn.execute(
            "update tenant set purpose = 'retired' where id = %s", (tenant_id,)
        )

    return {
        "tenant_id": tenant_id, "schema_id": schema_id,
        "dataset_id": version["dataset_id"], "version_id": version["id"],
        "person_id": person_id,
    }


def teardown(fixture: dict) -> None:
    """Undo make_retired_fixture(), including the version it sealed.

    Mirrors scripts/admin/nuke-tenant.py's own move (disable the immutability rule,
    delete, re-enable) rather than shelling out to that script: it runs on
    the host and this suite runs inside the API container, and there is
    nothing here it knows how to do that isn't already just as simple for
    the handful of rows this one fixture created.
    """
    with db() as conn:
        conn.execute("alter table dataset_version disable rule dataset_version_no_delete")
        try:
            conn.execute("delete from access_decision where tenant_id = %s",
                          (fixture["tenant_id"],))
            conn.execute("delete from dataset_version where id = %s",
                          (fixture["version_id"],))
            conn.execute("delete from dataset where id = %s", (fixture["dataset_id"],))
            conn.execute("delete from schema_contract where id = %s",
                          (fixture["schema_id"],))
            conn.execute("delete from directory where id = %s",
                          (fixture["person_id"],))
            conn.execute("delete from tenant where id = %s", (fixture["tenant_id"],))
        finally:
            conn.execute("alter table dataset_version enable rule dataset_version_no_delete")


def main() -> int:
    require_api()

    fixture = make_retired_fixture()
    RETIRED = fixture["tenant_id"]
    try:
        return run_checks(RETIRED)
    finally:
        teardown(fixture)


def run_checks(RETIRED: str) -> int:
    heading("U33: nothing more may be written to a closed tenant")

    refused = api("POST", "/datasets", json={
        "tenant_id": RETIRED, "name": f"after-closing-{uuid.uuid4().hex[:8]}",
    })
    check("the control plane refuses a write to a retired tenant",
          refused.status_code == 409, f"HTTP {refused.status_code}")
    check("and says the tenant is closed rather than failing obscurely",
          "retired" in refused.text, refused.text[:120])

    # Not through the API. Somebody with a database connection is the case the
    # trigger exists for, because a check in the control plane would not see it.
    direct = None
    with db() as conn:
        try:
            conn.execute(
                "insert into dataset (id, tenant_id, name) values (%s, %s, %s)",
                (str(uuid.uuid4()), RETIRED, f"direct-{uuid.uuid4().hex[:8]}"),
            )
        except psycopg.Error as exc:
            direct = exc
    check("and so does the database, to somebody bypassing the API",
          direct is not None, type(direct).__name__ if direct else "the insert succeeded")

    heading("U33: a live tenant is unaffected")

    allowed = api("POST", "/datasets", json={
        "tenant_id": CANARY, "name": f"still-open-{uuid.uuid4().hex[:8]}",
    })
    check("writes to a tenant that is still open are untouched",
          allowed.status_code in (200, 201), f"HTTP {allowed.status_code}")

    heading("U33: its records are still readable")

    # Not read through GET /datasets: that endpoint now derives its tenant
    # from the caller's own session (item 24's read-endpoint auth fix), and
    # this fixture's directory row is inserted directly rather than backed
    # by a real Kratos identity, so no session can be minted that is
    # actually scoped to RETIRED. The database is what "still readable"
    # means here regardless of who could log in as this tenant.
    total = db_one(
        "select count(*) as n from dataset where tenant_id = %s", (RETIRED,)
    )["n"]
    check("its datasets still list", total > 0, f"{total} datasets")

    # Not read through GET /access-decisions either, for the same reason as
    # /datasets just above: it now derives its tenant from the caller's own
    # session, and no session can be minted scoped to a fixture directory
    # row with no real Kratos identity behind it.
    decision_count = db_one(
        "select count(*) as n from access_decision where tenant_id = %s", (RETIRED,)
    )["n"]
    check("its audit trail still reads back",
          decision_count > 0, f"{decision_count} decisions")

    version = db_one(
        "select id from dataset_version where tenant_id = %s limit 1", (RETIRED,)
    )
    if version:
        lineage = api("GET", f"/lineage/{version['id']}")
        check("and the lineage of a version inside it still answers",
              lineage.status_code == 200, f"HTTP {lineage.status_code}")
    else:
        check("and the lineage of a version inside it still answers", False,
              "the tenant holds no versions to ask about")

    heading("U33: it is not offered as somebody to act as")

    people = api("GET", "/directory", params={"kind": "human"},
                headers=bearer_for(ENGINEER))
    tenants = {p["tenant_id"] for p in people.json()} if people.status_code == 200 else set()
    check("the chooser offers nobody from a closed tenant",
          RETIRED not in tenants, ", ".join(sorted(tenants)) or "nobody")
    check("and it offers somebody, so the check is not passing on an empty list",
          len(tenants) > 0, f"{len(people.json())} people")

    everyone = api("GET", "/directory", params={"kind": "human", "purpose": "all"},
                   headers=bearer_for(ENGINEER))
    check("they are still findable when somebody asks for every tenant",
          everyone.status_code == 200
          and len(everyone.json()) >= len(people.json()),
          f"{len(everyone.json())} against {len(people.json())}")

    heading("U33: no tenant-scoped table was missed")

    # The refusal is only as good as the list of tables it was attached to. This
    # compares that list against reality rather than trusting it, because a table
    # added later would otherwise be a hole nobody notices: writes to a closed
    # tenant would simply start working again.
    with db() as conn:
        scoped = {
            r["table_name"] for r in conn.execute("""
                select c.table_name
                from information_schema.columns c
                join information_schema.tables t
                  on t.table_name = c.table_name and t.table_schema = c.table_schema
                where c.column_name = 'tenant_id'
                  and c.table_schema = 'public'
                  and t.table_type = 'BASE TABLE'
            """).fetchall()
        }
        guarded = {
            r["rel"] for r in conn.execute("""
                select c.relname as rel
                from pg_trigger tg
                join pg_class c on c.oid = tg.tgrelid
                join pg_proc p on p.oid = tg.tgfoid
                where p.proname = 'refuse_write_to_retired_tenant'
                  and not tg.tgisinternal
            """).fetchall()
        }

    # Partitions inherit their parent's triggers, so they are not separate holes.
    parents = {t for t in scoped if not t.startswith("access_decision_")}
    unguarded = parents - guarded - EXEMPT

    check("every table carrying a tenant_id is guarded or named as an exception",
          not unguarded, ", ".join(sorted(unguarded)) or f"{len(guarded)} guarded")
    check("and the exceptions are ones that exist",
          EXEMPT <= scoped | {"tenant"}, ", ".join(sorted(EXEMPT - scoped - {"tenant"})))

    return summary("U33")


def db_one(sql: str, params: tuple) -> dict | None:
    with db() as conn:
        return conn.execute(sql, params).fetchone()


if __name__ == "__main__":
    sys.exit(main())
