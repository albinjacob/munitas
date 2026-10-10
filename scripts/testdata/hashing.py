"""Reproducible random numbers for generated rows.

Every value is a function of three things only: the seed, the name of the value, and the number of the row (or of the person) it
belongs to. Nothing depends on the order rows are made in, on the size of a batch, on how many threads there are or on the version
of any library, so the same seed gives the same table whether it is made in one piece or in a hundred.

The mixer is splitmix64, which numpy runs on unsigned 64-bit arrays with wrap-around, the same on every machine.
"""
from __future__ import annotations

import hashlib

import numpy as np

_U = np.uint64
_TWO_53 = float(2 ** 53)


def splitmix64(x: np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore"):
        z = x + _U(0x9E3779B97F4A7C15)
        z = (z ^ (z >> _U(30))) * _U(0xBF58476D1CE4E5B9)
        z = (z ^ (z >> _U(27))) * _U(0x94D049BB133111EB)
        return z ^ (z >> _U(31))


def _salt(seed: int, name: str) -> np.uint64:
    return _U(int.from_bytes(hashlib.sha256(f"{seed}:{name}".encode()).digest()[:8], "little"))


class Stream:
    """The random values for a set of rows. `rows` are the numbers the values are a function of: the row numbers of a batch, or, for
    something about a person, the person's number, so that a person's date of birth is the same wherever it is asked for."""

    def __init__(self, rows: np.ndarray, seed: int):
        self.rows = np.asarray(rows, dtype=np.uint64)
        self.seed = seed
        self.n = len(self.rows)

    def bits(self, name: str) -> np.ndarray:
        with np.errstate(over="ignore"):
            return splitmix64(self.rows * _U(0xD6E8FEB86659FD93) ^ _salt(self.seed, name))

    def u(self, name: str) -> np.ndarray:
        """Uniform numbers from 0 up to but not including 1."""
        return (self.bits(name) >> _U(11)).astype(np.float64) / _TWO_53

    def below(self, name: str, n) -> np.ndarray:
        """Whole numbers from 0 up to but not including n. `n` is a number or an array of numbers."""
        return np.minimum((self.u(name) * n).astype(np.int64), np.asarray(n, dtype=np.int64) - 1)

    def between(self, name: str, low: int, high: int) -> np.ndarray:
        """Whole numbers from low to high, both included."""
        return low + self.below(name, high - low + 1)

    def normal(self, name: str) -> np.ndarray:
        """Numbers shaped like a standard normal curve (the sum of four uniforms, rescaled), without the extreme tails."""
        total = self.u(name + ".a") + self.u(name + ".b") + self.u(name + ".c") + self.u(name + ".d")
        return (total - 2.0) * 1.7320508075688772

    def chance(self, name: str, p) -> np.ndarray:
        return self.u(name) < p

    def pick(self, name: str, cumulative: np.ndarray) -> np.ndarray:
        """Which of the choices a set of weights describes, given as a running total scaled to end at 1."""
        return np.minimum(np.searchsorted(cumulative, self.u(name), side="right"), len(cumulative) - 1)


def cumulative(weights) -> np.ndarray:
    w = np.asarray(weights, dtype=np.float64)
    c = np.cumsum(w)
    return c / c[-1]
