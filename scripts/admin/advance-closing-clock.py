"""Bring an organisation's closing dates forward, as if days had passed. For verification and recording only.

A closing takes 15 days and then another 15, which nobody can wait for while recording a
walkthrough or running a check. The platform works the phase out from two dates on the organisation
(`tenant_phase` in platform/schema.sql), so moving those dates is all that a clock moving on would
change. This changes nothing else: a legal hold, the records and the rules are exactly as they were.

    .venv\Scripts\python.exe scripts/admin/advance-closing-clock.py --tenant harbour --end-retiring
    .venv\Scripts\python.exe scripts/admin/advance-closing-clock.py --tenant harbour --end-closing

`--end-retiring` makes the first period end two hours ago, which leaves 15 days of the second, as it would
after 15 real days. `--end-closing` does the same for the second
(and the first, which has to end before it). It refuses an organisation that is not being closed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ports_config import PORTS  # noqa: E402

PG_DSN = os.environ.get("PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--end-retiring", action="store_true")
    group.add_argument("--end-closing", action="store_true")
    args = parser.parse_args()

    with psycopg.connect(PG_DSN, row_factory=dict_row, autocommit=True) as conn:
        row = conn.execute("select tenant_phase(id) as phase from tenant where id = %s", (args.tenant,)).fetchone()
        if not row or row["phase"] not in ("retiring", "closing", "purge_due"):
            print(f"{args.tenant!r} is not being closed ({row['phase'] if row else 'no such organisation'}). Nothing changed.")
            return 1
        if args.end_retiring:
            conn.execute("update tenant set retiring_until = now() - interval '2 hours', "
                         "closing_until = now() + interval '15 days' "
                         "where id = %s and retiring_until > now()", (args.tenant,))
        else:
            conn.execute("update tenant set retiring_until = least(retiring_until, now() - interval '2 hours'), "
                         "closing_until = now() - interval '1 hour' where id = %s", (args.tenant,))
        after = conn.execute("select tenant_phase(id) as phase from tenant where id = %s", (args.tenant,)).fetchone()
    print(f"{args.tenant}: {row['phase']} -> {after['phase']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
