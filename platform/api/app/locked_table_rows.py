"""Reading rows of a table about people that is stored as a table, a page at a time.

A table about people brought in through the large route is an Iceberg table of Parquet data files, with the locked columns stored as text. It can be ten
million rows, so it is never read whole: a page is found by position (which data file, which row in it), and a lease's sample is chosen in one pass over
the key column alone.

The position is an opaque `cursor`. Reading at a position is cheap whatever the position, because a Parquet file says how many rows each of its row groups
holds, so only the row group that holds the position is read.

The sample a lease opens is the same one the small route gives: the rows whose ids rank lowest when ranked by a hash of the version, the person and the id.
Ranking every id is one pass over one column, and only the few chosen rows are then read in full.
"""

from __future__ import annotations

import base64
import bisect
import hashlib
import json
from urllib.parse import urlparse

from . import db, iceberg

PAGE_DEFAULT = 200
PAGE_MOST = 1000


class NoTable(Exception):
    """The version has no stored table to read."""


class Files:
    """The data files of one version's table, in a fixed order, and a way to open them."""

    def __init__(self, version: dict):
        import pyarrow.fs as pafs
        from pyiceberg.table import StaticTable

        ref = db.one("select metadata_location from iceberg_table_ref where dataset_version_id = %s", (version["id"],))
        if not ref:
            raise NoTable(str(version["id"]))
        props = iceberg._file_io(version["tenant_id"], version["storage_backend"] or "seaweedfs")
        table = StaticTable.from_metadata(ref["metadata_location"], properties=props)
        self.paths = sorted(task.file.file_path for task in table.scan().plan_files())
        endpoint = urlparse(props["s3.endpoint"])
        self.fs = pafs.S3FileSystem(access_key=props["s3.access-key-id"], secret_key=props["s3.secret-access-key"], endpoint_override=endpoint.netloc,
                                    scheme=endpoint.scheme or "http", region=props["s3.region"])

    def open(self, index: int):
        import pyarrow.parquet as pq

        handle = self.fs.open_input_file(self.paths[index].removeprefix("s3://"))
        return handle, pq.ParquetFile(handle)


def encode(file: int, row: int) -> str:
    return base64.urlsafe_b64encode(json.dumps([file, row]).encode()).decode()


def decode(cursor: str | None) -> tuple[int, int]:
    if not cursor:
        return 0, 0
    try:
        file, row = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if isinstance(file, int) and isinstance(row, int) and file >= 0 and row >= 0:
            return file, row
    except (ValueError, TypeError):
        pass
    raise ValueError("that is not a place in the table")


def page(files: Files, cursor: str | None, limit: int) -> tuple[list[dict], str | None]:
    """Up to `limit` rows from the position, and the position after them, or None when the table ends."""
    import pyarrow as pa

    index, offset = decode(cursor)
    out: list[pa.Table] = []
    have = 0
    while index < len(files.paths) and have < limit:
        handle, parquet = files.open(index)
        try:
            passed = 0
            for group in range(parquet.metadata.num_row_groups):
                rows = parquet.metadata.row_group(group).num_rows
                if passed + rows <= offset:
                    passed += rows
                    continue
                table = parquet.read_row_group(group)
                start = max(0, offset - passed)
                take = min(limit - have, table.num_rows - start)
                out.append(table.slice(start, take))
                have += take
                offset = passed + start + take
                passed += rows
                if have >= limit:
                    break
            total = parquet.metadata.num_rows
        finally:
            handle.close()
        if offset >= total:
            index, offset = index + 1, 0
    rows = pa.concat_tables(out).to_pylist() if out else []
    more = index < len(files.paths)
    return rows, encode(index, offset) if more else None


def sample(files: Files, version_id: str, principal: str, key_column: str, limit: int) -> list[dict]:
    """The rows a lease opens, in the order they stand in the table: the `limit` whose ids rank lowest, found in one pass over the key column."""
    best: list[tuple[str, int, int, int]] = []        # (rank, file, row group, row in the group), kept sorted, never longer than `limit`
    for index in range(len(files.paths)):
        handle, parquet = files.open(index)
        try:
            for group in range(parquet.metadata.num_row_groups):
                keys = parquet.read_row_group(group, columns=[key_column]).column(0).to_pylist()
                for row, key in enumerate(keys):
                    rank = hashlib.sha256(f"{version_id}|{principal}|{key}".encode("utf-8")).hexdigest()
                    if len(best) < limit or rank < best[-1][0]:
                        bisect.insort(best, (rank, index, group, row))
                        del best[limit:]
        finally:
            handle.close()
    chosen: dict[tuple[int, int], list[int]] = {}
    for _, index, group, row in best:
        chosen.setdefault((index, group), []).append(row)
    rows = []
    for (index, group), picks in sorted(chosen.items()):
        handle, parquet = files.open(index)
        try:
            table = parquet.read_row_group(group)
        finally:
            handle.close()
        rows.extend(table.take(sorted(picks)).to_pylist())
    return rows
