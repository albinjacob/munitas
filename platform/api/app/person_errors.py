"""What can go wrong with a person's locked data, as exceptions the routes turn into answers.

Kept apart from the modules that raise them (`person_keys`, `locked_leases`) so neither has to import the other for its errors.
"""

from __future__ import annotations


class Refused(Exception):
    """The table or the contract cannot be sealed. Every problem is listed, because one at a time would be one attempt each."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class NotPermitted(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class LeaseConflict(Exception):
    """The lease is not in a state that allows this: already decided, already ended, or the person already holds one."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class AlreadyDecided(Exception):
    """Somebody already decided this, and a decision is made once."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class ErasureHeld(Exception):
    """A legal hold stands over the organisation, and a destroyed key cannot be brought back."""


class NoSuchVersion(LookupError):
    pass


class NoSuchLease(LookupError):
    pass
