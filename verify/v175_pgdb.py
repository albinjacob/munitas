r"""U186: the database layer (pgdb.py, over the permissively licensed pg8000) does what the code written for psycopg expects of it.

Runs against the live database, on the host or in the API container, and needs no table of the platform's: it uses temporary tables. The checkpointer on pgdb
(worker/pg_saver.py) is checked too, where LangGraph is installed (the host's environment, not the API's: that part says so when it cannot run).

    .venv\Scripts\python.exe verify\v175_pgdb.py

Checked one item at a time:

  * a statement's parameters: %s, %(name)s (used twice), %% with parameters and without, a percent sign in a statement that has none;
  * every kind of value goes over and comes back as it was: text with accents, whole numbers of every size, decimals, floats, booleans, bytes, uuids, dates, times
    with and without a zone, JSON, arrays of text, numbers and bytes, and the empty list, with NULL in each place it can be;
  * rows are tuples or dictionaries, the column names and the count of rows are right, and a statement with no result has none;
  * a refusal is raised as the class its SQLSTATE names, which is an IntegrityError and an Error, saying the constraint and the detail, as code that catches them expects;
  * a connection commits when its block ends and rolls back when it raised, a transaction block is a transaction or a savepoint, in either mode;
  * the connection options take effect, the connection string is read in both forms, and a wrong password is refused as an OperationalError;
  * the pool never holds more connections than it was given, makes callers wait and then time out, commits or rolls back, and replaces a connection the server killed;
  * the two copies of the layer are the same file, and the repository's root offers the same module;
  * the checkpointer keeps a graph's state and gives it back, with the filters and the order the library's own does.
"""

from __future__ import annotations

import datetime
import decimal
import json
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

from common import PG_DSN, check, heading, skip, summary  # noqa: E402

import pgdb  # noqa: E402


def main() -> int:
    parameters()
    values()
    rows()
    errors_()
    transactions()
    connecting()
    pool()
    copies()
    saver()
    return summary("U186")


def conn(**kw):
    return pgdb.connect(PG_DSN, autocommit=kw.pop("autocommit", True), **kw)


def one(c, sql, params=None):
    return c.execute(sql, params).fetchone()


def parameters() -> None:
    heading("A statement's parameters")
    c = conn()
    check("%s takes a value", one(c, "select %s::int + 1", (4,)) == (5,))
    check("%(name)s takes a value, and may be used twice", one(c, "select %(x)s::int + %(x)s::int, %(y)s::text", {"x": 2, "y": "two"}) == (4, "two"))
    check("%% is a percent sign when there are parameters", one(c, "select 'a%%b' like %s", ("a%b",)) == (True,))
    check("%% is a percent sign when the parameters are an empty list", one(c, "select 'a%%b'", ()) == ("a%b",))
    check("a statement with no parameters keeps every percent sign as it is", one(c, "select 'a%b', 'c%%d'") == ("a%b", "c%%d"))
    check("a text for a uuid, a date and a time with a zone is accepted, as the server decides what it is",
          one(c, "select %s::uuid, %s::date, %s::timestamptz", ("00000000-0000-0000-0000-000000000001", "2026-01-02", "2026-01-02T03:04:05Z"))
          == (uuid.UUID(int=1), datetime.date(2026, 1, 2), datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)))
    c.close()


