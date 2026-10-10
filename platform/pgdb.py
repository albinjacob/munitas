"""How this project talks to PostgreSQL: a small layer over pg8000, which is BSD-3 licensed and written in Python alone.

WHY THIS EXISTS

The project used psycopg 3, which is LGPL 3. A permissive licence throughout is a promise the project makes to people who use it, so the driver was replaced
by one with a permissive licence. Nothing else in the code base was written to a new driver: this module offers the part of psycopg's interface that the code
uses (`connect`, a connection that is a context manager, cursors with rows as tuples or dictionaries, `transaction()` blocks that nest as savepoints, a
connection pool, and the exception classes by SQLSTATE), so that a call site changed by one line and not by its meaning.

WHAT IT KEEPS THE SAME

* A statement is written with `%s` or `%(name)s` for a value and `%%` for a percent sign. Without parameters a percent sign is a percent sign.
* A value goes over as itself, and the server decides what it is (a text for a uuid or a timestamp column is accepted, as it was), a list is an array, a
  dictionary is JSON, bytes are bytea.
* A connection starts a transaction at its first statement unless `autocommit` is on, `commit()` and `rollback()` end it, and leaving a `with` block commits,
  or rolls back when the block raised, and closes.
* `with conn.transaction():` is a transaction when none is open, and a savepoint when one is, so a statement the database refuses undoes only itself.
* Rows are tuples, or dictionaries with `row_factory=dict_row`. A timestamp with a time zone comes back aware, a number as a Decimal, JSON parsed.
* An error is raised as the class its SQLSTATE names (`errors.ForeignKeyViolation`, `errors.CheckViolation`, ...), with `.sqlstate` and `.diag.constraint_name`
  and `.diag.message_detail`, and its text is the server's message and detail.

WHAT IT DOES NOT DO

No server-side cursors, no COPY, no asynchronous use: nothing here used them. A result is read whole. The pool is plain: a few connections, handed out one
at a time, tested after they have been idle, replaced when they have gone bad.

This file is copied to worker/pgdb.py, which has to ship in the worker's own images; scripts/check_pins_agree.py fails when the two differ.
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
import types
from collections import namedtuple
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import parse_qs, unquote, urlsplit

import pg8000.dbapi as _pg

__all__ = ["connect", "Connection", "Cursor", "ConnectionPool", "PoolTimeout", "dict_row", "tuple_row", "errors",
           "Error", "InterfaceError", "DatabaseError", "OperationalError", "IntegrityError", "ProgrammingError", "DataError", "InternalError"]


# ---- errors ---------------------------------------------------------------------------------------------------------------------------------------


class Diagnostics:
    """What the server said about an error: the fields psycopg's `exc.diag` offered, as far as this code looked at them."""

    def __init__(self, fields: dict):
        self.sqlstate = fields.get("C")
        self.severity = fields.get("S")
        self.message_primary = fields.get("M")
        self.message_detail = fields.get("D")
        self.message_hint = fields.get("H")
        self.schema_name = fields.get("s")
        self.table_name = fields.get("t")
        self.column_name = fields.get("c")
        self.constraint_name = fields.get("n")


class Error(Exception):
    """The base of every error this module raises, as psycopg.Error was."""

    sqlstate: str | None = None
    diag: Diagnostics

    def __init__(self, message: str = "", diag: Diagnostics | None = None):
        super().__init__(message)
        self.diag = diag or Diagnostics({})
        self.sqlstate = self.diag.sqlstate


class InterfaceError(Error): ...
class DatabaseError(Error): ...
class DataError(DatabaseError): ...
class OperationalError(DatabaseError): ...
class IntegrityError(DatabaseError): ...
class InternalError(DatabaseError): ...
class ProgrammingError(DatabaseError): ...


class PoolTimeout(OperationalError):
    """No connection became free in time."""


def _named(name: str, base: type, *sqlstates: str) -> type:
    return type(name, (base,), {"sqlstates": sqlstates})


