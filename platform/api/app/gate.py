"""A limit on how many requests of one kind are being served at once, that refuses instead of waiting.

The platform serves every organisation from one process with a fixed amount of memory, so a few people sending large files must not be able
to slow everybody else down. A request that would go over a limit is turned away at once, with the number of seconds to wait, rather than
being queued: a queue of waiting uploads holds their memory too.

Nothing here knows about uploads, people or organisations: a gate counts what it is told to count. It has no dependencies so a check can import
it on its own.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager


class Busy(Exception):
    """Too many requests of this kind are being served. `what` says which limit was reached, in words a person can act on."""

    def __init__(self, what: str, retry_after: int = 5):
        super().__init__(what)
        self.what, self.retry_after = what, retry_after


class Gate:
    """At most `total` requests at once across all keys, and at most `per_key` for any one key (one upload, one person)."""

    def __init__(self, total: int, per_key: int):
        self.total, self.per_key = total, per_key
        self._lock = threading.Lock()
        self._all = 0
        self._by_key: dict[str, int] = {}

    @contextmanager
    def enter(self, key: str, what_all: str, what_key: str):
        with self._lock:
            if self._all >= self.total:
                raise Busy(what_all)
            if self._by_key.get(key, 0) >= self.per_key:
                raise Busy(what_key, retry_after=2)
            self._all += 1
            self._by_key[key] = self._by_key.get(key, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                self._all -= 1
                self._by_key[key] -= 1
                if not self._by_key[key]:
                    del self._by_key[key]

    def in_flight(self) -> int:
        with self._lock:
            return self._all