def values() -> None:
    heading("Every kind of value goes over and comes back")
    c = conn()
    c.execute("create temp table t (a text, i2 int2, i4 int4, i8 int8, n numeric, f float8, b bool, y bytea, u uuid, d date, ts timestamp, tz timestamptz, j jsonb, ta text[], ia int[], ya bytea[])")
    aware = datetime.datetime(2026, 3, 4, 5, 6, 7, 123456, tzinfo=datetime.timezone.utc)
    row = ("héllo ☃ 'quoted' \"double\" \\ back", 5, 70_000, 2 ** 40, decimal.Decimal("-12345.678900"), 1.25, True, b"\x00\x01\xff\x00", uuid.uuid4(),
           datetime.date(2026, 3, 4), datetime.datetime(2026, 3, 4, 5, 6, 7), aware, json.dumps({"a": [1, 2.5, "x", None, True], "b": {"c": "d"}}),
           ["a", "b,c", 'q"r', "{s}", None], [1, None, 3], [b"\x00", b"b", None])
    c.execute("insert into t values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", row)
    got = one(c, "select * from t")
    check("text with accents, quotes and a backslash", got[0] == row[0])
    check("whole numbers of three sizes", got[1:4] == (5, 70_000, 2 ** 40))
    check("a decimal keeps every digit", got[4] == decimal.Decimal("-12345.678900") and str(got[4]) == "-12345.678900")
    check("a float, a boolean, bytes and a uuid", got[5:9] == (1.25, True, b"\x00\x01\xff\x00", row[8]))
    check("a date, a time without a zone, a time with a zone (aware, to the microsecond)", got[9] == row[9] and got[10] == row[10] and got[11] == aware and got[11].tzinfo is not None)
    check("JSON text is read back parsed", got[12] == {"a": [1, 2.5, "x", None, True], "b": {"c": "d"}})
    check("arrays of text, numbers and bytes, with NULL among them", got[13] == ["a", "b,c", 'q"r', "{s}", None] and got[14] == [1, None, 3] and got[15] == [b"\x00", b"b", None])
    c.execute("delete from t")
    c.execute("insert into t (a, i4, n, y, u, ts, j, ta) values (%s,%s,%s,%s,%s,%s,%s,%s)", (None, None, None, None, None, None, None, None))
    check("NULL in every place a value can be", one(c, "select a, i4, n, y, u, ts, j, ta from t") == (None,) * 8)
    c.execute("delete from t")
    c.execute("insert into t (ta, ia, ya) values (%s, %s, %s)", ([], [], []))
    check("an empty list is an empty array", one(c, "select ta, ia, ya from t") == ([], [], []))
    check("a dictionary is JSON, and a list of texts selects with = any()", one(c, "select %s::jsonb ->> 'k'", ({"k": "v"},)) == ("v",)
          and one(c, "select count(*) from generate_series(1,5) g where g::text = any(%s)", (["2", "4", "9"],)) == (2,))
    check("a large array of bytes goes through unnest", one(c, "select count(*), sum(length(x)) from unnest(%s::bytea[]) x", ([b"ab"] * 5000,)) == (5000, 10_000))
    check("a memoryview is bytes", one(c, "select %s::bytea", (memoryview(b"xyz"),)) == (b"xyz",))
    c.close()


def rows() -> None:
    heading("Rows and results")
    t = conn()
    d = conn(row_factory=pgdb.dict_row)
    check("a row is a tuple", one(t, "select 1 as a, 'x' as b") == (1, "x") and isinstance(one(t, "select 1"), tuple))
    check("a row is a dictionary with dict_row, in the order of the columns", list(one(d, "select 1 as a, 'x' as b").items()) == [("a", 1), ("b", "x")])
    cur = t.execute("select g as n, g * 2 as m from generate_series(1, 5) g")
    check("the description names the columns", [c.name for c in cur.description] == ["n", "m"])
    check("the count of rows is right, and fetchmany, fetchone and iteration agree", cur.rowcount == 5 and cur.fetchmany(2) == [(1, 2), (2, 4)] and cur.fetchone() == (3, 6) and list(cur) == [(4, 8), (5, 10)])
    check("a statement that returns nothing has no description, and an empty result is an empty list", t.execute("create temp table e (x int)").description is None and t.execute("select * from e").fetchall() == [])
    check("rowcount of an update says how many rows it changed", t.execute("insert into e select generate_series(1, 4)").rowcount == 4 and t.execute("update e set x = x + 1 where x > 2").rowcount == 2)
    check("a cursor has its own row factory", t.cursor(row_factory=pgdb.dict_row).execute("select 7 as k").fetchone() == {"k": 7})
    for c in (t, d):
        c.close()


