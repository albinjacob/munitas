"""The lists the generator draws from: names, streets, towns, diagnoses and merchant categories. Loaded once, kept as arrays."""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

import numpy as np
import pyarrow as pa

DIR = Path(__file__).parent / "pools"
LOCALES = ("en_GB", "en_US", "en_IE", "en_IN")
# How many people of each locale, as weights: a mix, so names, addresses and phone numbers read as a real population.
LOCALE_WEIGHTS = (50, 30, 8, 12)
CURRENCY = {"en_GB": "GBP", "en_US": "USD", "en_IE": "EUR", "en_IN": "INR"}


def _rows(name: str) -> list[dict]:
    with open(DIR / name, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class ByLocale:
    """One list per group, joined end to end so a pick is one index into one array. A group is a locale, or a locale and a sex."""

    def __init__(self, rows: list[dict], groups: int, group_of, field: str = "name"):
        values = []
        self.offset, self.count = np.zeros(groups, dtype=np.int64), np.zeros(groups, dtype=np.int64)
        for group in range(groups):
            mine = [r[field] for r in rows if group_of(r) == group]
            self.offset[group], self.count[group] = len(values), len(mine)
            values.extend(mine)
        self.values = pa.array(values, pa.string())

    def take(self, group: np.ndarray, u: np.ndarray) -> pa.Array:
        at = self.offset[group] + np.minimum((u * self.count[group]).astype(np.int64), self.count[group] - 1)
        return self.values.take(pa.array(at))


@lru_cache(maxsize=1)
def names() -> dict:
    """First names by locale and sex (group = locale * 2, plus 1 for male), surnames and streets by locale."""
    by_locale = lambda r: LOCALES.index(r["locale"])
    first = ByLocale(_rows("first_names.csv"), len(LOCALES) * 2, lambda r: LOCALES.index(r["locale"]) * 2 + (1 if r["sex"] == "M" else 0))
    return {"first": first, "last": ByLocale(_rows("last_names.csv"), len(LOCALES), by_locale),
            "street": ByLocale(_rows("streets.csv"), len(LOCALES), by_locale)}


@lru_cache(maxsize=1)
def towns() -> dict:
    """Towns per locale, with a running total of their weights so a weighted pick is a search."""
    rows = _rows("towns.csv")
    out = {}
    for number, locale in enumerate(LOCALES):
        mine = [r for r in rows if r["locale"] == locale]
        weights = np.array([float(r["weight"]) for r in mine])
        total = np.cumsum(weights) / weights.sum()
        out[number] = {"town": pa.array([r["town"] for r in mine]), "region": pa.array([r["region"] for r in mine]),
                       "prefix": [r["postcode_prefix"] for r in mine], "cumulative": total}
    return out


@lru_cache(maxsize=1)
def diagnoses() -> list[dict]:
    return [{"code": r["code"], "description": r["description"], "mean_stay_days": int(r["mean_stay_days"]),
             "cost_factor": float(r["cost_factor"]), "weight": float(r["weight"])} for r in _rows("icd10.csv")]


@lru_cache(maxsize=1)
def merchant_categories() -> list[dict]:
    return [{"mcc": r["mcc"], "description": r["description"], "group": r["grp"], "median_amount": float(r["median_amount"]),
             "weight": float(r["weight"])} for r in _rows("mcc.csv")]
