"""The agent's checkpointer on the project's own database layer.

LangGraph's `PostgresSaver` (package langgraph-checkpoint-postgres) keeps a graph's state in four tables so a run can be resumed after a failure. It is
written for psycopg, which is LGPL 3, and installing it installs psycopg. The project replaced that driver with a permissive one (pgdb, over pg8000), so
this is the same checkpointer on pgdb. The tables, the stored forms and the order of migrations are the library's own, unchanged, so a thread saved by
one can be read by the other (verify/v175_pgdb.py proves it in both directions); only the driver calls are different:

* a JSON value goes over as JSON text, which the column's type makes JSON;
* the metadata filter is cast to JSON where it is compared;
* a write of several statements is a transaction, which the library used a pipeline for, and a read is a plain cursor.

Everything else is copied from langgraph-checkpoint-postgres 2.0.25 (MIT, Copyright (c) LangChain, Inc.), whose notice is kept with this file's licence note
in the repository's third-party list. Only the synchronous saver is here: nothing in this project runs the graph asynchronously.
"""

from __future__ import annotations

import json
import random
import threading
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, Optional, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_serializable_checkpoint_metadata,
)
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.types import TASKS

from . import pgdb

MetadataInput = Optional[dict[str, Any]]

# To add a migration, add a string to the list: its position is its version number. These are the library's, in the library's order.
MIGRATIONS = [
    """CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER PRIMARY KEY
);""",
    """CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);""",
    """CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);""",
    """CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);""",
    "ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP not null;",
    # A no-op that keeps the versions in the migrations table right, because an empty migration was once added to the list.
    "SELECT 1;",
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id);
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);
    """,
    """ALTER TABLE checkpoint_writes ADD COLUMN task_path TEXT NOT NULL DEFAULT '';""",
]

SELECT_SQL = """
select
    thread_id,
    checkpoint,
    checkpoint_ns,
    checkpoint_id,
    parent_checkpoint_id,
    metadata,
    (
        select array_agg(array[bl.channel::bytea, bl.type::bytea, bl.blob])
        from jsonb_each_text(checkpoint -> 'channel_versions')
        inner join checkpoint_blobs bl
            on bl.thread_id = checkpoints.thread_id
            and bl.checkpoint_ns = checkpoints.checkpoint_ns
            and bl.channel = jsonb_each_text.key
            and bl.version = jsonb_each_text.value
    ) as channel_values,
    (
        select
        array_agg(array[cw.task_id::text::bytea, cw.channel::bytea, cw.type::bytea, cw.blob] order by cw.task_id, cw.idx)
        from checkpoint_writes cw
        where cw.thread_id = checkpoints.thread_id
            and cw.checkpoint_ns = checkpoints.checkpoint_ns
            and cw.checkpoint_id = checkpoints.checkpoint_id
    ) as pending_writes
from checkpoints """

SELECT_PENDING_SENDS_SQL = f"""
select
    checkpoint_id,
    array_agg(array[type::bytea, blob] order by task_path, task_id, idx) as sends
from checkpoint_writes
where thread_id = %s
    and checkpoint_id = any(%s)
    and channel = '{TASKS}'
group by checkpoint_id
"""

UPSERT_CHECKPOINT_BLOBS_SQL = """
    INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (thread_id, checkpoint_ns, channel, version) DO NOTHING
"""

UPSERT_CHECKPOINTS_SQL = """
    INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id)
    DO UPDATE SET
        checkpoint = EXCLUDED.checkpoint,
        metadata = EXCLUDED.metadata;
"""

UPSERT_CHECKPOINT_WRITES_SQL = """
    INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, type, blob)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx) DO UPDATE SET
        channel = EXCLUDED.channel,
        type = EXCLUDED.type,
        blob = EXCLUDED.blob;
"""

INSERT_CHECKPOINT_WRITES_SQL = """
    INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, type, blob)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx) DO NOTHING