def errors_() -> None:
    heading("Refusals are the class their SQLSTATE names")
    c = conn()
    c.execute("create temp table p (id int primary key, v int check (v > 0), r int)")
    c.execute("create temp table q (id int primary key, p_id int references p(id))")
    c.execute("insert into p values (1, 1, null)")

    def refused(sql, params=None):
        try:
            c.execute(sql, params)
        except pgdb.Error as exc:
            return exc
        return None

    cases = [("unique", "insert into p values (1, 1, null)", "UniqueViolation", "23505", "p_pkey"),
             ("check", "insert into p values (2, 0, null)", "CheckViolation", "23514", "p_v_check"),
             ("foreign key", "insert into q values (1, 99)", "ForeignKeyViolation", "23503", "q_p_id_fkey"),
             ("not null", "insert into p values (null, 1, null)", "NotNullViolation", "23502", None),
             ("text that is not a number", "select %s::int", "InvalidTextRepresentation", "22P02", None),
             ("a table that is not there", "select * from no_such_table_here", "UndefinedTable", "42P01", None),
             ("a column that is not there", "select no_such_column from p", "UndefinedColumn", "42703", None),
             ("a division by zero", "select 1/0", "DivisionByZero", "22012", None)]
    for label, sql, name, state, constraint in cases:
        exc = refused(sql, ("abc",) if "%s" in sql else None)
        right = exc is not None and type(exc) is getattr(pgdb.errors, name) and exc.sqlstate == state and isinstance(exc, pgdb.Error)
        if constraint:
            right = right and exc.diag.constraint_name == constraint and constraint in str(exc)
        check(f"{label} is {name} ({state})" + (f", naming {constraint}" if constraint else ""), right, repr(exc)[:150])
    exc = refused("insert into p values (1, 1, null)")
    check("a unique violation says which value, in its detail", exc is not None and "(id)=(1)" in str(exc) and "(id)=(1)" in exc.diag.message_detail, str(exc)[:120])
    check("the classes sit where code that catches the family expects", issubclass(pgdb.errors.CheckViolation, pgdb.errors.IntegrityError) and issubclass(pgdb.errors.IntegrityError, pgdb.Error)
          and issubclass(pgdb.errors.UndefinedTable, pgdb.errors.ProgrammingError))
    ro = conn()
    ro.execute("begin read only")
    try:
        ro.execute("create temp table z (x int)")
        read_only = None
    except pgdb.Error as e:
        read_only = e
    check("a write in a read-only transaction is ReadOnlySqlTransaction", type(read_only) is pgdb.errors.ReadOnlySqlTransaction, repr(read_only)[:100])
    c.close()
    ro.close()


def transactions() -> None:
    heading("Transactions")
    setup = conn()
    name = "u186_" + uuid.uuid4().hex[:8]
    setup.execute(f"create table {name} (x int)")
    try:
        def count():
            return one(setup, f"select count(*) from {name}")[0]

        with pgdb.connect(PG_DSN) as c:
            c.execute(f"insert into {name} values (1)")
        check("a block that ends commits", count() == 1)
        try:
            with pgdb.connect(PG_DSN) as c:
                c.execute(f"insert into {name} values (2)")
                raise RuntimeError("stop")
        except RuntimeError:
            pass
        check("a block that raised rolls back, and the exception is not swallowed", count() == 1)
        c = pgdb.connect(PG_DSN)
        c.execute(f"insert into {name} values (3)")
        outside = count()
        c.rollback()
        c.execute(f"insert into {name} values (4)")
        c.commit()
        check("nothing is seen before commit, rollback undoes, commit keeps", outside == 1 and count() == 2)
        c.close()
        a = pgdb.connect(PG_DSN, autocommit=True)
        a.execute(f"insert into {name} values (5)")
        check("with autocommit a statement is committed at once", count() == 3)
        with a.transaction():
            a.execute(f"insert into {name} values (6)")
        check("a transaction block commits at its end, in autocommit", count() == 4)
        try:
            with a.transaction():
                a.execute(f"insert into {name} values (7)")
                raise RuntimeError("stop")
        except RuntimeError:
            pass
        check("a transaction block that raised undoes what it did, in autocommit", count() == 4)
        with a.transaction():
            a.execute(f"insert into {name} values (8)")
            try:
                with a.transaction():
                    a.execute(f"insert into {name} values (9)")
                    a.execute(f"insert into {name} values ('x')")
            except pgdb.Error:
                pass
            a.execute(f"insert into {name} values (10)")
        check("an inner block is a savepoint: its failure undoes only itself", count() == 6 and one(setup, f"select count(*) from {name} where x in (9)")[0] == 0)
        a.close()
        n = pgdb.connect(PG_DSN)
        n.execute(f"insert into {name} values (11)")
        try:
            with n.transaction():
                n.execute(f"insert into {name} values (12)")
                raise RuntimeError("stop")
        except RuntimeError:
            pass
        n.commit()
        check("inside an open transaction a block is a savepoint, not the whole of it", count() == 7 and one(setup, f"select count(*) from {name} where x = 12")[0] == 0)
        with n.transaction():
            n.execute(f"insert into {name} values (13)")
        check("a block with no transaction open is one, and commits", count() == 8)
        n.close()
        check("a closed connection says so", n.closed is True)
    finally:
        setup.execute(f"drop table {name}")
        setup.close()


