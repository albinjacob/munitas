r"""L7: a table of ten million people is checked whole, locked a batch at a time by the platform, and read back, and one person among them is erased.

LARGE TIER. Never part of the ordinary suite. Run it with verify\run_large.py, which says what it will do, how long it takes and how much disk it needs
before it does anything.

A table about people is written in two passes by a worker that never holds a key. Checked: the worker's memory stays level through both passes; the platform
makes one key for each person and writes a line in the key log for each batch, not for each person; the table can be read a page at a time from either end
and a lease's sample is found in one pass; erasing one person among all of them destroys one key and does not rewrite the table. The locking rate
is printed, so the next plan can say how long ten million people take.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import flow  # noqa: E402
import people  # noqa: E402
from common import require_api, summary  # noqa: E402

ID = "L7"
TITLE = "a table of ten million people is locked a batch at a time, read back a page at a time, and one person is erased"
ROWS = flow.scaled(10_000_000)


def need() -> dict:
    return people.need(ROWS, "60 to 240 (not measured yet)")


def main() -> int:
    require_api()
    people.run(ROWS, ID)
    return summary(ID)


if __name__ == "__main__":
    sys.exit(main())