_BY_STATE: dict[str, type] = {}
_CLASSES = [
    _named("NotNullViolation", IntegrityError, "23502"), _named("ForeignKeyViolation", IntegrityError, "23503"),
    _named("UniqueViolation", IntegrityError, "23505"), _named("CheckViolation", IntegrityError, "23514"),
    _named("ExclusionViolation", IntegrityError, "23P01"),
    _named("InvalidTextRepresentation", DataError, "22P02"), _named("NumericValueOutOfRange", DataError, "22003"),
    _named("StringDataRightTruncation", DataError, "22001"), _named("InvalidDatetimeFormat", DataError, "22007"),
    _named("DatetimeFieldOverflow", DataError, "22008"), _named("DivisionByZero", DataError, "22012"),
    _named("ReadOnlySqlTransaction", InternalError, "25006"), _named("InFailedSqlTransaction", InternalError, "25P02"),
    _named("LockNotAvailable", OperationalError, "55P03"), _named("QueryCanceled", OperationalError, "57014"),
    _named("SerializationFailure", OperationalError, "40001"), _named("DeadlockDetected", OperationalError, "40P01"),
    _named("AdminShutdown", OperationalError, "57P01"), _named("TooManyConnections", OperationalError, "53300"),
    _named("UndefinedTable", ProgrammingError, "42P01"), _named("UndefinedColumn", ProgrammingError, "42703"),
    _named("UndefinedFunction", ProgrammingError, "42883"), _named("SyntaxError", ProgrammingError, "42601"),
    _named("DuplicateTable", ProgrammingError, "42P07"), _named("DuplicateObject", ProgrammingError, "42710"),
    _named("InsufficientPrivilege", ProgrammingError, "42501"), _named("InvalidParameterValue", DataError, "22023"),
]
for _cls in _CLASSES:
    for _state in _cls.sqlstates:
        _BY_STATE[_state] = _cls

# The names code catches, as a namespace: `except pgdb.errors.CheckViolation`.
errors = types.SimpleNamespace(**{c.__name__: c for c in _CLASSES}, Error=Error, DatabaseError=DatabaseError, IntegrityError=IntegrityError,
                               OperationalError=OperationalError, ProgrammingError=ProgrammingError, DataError=DataError, InternalError=InternalError,
                               InterfaceError=InterfaceError)


def _class_for(state: str | None) -> type:
    if state in _BY_STATE:
        return _BY_STATE[state]
    prefix = (state or "")[:2]
    if prefix == "23":
        return IntegrityError
    if prefix == "22":
        return DataError
    if prefix in ("42", "26", "34", "3D", "3F"):
        return ProgrammingError
    if prefix in ("08", "28", "53", "54", "55", "57", "58", "40", "XX"):
        return OperationalError
    return DatabaseError


def _wrap(exc: BaseException) -> Error:
    """The pg8000 error as the class its SQLSTATE names, saying what the server said."""
    fields = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
    if fields:
        diag = Diagnostics(fields)
        text = str(fields.get("M", "")) + (f"\nDETAIL:  {fields['D']}" if fields.get("D") else "")
        return _class_for(diag.sqlstate)(text, diag)
    if isinstance(exc, _pg.InterfaceError):
        return InterfaceError(str(exc))
    if isinstance(exc, (OSError, EOFError)):
        return OperationalError(str(exc))
    return DatabaseError(str(exc))


# ---- statements and rows --------------------------------------------------------------------------------------------------------------------------

_MARK = re.compile(r"%%|%\((\w+)\)s")


def _prepare(sql: str, params: Any) -> tuple[str, list]:
    """The statement and values pg8000 wants. A statement run without parameters keeps every percent sign as it is; `%(name)s` becomes `%s`, in order."""
    if params is None:
        return sql, []                       # nothing to put in, so nothing in the statement is read as a placeholder
    if isinstance(params, dict):
        values: list = []

        def swap(found: re.Match) -> str:
            if found.group(1) is None:
                return "%%"
            values.append(params[found.group(1)])
            return "%s"

        statement = _MARK.sub(swap, sql)
        return (statement if values else statement.replace("%%", "%")), [_value(v) for v in values]
    values = [_value(v) for v in params]
    if not values:
        return sql.replace("%%", "%"), []    # an empty list of values still makes `%%` a percent sign, as it does with values
    return sql, values


