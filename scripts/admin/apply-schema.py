"""Apply platform/schema.sql to the running database.

Postgres runs schema.sql once, when its data folder is first created, so a
change to the file reaches an existing install only if something applies it.
The file is written to be applied again (create ... if not exists, drop rule if
exists, create or replace), so this is safe on a database that already has
everything.

    .venv/Scripts/python.exe scripts/admin/apply-schema.py

Reports what it did not touch as well as what it did: the tables and triggers
named here are the ones the Iceberg work added, so a person can see they exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ports_config import PORTS  # noqa: E402

DSN = f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform"
SCHEMA = Path(__file__).resolve().parents[2] / "platform" / "schema.sql"


def main() -> int:
    sql = SCHEMA.read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True, connect_timeout=10) as conn:
        conn.execute(sql)
        have = {r[0] for r in conn.execute(
            "select table_name from information_schema.tables where table_schema = 'public'"
        ).fetchall()}
    for name in ("iceberg_table_ref", "catalog_token"):
        print(f"  {'present' if name in have else 'MISSING'}  {name}")
    return 0 if {"iceberg_table_ref", "catalog_token"} <= have else 1


if __name__ == "__main__":
    sys.exit(main())
