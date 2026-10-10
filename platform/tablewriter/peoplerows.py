"""A table about people, written in two passes, with the locking done by the platform.

The rows of a table about people are about somebody. The columns that identify a person are locked under a key made for that person, and erasing the
person destroys that key. A table worker writes the table but never holds a key, so:

  * The first pass reads the whole file and checks every row against the contract, including that every row names a person. Nothing is locked and
    no key exists yet, so a file that is wrong at its last row makes nothing.
  * The second pass reads the file again. The cells to be locked go to the platform in batches (`lock`), which finds or makes each person's key and sends
    back the locked cells and the person codes. The worker writes those, and the readable columns as they were, to the table.

The columns that name nobody stay readable and keep their types. A locked column is stored as text. A column that is replaced by the person code holds
the code. A last column, `person_code`, holds the code of each row's person.

While the second pass runs, the readable quasi-identifying columns and the person code of every row are written to a scratch file, and when the table is
done the group-size report is worked out from it with a query that spills to disk, so a table of ten million people is counted in the memory of one batch.
"""

from __future__ import annotations

import datetime
import decimal
import os
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Iterator

from . import types as kinds
from .textrows import Collector

PERSON_COLUMN = "person_code"
SUBJECT_ROWS = 5_000                    # rows sent to be locked in one call


@dataclass
class LockSpec:
    """What the writer needs to write a table about people. The `lock` function is the platform's: a worker's is a call to the control plane."""

    read_fields: list[dict]                    # the file's columns, with the types the contract gives them
    subject_column: str
    key_column: str
    locked: list[str]
    replaced: list[str]
    counted: list[str]                         # the readable quasi-identifying columns a group-size report counts
    threshold: int
    lock: Callable[[list[str], list[str], dict[str, list]], tuple[list[str], dict[str, list[str]]]]
    batch_rows: int = SUBJECT_ROWS


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def check_subjects(batch, spec: LockSpec, first_row: int, found: Collector) -> None:
    """Add a problem for every row of this batch that names nobody. `first_row` is the number of the batch's first row."""
    for i, value in enumerate(batch.column(batch.schema.get_field_index(spec.subject_column)).to_pylist()):
        if _blank(value):
            found.add(first_row + i, spec.subject_column, f"names nobody, because {spec.subject_column} is empty")


def _json(value, spec: dict):
    """A cell as the value that is locked: its JSON form, so that opening it gives back the same thing. Dates, times and decimals are their text."""
    if value is None:
        return None
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    return value


def locked_batches(batches: Iterator, spec: LockSpec, store_schema, checkpoint, counter: "GroupCounter | None" = None) -> Iterator:
    """The batches of the file as the table stores them: locked cells in place of the locked columns, the person code where it replaces a column and
    in its own last column. Each batch goes to the platform in calls of `spec.batch_rows` rows."""
    import pyarrow as pa

    by_name = {f["name"]: f for f in spec.read_fields}
    for batch in batches:
        checkpoint()
        names = batch.schema.names
        subjects = [str(v).strip() for v in batch.column(names.index(spec.subject_column)).to_pylist()]
        record_ids = [str(v) for v in batch.column(names.index(spec.key_column)).to_pylist()]
        values = {name: [_json(v, by_name[name]) for v in batch.column(names.index(name)).to_pylist()] for name in spec.locked}
        codes: list[str] = []
        locked: dict[str, list[str]] = {name: [] for name in spec.locked}
        for start in range(0, batch.num_rows, spec.batch_rows):
            stop = start + spec.batch_rows
            got_codes, got = spec.lock(subjects[start:stop], record_ids[start:stop], {n: v[start:stop] for n, v in values.items()})
            if len(got_codes) != len(subjects[start:stop]) or any(len(got[n]) != len(got_codes) for n in spec.locked):
                raise RuntimeError("the platform did not answer for every row it was sent")
            codes.extend(got_codes)
            for name in spec.locked:
                locked[name].extend(got[name])
        arrays = []
        for target in store_schema:
            if target.name == PERSON_COLUMN or target.name in spec.replaced:
                arrays.append(pa.array(codes, pa.string()))
            elif target.name in locked:
                arrays.append(pa.array(locked[target.name], pa.string()))
            else:
                arrays.append(batch.column(names.index(target.name)))
        if counter is not None:
            counter.add(batch, codes)
        yield pa.RecordBatch.from_arrays(arrays, schema=store_schema)