def _value(value: Any) -> Any:
    if isinstance(value, (memoryview, bytearray)):
        return bytes(value)
    if isinstance(value, (set, frozenset)):
        return list(value)
    return value


Column = namedtuple("Column", ["name", "type_code", "display_size", "internal_size", "precision", "scale", "null_ok"], defaults=(None,) * 6)


def tuple_row(names: list[str]) -> Callable[[Iterable], Any]:
    return tuple


def dict_row(names: list[str]) -> Callable[[Iterable], dict]:
    return lambda values: dict(zip(names, values))


class Cursor:
    """A cursor: statements go in, rows come out, as tuples or as dictionaries."""

    def __init__(self, connection: "Connection", row_factory: Callable | None = None):
        self.connection = connection
        self._raw = connection._raw.cursor()
        self._factory = row_factory or connection.row_factory or tuple_row
        self._make: Callable | None = None
        self.arraysize = 1

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._raw.close()

    @property
    def description(self) -> list[Column] | None:
        found = self._raw.description
        return None if found is None else [Column(*d) for d in found]

    @property
    def rowcount(self) -> int:
        return self._raw.rowcount

    def execute(self, sql: str, params: Any = None) -> "Cursor":
        statement, values = _prepare(sql, params)
        self.connection._began()
        try:
            self._raw.execute(statement, values)
        except (_pg.Error, OSError, EOFError) as exc:
            self.connection._failed(exc)
            raise _wrap(exc) from None
        names = [d[0] for d in (self._raw.description or [])]
        self._make = self._factory(names) if names else None
        return self

    def executemany(self, sql: str, seq: Iterable[Any]) -> "Cursor":
        for params in seq:
            self.execute(sql, params)
        return self

    def _row(self, values: Any) -> Any:
        return self._make(values) if self._make else tuple(values)

    def fetchone(self) -> Any:
        values = self._raw.fetchone()
        return None if values is None else self._row(values)

    def fetchmany(self, size: int | None = None) -> list:
        return [self._row(v) for v in self._raw.fetchmany(size or self.arraysize)]

    def fetchall(self) -> list:
        return [self._row(v) for v in self._raw.fetchall()]

    def __iter__(self) -> Iterator:
        while (row := self.fetchone()) is not None:
            yield row


# ---- the connection -------------------------------------------------------------------------------------------------------------------------------


def _dsn(dsn: str) -> dict:
    """The parts of a connection string, in either of the two forms PostgreSQL takes: a postgresql:// address, or a list of key=value pairs."""
    if "://" in dsn:
        parts = urlsplit(dsn)
        found = {"host": parts.hostname, "port": parts.port, "user": unquote(parts.username) if parts.username else None,
                 "password": unquote(parts.password) if parts.password else None, "dbname": parts.path.lstrip("/") or None}
        found.update({k: v[0] for k, v in parse_qs(parts.query).items()})
        return found
    return {k: v.strip("'") for k, v in re.findall(r"(\w+)\s*=\s*('(?:[^'\\]|\\.)*'|\S+)", dsn)}


