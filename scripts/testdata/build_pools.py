"""Rebuild the name and street lists in pools/ from Faker. Run rarely: the lists are committed, so the generator never needs Faker.

The lists are the one thing here that depends on a library's own data, so they are made once, with a fixed seed, and kept. Only
names that can be written in Windows-1252 are kept, so every file the generator makes can also be written in that encoding.
"""
from __future__ import annotations

import csv
from pathlib import Path

from faker import Faker

POOLS = Path(__file__).parent / "pools"
LOCALES = {"en_GB": 700, "en_US": 700, "en_IE": 400, "en_IN": 500}
STREETS = 350


def latin(text: str) -> bool:
    try:
        text.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def collect(make, wanted: int, tries: int = 40000) -> list[str]:
    """Distinct values in the order Faker gave them, so the more common ones come first."""
    seen: dict[str, None] = {}
    for _ in range(tries):
        value = make().strip()
        if value and latin(value) and "\n" not in value and value not in seen:
            seen[value] = None
            if len(seen) >= wanted:
                break
    return list(seen)


def main() -> None:
    Faker.seed(20261009)
    with open(POOLS / "first_names.csv", "w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["locale", "sex", "name"])
        for locale, wanted in LOCALES.items():
            fake = Faker(locale)
            for sex, method in (("F", "first_name_female"), ("M", "first_name_male")):
                for name in collect(getattr(fake, method), wanted // 2):
                    writer.writerow([locale, sex, name])
    with open(POOLS / "last_names.csv", "w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["locale", "name"])
        for locale, wanted in LOCALES.items():
            fake = Faker(locale)
            for name in collect(fake.last_name, wanted):
                writer.writerow([locale, name])
    with open(POOLS / "streets.csv", "w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["locale", "name"])
        for locale in LOCALES:
            fake = Faker(locale)
            for name in collect(fake.street_name, STREETS):
                writer.writerow([locale, name])
    print("pools written to", POOLS)


if __name__ == "__main__":
    main()