class GroupCounter:
    """Keeps, for every row, the readable quasi-identifying columns and the person's code, in a scratch file, and says from it how many different
    people share each combination. Values are used to group and are never kept past the report."""

    def __init__(self, counted: list[str], threshold: int, scratch: str | None):
        import pyarrow as pa
        import pyarrow.parquet as pq

        self.counted, self.threshold = counted, threshold
        self.directory = tempfile.mkdtemp(prefix="munitas-groups-", dir=scratch)
        self.path = os.path.join(self.directory, "rows.parquet")
        self.schema = pa.schema([pa.field(f"c{i}", pa.string()) for i in range(len(counted))] + [pa.field("person", pa.string())])
        self.writer = pq.ParquetWriter(self.path, self.schema, compression="zstd")
        self.rows = 0

    def add(self, batch, codes: list[str]) -> None:
        import pyarrow as pa

        columns = []
        for name in self.counted:
            column = batch.column(batch.schema.get_field_index(name))
            columns.append(column if pa.types.is_string(column.type) else column.cast(pa.string()))
        columns.append(pa.array(codes, pa.string()))
        self.writer.write_batch(pa.RecordBatch.from_arrays(columns, schema=self.schema))
        self.rows += batch.num_rows

    def report(self, memory: str = "400MB") -> dict:
        """The report the platform records: the same numbers and advice as group_sizes.compute gives for a table held in memory."""
        import duckdb

        self.writer.close()
        con = duckdb.connect(":memory:")
        try:
            con.execute(f"pragma memory_limit='{memory}'")
            con.execute("pragma threads=2")
            con.execute(f"pragma temp_directory='{os.path.join(self.directory, 'spill').replace(chr(92), '/')}'")
            quoted = self.path.replace(chr(92), "/").replace("'", "''")      # a view cannot take a parameter, and this path is one the writer made itself
            con.execute(f"create view t as select * from read_parquet('{quoted}')")
            people = con.execute("select count(distinct person) from t").fetchone()[0]
            found = self._count(con, list(range(len(self.counted))))
            advice = []
            if found["people_in_small_groups"]:
                for i, name in enumerate(self.counted):
                    after = self._count(con, [j for j in range(len(self.counted)) if j != i])["people_in_small_groups"]
                    if after < found["people_in_small_groups"]:
                        advice.append({"lock": name, "people_in_small_groups_after": after})
                advice.sort(key=lambda a: (a["people_in_small_groups_after"], a["lock"]))
            return {"threshold": self.threshold, "columns": self.counted, "people": int(people), **found, "advice": advice}
        finally:
            con.close()

    def _count(self, con, columns: list[int]) -> dict:
        if not columns:
            return {"groups": 0, "smallest_group": None, "groups_below": 0, "people_in_small_groups": 0}
        q = ", ".join(f"c{i}" for i in columns)
        con.execute(f"create or replace temp table p as select distinct {q}, person from t")
        groups, smallest, below = con.execute(
            f"select count(*), min(n), count(*) filter (where n < ?) from (select count(*) as n from p group by {q})", [self.threshold]).fetchone()
        people = con.execute(f"select count(distinct person) from (select person, count(*) over (partition by {q}) as n from p) where n < ?", [self.threshold]).fetchone()[0]
        con.execute("drop table p")
        return {"groups": int(groups), "smallest_group": None if smallest is None else int(smallest), "groups_below": int(below), "people_in_small_groups": int(people)}

    def close(self) -> None:
        import shutil

        try:
            self.writer.close()
        except Exception:  # noqa: BLE001 - already closed
            pass
        shutil.rmtree(self.directory, ignore_errors=True)