class Connection:
    """One connection. Use it with `with`, as psycopg's: commit at the end of the block, or roll back when it raised, then close."""

    def __init__(self, raw: _pg.Connection, row_factory: Callable | None = None, autocommit: bool = False):
        self._raw = raw
        self.row_factory = row_factory
        self._autocommit = bool(autocommit)
        self._raw.autocommit = self._autocommit
        self._open_transaction = False       # a transaction has begun and not ended
        self._depth = 0                      # how many transaction() blocks are open
        self._closed = False
        self.last_used = time.monotonic()

    # -- state

    @property
    def autocommit(self) -> bool:
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        if self._open_transaction and value != self._autocommit:
            raise ProgrammingError("autocommit cannot be changed while a transaction is open")
        self._autocommit = bool(value)
        self._raw.autocommit = self._autocommit

    @property
    def closed(self) -> bool:
        return self._closed

    def _began(self) -> None:
        self.last_used = time.monotonic()
        if not self._autocommit:
            self._open_transaction = True        # pg8000 begins one with the first statement

    def _failed(self, exc: BaseException) -> None:
        if not isinstance(exc, _pg.DatabaseError):
            self._closed = True                  # the connection itself went, not the statement

    # -- statements

    def cursor(self, row_factory: Callable | None = None) -> Cursor:
        return Cursor(self, row_factory)

    def execute(self, sql: str, params: Any = None) -> Cursor:
        return self.cursor().execute(sql, params)

    def commit(self) -> None:
        try:
            self._raw.commit()
        except (_pg.Error, OSError, EOFError) as exc:
            self._failed(exc)
            raise _wrap(exc) from None
        self._open_transaction = False

    def rollback(self) -> None:
        try:
            self._raw.rollback()
        except (_pg.Error, OSError, EOFError) as exc:
            self._failed(exc)
            raise _wrap(exc) from None
        self._open_transaction = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(Exception):
                self._raw.close()

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if not self._closed and self._open_transaction:
                self.rollback() if exc_type else self.commit()
        finally:
            self.close()

    # -- transaction blocks

    @contextlib.contextmanager
    def transaction(self) -> Iterator["Connection"]:
        """A transaction when none is open, and a savepoint inside one, so that a statement the database refuses undoes only itself."""
        own = self._depth == 0 and not self._open_transaction
        name = f"pgdb_savepoint_{self._depth + 1}"
        if own and self._autocommit:
            self.execute("begin")
            self._open_transaction = True
        elif not own:
            self.execute(f"savepoint {name}")
        self._depth += 1
        try:
            yield self
        except BaseException:
            self._depth -= 1
            if own:
                self._end("rollback")
            else:
                self.execute(f"rollback to savepoint {name}")
                self.execute(f"release savepoint {name}")
            raise
        else:
            self._depth -= 1
            if own:
                self._end("commit")
            else:
                self.execute(f"release savepoint {name}")

    def _end(self, how: str) -> None:
        if self._autocommit:
            self.execute(how)
            self._open_transaction = False
        else:
            self.commit() if how == "commit" else self.rollback()


def connect(conninfo: str = "", *, row_factory: Callable | None = None, autocommit: bool = False, connect_timeout: float | None = None,
            options: str | None = None, **overrides: Any) -> Connection:
    """Open a connection from a postgresql:// address or key=value pairs, as psycopg.connect did."""
    found = {**_dsn(conninfo), **overrides}
    timeout = connect_timeout if connect_timeout is not None else (float(found["connect_timeout"]) if found.get("connect_timeout") else None)
    ssl_mode = (found.get("sslmode") or "prefer").lower()
    ssl_context = None
    if ssl_mode in ("require", "verify-ca", "verify-full"):
        import ssl

        ssl_context = ssl.create_default_context()
        if ssl_mode == "require":
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
    try:
        raw = _pg.connect(user=found.get("user") or "postgres", password=found.get("password"), host=found.get("host") or "localhost",
                          port=int(found.get("port") or 5432), database=found.get("dbname") or found.get("database"), timeout=timeout,
                          ssl_context=ssl_context, application_name=found.get("application_name"))
    except _pg.InterfaceError as exc:
        raise OperationalError(str(exc)) from None         # the server could not be reached: an operational error, as psycopg's is
    except (_pg.Error, OSError, EOFError) as exc:
        raise _wrap(exc) from None
    if timeout is not None and getattr(raw, "_usock", None) is not None:
        raw._usock.settimeout(None)                    # the timeout was for connecting, and must not cut a long statement short
    conn = Connection(raw, row_factory=row_factory, autocommit=autocommit)
    if options:
        for name, value in re.findall(r"-c\s+(\w+)=(\S+)", options):
            conn.execute(f"set {name} = '{value}'")          # the settings of a session, as a connection's `options` set them
        if not autocommit:
            conn.commit()
    return conn


