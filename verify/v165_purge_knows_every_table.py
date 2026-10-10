"""U173: the purge of an organisation knows every table that holds its data without an organisation column of its own.

A table with no `tenant_id` column that points at one that has it can only be reached through that parent. If the purge does not know such a table, it
cannot remove the parent either: it stops with a foreign key error before anything is changed, and an organisation with pipelines or agents cannot be
closed. The list the purge keeps by hand (`INDIRECT_TABLES`) went stale this way when pipeline versions, step runs, agent run attempts, gate leaks,
notebook attempts and external credentials were added. This asks the database for every such table and fails when one is missing, so the next new table
is caught the day it is added, not the day somebody closes an organisation.

Read only. Runs inside the API container, where the purge's own code is:

    docker compose exec -T munitas-api python /verify/v165_purge_knows_every_table.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

sys.path.insert(0, "/app")  # the platform's own code, which sits beside the verify folder in the container

from common import check, db, heading, summary  # noqa: E402

from app import purge  # noqa: E402

FOREIGN_KEYS_FROM_TABLES_WITHOUT_AN_ORGANISATION = """
select c.conrelid::regclass::text as child, a.attname as col, c.confrelid::regclass::text as parent
  from pg_constraint c
  join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
 where c.contype = 'f' and c.connamespace = 'public'::regnamespace
   and not exists (select 1 from information_schema.columns k
                    where k.table_schema = 'public' and k.table_name = c.conrelid::regclass::text and k.column_name = 'tenant_id')
 order by 1, 2
"""


def main() -> int:
    heading("U173: every table that holds an organisation's data through a parent is one the purge deletes")
    with db() as conn:
        with_tenant = {r["table_name"] for r in conn.execute(
            "select distinct table_name from information_schema.columns where table_schema = 'public' and column_name = 'tenant_id'").fetchall()}
        links = conn.execute(FOREIGN_KEYS_FROM_TABLES_WITHOUT_AN_ORGANISATION).fetchall()

    reached = with_tenant | set(purge.INDIRECT_TABLES)
    children = sorted({r["child"] for r in links if r["parent"] in reached})
    check("there are tables that hold data through a parent, so this check has something to read", len(children) > 0, f"{len(children)} tables")
    for child in children:
        check(f"{child} is in the purge's list", child in purge.INDIRECT_TABLES, "listed" if child in purge.INDIRECT_TABLES else "MISSING")

    heading("U173: and the list names no table that is not there")
    tables = {r["child"] for r in links}
    for name in sorted(purge.INDIRECT_TABLES):
        check(f"{name} still exists and points at a table the purge reaches", name in tables, "yes" if name in tables else "no such table or no such link")
    return summary("U173")


if __name__ == "__main__":
    sys.exit(main())