def connecting() -> None:
    heading("Connecting")
    c = pgdb.connect(PG_DSN, autocommit=True, options="-c statement_timeout=4321")
    check("the options of a connection are set on it", one(c, "show statement_timeout") == ("4321ms",))
    c.close()
    from urllib.parse import urlsplit

    p = urlsplit(PG_DSN)
    pairs = f"host={p.hostname} port={p.port} dbname={p.path.lstrip('/')} user={p.username} password={p.password}"
    c = pgdb.connect(pairs, autocommit=True)
    check("a connection string of key=value pairs is read", one(c, "select 1") == (1,))
    c.close()
    c = pgdb.connect(PG_DSN + ("&" if "?" in PG_DSN else "?") + "sslmode=disable", autocommit=True, connect_timeout=5)
    check("a connection address with sslmode and a connect timeout is read, and the timeout does not cut a longer statement short", one(c, "select pg_sleep(0.3), 1")[1] == 1)
    c.close()
    try:
        pgdb.connect(PG_DSN.replace(f":{p.password}@", ":not-the-password@"), connect_timeout=5)
        wrong = None
    except pgdb.Error as exc:
        wrong = exc
    check("a wrong password is refused as an Error with a SQLSTATE", wrong is not None and wrong.sqlstate in ("28P01", "28000"), repr(wrong)[:120])
    try:
        pgdb.connect("postgresql://nobody@127.0.0.1:1/none", connect_timeout=2)
        down = None
    except pgdb.Error as exc:
        down = exc
    check("a database that cannot be reached is an OperationalError", isinstance(down, pgdb.OperationalError), repr(down)[:120])