# ---- the pool -------------------------------------------------------------------------------------------------------------------------------------


class ConnectionPool:
    """A few connections, handed out one at a time. `with pool.connection() as conn:` takes one, and gives it back, committed or rolled back."""

    def __init__(self, conninfo: str, *, min_size: int = 1, max_size: int = 10, open: bool = True, timeout: float = 30.0, check_after: float = 30.0,
                 kwargs: dict | None = None):
        self.conninfo, self.min_size, self.max_size = conninfo, min_size, max_size
        self.timeout, self._check_after = timeout, check_after
        self._kwargs = dict(kwargs or {})
        self._idle: list[Connection] = []
        self._made = 0
        self._cond = threading.Condition()
        self._closed = True
        if open:
            self.open()

    @property
    def closed(self) -> bool:
        return self._closed

    def open(self, wait: bool = False, timeout: float = 30.0) -> None:
        with self._cond:
            if not self._closed:
                return
            self._closed = False
        if wait:
            self.wait(timeout)

    def wait(self, timeout: float = 30.0) -> None:
        """Make the connections of the minimum size now, so the first request does not pay for them. Raises when the database cannot be reached."""
        started = time.monotonic()
        while True:
            try:
                have = []
                while len(have) < self.min_size:
                    have.append(self._take())
                for c in have:
                    self._give(c)
                return
            except Error:
                for c in have:
                    self._discard(c)
                if time.monotonic() - started > timeout:
                    raise PoolTimeout("the pool could not reach the database in time") from None
                time.sleep(0.5)

    def close(self) -> None:
        with self._cond:
            self._closed = True
            idle, self._idle = self._idle, []
            self._made -= len(idle)
            self._cond.notify_all()
        for c in idle:
            c.close()

    # -- handing out

    def _fresh(self) -> Connection:
        return connect(self.conninfo, **self._kwargs)

    def _take(self) -> Connection:
        deadline = time.monotonic() + self.timeout
        while True:
            with self._cond:
                if self._closed:
                    raise InterfaceError("the pool is not open")
                if self._idle:
                    conn = self._idle.pop()
                elif self._made < self.max_size:
                    self._made += 1
                    conn = None
                else:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise PoolTimeout(f"no connection became free in {self.timeout:g} seconds")
                    self._cond.wait(left)
                    continue
            if conn is None:
                try:
                    return self._fresh()
                except BaseException:
                    with self._cond:
                        self._made -= 1
                        self._cond.notify()
                    raise
            if time.monotonic() - conn.last_used > self._check_after or conn.closed:
                try:
                    if conn.closed:
                        raise InterfaceError("closed")
                    conn.execute("select 1")
                    if not conn.autocommit:
                        conn.rollback()
                except Error:
                    self._discard(conn)
                    continue
            return conn

    def _give(self, conn: Connection) -> None:
        if conn.closed:
            return self._discard(conn)
        try:
            if conn._open_transaction:
                conn.rollback()
        except Error:
            return self._discard(conn)
        with self._cond:
            if self._closed:
                self._made -= 1
                conn.close()
                return
            self._idle.append(conn)
            self._cond.notify()

    def _discard(self, conn: Connection) -> None:
        conn.close()
        with self._cond:
            self._made -= 1
            self._cond.notify()

    @contextlib.contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[Connection]:
        conn = self._take()
        try:
            yield conn
        except BaseException:
            try:
                if not conn.closed and conn._open_transaction:
                    conn.rollback()
            except Error:
                pass
            self._give(conn)
            raise
        else:
            try:
                if not conn.closed and conn._open_transaction:
                    conn.commit()
            finally:
                self._give(conn)