"""


class PgdbSaver(BaseCheckpointSaver[str]):
    """Checkpointer that stores checkpoints in a Postgres database, through pgdb."""

    lock: threading.Lock

    def __init__(self, conn: "pgdb.Connection | pgdb.ConnectionPool", serde: SerializerProtocol | None = None) -> None:
        super().__init__(serde=serde)
        self.conn = conn
        self.lock = threading.Lock()

    @classmethod
    @contextmanager
    def from_conn_string(cls, conn_string: str) -> Iterator["PgdbSaver"]:
        """A saver on its own connection to this database, closed when the block ends."""
        with pgdb.connect(conn_string, autocommit=True, row_factory=pgdb.dict_row) as conn:
            yield cls(conn)

    def setup(self) -> None:
        """Make the tables and run the migrations, which the library requires before a saver is first used."""
        with self._cursor() as cur:
            cur.execute(MIGRATIONS[0])
            row = cur.execute("SELECT v FROM checkpoint_migrations ORDER BY v DESC LIMIT 1").fetchone()
            version = -1 if row is None else row["v"]
            for v, migration in zip(range(version + 1, len(MIGRATIONS)), MIGRATIONS[version + 1:]):
                cur.execute(migration)
                cur.execute(f"INSERT INTO checkpoint_migrations (v) VALUES ({v})")

    # -- reading

    def list(self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None, before: RunnableConfig | None = None,
             limit: int | None = None) -> Iterator[CheckpointTuple]:
        """The checkpoints that match, newest first."""
        where, args = self._search_where(config, filter, before)
        query = SELECT_SQL + where + " ORDER BY checkpoint_id DESC"
        if limit:
            query += f" LIMIT {int(limit)}"
        with self._cursor() as cur:
            cur.execute(query, args)
            values = cur.fetchall()
            if not values:
                return
            if to_migrate := [v for v in values if v["checkpoint"]["v"] < 4 and v["parent_checkpoint_id"]]:
                cur.execute(SELECT_PENDING_SENDS_SQL, (values[0]["thread_id"], [v["parent_checkpoint_id"] for v in to_migrate]))
                grouped_by_parent = defaultdict(list)
                for value in to_migrate:
                    grouped_by_parent[value["parent_checkpoint_id"]].append(value)
                for sends in cur:
                    for value in grouped_by_parent[sends["checkpoint_id"]]:
                        if value["channel_values"] is None:
                            value["channel_values"] = []
                        self._migrate_pending_sends(sends["sends"], value["checkpoint"], value["channel_values"])
            for value in values:
                yield self._load_checkpoint_tuple(value)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """The checkpoint a config names, or the latest of its thread."""
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = get_checkpoint_id(config)
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        if checkpoint_id:
            args: tuple[Any, ...] = (thread_id, checkpoint_ns, checkpoint_id)
            where = "WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s"
        else:
            args = (thread_id, checkpoint_ns)
            where = "WHERE thread_id = %s AND checkpoint_ns = %s ORDER BY checkpoint_id DESC LIMIT 1"
        with self._cursor() as cur:
            cur.execute(SELECT_SQL + where, args)
            value = cur.fetchone()
            if value is None:
                return None
            if value["checkpoint"]["v"] < 4 and value["parent_checkpoint_id"]:
                cur.execute(SELECT_PENDING_SENDS_SQL, (thread_id, [value["parent_checkpoint_id"]]))
                if sends := cur.fetchone():
                    if value["channel_values"] is None:
                        value["channel_values"] = []
                    self._migrate_pending_sends(sends["sends"], value["checkpoint"], value["channel_values"])
            return self._load_checkpoint_tuple(value)

    # -- writing

    def put(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        """Save a checkpoint with the config it belongs to."""
        configurable = config["configurable"].copy()
        thread_id = configurable.pop("thread_id")
        checkpoint_ns = configurable.pop("checkpoint_ns")
        checkpoint_id = configurable.pop("checkpoint_id", None)
        copy = checkpoint.copy()
        copy["channel_values"] = copy["channel_values"].copy()
        next_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": checkpoint["id"]}}

        # Plain values are kept in the checkpoint's own row; everything else goes to the blobs table.
        blob_values = {}
        for k, v in checkpoint["channel_values"].items():
            if v is None or isinstance(v, (str, int, float, bool)):
                pass
            else:
                blob_values[k] = copy["channel_values"].pop(k)

        with self._cursor(transaction=True) as cur:
            if blob_versions := {k: v for k, v in new_versions.items() if k in blob_values}:
                cur.executemany(UPSERT_CHECKPOINT_BLOBS_SQL, self._dump_blobs(thread_id, checkpoint_ns, blob_values, blob_versions))
            cur.execute(UPSERT_CHECKPOINTS_SQL, (thread_id, checkpoint_ns, checkpoint["id"], checkpoint_id, json.dumps(copy),
                                                 json.dumps(get_serializable_checkpoint_metadata(config, metadata))))
        return next_config

    def put_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        """Save the writes a task made before its checkpoint exists."""
        query = UPSERT_CHECKPOINT_WRITES_SQL if all(w[0] in WRITES_IDX_MAP for w in writes) else INSERT_CHECKPOINT_WRITES_SQL
        with self._cursor(transaction=True) as cur:
            cur.executemany(query, self._dump_writes(config["configurable"]["thread_id"], config["configurable"]["checkpoint_ns"],
                                                     config["configurable"]["checkpoint_id"], task_id, task_path, writes))

    def delete_thread(self, thread_id: str) -> None:
        """Delete every checkpoint, blob and write of a thread."""
        with self._cursor(transaction=True) as cur:
            for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                cur.execute(f"DELETE FROM {table} WHERE thread_id = %s", (str(thread_id),))

    # -- plumbing

    @contextmanager
    def _cursor(self, *, transaction: bool = False) -> Iterator["pgdb.Cursor"]:
        """A cursor that returns rows as dictionaries, on a connection nobody else is using. A write is one transaction."""
        with self.lock:
            if isinstance(self.conn, pgdb.ConnectionPool):
                with self.conn.connection() as conn:
                    yield from self._use(conn, transaction)
            else:
                yield from self._use(self.conn, transaction)

    @staticmethod
    def _use(conn: "pgdb.Connection", transaction: bool) -> Iterator["pgdb.Cursor"]:
        if transaction:
            with conn.transaction():
                with conn.cursor(row_factory=pgdb.dict_row) as cur:
                    yield cur
        else:
            with conn.cursor(row_factory=pgdb.dict_row) as cur:
                yield cur

    def _load_checkpoint_tuple(self, value: dict) -> CheckpointTuple:
        return CheckpointTuple(
            {"configurable": {"thread_id": value["thread_id"], "checkpoint_ns": value["checkpoint_ns"], "checkpoint_id": value["checkpoint_id"]}},
            {**value["checkpoint"], "channel_values": {**(value["checkpoint"].get("channel_values") or {}), **self._load_blobs(value["channel_values"])}},
            value["metadata"],
            ({"configurable": {"thread_id": value["thread_id"], "checkpoint_ns": value["checkpoint_ns"], "checkpoint_id": value["parent_checkpoint_id"]}}
             if value["parent_checkpoint_id"] else None),
            self._load_writes(value["pending_writes"]),
        )

    def _migrate_pending_sends(self, pending_sends: list[tuple[bytes, bytes]], checkpoint: dict[str, Any], channel_values: list[tuple[bytes, bytes, bytes]]) -> None:
        if not pending_sends:
            return
        enc, blob = self.serde.dumps_typed([self.serde.loads_typed((c.decode(), b)) for c, b in pending_sends])
        channel_values.append((TASKS.encode(), enc.encode(), blob))
        checkpoint["channel_versions"][TASKS] = (max(checkpoint["channel_versions"].values()) if checkpoint["channel_versions"] else self.get_next_version(None, None))

    def _load_blobs(self, blob_values: list[tuple[bytes, bytes, bytes]]) -> dict[str, Any]:
        if not blob_values:
            return {}
        return {k.decode(): self.serde.loads_typed((t.decode(), v)) for k, t, v in blob_values if t.decode() != "empty"}

    def _dump_blobs(self, thread_id: str, checkpoint_ns: str, values: dict[str, Any], versions: ChannelVersions) -> list[tuple[str, str, str, str, str, bytes | None]]:
        if not versions:
            return []
        return [(thread_id, checkpoint_ns, k, cast(str, ver), *(self.serde.dumps_typed(values[k]) if k in values else ("empty", None))) for k, ver in versions.items()]

    def _load_writes(self, writes: list[tuple[bytes, bytes, bytes, bytes]]) -> list[tuple[str, str, Any]]:
        return [(tid.decode(), channel.decode(), self.serde.loads_typed((t.decode(), v))) for tid, channel, t, v in writes] if writes else []

    def _dump_writes(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str, task_id: str, task_path: str,
                     writes: Sequence[tuple[str, Any]]) -> list[tuple[str, str, str, str, str, int, str, str, bytes]]:
        return [(thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, WRITES_IDX_MAP.get(channel, idx), channel, *self.serde.dumps_typed(value))
                for idx, (channel, value) in enumerate(writes)]

    def get_next_version(self, current: str | None, channel: None) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(current.split(".")[0])
        return f"{current_v + 1:032}.{random.random():016}"

    def _search_where(self, config: RunnableConfig | None, filter: MetadataInput, before: RunnableConfig | None = None) -> tuple[str, list[Any]]:
        """The WHERE clause for `list`, and its values, given a config, a metadata filter and a checkpoint to list before."""
        wheres: list[str] = []
        param_values: list[Any] = []
        if config:
            wheres.append("thread_id = %s ")
            param_values.append(config["configurable"]["thread_id"])
            checkpoint_ns = config["configurable"].get("checkpoint_ns")
            if checkpoint_ns is not None:
                wheres.append("checkpoint_ns = %s")
                param_values.append(checkpoint_ns)
            if checkpoint_id := get_checkpoint_id(config):
                wheres.append("checkpoint_id = %s ")
                param_values.append(checkpoint_id)
        if filter:
            wheres.append("metadata @> %s::jsonb ")
            param_values.append(json.dumps(filter))
        if before is not None:
            wheres.append("checkpoint_id < %s ")
            param_values.append(get_checkpoint_id(before))
        return ("WHERE " + " AND ".join(wheres) if wheres else "", param_values)


__all__ = ["PgdbSaver"]
