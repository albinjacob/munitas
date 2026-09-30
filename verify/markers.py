"""Lines the verification scripts print for a program, not a person, to read.

One definition, imported by both sides, so the writer and the reader cannot
drift apart. Standard library only: run_all.py imports this and cannot import
common.py, which needs a database to be configured.
"""

# Printed by common.summary() at the end of every script, followed by one JSON
# object: {"passed": n, "failed": n, "skipped": n}. run_all.py reads it.
COUNTS_MARKER = "##MUNITAS-CHECK-COUNTS##"
