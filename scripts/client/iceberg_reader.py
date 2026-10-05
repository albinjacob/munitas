"""Read a Munitas Iceberg table with PyIceberg for as long as it takes.

The catalog hands out a storage key that stops working after a while (see
RUNBOOK, "Reading governed tables from DuckDB or PyIceberg"). DuckDB asks for a
fresh one by itself. PyIceberg does not: it opens each data file with the key it
held when it loaded the table, so a read that outlasts the key fails on the next
file with an access error.

This reads the table one data file at a time and loads the table again, which
asks the catalog for a fresh key and decides access afresh, whenever the key is
about to run out. If access has ended, loading is refused and the read stops
with the catalog's reason, which is how a revoked lease ends a long read.

    from iceberg_reader import read_batches
    for batch in read_batches(catalog, ("my_dataset", "v1")):
        ...   # a pyarrow RecordBatch
"""

from __future__ import annotations

import time
from collections.abc import Iterator

EXPIRY_PROPERTY = "s3.session-token-expires-at-ms"


def _expires_at(table) -> float | None:
    """When this table's key stops working, in seconds, or None if the catalog
    did not say (then the key is not known to expire)."""
    raw = table.io.properties.get(EXPIRY_PROPERTY)
    return int(raw) / 1000 if raw else None


def read_batches(catalog, identifier, *, margin_seconds: float = 10) -> Iterator:
    """Yield the table's rows as Arrow record batches, loading the table again
    when the key has less than `margin_seconds` left."""
    from pyiceberg.expressions import AlwaysTrue
    from pyiceberg.io.pyarrow import ArrowScan

    table = catalog.load_table(identifier)
    for task in table.scan().plan_files():
        expires = _expires_at(table)
        if expires is not None and time.time() > expires - margin_seconds:
            table = catalog.load_table(identifier)
        scan = ArrowScan(table.metadata, table.io, table.schema(), AlwaysTrue(), True)
        yield from scan.to_record_batches([task])
