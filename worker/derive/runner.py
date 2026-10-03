"""Runs one derivation's query, inside a container that has no network.

Platform-authored and bind-mounted read-only by the worker. It holds no
credential and could not use one: the inputs were copied to /data before it
started, and the result is written to /out for the worker to upload.

DuckDB is locked before the query is touched. External access is switched off
and the one folder it may read is /data, so a query can read the inputs it was
given and nothing else on the machine, and cannot write anywhere. The query is
also checked here to be a single SELECT, whatever the platform checked before,
because this is the boundary that matters.

Environment (all optional, for running it outside a container):
  DERIVE_DATA  folder holding one folder per input, named by its alias
  DERIVE_OUT   folder the result is written into
  DERIVE_JOB   the job file: sql, inputs, primary_key, fields

The last line printed is one JSON object: {"status": "ok", ...} or
{"status": "failed", "reason": ...}. A reason names a column or a kind of
error and never quotes a value, because a value in a message is data in a log.
"""

from __future__ import annotations

import datetime
import glob
import hashlib
import json
import os
import re
import sys

DATA = os.environ.get("DERIVE_DATA", "/data")
OUT = os.environ.get("DERIVE_OUT", "/out")
JOB = os.environ.get("DERIVE_JOB", "/job/job.json")
MAX_ROWS = int(os.environ.get("DERIVE_MAX_ROWS", 5_000_000))
MAX_BYTES = int(os.environ.get("DERIVE_MAX_BYTES", 512 * 1024 * 1024))
CHUNK = 5000
ALIAS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
# DuckDB errors whose message is about names and syntax, not about values.
MESSAGE_IS_SAFE = ("ParserException", "BinderException", "CatalogException")


def finish(status: str, **details) -> None:
    print(json.dumps({"status": status, **details}), flush=True)
    sys.exit(0 if status == "ok" else 2)


def convert(value, kind: str):
    if value is None:
        return None
    if kind == "int":
        return int(value)
    if kind == "float":
        return float(value)
    if kind == "bool":
        return bool(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    return str(value)


def run() -> None:
    import duckdb

    job = json.load(open(JOB, encoding="utf-8"))
    fields = job["fields"]
    names = [f["name"] for f in fields]
    kinds = {f["name"]: f["type"] for f in fields}

    con = duckdb.connect(":memory:")
    con.execute("SET threads = 1")
    con.execute("SET memory_limit = '400MB'")
    for item in job["inputs"]:
        alias = item["alias"]
        if not ALIAS.match(alias):
            finish("failed", reason="an input has a name that is not letters, digits and underscores")
        pattern = f"{DATA}/{alias}/iceberg/data/*.parquet"
        if not glob.glob(pattern):
            finish("failed", reason=f"input {alias} has no table files")
        con.execute(f'CREATE VIEW "{alias}" AS SELECT * FROM read_parquet(\'{pattern}\')')
    con.execute(f"SET allowed_directories = ['{DATA}']")
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")

    tree = json.loads(con.execute("SELECT json_serialize_sql(?)", [job["sql"]]).fetchone()[0])
    if tree.get("error") or len(tree.get("statements", [])) != 1:
        finish("failed", reason="only a single SELECT can be run")

    cursor = con.execute(job["sql"])
    produced = [d[0] for d in cursor.description]
    if produced != names:
        finish("failed", reason="the query now produces different columns than were confirmed")
    key_columns = job["primary_key"]
    key_positions = [names.index(k) for k in key_columns]

    os.makedirs(OUT, exist_ok=True)
    digest, written, rows, seen = hashlib.sha256(), 0, 0, set()
    # One row per line, so the platform can read the result as a stream and write it as a table without holding it whole.
    with open(os.path.join(OUT, "records.ndjson"), "wb") as out:
        def put(chunk: bytes) -> None:
            nonlocal written
            out.write(chunk)
            digest.update(chunk)
            written += len(chunk)
        while True:
            batch = cursor.fetchmany(CHUNK)
            if not batch:
                break
            for raw in batch:
                values = [convert(v, kinds[n]) for v, n in zip(raw, names)]
                key = tuple(values[i] for i in key_positions)
                if any(k is None for k in key):
                    finish("failed", reason=f"a primary key column is empty in row {rows + 1}")
                if key in seen:
                    finish("failed", reason=f"the primary key is not unique: row {rows + 1} repeats an earlier row")
                seen.add(key)
                try:
                    line = json.dumps(dict(zip(names, values)), ensure_ascii=False, allow_nan=False)
                except ValueError:
                    finish("failed", reason=f"a value in row {rows + 1} is not a finite number")
                put(line.encode("utf-8") + b"\n")
                rows += 1
                if rows > MAX_ROWS:
                    finish("failed", reason=f"the result has more than {MAX_ROWS} rows")
                if written > MAX_BYTES:
                    finish("failed", reason=f"the result is larger than {MAX_BYTES} bytes")
    if rows == 0:
        finish("failed", reason="the query produced no rows, so there is nothing to seal")
    finish("ok", rows=rows, bytes=written, sha256=digest.hexdigest())


if __name__ == "__main__":
    try:
        run()
    except SystemExit:
        raise
    except MemoryError:
        finish("failed", reason="the query ran out of memory")
    except Exception as exc:  # noqa: BLE001 - reported by class, never by content
        kind = type(exc).__name__
        if kind in MESSAGE_IS_SAFE:
            finish("failed", reason=str(exc).splitlines()[0][:300])
        if "OutOfMemory" in kind:
            finish("failed", reason="the query ran out of memory")
        finish("failed", reason=f"the query failed while running ({kind})")
