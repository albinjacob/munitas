"""Set one of an organisation's limits, or list them. The way a limit is changed until the Limits page can edit.

A limit is a ceiling on what people may ask for: who may ask to read rows of locked columns, the most rows and days, the most files and hours to open locked files in the page
and in a notebook. Each has a default (today's behaviour) and a most the platform allows. A limit that is set is written as a new row and never edits an older one, with who set it
and why, and the latest is the one in force. This checks the same bounds the platform checks, and refuses a value above the most the platform allows.

    .venv\\Scripts\\python.exe scripts/admin/set-limit.py --tenant health --list
    .venv\\Scripts\\python.exe scripts/admin/set-limit.py --tenant health --limit locked_rows.most_rows --value 30 --by "Priya Shah" --reason "the audit asked for 30"
    .venv\\Scripts\\python.exe scripts/admin/set-limit.py --tenant health --limit locked_rows.who_may_ask --value pipeline_operator,dpo --by "Priya Shah" --reason "researchers go through an engineer"
    .venv\\Scripts\\python.exe scripts/admin/set-limit.py --tenant health --limit locked_rows.most_rows --reset --by "Priya Shah" --reason "back to the default"

`--reset` writes the default as the new value (the history keeps the earlier ones). `--dry-run` says what would be written and writes nothing. A list of roles is written as comma-separated names;
an empty list (`--value ""`) means nobody may ask.

There is no second person here: whoever can run this holds the database. The page that edits will ask a second person to confirm a loosening; until then this is an operator's tool,
and every use is a row that says who, when and why.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pgdb
from pgdb import dict_row

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "platform" / "api"))
sys.path.insert(0, "/app")
from ports_config import PORTS  # noqa: E402
from app import limits as org_limits  # noqa: E402

PG_DSN = os.environ.get("MUNITAS_PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform")


def parse(limit: org_limits.Limit, text: str) -> object:
    if limit.kind == "roles":
        return [r.strip() for r in text.split(",") if r.strip()]
    try:
        return json.loads(text)
    except ValueError:
        return text


def listing(conn, tenant: str) -> None:
    rows = conn.execute("select distinct on (limit_key) limit_key, value, reason, set_by, set_at from limit_version where tenant_id = %s order by limit_key, set_at desc, id desc", (tenant,)).fetchall()
    stored = {r["limit_key"]: r for r in rows}
    for limit in org_limits.LIMITS:
        row = stored.get(limit.key)
        now = row["value"] if row else None
        shown = (now if now is not None else (list(limit.default) if limit.kind == "roles" else limit.default))
        ceiling = f", at most {limit.maximum} {limit.unit}" if limit.maximum else ""
        print(f"{limit.key:36} {json.dumps(shown):60} {'set by ' + row['set_by'] + ' on ' + str(row['set_at'])[:16] + ': ' + row['reason'] if row else 'the default'}{ceiling}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--limit", choices=[r.key for r in org_limits.LIMITS])
    parser.add_argument("--value")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--by", help="the name of the person who decided")
    parser.add_argument("--reason")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with pgdb.connect(PG_DSN, row_factory=dict_row, autocommit=True) as conn:
        if not conn.execute("select 1 as x from tenant where id = %s", (args.tenant,)).fetchone():
            print(f"There is no organisation called {args.tenant!r}.", file=sys.stderr)
            return 2
        if args.list:
            listing(conn, args.tenant)
            return 0
        if not args.limit or (args.value is None) == (not args.reset) or not args.by or not args.reason:
            print("To set a limit give --limit, --by, --reason and either --value or --reset. To see the limits, use --list.", file=sys.stderr)
            return 2
        limit = org_limits.BY_KEY[args.limit]
        wanted = list(limit.default) if (args.reset and limit.kind == "roles") else limit.default if args.reset else parse(limit, args.value)
        try:
            value = org_limits.check(limit, wanted)
        except org_limits.BadValue as exc:
            print(f"Not set: {exc}.", file=sys.stderr)
            return 1
        print(f"{args.tenant}: {limit.key} -> {json.dumps(value)} (by {args.by}: {args.reason})")
        if args.dry_run:
            print("Dry run: nothing was written.")
            return 0
        conn.execute("insert into limit_version (tenant_id, limit_key, value, reason, set_by, source) values (%s, %s, %s, %s, %s, 'operator script')",
                     (args.tenant, limit.key, json.dumps(value), args.reason.strip(), args.by.strip()))
        print("Written. New requests in this organisation use it now; a lease already granted keeps what it was granted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