def pool() -> None:
    heading("The pool")
    marker = "u186pool" + uuid.uuid4().hex[:6]
    p = pgdb.ConnectionPool(PG_DSN, min_size=1, max_size=4, open=False, timeout=2, kwargs={"row_factory": pgdb.dict_row, "application_name": marker})
    check("a pool made closed is closed, and opening it makes its minimum", p.closed and (p.open(wait=True) or True) and not p.closed)
    watch = conn()

    def live():
        return one(watch, "select count(*) from pg_stat_activity where application_name = %s", (marker,))[0]

    peak, held = [0], threading.Lock()
    seen: list[int] = []

    def work(i):
        with p.connection() as c:
            with held:
                seen.append(live())
            c.execute("select pg_sleep(0.15)")
            row = c.execute("select %s::int as n", (i,)).fetchone()
            assert row == {"n": i}

    threads = [threading.Thread(target=work, args=(i,)) for i in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    check("sixteen callers share four connections, never more", max(seen) <= 4 and live() <= 4, f"most seen {max(seen)}")
    # callers wait, then time out
    holders = []
    cms = [p.connection() for _ in range(4)]
    holders = [cm.__enter__() for cm in cms]
    started = time.monotonic()
    try:
        with p.connection():
            waited = None
    except pgdb.PoolTimeout as exc:
        waited = exc
    check("a fifth caller waits for the timeout and then gets a PoolTimeout", isinstance(waited, pgdb.PoolTimeout) and 1.5 < time.monotonic() - started < 5)
    for cm in cms:
        cm.__exit__(None, None, None)
    name = "u186_" + uuid.uuid4().hex[:8]
    watch.execute(f"create table {name} (x int)")
    try:
        with p.connection() as c:
            c.execute(f"insert into {name} values (1)")
        check("leaving a block commits what it did", one(watch, f"select count(*) from {name}")[0] == 1)
        try:
            with p.connection() as c:
                c.execute(f"insert into {name} values (2)")
                raise RuntimeError("stop")
        except RuntimeError:
            pass
        check("leaving a block by an exception rolls it back", one(watch, f"select count(*) from {name}")[0] == 1)
        with p.connection() as c:
            pid = one(c, "select pg_backend_pid() as pid")["pid"]
        watch.execute("select pg_terminate_backend(%s)", (pid,))
        time.sleep(0.3)
        p._check_after = 0
        with p.connection() as c:
            fresh = one(c, "select pg_backend_pid() as pid")["pid"]
        check("a connection the server killed is replaced, and the caller never sees it", fresh != pid)
    finally:
        watch.execute(f"drop table {name}")
    p.close()
    check("a closed pool refuses callers", p.closed and _raises(lambda: p.connection().__enter__(), pgdb.Error))
    watch.close()


def _raises(call, kind) -> bool:
    try:
        call()
    except kind:
        return True
    return False


def copies() -> None:
    heading("One implementation")
    real = ROOT / "platform" / "pgdb.py"
    worker = ROOT / "worker" / "pgdb.py"
    if real.exists() and worker.exists():
        check("platform/pgdb.py and worker/pgdb.py are the same file", real.read_bytes() == worker.read_bytes())
        shim = ROOT / "pgdb.py"
        check("the repository's root offers the same module, not a second copy", shim.exists() and pgdb.__file__ is not None and Path(pgdb.__file__).resolve() in (real.resolve(), Path("/app/pgdb.py")),
              str(pgdb.__file__))
    else:
        skip("the copies of the layer are the same file", "the repository is not here (this is the API container, whose copy is /app/pgdb.py)")


def saver() -> None:
    heading("The checkpointer")
    try:
        from langgraph.checkpoint.base import empty_checkpoint
    except ImportError:
        skip("the checkpointer keeps a graph's state and gives it back", "LangGraph is not installed in this Python (it is in the host's, not the API's)")
        return
    from worker.pg_saver import PgdbSaver

    with PgdbSaver.from_conn_string(PG_DSN) as saver:
        saver.setup()
        t = "u186-" + uuid.uuid4().hex[:8]
        cfg = {"configurable": {"thread_id": t, "checkpoint_ns": ""}}
        first = empty_checkpoint()
        first["channel_values"] = {"messages": [{"role": "user", "text": "héllo"}], "count": 3}
        first["channel_versions"] = {"messages": "00000000000000000000000000000001.0.1", "count": "00000000000000000000000000000001.0.2"}
        c1 = saver.put(cfg, first, {"source": "input", "step": 1, "writes": {"a": 1}, "parents": {}}, {"messages": first["channel_versions"]["messages"]})
        saver.put_writes({"configurable": {**c1["configurable"]}}, [("messages", {"k": [1, 2]}), ("other", b"bytes")], "task-1", "path")
        second = empty_checkpoint()
        second["channel_values"] = {"messages": [{"role": "user", "text": "héllo"}, {"role": "ai", "text": "there"}]}
        second["channel_versions"] = {"messages": "00000000000000000000000000000002.0.1"}
        c2 = saver.put({"configurable": {**c1["configurable"]}}, second, {"source": "loop", "step": 2, "writes": None, "parents": {}}, {"messages": second["channel_versions"]["messages"]})
        got = saver.get_tuple(c2)
        check("the latest checkpoint comes back with its values, and the one before it as its parent",
              got.checkpoint["channel_values"]["messages"][1]["text"] == "there" and got.parent_config["configurable"]["checkpoint_id"] == c1["configurable"]["checkpoint_id"])
        before = saver.get_tuple(c1)
        check("an earlier checkpoint carries its pending writes in order", [w[1] for w in before.pending_writes] == ["messages", "other"] and before.pending_writes[0][2] == {"k": [1, 2]})
        check("the plain value is kept in the checkpoint's own row and the rest in the blobs table",
              before.checkpoint["channel_values"]["count"] == 3 and before.checkpoint["channel_values"]["messages"] == [{"role": "user", "text": "héllo"}])
        listed = list(saver.list(cfg))
        check("list is newest first, filters on metadata, limits, and stops before a checkpoint",
              [x.config["configurable"]["checkpoint_id"] for x in listed] == [c2["configurable"]["checkpoint_id"], c1["configurable"]["checkpoint_id"]]
              and len(list(saver.list(cfg, filter={"source": "input"}))) == 1 and len(list(saver.list(cfg, limit=1))) == 1
              and [x.config["configurable"]["checkpoint_id"] for x in saver.list(cfg, before=got.config)] == [c1["configurable"]["checkpoint_id"]])
        saver.delete_thread(t)
        check("deleting a thread leaves nothing of it", saver.get_tuple(cfg) is None and not list(saver.list(cfg)))


if __name__ == "__main__":
    sys.exit(main())
